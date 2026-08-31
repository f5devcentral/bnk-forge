"""
F5 BNK (BIG-IP Next for Kubernetes) routes.

All insight views (Health, Topology, Policy Map) are powered by a single
unified data fetch via bnk_data_service. The /f5bnk/data endpoint fetches
all 16 CRD types + pods once, then each view applies its own analysis.

Frontend uses a shared React Query cache key so switching tabs doesn't
re-fetch from the cluster.
"""

import logging

from fastapi import APIRouter, Depends
from kubernetes import client as k8s_client
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from schemas.bnk import BnkDataResponse, BnkHealthEndpointResponse
from schemas.k8s import (
    AbortMigrationRequest,
    CisTranslateRequest,
    ConfirmCutoverRequest,
    ConfirmTeardownRequest,
    CreateMigrationRequest,
    MigrationActionResponse,
    ProxyMigrationListResponse,
    ProxyMigrationResponse,
    ProxyMigrationStepResponse,
    ProxyTranslateRequest,
    ProxyTranslateResponse,
    ProxyTranslateUnmapped,
)
from services.analyzer_metrics_service import get_analyzer_runtime_metrics
from services.backend_health_service import get_backend_health
from services.bnk.a2a_discovery import discover_a2a_agents
from services.bnk_data_service import (
    analyze_backends,
    analyze_health,
    analyze_policy_associations,
    analyze_topology,
    extract_palette_data,
    fetch_all_bnk_data,
)
from services.kubernetes_service import KubernetesService
from services.proxy_discovery_service import (
    _safe_list_all_custom,
    _safe_list_all_ingresses,
)
from services.proxy_translate_service import translate_to_bnk

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-f5bnk"])


# ============================================================================
# Unified BNK Data Endpoint
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/f5bnk/data",
    response_model=BnkDataResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch BNK data")
