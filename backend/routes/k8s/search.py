"""
Global Kubernetes Resource & Infrastructure Search routes.

Provides unified, multi-cluster search for:
- Hostnames & FQDNs (Ingress hosts, Gateway API HTTPRoutes, BNK VirtualServers, LoadBalancer IPs)
- Kubernetes Workloads & Services
- Infrastructure Clusters & Projects
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError

from fastapi import APIRouter, Depends, Query
from kubernetes import client as k8s_client
from kubernetes.client.rest import ApiException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models.kubernetes import KubernetesCluster
from models.project import Project
from routes.auth import require_viewer
from services.kubernetes import KubernetesService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/k8s", tags=["k8s-search"])


class IngressSearchResult(BaseModel):
    kind: str
    name: str
    namespace: str
    matched_host: str
    all_hosts: list[str] = Field(default_factory=list)
    cluster_id: int
    cluster_name: str
    cloud_provider: str | None = None
    region: str | None = None
    target_service: str | None = None
    status: str = "active"


class ClusterSearchResult(BaseModel):
    id: int
    name: str
    cloud_provider: str | None = None
    region: str | None = None
    status: str
    node_count: int | None = None
    detected_platform_profile: str | None = None


class ProjectSearchResult(BaseModel):
    id: int
    name: str
    description: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    module_count: int = 0
    deployed_count: int = 0
    failed_count: int = 0


class GlobalSearchResultResponse(BaseModel):
    query: str
    ingresses: list[IngressSearchResult] = Field(default_factory=list)
    clusters: list[ClusterSearchResult] = Field(default_factory=list)
    projects: list[ProjectSearchResult] = Field(default_factory=list)


def _scan_cluster_for_query(
    cluster_id: int,
    cluster_name: str,
    cloud_provider: str | None,
    region: str | None,
    query_str: str,
    k8s_svc: KubernetesService,
) -> list[IngressSearchResult]:
    """Scan a single cluster for matching Ingresses, HTTPRoutes, VirtualServers, and Services."""
    results: list[IngressSearchResult] = []
    q = query_str.lower().strip()
    if not q:
        return results

    try:
        cluster = k8s_svc.get_cluster(cluster_id)
        api_client = k8s_svc.load_kubeconfig(cluster)
    except Exception as e:
        logger.debug(f"Skipping cluster {cluster_name} (id={cluster_id}) due to client load error: {e}")
        return results

    # 1. Search Ingresses (networking.k8s.io)
    try:
        networking = k8s_client.NetworkingV1Api(api_client)
        ingresses = networking.list_ingress_for_all_namespaces(_request_timeout=3)
        for ing in (ingresses.items or []):
            name = ing.metadata.name or ""
            ns = ing.metadata.namespace or "default"
            hosts: list[str] = []
            target_svc: str | None = None

            for rule in (ing.spec.rules or []):
                if rule.host:
                    hosts.append(rule.host)
                if rule.http and rule.http.paths:
                    for path in rule.http.paths:
                        if path.backend and path.backend.service:
                            svc_name = path.backend.service.name
                            port = ""
                            if path.backend.service.port:
                                port = f":{path.backend.service.port.number or path.backend.service.port.name or ''}"
                            target_svc = f"{svc_name}{port}"

            for tls in (ing.spec.tls or []):
                if tls.hosts:
                    hosts.extend(tls.hosts)

            unique_hosts = list(dict.fromkeys(hosts))
            # Check for matches
            matched_host = next((h for h in unique_hosts if q in h.lower()), None)
            if not matched_host and (q in name.lower() or q in ns.lower() or (target_svc and q in target_svc.lower())):
                matched_host = unique_hosts[0] if unique_hosts else name

            if matched_host or q in name.lower():
                results.append(IngressSearchResult(
                    kind="Ingress",
                    name=name,
                    namespace=ns,
                    matched_host=matched_host or name,
                    all_hosts=unique_hosts,
                    cluster_id=cluster_id,
                    cluster_name=cluster_name,
                    cloud_provider=cloud_provider,
                    region=region,
                    target_service=target_svc,
                    status="active",
                ))
    except Exception as e:
        logger.debug(f"Ingress scan error on cluster {cluster_name}: {e}")

    # 2. Search Gateway API HTTPRoutes / GRPCRoutes & BNK VirtualServers
    try:
        custom_api = k8s_client.CustomObjectsApi(api_client)
        # HTTPRoutes
        try:
            http_routes = custom_api.list_cluster_custom_object(
                group="gateway.networking.k8s.io",
                version="v1",
                plural="httproutes",
                _request_timeout=3,
            )
            for item in (http_routes.get("items") or []):
                metadata = item.get("metadata", {})
                name = metadata.get("name", "")
                ns = metadata.get("namespace", "default")
                spec = item.get("spec", {})
                hostnames = spec.get("hostnames", [])
                matched_host = next((h for h in hostnames if q in h.lower()), None)
                if matched_host or q in name.lower():
                    results.append(IngressSearchResult(
                        kind="HTTPRoute",
                        name=name,
                        namespace=ns,
                        matched_host=matched_host or (hostnames[0] if hostnames else name),
                        all_hosts=hostnames,
                        cluster_id=cluster_id,
                        cluster_name=cluster_name,
                        cloud_provider=cloud_provider,
                        region=region,
                        target_service="Gateway Route",
                        status="active",
                    ))
        except ApiException:
            pass  # HTTPRoute CRD not installed

        # BNK VirtualServers (k8s.f5.com)
        try:
            vs_items = custom_api.list_cluster_custom_object(
                group="k8s.f5.com",
                version="v1",
                plural="virtualservers",
                _request_timeout=3,
            )
            for item in (vs_items.get("items") or []):
                metadata = item.get("metadata", {})
                name = metadata.get("name", "")
                ns = metadata.get("namespace", "default")
                spec = item.get("spec", {})
                host = spec.get("host", "")
                vip = spec.get("virtualServerAddress", "")
                hosts = [h for h in [host, vip] if h]
                matched_host = next((h for h in hosts if q in h.lower()), None)
                if matched_host or q in name.lower():
                    results.append(IngressSearchResult(
                        kind="VirtualServer",
                        name=name,
                        namespace=ns,
                        matched_host=matched_host or (hosts[0] if hosts else name),
                        all_hosts=hosts,
                        cluster_id=cluster_id,
                        cluster_name=cluster_name,
                        cloud_provider=cloud_provider,
                        region=region,
                        target_service=vip or "BNK VIP",
                        status="active",
                    ))
        except ApiException:
            pass  # VirtualServer CRD not installed

        # F5 SPK Egresses (f5-spk-egresses.k8s.f5net.com)
        try:
            for ver in ["v3", "v1", "v2"]:
                try:
                    egress_items = custom_api.list_cluster_custom_object(
                        group="k8s.f5net.com",
                        version=ver,
                        plural="f5-spk-egresses",
                        _request_timeout=3,
                    )
                    for item in (egress_items.get("items") or []):
                        metadata = item.get("metadata", {})
                        name = metadata.get("name", "")
                        ns = metadata.get("namespace", "default")
                        spec = item.get("spec", {})
                        subnet = spec.get("dnsNat46Ipv4Subnet", "") or spec.get("nat64Ipv6Subnet", "")
                        hosts = [subnet] if subnet else []
                        if q in name.lower() or q in ns.lower() or (subnet and q in subnet.lower()):
                            results.append(IngressSearchResult(
                                kind="Egress",
                                name=name,
                                namespace=ns,
                                matched_host=name,
                                all_hosts=hosts,
                                cluster_id=cluster_id,
                                cluster_name=cluster_name,
                                cloud_provider=cloud_provider,
                                region=region,
                                target_service=subnet or "Egress Gateway",
                                status="active",
                            ))
                    break
                except ApiException:
                    continue
        except Exception as e:
            logger.debug(f"F5 SPK Egress scan error on cluster {cluster_name}: {e}")

        # F5 BNK Gateways & Standard Gateways (gateway.networking.k8s.io / k8s.f5net.com)
        try:
            for grp, ver, pl, kd in [
                ("k8s.f5net.com", "v1", "f5-bnkgateways", "BNKGateway"),
                ("gateway.networking.k8s.io", "v1", "gateways", "Gateway"),
                ("gateway.k8s.f5net.com", "v1", "l4routes", "L4Route"),
            ]:
                try:
                    gw_items = custom_api.list_cluster_custom_object(
                        group=grp,
                        version=ver,
                        plural=pl,
                        _request_timeout=3,
                    )
                    for item in (gw_items.get("items") or []):
                        metadata = item.get("metadata", {})
                        name = metadata.get("name", "")
                        ns = metadata.get("namespace", "default")
                        if q in name.lower() or q in ns.lower():
                            results.append(IngressSearchResult(
                                kind=kd,
                                name=name,
                                namespace=ns,
                                matched_host=name,
                                all_hosts=[],
                                cluster_id=cluster_id,
                                cluster_name=cluster_name,
                                cloud_provider=cloud_provider,
                                region=region,
                                target_service=kd,
                                status="active",
                            ))
                except ApiException:
                    pass
        except Exception as e:
            logger.debug(f"Gateways scan error on cluster {cluster_name}: {e}")
    except Exception as e:
        logger.debug(f"Custom route scan error on cluster {cluster_name}: {e}")

    # 3. Search Services for LoadBalancer / ExternalIPs / VIP matches
    try:
        core_api = k8s_client.CoreV1Api(api_client)
        services = core_api.list_service_for_all_namespaces(_request_timeout=3)
        for svc in (services.items or []):
            name = svc.metadata.name or ""
            ns = svc.metadata.namespace or "default"
            spec = svc.spec
            status = svc.status

            ips: list[str] = []
            if spec.cluster_ip and spec.cluster_ip != "None":
                ips.append(spec.cluster_ip)
            if spec.external_i_ps:
                ips.extend(spec.external_i_ps)
            if status and status.load_balancer and status.load_balancer.ingress:
                for lb in status.load_balancer.ingress:
                    if lb.ip:
                        ips.append(lb.ip)
                    if lb.hostname:
                        ips.append(lb.hostname)

            matched_ip = next((ip for ip in ips if q in ip.lower()), None)
            if matched_ip or (q in name.lower() and spec.type in ["LoadBalancer", "NodePort"]):
                results.append(IngressSearchResult(
                    kind="Service",
                    name=name,
                    namespace=ns,
                    matched_host=matched_ip or name,
                    all_hosts=ips,
                    cluster_id=cluster_id,
                    cluster_name=cluster_name,
                    cloud_provider=cloud_provider,
                    region=region,
                    target_service=f"{name} ({spec.type})",
                    status="active",
                ))
    except Exception as e:
        logger.debug(f"Service scan error on cluster {cluster_name}: {e}")

    return results


@router.get(
    "/search",
    response_model=GlobalSearchResultResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("global k8s search")
def global_search(
    q: str = Query(..., min_length=1, description="Search query string (FQDN, hostname, IP, cluster, project)"),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Unified global multi-cluster and infrastructure search.

    Searches across:
    - Ingresses, HTTPRoutes, VirtualServers, and Services in all reachable clusters
    - Clusters (by name, cloud provider, region)
    - Projects (by name, description, cloud provider, region)
    """
    clean_q = q.strip()
    clean_q_lower = clean_q.lower()

    # 1. DB Search: Clusters
    all_db_clusters = db.query(KubernetesCluster).all()
    matching_clusters: list[ClusterSearchResult] = []
    active_clusters_to_scan: list[KubernetesCluster] = []

    for c in all_db_clusters:
        if (
            clean_q_lower in c.name.lower()
            or (c.cloud_provider and clean_q_lower in c.cloud_provider.lower())
            or (c.region and clean_q_lower in c.region.lower())
            or (c.detected_platform_profile and clean_q_lower in c.detected_platform_profile.lower())
        ):
            matching_clusters.append(
                ClusterSearchResult(
                    id=c.id,
                    name=c.name,
                    cloud_provider=c.cloud_provider,
                    region=c.region,
                    status=c.status or "active",
                    node_count=c.node_count,
                    detected_platform_profile=c.detected_platform_profile,
                )
            )
        if (c.status or "active").lower() == "active":
            active_clusters_to_scan.append(c)

    # 2. DB Search: Projects & OpenTofu Modules
    all_db_projects = db.query(Project).all()
    matching_projects: list[ProjectSearchResult] = []
    for p in all_db_projects:
        matched = (
            clean_q_lower in p.name.lower()
            or (p.description and clean_q_lower in p.description.lower())
            or (p.cloud_provider and clean_q_lower in p.cloud_provider.lower())
            or (p.project_type and clean_q_lower in p.project_type.lower())
        )
        if not matched and p.project_modules:
            for pm in p.project_modules:
                if (
                    (pm.path_in_project and clean_q_lower in pm.path_in_project.lower())
                    or (pm.library_module and clean_q_lower in pm.library_module.name.lower())
                ):
                    matched = True
                    break

        if matched:
            matching_projects.append(
                ProjectSearchResult(
                    id=p.id,
                    name=p.name,
                    description=p.description,
                    cloud_provider=p.cloud_provider or p.project_type,
                    region=None,
                    module_count=p.module_count or len(p.project_modules or []),
                    deployed_count=p.deployed_count or 0,
                    failed_count=p.failed_count or 0,
                )
            )

    # 3. Parallel Live Cluster Scanning
    k8s_svc = KubernetesService(db)
    found_ingresses: list[IngressSearchResult] = []

    if active_clusters_to_scan:
        with ThreadPoolExecutor(max_workers=min(10, len(active_clusters_to_scan))) as executor:
            futures = [
                executor.submit(
                    _scan_cluster_for_query,
                    cluster.id,
                    cluster.name,
                    cluster.cloud_provider,
                    cluster.region,
                    clean_q,
                    k8s_svc,
                )
                for cluster in active_clusters_to_scan
            ]
            try:
                for future in as_completed(futures, timeout=6.0):
                    try:
                        cluster_results = future.result()
                        found_ingresses.extend(cluster_results)
                    except Exception as e:
                        logger.debug(f"Search thread failed: {e}")
            except (TimeoutError, FuturesTimeoutError, Exception) as e:
                logger.debug(f"Search cluster scanning interrupted or timed out: {e}")
                # Harvest whichever futures have already completed
                for future in futures:
                    if future.done() and not future.cancelled():
                        try:
                            cluster_results = future.result()
                            found_ingresses.extend(cluster_results)
                        except Exception:
                            pass

    # Deduplicate live resource results
    seen_keys: set[tuple[str, int, str, str]] = set()
    deduped_ingresses: list[IngressSearchResult] = []
    for ing in found_ingresses:
        key = (ing.kind, ing.cluster_id, ing.namespace, ing.name)
        if key not in seen_keys:
            seen_keys.add(key)
            deduped_ingresses.append(ing)

    return GlobalSearchResultResponse(
        query=clean_q,
        ingresses=deduped_ingresses[:limit],
        clusters=matching_clusters[:limit],
        projects=matching_projects[:limit],
    )
