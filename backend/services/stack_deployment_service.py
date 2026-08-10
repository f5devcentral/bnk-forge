"""
Stack Deployment Service

Orchestrates deployment of multiple modules from stack templates.
Handles dependency ordering, progress tracking, and error handling.

ENG-006: This service retains its own db.commit() calls because it is called
exclusively from Celery tasks (stack_tasks.py, opentofu_tasks.py) which are
the outermost callers. The service manages multi-step deployment state that
needs intermediate commits for progress tracking.
"""

import base64
import json
import logging
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from kubernetes import client
from kubernetes.client import ApiClient
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.orm.attributes import flag_modified

from models import (
    KubernetesCluster,
    ModuleLibrary,
    Project,
    ProjectModule,
    ProjectSecret,
    StackInstance,
    StackTemplate,
)
from models.enums import ModuleStatus, StackInstanceStatus
from services.defaults_service import get_default
from services.kubernetes_service import KubernetesService
from services.platform_context_service import PlatformContextService
from services.secrets_service import SecretsService
from services.stack_service import StackService
from services.workspace_manager import WorkspaceManager

logger = logging.getLogger(__name__)

_TEMPLATES_JSON_PATH = Path(__file__).parent.parent / "data" / "stack_templates.json"


def _template_requires_existing_cluster(slug: str) -> bool:
    """Return True if the named template requires a pre-existing cluster.

    Reads the requires_existing_cluster flag from stack_templates.json, falling
    back to the legacy hardcoded slug set for templates not yet in the JSON file.
    """
    try:
        templates: list[dict] = json.loads(_TEMPLATES_JSON_PATH.read_text())
        for t in templates:
            if t.get("slug") == slug:
                return bool(t.get("requires_existing_cluster", False))
    except Exception:
        logger.warning("Could not load stack_templates.json; using legacy slug check")
    # Legacy fallback: catches f5-bnk-2.2 which is not in stack_templates.json
    return slug in {"bnk-on-k8s", "f5-bnk-2.2"}