def get_bnk_data(
    cluster_id: int,
    namespace: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Unified BNK data endpoint — fetches all 16 CRD types + pods once.

    Returns health analysis, topology graph, and policy associations
    in a single response. The frontend caches this under one query key
    so switching between Health, Topology, and Policy Map tabs is instant.
    """
    k8s_service = KubernetesService(db)
    data = fetch_all_bnk_data(k8s_service, cluster_id, namespace, include_nodes=True)

    health = analyze_health(data)
    topo = analyze_topology(data)
    policy = analyze_policy_associations(data)
    backends = analyze_backends(data, topo["topology"])
    palette = extract_palette_data(data)

    return {
        "health": health,
        "topology": topo["topology"],
        "dataPlane": topo["dataPlane"],
        "referenceGrants": topo.get("referenceGrants", []),
        "topologyCounts": topo["counts"],
        "policyAssociations": policy["associations"],
        "policyCount": policy["count"],
        "backends": backends,
        "palette": palette,
        "cluster_id": cluster_id,
        "namespace": namespace,
    }


# ============================================================================
# Individual endpoints (kept for backward compatibility / direct access)
# These now delegate to the shared data service.
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/f5bnk/health",
    response_model=BnkHealthEndpointResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get BNK health")
def get_bnk_health(
    cluster_id: int,
    namespace: str | None = None,
    db: Session = Depends(get_db),
):
    """BNK health dashboard — delegates to shared data service."""
    k8s_service = KubernetesService(db)
    data = fetch_all_bnk_data(k8s_service, cluster_id, namespace, include_nodes=True)
    result = analyze_health(data)
    result["cluster_id"] = cluster_id
    return result


@router.get("/k8s/clusters/{cluster_id}/f5bnk/gateway-topology", dependencies=[Depends(require_viewer)])
@handle_route_errors("get gateway topology")
def get_gateway_topology(
    cluster_id: int,
    namespace: str | None = None,
    db: Session = Depends(get_db),
):
    """Gateway topology graph — delegates to shared data service."""
    k8s_service = KubernetesService(db)
    data = fetch_all_bnk_data(k8s_service, cluster_id, namespace)
    result = analyze_topology(data)
    result["cluster_id"] = cluster_id
    result["namespace"] = namespace
    return result


@router.get("/k8s/clusters/{cluster_id}/f5bnk/policy-gateway-associations", dependencies=[Depends(require_viewer)])
@handle_route_errors("get policy-gateway associations")
def get_policy_gateway_associations(
    cluster_id: int,
    namespace: str | None = None,
    db: Session = Depends(get_db),
):
    """Policy-gateway associations — delegates to shared data service."""
    k8s_service = KubernetesService(db)
    data = fetch_all_bnk_data(k8s_service, cluster_id, namespace)
    result = analyze_policy_associations(data)
    result["cluster_id"] = cluster_id
    result["namespace"] = namespace
    return result


# ============================================================================
# F5BigAnalyzer runtime metrics (Prometheus proxy)
# ============================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/analyzers/{namespace}/{name}/runtime-metrics",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch analyzer runtime metrics")
def get_analyzer_metrics(
    cluster_id: int,
    namespace: str,
    name: str,
    db: Session = Depends(get_db),
):
    """
    Runtime metrics for one F5BigAnalyzer, sourced from the Prometheus
    endpoint declared on its ``spec.dataSources[]``. Queries are proxied
    through the K8s API-server service-proxy so Prometheus doesn't need
    external exposure.

    Response shape is fixed even on error — frontend renders gracefully
    when ``available: false``.
    """
    return get_analyzer_runtime_metrics(db, cluster_id, namespace, name)


@router.get(
    "/k8s/clusters/{cluster_id}/analyzers/{namespace}/{name}/backend-health",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("fetch analyzer backend health")
def get_backend_health_route(
    cluster_id: int,
    namespace: str,
    name: str,
    db: Session = Depends(get_db),
):
    """
    Per-pod inference-backend health for one F5BigAnalyzer's monitored apps.

    Walks ``spec.applications[]`` → HTTPRoute → backendRefs[] → matching
    Services, then subprocesses ``llmtop --once --output json`` scoped to
    those pods. Returns one row per pod with KV cache, queue depth, latency
    percentiles, and token throughput. GPU fields are null until a DCGM
    exporter is wired (intentional — Bedrock shims have no GPU).
    """
    return get_backend_health(db, cluster_id, namespace, name)


# ============================================================================
# A2A Protocol
# ============================================================================

@router.get("/k8s/clusters/{cluster_id}/f5bnk/a2a/agents", dependencies=[Depends(require_viewer)])
@handle_route_errors("discover A2A agents")
def get_a2a_agents(
    cluster_id: int,
    namespace: str | None = None,
    probe: bool = False,
    db: Session = Depends(get_db),
):
    """
    Discover A2A agent candidates behind BNK Gateways.

    Scans HTTPRoute backends for services that could be A2A agents.
    With ``probe=true``, attempts to fetch ``/.well-known/agent-card.json``
    from each backend service via the K8s service proxy API.

    Reuses the shared BNK data fetch + topology analysis — no extra
    K8s API calls for the candidate list (probe adds one call per service).
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster) if probe else None

    data = fetch_all_bnk_data(k8s_service, cluster_id, namespace)
    topo = analyze_topology(data)

    agents = discover_a2a_agents(
        data, topo["topology"],
        api_client=api_client,
        probe=probe,
    )

    return {
        "agents": agents,
        "count": len(agents),
        "probed": probe,
        "cluster_id": cluster_id,
        "namespace": namespace,
    }


# ============================================================================
# Proxy Translation (D-021 P2)
# ============================================================================

