"""
Cluster Management Service — DB and business logic for Kubernetes cluster management.

Extracted from routes/k8s/clusters.py to separate HTTP handling from domain logic.

Covers:
- Cluster CRUD (create, list, get, update, delete)
- EKS auto-detection and registration
- Kubeconfig refresh
"""

import base64
import logging
import os
import subprocess
import tempfile
from typing import Any
from urllib.parse import urlparse

import yaml
from sqlalchemy import func

from core.encryption import decrypt_value, encrypt_value
from core.errors import BadRequestError, ConflictError, InternalError, classify_aws_subprocess_stderr
from models import KubernetesCluster
from services.base_service import BaseService
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.platform_context_service import PlatformContextService

logger = logging.getLogger(__name__)


class ClusterManagementService(BaseService):
    """Service layer for Kubernetes cluster management operations."""

    # _get_cluster() inherited from BaseService

    @staticmethod
    def _is_roks_managed_cluster_module(module_library) -> bool:
        """Identify IBM/ROKS managed-cluster modules from metadata and outputs."""
        name = (getattr(module_library, "name", "") or "").lower()
        provider = (getattr(module_library, "provider", "") or "").lower()
        category = (getattr(module_library, "category", "") or "").lower()
        outputs_meta = getattr(module_library, "outputs_metadata", None)
        if isinstance(outputs_meta, dict):
            key_outputs = outputs_meta.get("key_outputs", [])
        elif isinstance(outputs_meta, list):
            key_outputs = outputs_meta
        else:
            key_outputs = []
        output_names = {
            str(item.get("name", "")).lower()
            for item in key_outputs
            if isinstance(item, dict)
        }

        if name == "roks":
            return True

        if provider != "ibm":
            return False

        if category != "infra":
            return False

        required_output_markers = {
            "cluster_name",
            "cluster_id",
            "openshift_cluster_public_endpoint",
        }
        return required_output_markers.issubset(output_names)

    @classmethod
    def _classify_managed_cluster_module(cls, module_library) -> str | None:
        """Return the managed cluster type handled by detection, if any."""
        name = (getattr(module_library, "name", "") or "").lower()
        if name == "eks":
            return "eks"
        if cls._is_roks_managed_cluster_module(module_library):
            return "roks"
        return None

    # ================================================================
    # Managed Cluster Auto-Detection
    # ================================================================

    def detect_managed_clusters(self, project_id: int) -> dict[str, Any]:
        """Detect deployed managed-cluster modules in the project and register them."""
        from models import ModuleLibrary, ProjectModule
        from services.eks_service import matches_eks_output_contract, register_eks_cluster

        self._get_project(project_id)

        candidate_modules = self.db.query(ProjectModule).join(ModuleLibrary).filter(
            ProjectModule.project_id == project_id,
            ProjectModule.status == "applied",
            ProjectModule.outputs.isnot(None)
        ).all()

        def _effective_managed_type(module) -> str | None:
            """Return the effective managed-cluster type for a candidate module.

            Checks the name-based classifier first, then falls back to an output-
            contract check for EKS (supports modules whose library name is not "eks"
            but whose outputs satisfy the EKS registration contract, e.g.
            ``aws_eks_cluster_create`` in the opinionated EKS blueprint).
            """
            name_type = self._classify_managed_cluster_module(module.library_module)
            if name_type is not None:
                return name_type
            if matches_eks_output_contract(module.outputs or {}):
                return "eks"
            return None

        managed_modules = [
            module for module in candidate_modules
            if _effective_managed_type(module) is not None
        ]

        if not managed_modules:
            return {"success": True, "message": "No deployed managed clusters found in this project",
                    "registered": [], "skipped": [], "errors": []}

        registered_clusters = []
        skipped_clusters = []
        errors = []

        for module in managed_modules:
            try:
                managed_type = _effective_managed_type(module)
                if managed_type == "roks":
                    cluster = self._register_roks_cluster(module)
                else:
                    cluster = register_eks_cluster(self.db, module)
                registered_clusters.append({
                    "id": cluster.id, "name": cluster.name,
                    "module_id": module.id, "status": "registered"
                })
            except ValueError as e:
                errors.append({"module_id": module.id, "error": str(e)})
            except Exception as e:
                if "already registered" in str(e).lower():
                    skipped_clusters.append({"module_id": module.id, "reason": "already_registered"})
                else:
                    errors.append({"module_id": module.id, "error": str(e)})

        return {
            "success": True,
            "message": f"Found {len(managed_modules)} managed cluster module(s), registered {len(registered_clusters)} cluster(s)",
            "registered": registered_clusters,
            "skipped": skipped_clusters,
            "errors": errors
        }

    def detect_eks_clusters(self, project_id: int) -> dict[str, Any]:
        """Backward-compatible wrapper for managed cluster detection."""
        result = self.detect_managed_clusters(project_id)
        if not result.get("registered") and result.get("message") == "No deployed managed clusters found in this project":
            result = dict(result)
            result["message"] = "No deployed EKS modules found in this project"
        return result

    @staticmethod
    def _decode_module_kubeconfig_output(outputs: dict[str, Any]) -> str | None:
        """Pull a kubeconfig from terraform outputs and return the YAML.

        Accepts kubeconfig under any of: ``kubeconfig``, ``kubeconfig_yaml``,
        ``kube_config``. The value may be raw YAML or base64-encoded YAML —
        we sniff for ``apiVersion:`` to decide.
        """
        raw = outputs.get("kubeconfig") or outputs.get("kubeconfig_yaml") or outputs.get("kube_config")
        if not raw or not isinstance(raw, str):
            return None

        candidate = raw.strip()
        if "apiVersion:" not in candidate:
            try:
                candidate = base64.b64decode(candidate).decode("utf-8")
            except Exception:
                logger.warning("ROKS kubeconfig output is neither YAML nor valid base64, ignoring")
                return None

        if "apiVersion:" not in candidate:
            logger.warning("ROKS kubeconfig output decoded but does not look like a kubeconfig, ignoring")
            return None

        return candidate

    @staticmethod
    def _kubeconfig_default_context(kubeconfig_yaml: str | None) -> str | None:
        """Pick the kubeconfig's current-context (or first listed context)."""
        if not kubeconfig_yaml:
            return None
        try:
            cfg = yaml.safe_load(kubeconfig_yaml)
        except Exception:
            return None
        if not isinstance(cfg, dict):
            return None
        current = cfg.get("current-context")
        if current:
            return current
        contexts = cfg.get("contexts") or []
        if contexts and isinstance(contexts[0], dict):
            return contexts[0].get("name")
        return None

    @classmethod
    def _encrypt_module_kubeconfig_output(cls, outputs: dict[str, Any]) -> str | None:
        """Backward-compatible wrapper: decode and encrypt in one step."""
        decoded = cls._decode_module_kubeconfig_output(outputs)
        return encrypt_value(decoded) if decoded else None

    def _register_roks_cluster(self, module) -> KubernetesCluster:
        """Register a deployed ROKS cluster from module outputs.

        Thin wrapper kept for backward compatibility. The implementation
        lives in services/roks_service.py — see eks_service.py for the
        sibling pattern.
        """
        from services.roks_service import register_roks_cluster
        return register_roks_cluster(self.db, module)

    # ================================================================
    # CRUD
    # ================================================================

    @staticmethod
    def _derive_ssh_remote_target_from_api_server(api_server: str | None) -> tuple[str | None, int | None]:
        """Best-effort derive SSH remote host/port from kubeconfig API server URL."""
        if not api_server:
            return None, None

        try:
            parsed = urlparse(api_server)
            return parsed.hostname, parsed.port
        except Exception:
            return None, None

    def create_cluster(self, project_id: int, cluster_data) -> dict[str, Any]:
        """Add a Kubernetes cluster configuration to a project."""
        self._get_project(project_id)

        # Decode kubeconfig
        try:
            kubeconfig_yaml = base64.b64decode(cluster_data.kubeconfig).decode('utf-8')
        except Exception as e:
            raise BadRequestError(f"Invalid base64 kubeconfig: {e}")

        # Parse kubeconfig
        try:
            kubeconfig_dict = yaml.safe_load(kubeconfig_yaml)
            context = cluster_data.context
            if not context:
                context = kubeconfig_dict.get("current-context")
                if not context and kubeconfig_dict.get("contexts"):
                    context = kubeconfig_dict["contexts"][0]["name"]

            # Find API server — match the selected context's cluster if possible
            api_server = None
            ctx_cluster_name = None
            for ctx_entry in kubeconfig_dict.get("contexts", []):
                if ctx_entry.get("name") == context:
                    ctx_cluster_name = ctx_entry.get("context", {}).get("cluster")
                    break
            for cl_entry in kubeconfig_dict.get("clusters", []):
                if ctx_cluster_name and cl_entry.get("name") == ctx_cluster_name:
                    api_server = cl_entry.get("cluster", {}).get("server")
                    break
            # Fallback to first cluster if context match failed
            if not api_server and kubeconfig_dict.get("clusters"):
                api_server = kubeconfig_dict["clusters"][0].get("cluster", {}).get("server")
        except Exception as e:
            raise BadRequestError(f"Invalid kubeconfig YAML: {e}")

        # Validate and normalize portability (raises KubeconfigUnportableError → 422)
        kubeconfig_yaml = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.MANUAL_UPLOAD
        )

        kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)

        # Duplicate name check
        existing = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.name == cluster_data.name
        ).first()
        if existing:
            raise ConflictError("cluster", f"Cluster '{cluster_data.name}' already exists")

        # Auto-extract SSH tunnel target from kubeconfig API server URL
        ssh_remote_host = cluster_data.ssh_remote_k8s_host or "localhost"
        ssh_remote_port = cluster_data.ssh_remote_k8s_port or 6443
        if (cluster_data.ssh_tunnel_enabled and api_server
                and ssh_remote_host == "localhost" and ssh_remote_port == 6443):
            # User didn't override — auto-extract from kubeconfig
            parsed_host, parsed_port = self._derive_ssh_remote_target_from_api_server(api_server)
            if parsed_host:
                ssh_remote_host = parsed_host
            if parsed_port:
                ssh_remote_port = parsed_port

        # Default cloud_provider to 'on-prem' if not specified — gives the platform
        # detection something to work with instead of leaving it as 'unknown'.
        # Subsequent scans can refine this (e.g. detect OpenShift via CRDs).
        cloud_provider_value = cluster_data.cloud_provider or "on-prem"

        cluster = KubernetesCluster(
            project_id=project_id,
            name=cluster_data.name,
            context=context,
            api_server=api_server,
            kubeconfig_encrypted=kubeconfig_encrypted,
            cloud_provider=cloud_provider_value,
            region=cluster_data.region,
            default_namespace=cluster_data.default_namespace,
            ssh_tunnel_enabled=cluster_data.ssh_tunnel_enabled or False,
            ssh_remote_k8s_host=ssh_remote_host,
            ssh_remote_k8s_port=ssh_remote_port,
            ssh_credential_id=cluster_data.ssh_credential_id,
            ssh_host_override=cluster_data.ssh_host_override or None,
            status="active"
        )

        PlatformContextService.apply_cluster_context(cluster)

        self.db.add(cluster)
        self.db.flush()
        self.db.refresh(cluster)

        # D-022: upsert fleet membership for the newly created cluster.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "cluster", cluster.id)
        except Exception:
            logger.exception("Fleet reconcile failed for new cluster id=%s", cluster.id)

        context = PlatformContextService.serialize_cluster_context(cluster)
        return {
            "id": cluster.id, "name": cluster.name, "context": cluster.context,
            "api_server": cluster.api_server, "cloud_provider": cluster.cloud_provider,
            "detected_platform_profile": context.detected_platform_profile,
            "detected_platform_provider": context.detected_platform_provider,
            "platform_capabilities": context.platform_capabilities,
            "platform_constraints": context.platform_constraints,
            "region": cluster.region, "default_namespace": cluster.default_namespace,
            "status": cluster.status, "project_id": cluster.project_id
        }

    def list_all_clusters(self) -> dict[str, Any]:
        """List all Kubernetes clusters (global).

        This endpoint is instance-wide (require_viewer, not project-scoped), so
        it must not expose the ADR-424 bnk_config -- host/DPU membership,
        control-plane host, tmfifo pool CIDR -- cross-project to any viewer
        (#116). bnk_config is redacted here; the project-scoped list and the
        per-cluster detail keep it. No frontend consumer of this global list
        reads bnk_config (only the project-scoped K8sClusterList does), so this
        removes the disclosure without losing a feature -- and it also drops the
        now-unnecessary membership bulk-fetch those fields required.
        """
        from sqlalchemy.orm import selectinload

        from routes.k8s._shared import serialize_cluster

        clusters = (
            self.db.query(KubernetesCluster)
            .options(selectinload(KubernetesCluster.bnk_config))
            .all()
        )
        result = [serialize_cluster(c, include_bnk_config=False) for c in clusters]
        return {"clusters": result, "count": len(result)}

    def list_project_clusters(self, project_id: int) -> dict[str, Any]:
        """List all Kubernetes clusters for a project.

        NOTE (#116): this list still renders bnk_config, and its route is
        require_viewer (any authenticated user) with NO membership/ownership
        check -- project_id is a path param anyone may supply. Combined with the
        global list (which still returns each cluster's project_id), any viewer
        can read any project's bnk_config in two requests. Redacting bnk_config
        on the global list (this change) is a strict improvement but does NOT
        fully close #116: the project list must enforce per-project membership
        first, and that is a pre-existing tenancy-model decision (require_viewer
        is role-based across the app) larger than this change. Until that lands,
        bnk_config here is readable by any authenticated user.
        """
        from sqlalchemy.orm import selectinload

        from routes.k8s._shared import serialize_cluster
        from services.bnk_cluster_service import BnkClusterService

        self._get_project(project_id)
        clusters = (
            self.db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project_id)
            .options(selectinload(KubernetesCluster.bnk_config))
            .all()
        )
        bnk_ids = [c.id for c in clusters if getattr(c, "bnk_config", None)]
        membership_map = BnkClusterService(self.db).bulk_cluster_membership(bnk_ids)
        result = [
            serialize_cluster(c, include_project_id=False, membership=membership_map.get(c.id))
            for c in clusters
        ]
        return {"clusters": result, "count": len(result)}

    def get_cluster_details(self, cluster_id: int) -> dict[str, Any]:
        """Get cluster details.

        NOTE (#116): GET /k8s/clusters/{cluster_id} is require_viewer with NO
        project scope (unlike the PUT/DELETE on the same path, which use
        require_cluster_owner). This handler hand-builds its dict and must NOT
        gain a bnk_config key -- reusing serialize_cluster here (or adding
        bnk_config by hand) would reintroduce the cross-project disclosure #116
        closes, one request further along, and the id needed comes straight from
        the global list. If bnk_config is ever needed on detail, scope this route
        to the project first.
        """
        cluster = self._get_cluster(cluster_id)
        context = PlatformContextService.serialize_cluster_context(cluster)
        return {
            "id": cluster.id, "name": cluster.name, "context": cluster.context,
            "api_server": cluster.api_server, "cloud_provider": cluster.cloud_provider,
            "detected_platform_profile": context.detected_platform_profile,
            "detected_platform_provider": context.detected_platform_provider,
            "platform_capabilities": context.platform_capabilities,
            "platform_constraints": context.platform_constraints,
            "region": cluster.region, "default_namespace": cluster.default_namespace,
            "status": cluster.status, "version": cluster.version,
            "project_id": cluster.project_id,
            # ADR-478/494: release FK ids — deployable = intent; running = observed by scan.
            "deployable_release_id": cluster.deployable_release_id,
            "running_release_id": cluster.running_release_id,
            "last_synced_at": cluster.last_synced_at.isoformat() if cluster.last_synced_at else None,
            "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
            "updated_at": cluster.updated_at.isoformat() if cluster.updated_at else None
        }

    def update_cluster(self, cluster_id: int, cluster_data) -> dict:
        """Update cluster configuration."""
        from routes.k8s._shared import serialize_cluster
        cluster = self._get_cluster(cluster_id)

        requested_ssh_remote_host = cluster_data.ssh_remote_k8s_host
        requested_ssh_remote_port = cluster_data.ssh_remote_k8s_port
        requested_ssh_tunnel_enabled = cluster_data.ssh_tunnel_enabled

        if cluster_data.name is not None:
            existing = self.db.query(KubernetesCluster).filter(
                KubernetesCluster.name == cluster_data.name,
                KubernetesCluster.id != cluster_id
            ).first()
            if existing:
                raise ConflictError("cluster", f"Cluster '{cluster_data.name}' already exists")
            cluster.name = cluster_data.name

        if cluster_data.kubeconfig is not None:
            try:
                kubeconfig_yaml = base64.b64decode(cluster_data.kubeconfig).decode('utf-8')
            except Exception as e:
                raise BadRequestError(f"Invalid base64 kubeconfig: {e}")
            # Validate and normalize portability (raises KubeconfigUnportableError → 422)
            kubeconfig_yaml = normalize_kubeconfig(
                kubeconfig_yaml, source=NormalizationSource.MANUAL_UPLOAD
            )
            cluster.kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)

        if cluster_data.cloud_provider is not None:
            cluster.cloud_provider = cluster_data.cloud_provider
        if cluster_data.region is not None:
            cluster.region = cluster_data.region
        if cluster_data.context is not None:
            cluster.context = cluster_data.context
        if cluster_data.default_namespace is not None:
            cluster.default_namespace = cluster_data.default_namespace
        if cluster_data.ssh_tunnel_enabled is not None:
            cluster.ssh_tunnel_enabled = cluster_data.ssh_tunnel_enabled
        if cluster_data.ssh_remote_k8s_host is not None:
            cluster.ssh_remote_k8s_host = cluster_data.ssh_remote_k8s_host
        if cluster_data.ssh_remote_k8s_port is not None:
            cluster.ssh_remote_k8s_port = cluster_data.ssh_remote_k8s_port
        if cluster_data.ssh_credential_id is not None:
            cluster.ssh_credential_id = cluster_data.ssh_credential_id or None
        if hasattr(cluster_data, "ssh_host_override"):
            cluster.ssh_host_override = cluster_data.ssh_host_override or None
        if getattr(cluster_data, "enabled_prerequisites", None) is not None:
            from services.scanner.recommendations import (
                KNOWN_PREREQUISITE_IDS,
                LOCKED_PREREQUISITE_IDS,
            )
            requested = list(cluster_data.enabled_prerequisites)
            unknown = set(requested) - set(KNOWN_PREREQUISITE_IDS)
            if unknown:
                raise BadRequestError(
                    f"Unknown prerequisite IDs: {sorted(unknown)}. "
                    f"Allowed: {sorted(KNOWN_PREREQUISITE_IDS)}"
                )
            normalized = sorted(set(requested) | set(LOCKED_PREREQUISITE_IDS))
            cluster.enabled_prerequisites = normalized

        # If tunnel is enabled during update and caller did not set an explicit
        # remote host/port, auto-derive from API server when the target is still
        # at defaults. This keeps create/update behavior aligned.
        next_ssh_tunnel_enabled = (
            cluster.ssh_tunnel_enabled
            if requested_ssh_tunnel_enabled is None
            else requested_ssh_tunnel_enabled
        )
        auto_target_eligible = (
            next_ssh_tunnel_enabled
            and requested_ssh_remote_host is None
            and requested_ssh_remote_port is None
            and (cluster.ssh_remote_k8s_host or "localhost") == "localhost"
            and (cluster.ssh_remote_k8s_port or 6443) == 6443
        )
        if auto_target_eligible:
            parsed_host, parsed_port = self._derive_ssh_remote_target_from_api_server(cluster.api_server)
            if parsed_host:
                cluster.ssh_remote_k8s_host = parsed_host
            if parsed_port:
                cluster.ssh_remote_k8s_port = parsed_port

        PlatformContextService.apply_cluster_context(cluster)

        self.db.flush()
        self.db.refresh(cluster)

        # D-022: re-reconcile fleet membership after context update.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "cluster", cluster.id)
        except Exception:
            logger.exception("Fleet reconcile failed on cluster update id=%s", cluster.id)

        return serialize_cluster(cluster)

    def delete_cluster(self, cluster_id: int) -> dict[str, Any]:
        """Delete cluster configuration."""
        cluster = self._get_cluster(cluster_id)
        cluster_name = cluster.name

        # tmfifo IP release is handled by the KubernetesCluster before_delete
        # mapper event in models/kubernetes.py (ADR-424 finding B) — no
        # per-caller wiring needed here.
        self.db.delete(cluster)
        self.db.flush()
        return {"message": f"Cluster '{cluster_name}' deleted successfully"}

    # ================================================================
    # Kubeconfig Refresh
    # ================================================================

    def refresh_kubeconfig(self, cluster_id: int) -> dict[str, Any]:
        """Refresh kubeconfig — EKS via AWS CLI, or on-prem via SSH probe."""
        from utils.provider_config import normalize_cloud_provider

        cluster = self._get_cluster(cluster_id)
        # Compare provider case-insensitively so "IBM" routes the same as "ibm".
        provider = normalize_cloud_provider(cluster.cloud_provider)

        # SSH-based refresh for clusters with SSH credentials
        ssh_cred_id = cluster.ssh_credential_id
        if not ssh_cred_id:
            project = cluster.project
            if project:
                ssh_cred_id = project.ssh_credential_id

        if ssh_cred_id and provider not in ["eks", "aws"]:
            return self._refresh_kubeconfig_via_ssh(cluster, ssh_cred_id)

        if provider in ("ibm", "ibmcloud", "roks"):
            return self._refresh_kubeconfig_ibm(cluster)

        if provider not in ["eks", "aws"]:
            raise BadRequestError(
                f"Refresh kubeconfig only supported for EKS/AWS or SSH-enabled clusters. This cluster is {cluster.cloud_provider}")

        if not cluster.name or not cluster.region:
            raise BadRequestError("Cluster name and region required for kubeconfig refresh")

        from services.credentials_service import get_cloud_credentials_env

        project = cluster.project
        aws_env = get_cloud_credentials_env(project, self.db)
        aws_env['AWS_DEFAULT_REGION'] = cluster.region

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            temp_kubeconfig = f.name

        try:
            cmd = [
                'aws', 'eks', 'update-kubeconfig',
                '--name', cluster.name,
                '--region', cluster.region,
                '--kubeconfig', temp_kubeconfig,
                '--alias', cluster.name
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=aws_env)

            if result.returncode != 0:
                output = result.stderr or result.stdout or ""
                aws_auth = classify_aws_subprocess_stderr(output, "generate kubeconfig")
                if aws_auth is not None:
                    raise aws_auth
                raise InternalError(f"Failed to generate kubeconfig: {output}")

            with open(temp_kubeconfig) as f:
                kubeconfig_yaml = f.read()

            # Assert invariant — aws eks update-kubeconfig must produce portable output.
            # Hard-fail on violation so the bug surfaces loudly (kubeconfig_invariant_violation).
            try:
                kubeconfig_yaml = normalize_kubeconfig(
                    kubeconfig_yaml, source=NormalizationSource.CLOUD_API_GENERATED
                )
            except Exception as norm_exc:
                logger.error(
                    "kubeconfig_invariant_violation: EKS CLI produced non-portable kubeconfig "
                    "for cluster %s: %s",
                    cluster.name, norm_exc,
                )
                raise

            try:
                kubeconfig_data = yaml.safe_load(kubeconfig_yaml)
                actual_context = kubeconfig_data.get('current-context')
                if actual_context:
                    cluster.context = actual_context
            except Exception as e:
                logger.warning(f"Failed to parse kubeconfig to update context: {e}")

            cluster.kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)
            cluster.updated_at = func.now()
            PlatformContextService.apply_cluster_context(cluster)

            return {
                "message": f"Kubeconfig refreshed successfully for cluster '{cluster.name}'",
                "cluster_id": cluster.id,
                "cluster_name": cluster.name,
                "region": cluster.region,
                "refresh_method": "eks",
            }
        finally:
            if os.path.exists(temp_kubeconfig):
                os.unlink(temp_kubeconfig)

    def _refresh_kubeconfig_via_ssh(self, cluster: KubernetesCluster, ssh_cred_id: int) -> dict[str, Any]:
        """Refresh kubeconfig by re-probing the SSH host."""
        from services.ssh_credential_service import SSHCredentialService

        svc = SSHCredentialService(self.db)
        probe = svc.probe_kubeconfig(ssh_cred_id)

        if not probe.get("success"):
            raise BadRequestError(probe.get("error", "SSH probe failed during kubeconfig refresh"))

        kubeconfig_b64 = probe.get("kubeconfig")
        if not kubeconfig_b64:
            raise BadRequestError("SSH probe returned no kubeconfig")

        # Decode and store
        kubeconfig_yaml = base64.b64decode(kubeconfig_b64).decode("utf-8")
        # Paranoia check: SSH remote-flatten should have produced portable output.
        # Hard-fail if file refs survived so the bug is loud, not silently stored.
        kubeconfig_yaml = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.SSH_DISCOVERY
        )
        cluster.kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)
        cluster.updated_at = func.now()

        # Update context and API server from refreshed kubeconfig (match EKS path behavior)
        try:
            kubeconfig_data = yaml.safe_load(kubeconfig_yaml)
            # Update context — use the cluster's existing context if still present, else current-context
            if kubeconfig_data.get("contexts"):
                available_contexts = [c.get("name") for c in kubeconfig_data["contexts"]]
                if cluster.context not in available_contexts:
                    new_context = kubeconfig_data.get("current-context") or (available_contexts[0] if available_contexts else None)
                    if new_context:
                        logger.info(f"SSH refresh: context '{cluster.context}' no longer available, updating to '{new_context}'")
                        cluster.context = new_context

            # Update API server URL from the active context
            active_context = cluster.context or kubeconfig_data.get("current-context")
            selected_cluster_name = None
            if active_context:
                ctx_cluster_name = None
                for ctx in kubeconfig_data.get("contexts", []):
                    if ctx.get("name") == active_context:
                        ctx_cluster_name = ctx.get("context", {}).get("cluster")
                        break
                if ctx_cluster_name:
                    selected_cluster_name = ctx_cluster_name
                    for cl_entry in kubeconfig_data.get("clusters", []):
                        if cl_entry.get("name") == ctx_cluster_name:
                            new_api_server = cl_entry.get("cluster", {}).get("server")
                            if new_api_server:
                                cluster.api_server = new_api_server
                                # Auto-update SSH tunnel target if tunnel is enabled and target was auto-configured
                                if cluster.ssh_tunnel_enabled:
                                    try:
                                        from urllib.parse import urlparse
                                        parsed = urlparse(new_api_server)
                                        if parsed.hostname:
                                            cluster.ssh_remote_k8s_host = parsed.hostname
                                        if parsed.port:
                                            cluster.ssh_remote_k8s_port = parsed.port
                                    except Exception:
                                        pass
                            break

            # For SSH-routed generic/on-prem clusters, prefer routing the tunnel to the
            # actual cluster endpoint encoded in the selected kubeconfig context while
            # preserving the original kubeconfig server/certs for Kubernetes auth.
            # Rewriting the stored API server to the remote host IP can break auth when
            # the kubeconfig only trusts/authenticates the original localhost-kind endpoint.
            if cluster.ssh_tunnel_enabled and selected_cluster_name:
                for cl_entry in kubeconfig_data.get("clusters", []):
                    if cl_entry.get("name") != selected_cluster_name:
                        continue
                    selected_cluster = cl_entry.get("cluster", {})
                    selected_server = selected_cluster.get("server")
                    if selected_server:
                        from urllib.parse import urlparse

                        parsed = urlparse(selected_server)
                        if parsed.hostname:
                            cluster.ssh_remote_k8s_host = parsed.hostname
                        if parsed.port:
                            cluster.ssh_remote_k8s_port = parsed.port
                    break
        except Exception as e:
            logger.warning(f"Failed to parse refreshed kubeconfig for context/API server update: {e}")

        PlatformContextService.apply_cluster_context(cluster)

        return {
            "message": f"Kubeconfig refreshed via SSH for cluster '{cluster.name}'",
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "region": cluster.region,
            "refresh_method": "ssh",
        }

    def _refresh_kubeconfig_ibm(self, cluster: KubernetesCluster) -> dict[str, Any]:
        """Re-derive a ROKS cluster's kubeconfig by refreshing its IAM bearer token.

        ROKS kubeconfigs pin a short-lived IAM token (~1h); once it expires the
        stored kubeconfig can no longer authenticate against the cluster API.
        Resolve the project's IBM Cloud credential template, exchange its API
        key for a fresh IAM token, rewrite the embedded token, and persist.
        """
        if not cluster.kubeconfig_encrypted:
            raise BadRequestError(
                f"Cluster '{cluster.name}' has no stored kubeconfig to refresh")

        kubeconfig_yaml = decrypt_value(cluster.kubeconfig_encrypted)
        if not kubeconfig_yaml:
            raise InternalError(
                f"Failed to decrypt stored kubeconfig for cluster '{cluster.name}'")

        from services.ibm_cloud_service import IBMCloudService
        from services.roks_service import refresh_kubeconfig_iam_token

        project = cluster.project
        template_id = getattr(project, "credential_template_id", None) if project else None
        template = IBMCloudService(self.db).resolve_effective_template(template_id)

        api_key = decrypt_value(template.ibmcloud_api_key_encrypted)
        if not api_key:
            raise BadRequestError(
                f"IBM credential template '{template.name}' does not contain a usable API key")

        refreshed = refresh_kubeconfig_iam_token(kubeconfig_yaml, api_key)
        if not refreshed:
            raise InternalError(
                f"Failed to refresh IBM IAM bearer token for cluster '{cluster.name}'")

        # Assert invariant — the refreshed kubeconfig must stay portable.
        refreshed = normalize_kubeconfig(refreshed, source=NormalizationSource.CLOUD_API_GENERATED)

        cluster.kubeconfig_encrypted = encrypt_value(refreshed)
        cluster.updated_at = func.now()
        PlatformContextService.apply_cluster_context(cluster)

        return {
            "message": f"Kubeconfig refreshed successfully for cluster '{cluster.name}'",
            "cluster_id": cluster.id,
            "cluster_name": cluster.name,
            "region": cluster.region,
            "refresh_method": "ibm",
        }