class StackDeploymentService:
    """
    Service for deploying and managing stack instances.

    Handles:
    - Creating ProjectModules from stack templates
    - Deploying modules in order
    - Tracking deployment progress
    - Destroying stacks in reverse order
    """

    def __init__(self, db: Session):
        self.db = db
        self.k8s_service = KubernetesService(db)

    AWS_VPC_SUBNET_VAR_NAMES = [
        "public_subnet_cidr",
        "private_external_subnet_a_cidr",
        "private_external_subnet_b_cidr",
        "private_internal_subnet_a_cidr",
        "private_internal_subnet_b_cidr",
    ]

    def _derive_aws_vpc_subnet_defaults(self, vpc_cidr: str) -> dict[str, str]:
        """Derive deterministic subnet CIDRs inside the provided VPC CIDR."""
        network = ip_network(vpc_cidr, strict=False)
        if network.version != 4:
            raise ValueError("vpc_cidr must be an IPv4 CIDR")

        # We reserve sparse indexes (1/10/11/20/21) to preserve familiar spacing.
        # To support index 21, we need at least 32 child subnets => +5 bits.
        # Keep /24 for broader VPCs, but never exceed AWS /28 subnet minimum size.
        if network.prefixlen > 23:
            raise ValueError(
                f"vpc_cidr '{vpc_cidr}' is too narrow for default 5-subnet layout. "
                "Use a broader CIDR (recommended /16 to /23) or provide all subnet CIDRs explicitly."
            )

        target_prefix = max(24, network.prefixlen + 5)
        target_prefix = min(target_prefix, 28)
        subnet_list = list(network.subnets(new_prefix=target_prefix))

        required_indexes = [1, 10, 11, 20, 21]
        if len(subnet_list) <= max(required_indexes):
            raise ValueError(
                f"vpc_cidr '{vpc_cidr}' cannot derive required subnet layout (only {len(subnet_list)} subnets at /{target_prefix})."
            )

        return {
            "public_subnet_cidr": str(subnet_list[1]),
            "private_external_subnet_a_cidr": str(subnet_list[10]),
            "private_external_subnet_b_cidr": str(subnet_list[11]),
            "private_internal_subnet_a_cidr": str(subnet_list[20]),
            "private_internal_subnet_b_cidr": str(subnet_list[21]),
        }

    @staticmethod
    def _stack_module_path(stack_instance: StackInstance, module_path: str) -> str:
        return f"stack-{stack_instance.id}/{module_path}"

    def _build_stack_module_variables(
        self,
        stack_instance: StackInstance,
        module_path: str,
        module_vars: dict,
    ) -> dict:
        """Build final module variables using the same precedence as initial materialization."""
        template = stack_instance.template
        project = stack_instance.project
        final_vars = dict(module_vars)

        platform_defaults = template.platform_defaults or {}
        if platform_defaults and project.cloud_provider:
            profile_key = PlatformContextService._MANAGED_CLOUD_PROFILE_MAP.get(project.cloud_provider.strip().lower())
            if not profile_key:
                profile_key = PlatformContextService._ONPREM_PROVIDER_PROFILE_MAP.get(project.cloud_provider.strip().lower())
            if profile_key and profile_key in platform_defaults:
                final_vars.update(platform_defaults[profile_key])

        if stack_instance.variables:
            module_specific_vars = stack_instance.variables.get(module_path, {})
            explicit_user_keys: set[str] = set()
            if isinstance(module_specific_vars, dict):
                final_vars.update(module_specific_vars)
                explicit_user_keys.update(module_specific_vars.keys())

            for key, value in stack_instance.variables.items():
                if "/" not in key and not isinstance(value, dict):
                    final_vars[key] = value
                    explicit_user_keys.add(key)

            final_vars = self._normalize_aws_vpc_subnet_cidrs(
                module_path=module_path,
                final_vars=final_vars,
                explicit_user_keys=explicit_user_keys,
            )

        return final_vars

    def _reconcile_stack_modules_from_template(self, stack_instance: StackInstance) -> tuple[list[ProjectModule], str | None]:
        """Backfill any required template modules missing from an existing stack instance."""
        template = stack_instance.template
        project = stack_instance.project

        if not template or not template.modules:
            return [], None

        existing_modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.stack_instance_id == stack_instance.id)
            .order_by(ProjectModule.deployment_order.asc(), ProjectModule.id.asc())
            .all()
        )
        module_map = {
            module.path_in_project.removeprefix(f"stack-{stack_instance.id}/"): module
            for module in existing_modules
        }
        existing_module_ids = {module.id for module in existing_modules}
        created_modules: list[ProjectModule] = []

        for idx, module_def in enumerate(template.modules):
            module_path = module_def.get("path")
            if not module_path or module_path in module_map:
                continue

            if module_def.get("required", True) is False:
                logger.warning(
                    f"Reconcile stack {stack_instance.id}: optional module "
                    f"'{module_path}' missing from instance — skipping backfill "
                    f"(not part of the deployed stack)"
                )
                continue

            library_module = self._lookup_library_module(module_path)
            if not library_module:
                return [], f"Required module '{module_path}' not found in library"

            final_vars = self._build_stack_module_variables(
                stack_instance=stack_instance,
                module_path=module_path,
                module_vars=module_def.get("variables", {}),
            )

            project_module = ProjectModule(
                project_id=project.id,
                module_library_id=library_module.id,
                path_in_project=self._stack_module_path(stack_instance, module_path),
                variable_overrides=final_vars,
                deployment_order=idx,
                enabled=True,
                status=ModuleStatus.NOT_INITIALIZED,
                stack_instance_id=stack_instance.id,
            )
            self.db.add(project_module)
            self.db.flush()
            created_modules.append(project_module)
            module_map[module_path] = project_module

        for idx, module_def in enumerate(template.modules):
            module_path = module_def.get("path")
            module = module_map.get(module_path)
            if not module:
                continue

            module.deployment_order = idx
            library_module = module.library_module
            if not library_module:
                module.dependencies = []
                continue

            deps_metadata = library_module.dependencies_metadata or {}
            required_deps = deps_metadata.get("required", [])
            module.dependencies = [
                module_map[dep_path].id
                for dep in required_deps
                if (dep_path := dep.get("module")) in module_map
            ]

        stack_instance.total_steps = len(existing_module_ids | {module.id for module in created_modules})
        stack_instance.current_step = sum(1 for module in existing_modules if module.status == ModuleStatus.APPLIED)
        self.db.commit()
        self.db.refresh(stack_instance)
        return created_modules, None

    def _normalize_aws_vpc_subnet_cidrs(
        self,
        module_path: str,
        final_vars: dict,
        explicit_user_keys: set[str],
    ) -> dict:
        """
        Ensure AWS VPC subnet CIDRs align with vpc_cidr.

        If template/default subnet CIDRs drift outside a user-provided vpc_cidr,
        derive safe defaults within the VPC. If the user explicitly supplied an
        invalid/out-of-range subnet, fail truthfully instead of silently overriding.
        """
        if module_path != "infra/aws/vpc":
            return final_vars

        vpc_cidr = final_vars.get("vpc_cidr")
        if not vpc_cidr:
            return final_vars

        vpc_network = ip_network(vpc_cidr, strict=False)
        derived_subnets = self._derive_aws_vpc_subnet_defaults(vpc_cidr)

        for subnet_key in self.AWS_VPC_SUBNET_VAR_NAMES:
            current_value = final_vars.get(subnet_key)
            if not current_value:
                final_vars[subnet_key] = derived_subnets[subnet_key]
                continue

            try:
                current_network = ip_network(current_value, strict=False)
                in_vpc = current_network.subnet_of(vpc_network)
            except ValueError:
                in_vpc = False

            if in_vpc:
                continue

            if subnet_key in explicit_user_keys:
                raise ValueError(
                    f"Explicit subnet '{subnet_key}={current_value}' is not within vpc_cidr '{vpc_cidr}'."
                )

            logger.info(
                "Auto-adjusting %s from %s to %s to match vpc_cidr=%s",
                subnet_key,
                current_value,
                derived_subnets[subnet_key],
                vpc_cidr,
            )
            final_vars[subnet_key] = derived_subnets[subnet_key]

        return final_vars

    # ------------------------------------------------------------------
    # S19-001: Pre-deploy secrets validation
    # ------------------------------------------------------------------

    def get_required_secret_prerequisites(
        self,
        template: StackTemplate,
    ) -> list[dict]:
        """
        Extract project_secret prerequisites from a stack template.

        Returns:
            List of dicts with keys: name, description, type
        """
        prerequisites = template.prerequisites or []
        return [
            p for p in prerequisites
            if p.get("type") == "project_secret" and p.get("name")
        ]

    def resolve_secret_source(self, secret_name: str, existing_names: set[str]) -> tuple[bool, str | None]:
        """Resolve secret satisfaction + source label with global FAR fallback."""
        if secret_name in existing_names:
            return True, "project"
        if secret_name == "cne_pull_secret":
            fallback = get_default(self.db, "bnk.far_pull_secret_default")
            if isinstance(fallback, str) and fallback.strip():
                return True, "system_default"
        return False, None

    def _get_secret_value_from_project_or_stack(
        self,
        *,
        secret_name: str,
        project_id: int,
        stack_variables: dict | None,
    ) -> tuple[str | None, str | None]:
        """Get secret value with source: project -> stack_variable -> system_default."""
        secrets_service = SecretsService(self.db)
        project_secret = secrets_service.get_secret_by_name(project_id, secret_name)
        if project_secret and project_secret.secret_type == "value":
            try:
                value = secrets_service.get_decrypted_value(project_secret)
            except Exception:
                value = None
            if value:
                return value, "project"

        if stack_variables:
            direct_value = stack_variables.get(secret_name)
            if isinstance(direct_value, str) and direct_value.strip():
                return direct_value, "stack_variable"
            for _module_path, nested in stack_variables.items():
                if isinstance(nested, dict):
                    nested_value = nested.get(secret_name)
                    if isinstance(nested_value, str) and nested_value.strip():
                        return nested_value, "stack_variable"

        if secret_name == "cne_pull_secret":
            fallback = get_default(self.db, "bnk.far_pull_secret_default")
            if isinstance(fallback, str) and fallback.strip():
                return fallback, "system_default"

        return None, None

    @staticmethod
    def _validate_jwt_token(value: str | None) -> tuple[bool, str | None]:
        if not value or not value.strip():
            return False, "JWT token is empty"
        token = value.strip()
        if token.count(".") != 2:
            return False, "JWT must have three dot-separated segments"
        return True, None

    @staticmethod
    def _validate_cne_pull_secret(value: str | None) -> tuple[bool, str | None]:
        if not value or not value.strip():
            return False, "cne_pull_secret is empty"

        raw = value.strip()
        try:
            decoded = base64.b64decode(raw).decode("utf-8")
        except Exception as exc:
            return False, f"cne_pull_secret is not valid base64: {exc}"

        try:
            parsed = json.loads(decoded)
        except Exception as exc:
            return False, f"cne_pull_secret decoded content is not valid JSON: {exc}"

        auths = parsed.get("auths") if isinstance(parsed, dict) else None
        if not isinstance(auths, dict):
            return False, "cne_pull_secret JSON must contain an 'auths' object"

        host_entry = auths.get("repo.f5.com")
        if not isinstance(host_entry, dict):
            return False, "cne_pull_secret auths must include repo.f5.com"
        if not host_entry.get("auth"):
            return False, "cne_pull_secret auths.repo.f5.com.auth is missing"

        return True, None

    def _validate_secret_content(
        self,
        *,
        secret_name: str,
        project_id: int,
        stack_variables: dict | None,
    ) -> tuple[bool, str | None, str | None]:
        value, source = self._get_secret_value_from_project_or_stack(
            secret_name=secret_name,
            project_id=project_id,
            stack_variables=stack_variables,
        )
        if source is None:
            return False, "Secret value is missing", None

        if secret_name == "jwt_token":
            ok, error = self._validate_jwt_token(value)
        elif secret_name == "cne_pull_secret":
            ok, error = self._validate_cne_pull_secret(value)
        else:
            ok, error = True, None

        return ok, error, source

    def validate_required_secrets(
        self,
        project_id: int,
        template: StackTemplate,
    ) -> tuple[bool, list[dict]]:
        """
        S19-001: Pre-flight check — verify all project_secret prerequisites
        exist before deploying a stack.

        Checks the template's ``prerequisites`` list for entries with
        ``type: "project_secret"`` and verifies that secrets with matching
        names are present and active in the project.

        Args:
            project_id: Project to check secrets for
            template: StackTemplate whose prerequisites to validate

        Returns:
            Tuple of (all_satisfied: bool, missing_secrets: List[Dict])
            Each missing dict has: name, description
        """
        required = self.get_required_secret_prerequisites(template)
        if not required:
            return True, []

        # Fetch existing active secret names for this project in one query
        existing_names = {
            row[0]
            for row in self.db.query(ProjectSecret.name).filter(
                ProjectSecret.project_id == project_id,
                ProjectSecret.is_active,
            ).all()
        }

        missing = []
        for prereq in required:
            secret_name = prereq["name"]
            exists, _source = self.resolve_secret_source(secret_name, existing_names)
            if not exists:
                guidance = "Configure this secret in Project Settings → Secrets before deploying."
                if secret_name == "cne_pull_secret":
                    guidance = (
                        "Configure cne_pull_secret in Project Settings → Secrets. "
                        "A global fallback can be configured in System → Defaults."
                    )
                missing.append({
                    "name": secret_name,
                    "description": prereq.get("description", ""),
                    "guidance": guidance,
                })

        return len(missing) == 0, missing

    def _requires_existing_cluster_preflight(self, template: StackTemplate) -> bool:
        slug = (template.slug or "").strip().lower()
        return _template_requires_existing_cluster(slug)

    def _get_project_clusters(self, project_id: int) -> list[KubernetesCluster]:
        return (
            self.db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project_id)
            .order_by(KubernetesCluster.id.asc())
            .all()
        )

    @staticmethod
    def _build_check_result(
        *,
        check_id: str,
        title: str,
        status: str,
        blocking: bool,
        message: str,
        guidance: str | None = None,
        details: dict | None = None,
    ) -> dict:
        payload = {
            "id": check_id,
            "title": title,
            "status": status,
            "blocking": blocking,
            "message": message,
        }
        if guidance:
            payload["guidance"] = guidance
        if details:
            payload["details"] = details
        return payload

    def _check_cluster_runtime_reachability(self, cluster: KubernetesCluster) -> tuple[dict, ApiClient | None]:
        api_server = (cluster.api_server or "").strip()
        parsed_host = None
        if api_server:
            try:
                parsed_host = urlparse(api_server).hostname
            except Exception:
                parsed_host = None

        loopback_endpoint = False
        if parsed_host:
            host = parsed_host.strip().lower()
            if host in {"localhost", "0.0.0.0"}:
                loopback_endpoint = True
            else:
                try:
                    loopback_endpoint = ip_address(host).is_loopback
                except ValueError:
                    loopback_endpoint = False

        if loopback_endpoint and not bool(cluster.ssh_tunnel_enabled):
            endpoint_label = parsed_host or api_server
            return (
                self._build_check_result(
                    check_id="cluster_runtime_reachability",
                    title="Cluster runtime reachability",
                    status="fail",
                    blocking=True,
                    message=(
                        f"Kubeconfig API server '{endpoint_label}' is a host-loopback endpoint. "
                        "Forge backend/worker runtime cannot use host-local endpoints directly."
                    ),
                    guidance=(
                        "Update this cluster kubeconfig to use an API endpoint routable from Forge containers "
                        "(not localhost/127.0.0.1), or enable SSH tunnel routing for this cluster if a jump host "
                        "must be used."
                    ),
                    details={"cluster_id": cluster.id, "api_server": cluster.api_server, "detected_host": parsed_host},
                ),
                None,
            )

        try:
            api_client = self.k8s_service.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)
            v1.list_namespace(limit=1, _request_timeout=10)
            version_info = client.VersionApi(api_client).get_code()
            message = (
                f"Cluster '{cluster.name}' is reachable from Forge runtime"
                f" (Kubernetes {version_info.major}.{version_info.minor})."
            )
            return (
                self._build_check_result(
                    check_id="cluster_runtime_reachability",
                    title="Cluster runtime reachability",
                    status="pass",
                    blocking=False,
                    message=message,
                    details={"cluster_id": cluster.id},
                ),
                api_client,
            )
        except Exception as exc:
            message = (
                f"Forge runtime cannot reach cluster '{cluster.name}' with current kubeconfig/auth settings: {exc}"
            )
            guidance = (
                "Verify this cluster's kubeconfig endpoint is reachable from backend/worker containers, "
                "not just from your host machine. If this is a private endpoint, configure network access "
                "or SSH tunnel routing for Forge runtime."
            )
            lower_message = str(exc).lower()
            if any(token in lower_message for token in ["localhost", "127.0.0.1", "::1"]):
                guidance = (
                    "This looks like a loopback/host-local kubeconfig endpoint. Use a cluster API endpoint that is "
                    "routable from Forge backend/worker containers, or enable SSH tunnel routing for this cluster."
                )
            return (
                self._build_check_result(
                    check_id="cluster_runtime_reachability",
                    title="Cluster runtime reachability",
                    status="fail",
                    blocking=True,
                    message=message,
                    guidance=guidance,
                    details={"cluster_id": cluster.id},
                ),
                None,
            )

    def _run_existing_cluster_preflight(self, project_id: int) -> dict:
        checks: list[dict] = []
        selected_cluster: dict | None = None

        clusters = self._get_project_clusters(project_id)
        if not clusters:
            checks.append(
                self._build_check_result(
                    check_id="cluster_selected",
                    title="Registered cluster selection",
                    status="fail",
                    blocking=True,
                    message="No Kubernetes cluster is registered for this project.",
                    guidance=(
                        "Register a Kubernetes cluster in this project before deploying the "
                        "BNK existing-cluster blueprint."
                    ),
                )
            )
            return {"checks": checks, "selected_cluster": None}

        if len(clusters) > 1:
            checks.append(
                self._build_check_result(
                    check_id="cluster_selection_ambiguity",
                    title="Multiple registered clusters",
                    status="fail",
                    blocking=True,
                    message=(
                        f"Project has {len(clusters)} registered clusters but this BNK existing-cluster flow "
                        "does not include an explicit selected cluster identity."
                    ),
                    guidance=(
                        "Select a single target cluster for this project (or remove extra cluster registrations) "
                        "before running BNK existing-cluster deployment."
                    ),
                    details={
                        "cluster_count": len(clusters),
                        "cluster_ids": [c.id for c in clusters],
                    },
                )
            )
            checks.append(
                self._build_check_result(
                    check_id="cluster_runtime_reachability",
                    title="Cluster runtime reachability",
                    status="warn",
                    blocking=False,
                    message="Skipped because cluster target is ambiguous.",
                )
            )
            return {"checks": checks, "selected_cluster": None}

        cluster = clusters[0]
        selected_cluster = {
            "id": cluster.id,
            "name": cluster.name,
            "api_server": cluster.api_server,
        }
        checks.append(
            self._build_check_result(
                check_id="cluster_selected",
                title="Registered cluster selection",
                status="pass",
                blocking=False,
                message=f"Using registered cluster '{cluster.name}' for preflight checks.",
                details={"cluster_id": cluster.id},
            )
        )

        reachability_check, api_client = self._check_cluster_runtime_reachability(cluster)
        checks.append(reachability_check)
        if api_client is None:
            return {"checks": checks, "selected_cluster": selected_cluster}

        return {"checks": checks, "selected_cluster": selected_cluster}

    def _extract_variable_names(self, stack_variables: dict | None) -> set[str]:
        """Extract all secret-relevant variable names from stack instance variables.

        Stack variables can be flat (``{"jwt_token": "..."}``)) or nested under
        module paths (``{"bnk/flo": {"jwt_token": "..."}}``).  Returns the union
        of both as a set of names whose values are truthy (non-empty).
        """
        if not stack_variables:
            return set()
        names: set[str] = set()
        for key, value in stack_variables.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if nested_value:
                        names.add(nested_key)
            elif value:
                names.add(key)
        return names

    def check_prerequisites(
        self,
        project_id: int,
        template: StackTemplate,
        stack_variables: dict | None = None,
    ) -> dict:
        """
        S19-001: Full prerequisites check for a stack template against a project.

        Args:
            project_id: Project to check against
            template: StackTemplate whose prerequisites to validate
            stack_variables: Optional stack instance variables dict.  When
                provided, variable names present here count as satisfied
                secrets (SEC-001 — wizard-provided values).

        Returns a structured result suitable for API responses and frontend display.
        """
        required_secrets = self.get_required_secret_prerequisites(template)

        # Fetch existing active secret names
        existing_names = {
            row[0]
            for row in self.db.query(ProjectSecret.name).filter(
                ProjectSecret.project_id == project_id,
                ProjectSecret.is_active,
            ).all()
        }

        # SEC-001: Also consider stack instance variables as satisfied secrets
        stack_var_names = self._extract_variable_names(stack_variables)

        secrets_status = []
        missing = []
        for prereq in required_secrets:
            secret_name = prereq["name"]
            exists, source = self.resolve_secret_source(secret_name, existing_names)
            # SEC-001: Fall back to stack variables if not in project secrets
            if not exists and secret_name in stack_var_names:
                exists = True
                source = "stack_variable"
            entry = {
                "name": secret_name,
                "description": prereq.get("description", ""),
                "exists": exists,
                "source": source,
            }

            if exists and secret_name in {"jwt_token", "cne_pull_secret"}:
                valid, validation_error, validated_from = self._validate_secret_content(
                    secret_name=secret_name,
                    project_id=project_id,
                    stack_variables=stack_variables,
                )
                entry["valid"] = valid
                if validated_from:
                    entry["validated_source"] = validated_from
                if not valid and validation_error:
                    entry["validation_error"] = validation_error

            secrets_status.append(entry)
            if not exists:
                guidance = "Configure this secret in Project Settings → Secrets before deploying."
                if secret_name == "cne_pull_secret":
                    guidance = (
                        "Configure cne_pull_secret in Project Settings → Secrets. "
                        "A global fallback can be configured in System → Defaults."
                    )
                entry["guidance"] = guidance
                missing.append(entry)
                continue

            if entry.get("valid") is False:
                guidance = "Update the secret in Project Settings → Secrets and retry deployment."
                if secret_name == "cne_pull_secret":
                    guidance = (
                        "Upload a valid FAR docker config secret in Project Settings → Secrets, "
                        "or configure a valid global fallback in System → Defaults."
                    )
                if secret_name == "jwt_token":
                    guidance = "Upload or paste a valid JWT token (three dot-separated segments)."
                entry["guidance"] = guidance
                missing.append(entry)

        preflight_checks: list[dict] = []
        selected_cluster: dict | None = None
        if self._requires_existing_cluster_preflight(template):
            preflight = self._run_existing_cluster_preflight(project_id)
            preflight_checks = preflight.get("checks") or []
            selected_cluster = preflight.get("selected_cluster")

        blocking_failures = [
            check for check in preflight_checks
            if check.get("status") == "fail" and bool(check.get("blocking"))
        ]

        pass_count = sum(1 for check in preflight_checks if check.get("status") == "pass")
        warn_count = sum(1 for check in preflight_checks if check.get("status") == "warn")
        fail_count = sum(1 for check in preflight_checks if check.get("status") == "fail")

        return {
            "all_satisfied": len(missing) == 0 and len(blocking_failures) == 0,
            "missing_secrets": missing,
            "required_secrets": secrets_status,
            "secret_source_policy": {
                "project_secret_precedence": True,
                "global_cne_pull_secret_default_supported": True,
            },
            "selected_cluster": selected_cluster,
            "preflight_checks": preflight_checks,
            "preflight_summary": {
                "pass": pass_count,
                "warn": warn_count,
                "fail": fail_count,
                "blocking_failures": len(blocking_failures),
            },
        }

    def _lookup_library_module(self, module_path: str) -> ModuleLibrary | None:
        """Resolve a template module path against the synced module_library table.

        Uses the same query (active rows, newest sync wins) that the deploy path
        uses so the preflight and the apply agree on what "present" means.
        """
        return (
            self.db.query(ModuleLibrary)
            .filter(
                ModuleLibrary.path == module_path,
                ModuleLibrary.is_active,
            )
            .order_by(
                ModuleLibrary.is_latest.desc(),
                ModuleLibrary.last_synced.desc().nullslast(),
                ModuleLibrary.id.desc(),
            )
            .first()
        )

    def verify_template_modules_present(
        self,
        template: StackTemplate,
    ) -> tuple[bool, list[dict], list[dict]]:
        """Fail-closed, aggregated module-presence preflight (D-029 P0).

        Resolves EVERY template module against the synced module_library in one
        pass and aggregates the full set of gaps before returning — it does not
        stop at the first missing module. A deploy must not begin applying modules
        when any required module is unresolved.

        Returns:
            (ok, missing_required, skipped_optional) where:
              - ok is False if ANY required module is missing from the library
              - missing_required is a list of {"path", "name"} for every absent
                required module (the complete picture, not just the first)
              - skipped_optional is a list of {"path", "name"} for absent
                non-required modules — surfaced (not silently dropped) so a
                relaxed `required` flag can never hide a gap without a trace
        """
        missing_required: list[dict] = []
        skipped_optional: list[dict] = []

        if not template or not template.modules:
            return True, [], []

        for module_def in template.modules:
            module_path = module_def.get("path")
            if not module_path:
                continue
            module_name = module_def.get("name", module_path)
            is_required = module_def.get("required", True)

            if self._lookup_library_module(module_path) is not None:
                continue

            entry = {"path": module_path, "name": module_name}
            if is_required:
                missing_required.append(entry)
            else:
                skipped_optional.append(entry)
                logger.warning(
                    f"Optional module '{module_path}' not found in library — "
                    f"skipping (will be absent from the deployed stack)"
                )

        return len(missing_required) == 0, missing_required, skipped_optional

    def _missing_modules_error(self, missing_required: list[dict]) -> str:
        """Build the aggregate fail-closed error string enumerating every gap."""
        git_ref = get_default(self.db, "module_library.git_ref") or "unknown ref"
        paths = ", ".join(m["path"] for m in missing_required)
        return (
            f"Required modules missing from catalog (synced from {git_ref}): {paths}. "
            f"Re-sync the module library or check the catalog branch — "
            f"deploy blocked so no partial stack is applied."
        )

    def create_project_modules_from_template(
        self,
        stack_instance: StackInstance
    ) -> tuple[bool, list[ProjectModule], str]:
        """
        Create ProjectModule instances from stack template.

        Args:
            stack_instance: StackInstance to deploy

        Returns:
            Tuple of (success: bool, modules: List[ProjectModule], error: str)
        """
        template = stack_instance.template
        project = stack_instance.project

        if not template or not template.modules:
            return False, [], "Stack template has no modules defined"

        # D-029 P0: Fail-closed, aggregated module-presence preflight. Resolve
        # ALL modules first and block the deploy with the complete list of gaps
        # before any ProjectModule is created — never apply a partial stack.
        ok, missing_required, skipped_optional = self.verify_template_modules_present(template)
        if not ok:
            error = self._missing_modules_error(missing_required)
            logger.error(error)
            return False, [], error
        if skipped_optional:
            logger.warning(
                f"Stack template '{template.slug}': "
                f"{len(skipped_optional)} optional module(s) absent from library and skipped: "
                f"{', '.join(m['path'] for m in skipped_optional)}"
            )

        created_modules = []
        module_map = {}  # path -> ProjectModule for dependency linking

        try:
            # First pass: Create all ProjectModules (S14-006: idempotent — skip existing on retry)
            for idx, module_def in enumerate(template.modules):
                module_path = module_def.get("path")
                module_name = module_def.get("name", module_path)
                module_vars = module_def.get("variables", {})

                # S14-006: Check if module already exists from a previous failed attempt
                existing = self.db.query(ProjectModule).filter(
                    ProjectModule.project_id == project.id,
                    ProjectModule.stack_instance_id == stack_instance.id,
                    ProjectModule.path_in_project == f"stack-{stack_instance.id}/{module_path}"
                ).first()
                if existing:
                    created_modules.append(existing)
                    module_map[module_path] = existing
                    logger.info(f"Reusing existing ProjectModule {existing.id} for '{module_name}' (retry)")
                    continue

                # Find module in library. The fail-closed preflight above
                # (verify_template_modules_present) already aggregated and blocked
                # on any missing REQUIRED module, so a miss here can only be a
                # non-required module — surface the skip (don't silently drop it).
                library_module = self._lookup_library_module(module_path)

                if not library_module:
                    logger.warning(
                        f"Optional module '{module_path}' not found in library — "
                        f"skipping (not part of the deployed stack)"
                    )
                    continue

                # Merge variables in order of precedence:
                # 1. Stack template module defaults (lowest)
                # 2. Platform-specific overrides from template.platform_defaults
                # 3. User-provided variables for this specific module
                # 4. Stack-level variables (highest - applies to all modules)
                final_vars = {}

                # Start with template defaults
                final_vars.update(module_vars)

                # Apply platform-specific variable overrides
                platform_defaults = template.platform_defaults or {}
                if platform_defaults and project.cloud_provider:
                    # Map project.cloud_provider (e.g. "aws") → platform profile key (e.g. "eks")
                    profile_key = PlatformContextService._MANAGED_CLOUD_PROFILE_MAP.get(
                        project.cloud_provider.strip().lower()
                    )
                    if not profile_key:
                        # Check on-prem mappings
                        profile_key = PlatformContextService._ONPREM_PROVIDER_PROFILE_MAP.get(
                            project.cloud_provider.strip().lower()
                        )
                    if profile_key and profile_key in platform_defaults:
                        final_vars.update(platform_defaults[profile_key])
                        logger.info(
                            f"Applied platform_defaults['{profile_key}'] to module '{module_name}'"
                        )

                # Add user-provided variables for this module
                # Variables can be provided as {"module/path": {"var": "value"}} format
                if stack_instance.variables:
                    # Check for module-specific variables
                    module_specific_vars = stack_instance.variables.get(module_path, {})
                    explicit_user_keys: set[str] = set()
                    if isinstance(module_specific_vars, dict):
                        final_vars.update(module_specific_vars)
                        explicit_user_keys.update(module_specific_vars.keys())

                    # Also check for flat variables (legacy/stack-wide)
                    # Only apply if they're not module-path keys
                    for key, value in stack_instance.variables.items():
                        if "/" not in key and not isinstance(value, dict):
                            final_vars[key] = value
                            explicit_user_keys.add(key)

                    final_vars = self._normalize_aws_vpc_subnet_cidrs(
                        module_path=module_path,
                        final_vars=final_vars,
                        explicit_user_keys=explicit_user_keys,
                    )

                # Persist stack-wide inputs into the project variable defaults so any
                # later module retry/redeploy can still assemble the same blueprint
                # inputs even after stack creation time. Without this, downstream
                # modules can "forget" shared inputs like user_ip/ecr_registry.
                if stack_instance.variables:
                    existing_project_vars = project.project_variables if isinstance(project.project_variables, dict) else {}
                    project_vars = dict(existing_project_vars)
                    existing_variable_defaults = project_vars.get("variable_defaults", {})
                    variable_defaults = dict(existing_variable_defaults) if isinstance(existing_variable_defaults, dict) else {}
                    for key, value in StackService._extract_stack_variable_defaults(stack_instance.variables).items():
                        variable_defaults[key] = value
                    project_vars["variable_defaults"] = variable_defaults
                    project.project_variables = project_vars
                    flag_modified(project, "project_variables")
                    self.db.add(project)

                # Create ProjectModule
                project_module = ProjectModule(
                    project_id=project.id,
                    module_library_id=library_module.id,
                    path_in_project=f"stack-{stack_instance.id}/{module_path}",
                    variable_overrides=final_vars,
                    deployment_order=idx,
                    enabled=True,
                    status=ModuleStatus.NOT_INITIALIZED,
                    # IMP-020: dependencies set in second pass via M2M relationship
                    stack_instance_id=stack_instance.id  # Link to stack
                )

                self.db.add(project_module)
                self.db.flush()  # Get ID without committing

                created_modules.append(project_module)
                module_map[module_path] = project_module

                logger.info(
                    f"Created ProjectModule {project_module.id} for '{module_name}' "
                    f"(path: {module_path})"
                )

            # BM-019: Skip already-deployed infrastructure modules.
            # When deploying a BNK Full PoC blueprint on a project where DPU Infrastructure
            # was already deployed, pre-mark matching modules as APPLIED so the chaining
            # engine skips them without re-running.
            if template.category == "bare-metal":
                self._pre_mark_applied_modules(project, stack_instance, created_modules, module_map)

            # Bare-metal topology auto-selection: disable modules not applicable
            # to the host's discovered topology (e.g., set-nic-mode only for regular/bf3-ipmi)
            if template.category == "bare-metal":
                _auto_select_topology_modules(self.db, stack_instance, created_modules, module_map)

            # Second pass: Set up dependencies from module.json metadata
            # Uses real dependency graph instead of forcing sequential execution
            for module in created_modules:
                library_module = module.library_module
                if not library_module:
                    # Fallback for modules without library link — no deps
                    module.dependencies = []
                    continue

                deps_metadata = library_module.dependencies_metadata or {}
                required_deps = deps_metadata.get("required", [])
                dep_ids = []

                for dep in required_deps:
                    dep_path = dep.get("module")
                    if dep_path and dep_path in module_map:
                        dep_ids.append(module_map[dep_path].id)
                    elif dep_path:
                        logger.warning(
                            f"Module '{library_module.path}' declares dependency on "
                            f"'{dep_path}' but it's not in this stack — skipping"
                        )

                module.dependencies = dep_ids
                if dep_ids:
                    dep_names = [
                        module_map[d.get("module")].library_module.name
                        for d in required_deps
                        if d.get("module") in module_map
                    ]
                    logger.info(
                        f"Module '{library_module.name}' depends on: {dep_names}"
                    )
                else:
                    logger.info(
                        f"Module '{library_module.name}' has no dependencies in this stack"
                    )

            # BM-020: Enforce strict sequential ordering for bare-metal stacks.
            # Catalog module.json files may declare DAG-style dependencies (e.g. bnk/flo
            # depending on both k8s/bnk-prerequisites AND k8s/bnk-cert-issuer).  On
            # bare-metal hosts, all modules SSH into the same host — parallel execution
            # is unsafe. Override whatever the catalogs declared with a simple linear
            # chain based on template order. This runs AFTER the metadata-linking pass
            # so it wins over any catalog-supplied deps.
            if template.category == "bare-metal":
                self._enforce_sequential_chain(created_modules, module_map)

            # Commit all changes
            self.db.commit()

            # Update stack instance with deployed module IDs
            stack_instance.deployed_modules = [m.id for m in created_modules]
            stack_instance.total_steps = len(created_modules)
            stack_instance.current_step = 0
            self.db.commit()

            logger.info(
                f"Created {len(created_modules)} ProjectModules for stack '{stack_instance.name}'"
            )

            return True, created_modules, ""

        except Exception as e:
            self.db.rollback()
            error_msg = f"Failed to create ProjectModules: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, [], error_msg

    def _pre_mark_applied_modules(
        self,
        project: Project,
        stack_instance: StackInstance,
        created_modules: list[ProjectModule],
        module_map: dict[str, ProjectModule],
    ) -> int:
        """BM-019: Pre-mark modules as APPLIED if they match previously-applied modules.

        When a BNK Full PoC blueprint is deployed on a project that already has a completed
        DPU Infrastructure blueprint, modules with matching paths are marked APPLIED to avoid
        re-running infrastructure that's already in place.

        Matching criteria:
        - Same project
        - Different stack (not the current one)
        - Module path matches (e.g., "bare-metal/probe-dpu")
        - Source module status is APPLIED

        Returns the count of pre-marked modules.
        """
        # Build a map of all previously-applied modules in this project.
        # Key: module path (e.g., "bare-metal/probe-dpu"), Value: first match found.
        previous_applied: dict[str, ProjectModule] = {}

        existing_modules = (
            self.db.query(ProjectModule)
            .filter(
                ProjectModule.project_id == project.id,
                ProjectModule.stack_instance_id != stack_instance.id,
                ProjectModule.status == ModuleStatus.APPLIED,
            )
            .all()
        )

        for pm in existing_modules:
            # Extract the module path from path_in_project.
            # Format: "stack-{id}/{module_path}" — take everything after the first "/".
            path_in_project = pm.path_in_project or ""
            parts = path_in_project.split("/", 1)
            if len(parts) == 2 and parts[0].startswith("stack-"):
                module_path = parts[1]
                # Only record the first (most recent ordering) match per path.
                if module_path not in previous_applied:
                    previous_applied[module_path] = pm

        if not previous_applied:
            return 0

        pre_marked = 0
        for module_path, new_module in module_map.items():
            if new_module.status != ModuleStatus.NOT_INITIALIZED:
                continue  # Already has a state (e.g., from retry) — do not overwrite.

            source = previous_applied.get(module_path)
            if source:
                if source.outputs is None:
                    logger.warning(
                        "BM-019: Skipping pre-mark for module '%s' — source module %d has no outputs",
                        module_path,
                        source.id,
                    )
                    continue
                new_module.status = ModuleStatus.APPLIED
                new_module.outputs = source.outputs  # Copy outputs for downstream wiring.
                new_module.last_deployed_at = source.last_deployed_at
                pre_marked += 1
                logger.info(
                    "BM-019: Pre-marked module '%s' as APPLIED (source: module %d from stack %s)",
                    module_path,
                    source.id,
                    source.stack_instance_id,
                )

        if pre_marked:
            logger.info(
                "BM-019: Pre-marked %d/%d modules as APPLIED from previous deployments",
                pre_marked,
                len(created_modules),
            )

        return pre_marked

    def _enforce_sequential_chain(
        self,
        created_modules: list[ProjectModule],
        module_map: dict[str, ProjectModule],
    ) -> None:
        """Enforce strict sequential execution: each module depends on the previous one.

        For bare-metal deployments, all modules SSH into the same host — parallel
        execution is unsafe and can corrupt state.  This method replaces any
        DAG-style dependencies (which may have been set from catalog module.json
        metadata) with a simple linear chain derived from template order
        (deployment_order).

        Must be called AFTER the module.json dependency-linking pass so that it
        wins over any catalog-supplied deps.
        """
        if not created_modules:
            return

        sorted_modules = sorted(created_modules, key=lambda m: m.deployment_order or 0)

        # First module has no in-stack predecessors
        sorted_modules[0].dependencies = []

        for i in range(1, len(sorted_modules)):
            prev = sorted_modules[i - 1]
            curr = sorted_modules[i]
            # Exactly one dependency: the immediately preceding module
            curr.dependencies = [prev.id]

        logger.info(
            "BM-020: Enforced strict sequential chain for %d modules",
            len(sorted_modules),
        )

    def deploy_stack(self, stack_instance: StackInstance) -> tuple[bool, str]:
        """
        Deploy a stack instance.

        Creates ProjectModules and initiates deployment.
        Actual module deployment happens via Celery tasks.

        Args:
            stack_instance: StackInstance to deploy

        Returns:
            Tuple of (success: bool, message: str)
        """
        # Validate stack instance
        if stack_instance.status == StackInstanceStatus.DEPLOYING:
            return False, "Stack is already deploying"

        # S15-027: Prevent deploying while stack is being destroyed
        if stack_instance.status == StackInstanceStatus.DESTROYING:
            return False, "Stack is currently being destroyed. Wait for destroy to complete."

        # S18-001: Allow redeploy of deployed stacks — modules already exist,
        # just let run_deploy() handle the incremental apply via vars_changed()
        if stack_instance.status == StackInstanceStatus.DEPLOYED:
            module_count = len(stack_instance.modules)
            logger.info(
                f"Stack '{stack_instance.name}' is deployed with {module_count} modules, "
                f"allowing redeploy (S18-001)"
            )
            return True, f"Stack ready for redeployment with {module_count} modules"

        # S19-001: Pre-flight secrets validation (SEC-001: includes stack variables)
        template = stack_instance.template
        if template:
            prereq_report = self.check_prerequisites(
                stack_instance.project_id, template,
                stack_variables=stack_instance.variables,
            )
            missing = prereq_report.get("missing_secrets") or []
            blocking_checks = [
                check
                for check in (prereq_report.get("preflight_checks") or [])
                if check.get("status") == "fail" and bool(check.get("blocking"))
            ]

            if missing or blocking_checks:
                parts = []
                if missing:
                    names = ", ".join(m["name"] for m in missing)
                    missing_guidance = [m.get("guidance") for m in missing if m.get("guidance")]
                    guidance_suffix = f" {' '.join(dict.fromkeys(missing_guidance))}" if missing_guidance else ""
                    parts.append(
                        f"Missing required project secrets: {names}. "
                        f"Configure these secrets in Project Settings → Secrets before deploying."
                        f"{guidance_suffix}"
                    )
                if blocking_checks:
                    blocker_messages = "; ".join(
                        f"{check.get('title')}: {check.get('message')}"
                        for check in blocking_checks
                    )
                    parts.append(f"Cluster preflight blockers: {blocker_messages}")
                return False, " ".join(parts)

        # Reload with relationships
        self.db.refresh(stack_instance)
        stack_instance.current_step = sum(1 for module in stack_instance.modules if module.status == ModuleStatus.APPLIED)

        backfilled_modules, backfill_error = self._reconcile_stack_modules_from_template(stack_instance)
        if backfill_error:
            stack_instance.status = StackInstanceStatus.FAILED
            stack_instance.error_message = backfill_error
            stack_instance.completed_at = datetime.now(UTC)
            self.db.commit()
            return False, backfill_error

        if backfilled_modules:
            logger.info(
                "Backfilled %s missing template module(s) for stack '%s': %s",
                len(backfilled_modules),
                stack_instance.name,
                [module.path_in_project for module in backfilled_modules],
            )

        # Update status to 'pending' (modules created but not yet deployed)
        # Status changes to 'deploying' when user actually runs Deploy All
        stack_instance.status = StackInstanceStatus.PENDING
        stack_instance.started_at = datetime.now(UTC)
        stack_instance.error_message = None
        self.db.commit()

        logger.info(f"Starting deployment of stack '{stack_instance.name}' (ID: {stack_instance.id})")

        # Create ProjectModules from template
        success, modules, error = self.create_project_modules_from_template(stack_instance)

        if not success:
            stack_instance.status = StackInstanceStatus.FAILED
            stack_instance.error_message = error
            stack_instance.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(f"Stack deployment failed: {error}")
            return False, error

        if not modules:
            stack_instance.status = StackInstanceStatus.FAILED
            stack_instance.error_message = "No modules created from template"
            stack_instance.completed_at = datetime.now(UTC)
            self.db.commit()
            return False, "No modules created from template"

        logger.info(
            f"Stack '{stack_instance.name}' ready for deployment with {len(modules)} modules. "
            f"Deploy modules individually via API or use Celery task for automated deployment."
        )

        return True, f"Stack prepared for deployment with {len(modules)} modules"

    def update_stack_progress(self, stack_instance: StackInstance) -> None:
        """
        Update stack deployment/destroy progress based on module statuses.

        Handles both deployment and destruction status updates.

        Args:
            stack_instance: StackInstance to update
        """
        # S15-026: Re-fetch stack with FOR UPDATE to prevent race condition
        # between task callbacks and orchestrator both writing stack status
        stack_instance = self.db.query(StackInstance).filter(
            StackInstance.id == stack_instance.id
        ).with_for_update().first()
        if not stack_instance:
            return

        # Get all modules
        modules = self.db.query(ProjectModule).filter(
            ProjectModule.stack_instance_id == stack_instance.id
        ).all()

        if not modules:
            # All modules deleted - stack is fully destroyed
            if stack_instance.status == StackInstanceStatus.DESTROYING:
                stack_instance.status = StackInstanceStatus.DESTROYED
                stack_instance.completed_at = datetime.now(UTC)
                stack_instance.deployed_modules = []
                logger.info(f"Stack '{stack_instance.name}' destroyed successfully!")
            self.db.commit()
            return

        total = len(modules)

        # BUG-009: Detect stale transitional modules — in-progress status but no active task.
        # Must run before status counting to produce truthful counts.
        from models import Task as TaskModel
        from models.enums import TaskStatus

        transitional_statuses = (
            ModuleStatus.INITIALIZING, ModuleStatus.PLANNING,
            ModuleStatus.APPLYING, ModuleStatus.DESTROYING,
        )
        for m in modules:
            if m.status in transitional_statuses:
                active_task = self.db.query(TaskModel).filter(
                    TaskModel.module_id == m.id,
                    TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]),
                ).first()
                if not active_task:
                    last_task = self.db.query(TaskModel).filter(
                        TaskModel.module_id == m.id,
                    ).order_by(TaskModel.id.desc()).first()
                    old_status = m.status
                    if last_task and last_task.status == TaskStatus.FAILED:
                        if old_status == ModuleStatus.APPLYING:
                            m.status = ModuleStatus.APPLY_FAILED
                        elif old_status == ModuleStatus.INITIALIZING:
                            m.status = ModuleStatus.INIT_FAILED
                        elif old_status == ModuleStatus.PLANNING:
                            m.status = ModuleStatus.PLAN_FAILED
                        elif old_status == ModuleStatus.DESTROYING:
                            m.status = ModuleStatus.DESTROY_FAILED
                        m.deployment_error = last_task.error or "Task failed"
                        logger.info(f"BUG-009: Recovered stale module {m.id} from {old_status} → {m.status}")
                    elif last_task and last_task.status == TaskStatus.COMPLETED:
                        if old_status == ModuleStatus.DESTROYING:
                            m.status = ModuleStatus.DESTROYED
                        else:
                            m.status = ModuleStatus.APPLIED
                        logger.info(f"BUG-009: Recovered stale module {m.id} from {old_status} → {m.status}")
                    elif last_task is not None:
                        # last_task exists but status is neither FAILED nor COMPLETED
                        # (e.g. PENDING/QUEUED row that was orphaned without an active match).
                        # Safe to reset — the task record gives us evidence something went wrong.
                        m.status = ModuleStatus.NOT_INITIALIZED
                        m.deployment_error = "Module was in a transitional state with no active task — reset"
                        logger.warning(f"BUG-009: Reset stale module {m.id} from {old_status} → not_initialized")
                    else:
                        # No task record at all — module may be genuinely in-flight (task dispatch
                        # not yet committed) or legitimately active.  Do NOT reset; leave the
                        # transitional status intact so the OPERATION_IN_PROGRESS guard fires.
                        logger.debug(
                            "BUG-009: Module %d is in %s with no task history — leaving status unchanged",
                            m.id,
                            old_status,
                        )

        # Count statuses for deployment
        applied = sum(1 for m in modules if m.status == ModuleStatus.APPLIED)
        failed = sum(1 for m in modules if "failed" in str(m.status).lower())

        # Count statuses for destruction
        destroyed = sum(1 for m in modules if m.status == ModuleStatus.DESTROYED)
        sum(1 for m in modules if m.status == ModuleStatus.DESTROYING)

        # Handle destroying status
        if stack_instance.status == StackInstanceStatus.DESTROYING:
            if failed > 0:
                stack_instance.status = StackInstanceStatus.FAILED
                failed_modules = [m for m in modules if "failed" in str(m.status).lower()]
                stack_instance.error_message = f"Module destruction failed: {failed_modules[0].deployment_error}"
                stack_instance.completed_at = datetime.now(UTC)
            elif destroyed == total:
                stack_instance.status = StackInstanceStatus.DESTROYED
                stack_instance.completed_at = datetime.now(UTC)
                logger.info(f"Stack '{stack_instance.name}' destroyed successfully!")
            # else: still destroying, no change needed

            stack_instance.current_step = destroyed
            self.db.commit()
            return

        # Reconcile stale deployment-ish stack rows when all modules are already
        # destroyed. This can happen after destructive recovery paths that remove
        # module resources/rows outside the normal destroy orchestration timing.
        if destroyed == total:
            stack_instance.status = StackInstanceStatus.DESTROYED
            stack_instance.current_step = destroyed
            stack_instance.completed_at = datetime.now(UTC)
            stack_instance.deployed_modules = []
            self.db.commit()
            return

        # Handle deploying status
        stack_instance.current_step = applied

        if failed > 0:
            stack_instance.status = StackInstanceStatus.FAILED
            failed_modules = [m for m in modules if "failed" in str(m.status).lower()]
            stack_instance.error_message = f"Module deployment failed: {failed_modules[0].deployment_error}"
            stack_instance.completed_at = datetime.now(UTC)
        elif applied == total:
            stack_instance.status = StackInstanceStatus.DEPLOYED
            stack_instance.completed_at = datetime.now(UTC)
            logger.info(f"Stack '{stack_instance.name}' deployed successfully!")
        else:
            # Recover stack status when no modules are failed (covers both normal
            # progression and recovery from FAILED after a successful re-run).
            # Only set to DEPLOYING if there are actually in-progress modules.
            # Prevents stale DEPLOYING status from single-module partial operations
            # where all modules have settled to a terminal state (applied/failed/etc.).
            active = sum(
                1
                for m in modules
                if m.status
                in (
                    ModuleStatus.INITIALIZING,
                    ModuleStatus.PLANNING,
                    ModuleStatus.APPLYING,
                    ModuleStatus.DESTROYING,
                )
            )
            if active > 0:
                stack_instance.status = StackInstanceStatus.DEPLOYING
            elif applied > 0:
                # Some modules applied, none active — partial deploy, revert to PENDING
                # so guards (delete, etc.) are not incorrectly blocked
                stack_instance.status = StackInstanceStatus.PENDING
            # else: all not_initialized or unknown terminal states — leave status as-is

        self.db.commit()

    def destroy_stack(self, stack_instance: StackInstance) -> tuple[bool, str]:
        """
        Destroy a stack instance.

        Destroys all modules in reverse dependency order using layer-based execution.
        Modules with no dependents (highest layer) are destroyed first, then waits
        for completion before destroying the next layer.

        Args:
            stack_instance: StackInstance to destroy

        Returns:
            Tuple of (success: bool, message: str)
        """
        if stack_instance.status == StackInstanceStatus.DESTROYING:
            return False, "Stack is already being destroyed"

        workspace = WorkspaceManager(self.db)

        def _cleanup_blueprint_workspace_best_effort() -> None:
            try:
                workspace.cleanup_blueprint_workspace(stack_instance.project_id, stack_instance.id)
            except Exception as e:
                logger.warning(
                    "Failed cleaning blueprint workspace for stack %s: %s",
                    stack_instance.id,
                    e,
                )

        if not stack_instance.modules:
            # No modules, just delete the stack instance
            _cleanup_blueprint_workspace_best_effort()
            self.db.delete(stack_instance)
            self.db.commit()
            return True, "Stack deleted (no modules to destroy)"

        # Clean up orphaned module IDs (modules that no longer exist)
        existing_modules = self.db.query(ProjectModule).filter(
            ProjectModule.stack_instance_id == stack_instance.id
        ).all()
        existing_ids = {m.id for m in existing_modules}
        orphaned_ids = {m.id for m in stack_instance.modules} - existing_ids

        if orphaned_ids:
            logger.warning(f"Cleaning up {len(orphaned_ids)} orphaned module IDs from stack: {orphaned_ids}")

        if not existing_modules:
            # All modules gone, just delete the stack
            _cleanup_blueprint_workspace_best_effort()
            self.db.delete(stack_instance)
            self.db.commit()
            return True, "Stack deleted (all modules already removed)"

        # BUG-008: Recover stale transitional modules before destroy guard check.
        # Modules stuck in applying/initializing/etc. with no active Celery task
        # should be recovered so they don't block destruction indefinitely.
        from models import Task as TaskModel
        from models.enums import TaskStatus

        transitional_statuses = (
            ModuleStatus.INITIALIZING, ModuleStatus.PLANNING,
            ModuleStatus.APPLYING, ModuleStatus.DESTROYING,
        )
        for module in existing_modules:
            if module.status in transitional_statuses:
                active_task = self.db.query(TaskModel).filter(
                    TaskModel.module_id == module.id,
                    TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]),
                ).first()
                if not active_task:
                    last_task = self.db.query(TaskModel).filter(
                        TaskModel.module_id == module.id,
                    ).order_by(TaskModel.id.desc()).first()
                    old_status = module.status
                    if last_task and last_task.status == TaskStatus.FAILED:
                        if old_status == ModuleStatus.APPLYING:
                            module.status = ModuleStatus.APPLY_FAILED
                        elif old_status == ModuleStatus.DESTROYING:
                            module.status = ModuleStatus.DESTROY_FAILED
                        else:
                            module.status = ModuleStatus.INIT_FAILED
                        module.deployment_error = last_task.error or "Task failed"
                    elif last_task and last_task.status == TaskStatus.COMPLETED:
                        if old_status == ModuleStatus.DESTROYING:
                            module.status = ModuleStatus.DESTROYED
                        else:
                            module.status = ModuleStatus.APPLIED
                    else:
                        module.status = ModuleStatus.NOT_INITIALIZED
                        module.deployment_error = "Stale transitional state recovered during destroy"
                    logger.info(f"BUG-008: Recovered stale module {module.id} from {old_status} → {module.status}")
        self.db.commit()

        # Check which modules have actual infrastructure to destroy
        modules_to_destroy = []
        modules_to_delete = []

        for module in existing_modules:
            if module.status == ModuleStatus.DESTROYED:
                # Already destroyed, just remove the module record
                modules_to_delete.append(module)
            elif module.status in transitional_statuses:
                # BUG-005: Cannot destroy while operation in progress
                # (After BUG-008 recovery, only genuinely active modules reach here)
                module_name = module.library_module.name if module.library_module else f"Module {module.id}"
                return False, (
                    f"Cannot destroy stack: module '{module_name}' has operation in progress "
                    f"(status: {module.status}). Wait for it to complete or cancel the operation first."
                )
            elif module.status in [ModuleStatus.APPLIED, ModuleStatus.APPLY_FAILED, ModuleStatus.DESTROY_FAILED]:
                # CP-009: Has or may have infrastructure to destroy
                # Don't require outputs — modules can create resources without output definitions
                modules_to_destroy.append(module)
            else:
                # No infrastructure deployed (not_initialized, initialized, planned, init_failed, plan_failed)
                modules_to_delete.append(module)

        # Delete modules with no infrastructure immediately
        for module in modules_to_delete:
            logger.info(f"Module {module.id} has no infrastructure (status={module.status}), deleting directly")
            self.db.delete(module)

        if modules_to_delete:
            self.db.commit()

        if not modules_to_destroy:
            # No modules need actual destruction, stack is done
            logger.info(f"No destroy tasks needed for stack '{stack_instance.name}', deleting stack record")
            stack_instance.deployed_modules = []
            stack_instance.status = StackInstanceStatus.DESTROYED
            self.db.commit()
            _cleanup_blueprint_workspace_best_effort()
            self.db.delete(stack_instance)
            self.db.commit()
            return True, f"Stack deleted ({len(modules_to_delete)} module records removed)"

        # Update status
        stack_instance.status = StackInstanceStatus.DESTROYING
        stack_instance.total_steps = len(modules_to_destroy)
        stack_instance.current_step = 0
        stack_instance.deployed_modules = [m.id for m in modules_to_destroy]
        self.db.commit()

        logger.info(f"Starting destruction of stack '{stack_instance.name}' (ID: {stack_instance.id}) with {len(modules_to_destroy)} modules")

        # D-001 Phase 3 (S3b): stable run identifier for this destroy run.
        # ParallelExecution record removed (table dropped in v2_119).
        run_handle = uuid4().hex

        # D-001 Phase 3: event-driven self-triggering reverse-DAG destroy.
        # Dispatch leaf modules (no deployed dependents within the destroy scope),
        # then _trigger_next_destroy_module queues the rest as each completes.
        try:
            first_task_id = self._dispatch_first_destroy_wave_stack(
                stack_instance, modules_to_destroy, run_handle=run_handle
            )
            logger.info(
                "Event-chain stack destroy initiated for stack %s "
                "(run_handle: %s, first wave task id: %s, %d modules)",
                stack_instance.id, run_handle, first_task_id, len(modules_to_destroy),
            )

            message = f"Stack destruction initiated for {len(modules_to_destroy)} modules (event-chain)"
            if modules_to_delete:
                message += f" ({len(modules_to_delete)} deleted directly)"

            return True, message

        except Exception as e:
            error_msg = f"Failed to start stack destruction: {str(e)}"
            logger.error(error_msg, exc_info=True)
            stack_instance.status = StackInstanceStatus.FAILED
            stack_instance.error_message = error_msg
            self.db.commit()
            return False, error_msg

    def _dispatch_first_destroy_wave_stack(
        self,
        stack_instance,
        modules_to_destroy: list,
        run_handle: str | None = None,
    ) -> str | None:
        """
        Dispatch destroy for leaf modules in the stack (no deployed dependents within scope).

        Mirrors _dispatch_first_destroy_wave from ParallelExecutionService but scoped to
        the stack's deployed_modules list.

        Args:
            stack_instance: The StackInstance being destroyed.
            modules_to_destroy: Modules in the stack with infrastructure to destroy.
            run_handle: Stable run identifier (uuid4 hex) written onto every Task row
                this wave creates. Passed from _execute_stack_destroy (S2).

        Returns the Celery task id of the first task dispatched (stable UI handle),
        or None if nothing needed dispatching.
        """
        from datetime import UTC, datetime

        from models import Task as TaskModel
        from models.enums import ModuleStatus, TaskStatus
        from services.execution.task_dispatch import dispatch_destroy_signature
        from tasks.parallel_tasks import NO_INFRA_STATUSES

        first_task_id: str | None = None

        for module in modules_to_destroy:
            # Skip no-infra modules (should not be in modules_to_destroy, but be safe)
            if module.status in NO_INFRA_STATUSES:
                continue

            # Find deployed dependents within the stack scope
            deployed_dependents = [
                m for m in modules_to_destroy
                if module.id in (m.dependencies or []) and m.status not in NO_INFRA_STATUSES
            ]
            if deployed_dependents:
                continue  # Not a leaf yet

            # Idempotency guard (S15-008)
            existing = self.db.query(TaskModel).filter(
                TaskModel.module_id == module.id,
                TaskModel.task_type == "destroy",
                TaskModel.status.in_([
                    TaskStatus.PENDING.value,
                    TaskStatus.QUEUED.value,
                    TaskStatus.IN_PROGRESS.value,
                ]),
            ).first()
            if existing:
                logger.info(
                    "First stack destroy wave: skipping module %s — task %s already %s",
                    module.id, existing.id, existing.status,
                )
                if first_task_id is None:
                    first_task_id = existing.celery_task_id
                continue

            # Create Task and dispatch
            task = TaskModel(
                task_type="destroy",
                status=TaskStatus.QUEUED.value,
                project_id=module.project_id,
                module_id=module.id,
                created_at=datetime.now(UTC),
                run_handle=run_handle,  # S2: stamp run_handle onto every Task row
            )
            self.db.add(task)
            module.status = ModuleStatus.DESTROYING.value
            self.db.flush()
            self.db.refresh(task)

            sig = dispatch_destroy_signature(task.id, module)
            async_result = sig.apply_async()
            task.celery_task_id = async_result.id

            if first_task_id is None:
                first_task_id = async_result.id

            logger.info(
                "First stack destroy wave: dispatched task %s (celery %s) for module %s "
                "(run_handle: %s)",
                task.id, async_result.id, module.id, run_handle,
            )

        self.db.commit()
        return first_task_id

    def get_deployment_status(self, stack_instance: StackInstance) -> dict:
        """
        Get detailed deployment status for a stack.

        Args:
            stack_instance: StackInstance to check

        Returns:
            Dict with deployment status details
        """
        if not stack_instance.modules:
            return {
                "status": stack_instance.status,
                "current_step": 0,
                "total_steps": 0,
                "modules": [],
                "progress_percentage": 0
            }

        # B3: load_only() — status display only needs library_module.name
        modules = self.db.query(ProjectModule).options(
            joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name)
        ).filter(
            ProjectModule.stack_instance_id == stack_instance.id
        ).order_by(ProjectModule.deployment_order).all()

        module_statuses = []
        for module in modules:
            module_statuses.append({
                "id": module.id,
                "name": module.library_module.name if module.library_module else "Unknown",
                "path": module.path_in_project,
                "status": module.status,
                "deployment_order": module.deployment_order,
                "error": module.deployment_error
            })

        completed = sum(1 for m in modules if m.status == ModuleStatus.APPLIED)
        total = len(modules)
        progress_percentage = int((completed / total) * 100) if total > 0 else 0

        return {
            "status": stack_instance.status,
            "current_step": stack_instance.current_step,
            "total_steps": stack_instance.total_steps or total,
            "modules": module_statuses,
            "progress_percentage": progress_percentage,
            "started_at": stack_instance.started_at.isoformat() if stack_instance.started_at else None,
            "completed_at": stack_instance.completed_at.isoformat() if stack_instance.completed_at else None,
            "error_message": stack_instance.error_message
        }