@router.post(
    "/k8s/clusters/{cluster_id}/proxies/translate",
    response_model=ProxyTranslateResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("translate proxy to BNK")
def translate_proxy_to_bnk(
    cluster_id: int,
    body: ProxyTranslateRequest,
    db: Session = Depends(get_db),
) -> ProxyTranslateResponse:
    """Preview BNK Gateway API equivalent of an existing proxy.

    Re-fetches live Ingress/HTTPRoute objects from the cluster, then calls
    the pure translator.  Read-only — no cluster mutations.

    Returns rendered GatewayClass + Gateway + HTTPRoute YAML plus a list of
    unmapped/lossy constructs (D-017: never silently dropped).
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    # --- Re-fetch live source objects (all I/O lives here, not in translator) ---
    networking = k8s_client.NetworkingV1Api(api_client)
    custom = k8s_client.CustomObjectsApi(api_client)

    # Fetch Ingress objects (for IngressClass-backed proxies)
    raw_ingresses = _safe_list_all_ingresses(networking)
    # Filter to ingresses that match the requested class
    class_name = body.class_name
    source_ingresses = [
        ing for ing in raw_ingresses
        if (
            (ing.spec.ingress_class_name or "").lower() == class_name.lower()
            or (ing.metadata.annotations or {}).get(
                "kubernetes.io/ingress.class", ""
            ).lower() == class_name.lower()
        )
    ]

    # Fetch HTTPRoutes (for GatewayClass-backed proxies)
    raw_httproutes = _safe_list_all_custom(
        custom,
        group="gateway.networking.k8s.io",
        version="v1",
        plural="httproutes",
    )

    # Fetch Gateways (for listener reconstruction)
    raw_gateways = _safe_list_all_custom(
        custom,
        group="gateway.networking.k8s.io",
        version="v1",
        plural="gateways",
    )

    # Filter HTTPRoutes to those whose parent Gateway belongs to the requested class
    matching_gw_names: set[str] = set()
    source_gateways: list[dict] = []
    for gw in raw_gateways:
        gw_class = gw.get("spec", {}).get("gatewayClassName", "")
        if gw_class.lower() == class_name.lower():
            gw_name = gw.get("metadata", {}).get("name", "")
            matching_gw_names.add(gw_name)
            source_gateways.append(gw)

    source_httproutes: list[dict] = []
    for route in raw_httproutes:
        for parent in route.get("spec", {}).get("parentRefs", []):
            if parent.get("name") in matching_gw_names:
                source_httproutes.append(route)
                break

    # --- Call pure translator ---
    result = translate_to_bnk(
        proxy_type=body.proxy_type,
        source_kind=body.source_kind,
        source_ingresses=source_ingresses,
        source_httproutes=source_httproutes,
        source_gateways=source_gateways,
        gateway_class_name=body.gateway_class_name,
        gateway_name=body.gateway_name,
        target_namespace=body.target_namespace,
    )

    logger.info(
        "Proxy translation: cluster=%d proxy_type=%s class=%s "
        "ingresses=%d httproutes=%d unmapped=%d",
        cluster_id, body.proxy_type, class_name,
        len(source_ingresses), len(source_httproutes), len(result.unmapped),
    )

    return ProxyTranslateResponse(
        cluster_id=cluster_id,
        proxy_type=body.proxy_type,
        source_kind=body.source_kind,
        class_name=class_name,
        gatewayclass_yaml=result.gatewayclass_yaml,
        gateway_yaml=result.gateway_yaml,
        httproute_yaml=result.httproute_yaml,
        combined_yaml=result.combined_yaml,
        unmapped=[
            ProxyTranslateUnmapped(
                source_kind=u.source_kind,
                source_name=u.source_name,
                source_namespace=u.source_namespace,
                construct_type=u.construct,
                detail=u.detail,
                reason=u.reason,
            )
            for u in result.unmapped
        ],
        source=result.source,
    )


# ============================================================================
# CIS Translation (D-023 P3) — preview CIS VirtualServer/TransportServer → BNK
# ============================================================================

@router.post(
    "/k8s/clusters/{cluster_id}/proxies/cis/translate",
    response_model=ProxyTranslateResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("translate CIS proxy to BNK")
def translate_cis_to_bnk_route(
    cluster_id: int,
    body: CisTranslateRequest,
    db: Session = Depends(get_db),
) -> ProxyTranslateResponse:
    """Preview BNK Gateway API equivalent of CIS VirtualServer/TransportServer CRs.

    Re-fetches full CIS CR specs from the cluster (P1 stubs are name/ns only),
    then calls the pure CIS translator.  Read-only — no cluster mutations.

    Returns rendered GatewayClass + Gateway + HTTPRoute (+TCPRoute/UDPRoute) YAML
    plus a list of unmapped/lossy constructs (D-017: never silently dropped).

    ESCALATION: Policy / policyName / waf / botDefense / firewallPolicy refs are
    routed to unmapped[] — the exact BNK security CRD kind is unconfirmed.
    """
    from services.proxy_translate_cis_service import translate_cis_to_bnk
    from services.scanner.fetch import (
        _fetch_cis_as3_configmaps,
        _fetch_cis_crs_full,
        _fetch_cis_f5_ingresses,
        _fetch_cis_ingresslinks_full,
        _fetch_cis_tlsprofiles_full,
        _fetch_openshift_routes,
    )

    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    source_mode = (body.source_mode or "virtualserver").lower()

    # Re-fetch full VS + TS CR specs (all I/O lives here; translator is pure)
    source_vs: list[dict] = []
    source_ts: list[dict] = []
    as3_declarations: list[dict] = []
    source_ingresses: list[dict] = []
    source_routes: list[dict] = []
    source_ingresslinks: list[dict] = []
    source_tlsprofiles: list[dict] = []
    malformed_parse_errors: dict[str, str] = {}

    if source_mode in ("virtualserver", "both"):
        all_vs = _fetch_cis_crs_full(api_client, "virtualservers")
        all_ts = _fetch_cis_crs_full(api_client, "transportservers")
        # Also fetch TLSProfiles for VS tlsProfileName resolution
        source_tlsprofiles = _fetch_cis_tlsprofiles_full(api_client)

        # Filter by requested names / namespace (empty list = translate all)
        def _filter_crs(items: list[dict], names: list[str], ns: str | None) -> list[dict]:
            out = items
            if ns:
                out = [i for i in out if (i.get("metadata") or {}).get("namespace") == ns]
            if names:
                name_set = set(names)
                out = [i for i in out if (i.get("metadata") or {}).get("name") in name_set]
            return out

        source_vs = _filter_crs(all_vs, body.vs_names, body.vs_namespace)
        source_ts = _filter_crs(all_ts, body.ts_names, body.ts_namespace)

    if source_mode in ("ingresslink", "both"):
        raw_ingresslinks = _fetch_cis_ingresslinks_full(api_client)
        source_ingresslinks = raw_ingresslinks

    if source_mode in ("as3_configmap", "both"):
        raw_configmaps = _fetch_cis_as3_configmaps(api_client)
        # Filter by namespace / name
        if body.configmap_namespace:
            raw_configmaps = [
                cm for cm in raw_configmaps
                if cm.get("namespace") == body.configmap_namespace
            ]
        if body.configmap_names:
            cm_name_set = set(body.configmap_names)
            raw_configmaps = [cm for cm in raw_configmaps if cm.get("name") in cm_name_set]

        # Build map of source_name → parse_error for post-translation detail patching
        for cm in raw_configmaps:
            if cm.get("template_parse_error") or cm.get("template") is None:
                # Malformed template → emit a seed unmapped entry via AS3 parser
                # by passing an invalid (empty) declaration; the parser handles it
                src_name = f"{cm.get('namespace','')}/{cm.get('name','')}"
                parse_err = cm.get("template_parse_error") or "missing template key"
                malformed_parse_errors[src_name] = parse_err
                as3_declarations.append({
                    "_source_kind": "AS3ConfigMap",
                    "_source_name": src_name,
                    "_source_namespace": cm.get("namespace", ""),
                    "_template_parse_error": parse_err,
                    # Intentionally empty class to trigger parse-error path
                    "class": None,
                })
            else:
                template = cm["template"]
                # Detect legacy non-AS3 ConfigMap schema (frontend/backend keys, no class:AS3)
                top_class = template.get("class") if isinstance(template, dict) else None
                is_legacy = (
                    isinstance(template, dict)
                    and top_class not in ("AS3", "ADC")
                    and (
                        "frontend" in template
                        or "backend" in template
                        or "virtualServer" in template
                        or "class" not in template
                    )
                )
                if is_legacy:
                    src_name = f"{cm.get('namespace','')}/{cm.get('name','')}"
                    parse_err = (
                        "legacy non-AS3 F5 ConfigMap schema (frontend/backend) is not "
                        "auto-translated; migrate it to AS3 or recreate as a VirtualServer CR"
                    )
                    malformed_parse_errors[src_name] = parse_err
                    as3_declarations.append({
                        "_source_kind": "AS3ConfigMap",
                        "_source_name": src_name,
                        "_source_namespace": cm.get("namespace", ""),
                        "_template_parse_error": parse_err,
                        # No-valid-class → triggers parse-error unmapped path
                        "class": "legacy-non-as3",
                    })
                else:
                    decl = dict(template)
                    decl["_source_kind"] = "AS3ConfigMap"
                    decl["_source_name"] = f"{cm.get('namespace','')}/{cm.get('name','')}"
                    decl["_source_namespace"] = cm.get("namespace", "")
                    as3_declarations.append(decl)

    if source_mode in ("ingress", "both"):
        raw_ingresses = _fetch_cis_f5_ingresses(api_client)
        # Filter by namespace / name
        if body.ingress_namespace:
            raw_ingresses = [
                ing for ing in raw_ingresses
                if (ing.get("metadata") or {}).get("namespace") == body.ingress_namespace
            ]
        if body.ingress_names:
            ing_name_set = set(body.ingress_names)
            raw_ingresses = [
                ing for ing in raw_ingresses
                if (ing.get("metadata") or {}).get("name") in ing_name_set
            ]
        source_ingresses = raw_ingresses

    if source_mode in ("route", "both"):
        raw_routes = _fetch_openshift_routes(api_client)
        # Filter by namespace / name
        if body.route_namespace:
            raw_routes = [
                r for r in raw_routes
                if (r.get("metadata") or {}).get("namespace") == body.route_namespace
            ]
        if body.route_names:
            route_name_set = set(body.route_names)
            raw_routes = [
                r for r in raw_routes
                if (r.get("metadata") or {}).get("name") in route_name_set
            ]
        source_routes = raw_routes

    # Determine the honest source_kind for the response
    if source_mode == "as3_configmap":
        resp_source_kind = "AS3ConfigMap"
    elif source_mode == "ingress":
        resp_source_kind = "Ingress"
    elif source_mode == "route":
        resp_source_kind = "Route"
    elif source_mode == "ingresslink":
        resp_source_kind = "IngressLink"
    elif source_mode == "both":
        resp_source_kind = "Mixed"
    else:
        resp_source_kind = "VirtualServer"

    # Call pure translator
    result = translate_cis_to_bnk(
        virtualservers=source_vs,
        transportservers=source_ts,
        as3_declarations=as3_declarations,
        ingresses=source_ingresses,
        routes=source_routes,
        ingresslinks=source_ingresslinks,
        tlsprofiles=source_tlsprofiles,
        gateway_class_name=body.gateway_class_name,
        gateway_name=body.gateway_name,
        target_namespace=body.target_namespace,
    )

    # Patch detail on malformed-template unmapped entries to show the real parse error
    # instead of the generic "class=None" that the parser emits for the seed dict.
    if malformed_parse_errors:
        for u in result.unmapped:
            if u.construct == "as3_parse_error" and u.source_name in malformed_parse_errors:
                u.detail = malformed_parse_errors[u.source_name]

    logger.info(
        "CIS translation: cluster=%d mode=%s vs=%d ts=%d as3=%d ing=%d routes=%d il=%d unmapped=%d",
        cluster_id, source_mode, len(source_vs), len(source_ts),
        len(as3_declarations), len(source_ingresses), len(source_routes),
        len(source_ingresslinks), len(result.unmapped),
    )

    # Derive a class_name for the response
    if source_vs:
        first_vs_meta = (source_vs[0].get("metadata") or {})
        class_name = first_vs_meta.get("name") or "cis-virtualserver"
    elif as3_declarations:
        class_name = as3_declarations[0].get("_source_name") or "cis-as3"
    elif source_ingresses:
        ing_meta = (source_ingresses[0].get("metadata") or {})
        class_name = ing_meta.get("name") or "cis-ingress"
    elif source_routes:
        route_meta = (source_routes[0].get("metadata") or {})
        class_name = route_meta.get("name") or "cis-route"
    elif source_ingresslinks:
        il_meta = (source_ingresslinks[0].get("metadata") or {})
        class_name = il_meta.get("name") or "cis-ingresslink"
    else:
        class_name = "cis-virtualserver"

    return ProxyTranslateResponse(
        cluster_id=cluster_id,
        proxy_type="cis-bigip",
        source_kind=resp_source_kind,
        class_name=class_name,
        gatewayclass_yaml=result.gatewayclass_yaml,
        gateway_yaml=result.gateway_yaml,
        httproute_yaml=result.httproute_yaml,
        combined_yaml=result.combined_yaml,
        unmapped=[
            ProxyTranslateUnmapped(
                source_kind=u.source_kind,
                source_name=u.source_name,
                source_namespace=u.source_namespace,
                construct_type=u.construct,
                detail=u.detail,
                reason=u.reason,
            )
            for u in result.unmapped
        ],
        source=result.source,
    )


# ============================================================================
# Proxy Migration (D-021 P3) — guided, gated, reversible proxy-to-BNK cutover
# ============================================================================

def _migration_to_response(m) -> ProxyMigrationResponse:  # type: ignore[return]
    """Convert a ProxyMigration ORM row to a ProxyMigrationResponse."""
    return ProxyMigrationResponse(
        id=m.id,
        cluster_id=m.cluster_id,
        project_id=m.project_id,
        source_descriptor=m.source_descriptor or {},
        bnk_gateway_name=m.bnk_gateway_name,
        bnk_gateway_namespace=m.bnk_gateway_namespace,
        verify_result=m.verify_result,
        teardown_info=m.teardown_info,
        status=m.status,
        current_step=m.current_step,
        error_message=m.error_message,
        error_step=m.error_step,
        triggered_by=m.triggered_by,
        cutover_confirmed_by=m.cutover_confirmed_by,
        cutover_confirmed_at=m.cutover_confirmed_at.isoformat() if m.cutover_confirmed_at else None,
        created_at=m.created_at.isoformat() if m.created_at else None,
        started_at=m.started_at.isoformat() if m.started_at else None,
        completed_at=m.completed_at.isoformat() if m.completed_at else None,
        duration_seconds=m.duration_seconds,
        steps=[
            ProxyMigrationStepResponse(
                id=s.id,
                step_index=s.step_index,
                name=s.name,
                description=s.description,
                status=s.status,
                requires_confirm=s.requires_confirm,
                output_log=s.output_log,
                result_data=s.result_data,
                error_message=s.error_message,
                started_at=s.started_at.isoformat() if s.started_at else None,
                completed_at=s.completed_at.isoformat() if s.completed_at else None,
                duration_seconds=s.duration_seconds,
            )
            for s in (m.steps or [])
        ],
    )


@router.get(
    "/k8s/clusters/{cluster_id}/proxies/migrations",
    response_model=ProxyMigrationListResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list proxy migrations")
def list_proxy_migrations(
    cluster_id: int,
    project_id: int | None = None,
    db: Session = Depends(get_db),
) -> ProxyMigrationListResponse:
    """List all proxy migration runs for a cluster."""
    from services.proxy_migration_service import ProxyMigrationService

    svc = ProxyMigrationService(db)
    migrations = svc.list_migrations(cluster_id, project_id=project_id)
    return ProxyMigrationListResponse(
        migrations=[_migration_to_response(m) for m in migrations],
        count=len(migrations),
        cluster_id=cluster_id,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/proxies/migrations",
    response_model=ProxyMigrationResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("create proxy migration")
def create_proxy_migration(
    cluster_id: int,
    body: CreateMigrationRequest,
    db: Session = Depends(get_db),
) -> ProxyMigrationResponse:
    """Create and start a guided proxy-to-BNK migration run.

    Caller must provide combined_yaml from the translate endpoint.
    The migration is created and the execute Celery task is enqueued immediately.
    """
    from services.proxy_migration_service import ProxyMigrationService
    from tasks.proxy_migration_tasks import execute_proxy_migration

    svc = ProxyMigrationService(db)
    migration = svc.create_migration(
        cluster_id=cluster_id,
        source_descriptor=body.source_descriptor,
        combined_yaml=body.combined_yaml,
        project_id=body.project_id,
        teardown_info=body.teardown_info,
    )
    db.commit()

    execute_proxy_migration.delay(migration.id)

    logger.info("Proxy migration %d created for cluster %d", migration.id, cluster_id)
    return _migration_to_response(migration)


@router.get(
    "/k8s/clusters/{cluster_id}/proxies/migrations/{migration_id}",
    response_model=ProxyMigrationResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get proxy migration")
def get_proxy_migration(
    cluster_id: int,
    migration_id: int,
    db: Session = Depends(get_db),
) -> ProxyMigrationResponse:
    """Get a proxy migration run by ID."""
    from services.proxy_migration_service import ProxyMigrationService

    svc = ProxyMigrationService(db)
    m = svc.get_migration(migration_id)
    if m.cluster_id != cluster_id:
        from core.errors import NotFoundError
        raise NotFoundError("ProxyMigration", migration_id)
    return _migration_to_response(m)


@router.post(
    "/k8s/clusters/{cluster_id}/proxies/migrations/{migration_id}/confirm-cutover",
    response_model=MigrationActionResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("confirm proxy migration cutover")
def confirm_proxy_migration_cutover(
    cluster_id: int,
    migration_id: int,
    body: ConfirmCutoverRequest,
    db: Session = Depends(get_db),
) -> MigrationActionResponse:
    """Record operator confirmation that DNS/VIP has been pointed to the BNK gateway.

    LAYER 1 GATE: only allowed when migration status == awaiting_cutover.
    Forge does NOT flip DNS/VIP — this records the operator's external action.
    After confirmation, the execute Celery task is re-enqueued to continue.
    """
    from core.errors import NotFoundError
    from services.proxy_migration_service import ProxyMigrationService
    from tasks.proxy_migration_tasks import execute_proxy_migration

    svc = ProxyMigrationService(db)
    # Ownership check BEFORE mutation (TOCTOU guard)
    m = svc.get_migration(migration_id)
    if m.cluster_id != cluster_id:
        raise NotFoundError("ProxyMigration", migration_id)

    migration = svc.confirm_cutover(migration_id, confirmed_by=body.confirmed_by)
    execute_proxy_migration.delay(migration_id)

    return MigrationActionResponse(
        success=True,
        message=f"Cutover confirmed by {body.confirmed_by} — continuing migration",
        migration_id=migration_id,
        status=migration.status,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/proxies/migrations/{migration_id}/confirm-teardown",
    response_model=MigrationActionResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("confirm proxy migration teardown")
def confirm_proxy_migration_teardown(
    cluster_id: int,
    migration_id: int,
    body: ConfirmTeardownRequest,
    db: Session = Depends(get_db),
) -> MigrationActionResponse:
    """Confirm teardown of the old proxy after cutover.

    Only allowed when status == awaiting_teardown.
    After confirmation, the execute Celery task is re-enqueued to run teardown_old.
    """
    from core.errors import NotFoundError
    from services.proxy_migration_service import ProxyMigrationService
    from tasks.proxy_migration_tasks import execute_proxy_migration

    svc = ProxyMigrationService(db)
    # Ownership check BEFORE mutation (TOCTOU guard)
    m = svc.get_migration(migration_id)
    if m.cluster_id != cluster_id:
        raise NotFoundError("ProxyMigration", migration_id)

    migration = svc.confirm_teardown(migration_id, confirmed_by=body.confirmed_by)
    execute_proxy_migration.delay(migration_id)

    return MigrationActionResponse(
        success=True,
        message=f"Teardown confirmed by {body.confirmed_by} — old proxy will be removed",
        migration_id=migration_id,
        status=migration.status,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/proxies/migrations/{migration_id}/abort",
    response_model=MigrationActionResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("abort proxy migration")
def abort_proxy_migration(
    cluster_id: int,
    migration_id: int,
    body: AbortMigrationRequest,
    db: Session = Depends(get_db),
) -> MigrationActionResponse:
    """Abort a proxy migration run.

    Pre-cutover: deletes any BNK resources we applied (old proxy untouched).
    Post-cutover: records ABORTED with manual-fallback instructions — forge
    cannot flip DNS/VIP back automatically.
    """
    from core.errors import NotFoundError
    from services.proxy_migration_service import ProxyMigrationService

    svc = ProxyMigrationService(db)
    # Ownership check BEFORE mutation (TOCTOU guard — mirrors confirm-cutover/teardown)
    m = svc.get_migration(migration_id)
    if m.cluster_id != cluster_id:
        raise NotFoundError("ProxyMigration", migration_id)

    migration = svc.abort_migration(migration_id, by=body.by)

    return MigrationActionResponse(
        success=True,
        message=migration.error_message or f"Migration {migration_id} aborted",
        migration_id=migration_id,
        status=migration.status,
    )