# ── Bare-metal topology auto-selection ────────────────────────────────


# Mapping: topology → set of optional module paths to ENABLE
# Modules not in this map for the given topology remain disabled.
_TOPOLOGY_MODULE_MAP: dict[str, set[str]] = {
    "regular": {"bare-metal/set-nic-mode"},
    "bf3": {"bare-metal/stage-vf-netplan", "bare-metal/install-fallback-timer"},
    "bf3_ipmi": {"bare-metal/set-nic-mode", "bare-metal/deposit-phase-c", "bare-metal/power-cycle-bmc", "bare-metal/wait-connectivity"},
    "bmc": {"bare-metal/stage-vf-netplan", "bare-metal/deposit-phase-c", "bare-metal/power-cycle-bmc", "bare-metal/wait-connectivity"},
    # dual_dpu_obmc Phase 1 is handled entirely by the orchestrator's DUAL_DPU_OBMC_STEPS
    # (probe → flash via BMC rshim → wait via IPv6 link-local → setup DPU networking).
    # All Phase 1 steps are required, not optional blueprint modules, so no optionals to enable.
    "dual_dpu_obmc": set(),
}


def _auto_select_topology_modules(
    db: Session,
    stack_instance: StackInstance,
    created_modules: list[ProjectModule],
    module_map: dict[str, ProjectModule],
) -> None:
    """Disable optional bare-metal modules not applicable to the host's topology.

    Called after modules are created from a bare-metal blueprint.
    Reads the BareMetalHost's topology from the stack_instance variables,
    then disables optional modules that aren't relevant.

    Required modules (probe-dpu, flash-dpu, etc.) are never disabled.
    """
    from models.bare_metal import BareMetalHost

    # Resolve host from stack variables
    stack_vars = stack_instance.variables or {}
    host_id = stack_vars.get("bare_metal_host_id")
    if not host_id:
        # Try finding it in module variables
        for _path, pm in module_map.items():
            host_id = (pm.variable_overrides or {}).get("bare_metal_host_id")
            if host_id:
                break

    if not host_id:
        logger.debug("No bare_metal_host_id found — skipping topology auto-selection")
        return

    host = db.query(BareMetalHost).filter(BareMetalHost.id == int(host_id)).first()
    if not host or not host.topology:
        logger.debug("Host %s has no topology set — skipping auto-selection", host_id)
        return

    topology = host.topology.lower()
    enabled_optionals = _TOPOLOGY_MODULE_MAP.get(topology, set())
    disabled_count = 0

    for pm in created_modules:
        lib_module = pm.library_module
        if not lib_module:
            continue

        module_path = lib_module.path

        # Only touch optional bare-metal modules
        if not module_path.startswith("bare-metal/"):
            continue

        # Check if this module is optional in the template
        template_modules = (stack_instance.template.modules if stack_instance.template else []) or []
        is_optional = False
        for tmpl_mod in template_modules:
            if tmpl_mod.get("path") == module_path and not tmpl_mod.get("required", True):
                is_optional = True
                break

        if not is_optional:
            continue  # Required modules are always enabled

        # Disable optional modules not in the topology's enabled set
        if module_path not in enabled_optionals:
            pm.enabled = False
            disabled_count += 1
            logger.info(
                "Topology '%s': disabled optional module '%s' for host %s",
                topology, module_path, host.name,
            )

    if disabled_count > 0:
        logger.info(
            "Topology auto-selection for '%s': disabled %d optional modules",
            topology, disabled_count,
        )
