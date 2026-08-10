"""
Variable Assembler — single source of truth for variable assembly.

Builds the complete variable dict for module execution using the layered chain:
  1.   Project-level/system variables (project_name, environment, region, etc.)
  2.   Project Variables (user-defined project-wide vars)
  2.5. Project context resolution (BM-020: DPU settings, version profile, host)
  3.   Dependency output wiring (from module.json metadata — explicit wiring)
  4.   Auto-wire common variables (match output names across applied modules)
  4.5. Module-specific transforms (BM-020: canonical → module-specific vars)
  5.   User-provided variables (module-level overrides)
  6.   Project secrets (file paths and values)
  7.   Variable mappings (rename/transform via VariableMapping records)

This module is shared by BOTH engines:
  - OpenTofu engine: OpenTofuRuntime.build_variables() delegates here
  - K8s engine: called directly by Celery task layer

Consolidated from execution_engine.py (P0 Phase 2).
"""

import base64
import json
import logging
from typing import Any

from sqlalchemy.orm import Session, joinedload

from core.encryption import decrypt_value, decrypt_value_or_none
from models.ssh_credential import SSHCredential
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from utils.naming import slugify_aws_name, slugify_gcp_name, slugify_s3_name

logger = logging.getLogger(__name__)

# Mirrors the frontend deploy-dialog sentinel. Inputs that should be filled
# from the project's credential template are submitted with this token so the
# UI can show the user the value will be inherited; we substitute it here as
# a last line of defense in case earlier resolution paths missed it.
INHERIT_FROM_TEMPLATE_SENTINEL = "__inherited_from_template__"


def _resolve_inherit_sentinels(module, user_vars: dict[str, Any]) -> dict[str, Any]:
    if not user_vars:
        return user_vars
    if not any(v == INHERIT_FROM_TEMPLATE_SENTINEL for v in user_vars.values()):
        return user_vars

    template = getattr(getattr(module, "project", None), "credential_template", None)
    if template is None:
        return user_vars

    resolved = dict(user_vars)
    if template.provider == "ibm":
        for name, value in list(resolved.items()):
            if value != INHERIT_FROM_TEMPLATE_SENTINEL:
                continue
            if name == "ibmcloud_api_key" and template.ibmcloud_api_key_encrypted:
                api_key = decrypt_value_or_none(template.ibmcloud_api_key_encrypted)
                if api_key:
                    resolved[name] = api_key
            elif name == "ibmcloud_resource_group" and template.ibmcloud_resource_group:
                resolved[name] = template.ibmcloud_resource_group
            elif name in ("region", "ibmcloud_cluster_region", "ibmcloud_region") and template.region:
                resolved[name] = template.region
    return resolved


def _pick_bnk_external_vip(db: Session, cluster) -> str | None:
    """
    Pick a sensible Gateway VIP from a cluster's BNK external F5SPKVlan.

    Queries the cluster for F5SPKVlan CRs (k8s.f5net.com/v1), finds the
    external one (category=external or internal=false), and returns an
    IP near the top of its network that isn't the VLAN's own self-IP.

    Returns None if the cluster is unreachable, has no BNK VLAN, or we
    can't compute a safe IP. Callers should treat None as "no auto-value"
    and fall back to the module's default.

    This is best-effort; the picked IP may still collide with an existing
    workload. For production, users should specify gateway_vip explicitly.
    """
    import ipaddress

    from kubernetes.client.rest import ApiException

    from services.kubernetes_service import KubernetesService

    k8s = KubernetesService(db)
    try:
        api = k8s.load_kubeconfig(cluster)
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway_vip auto-pick: kubeconfig load failed: %s", exc)
        return None

    # Fail fast — this lookup is best-effort and runs on every init.
    # A freshly-provisioned EKS endpoint can be unreachable for several minutes
    # while DNS propagates; default urllib3 retries (~150s × 3) used to add ~10
    # minutes to every dependent module's init. (connect_timeout, read_timeout)
    try:
        api.api_client.rest_client.pool_manager.connection_pool_kw[
            "retries"
        ] = 0
    except Exception:  # noqa: BLE001
        pass

    try:
        response = api.call_api(
            resource_path="/apis/k8s.f5net.com/v1/f5-spk-vlans",
            method="GET",
            response_type="object",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
            _request_timeout=(2, 3),
        )
    except ApiException as exc:
        logger.debug("gateway_vip auto-pick: F5SPKVlan list failed (%s)", exc.status)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("gateway_vip auto-pick: F5SPKVlan list error: %s", exc)
        return None

    items = response.get("items") if isinstance(response, dict) else None
    if not isinstance(items, list):
        return None

    for item in items:
        spec = item.get("spec", {}) if isinstance(item, dict) else {}
        if not isinstance(spec, dict):
            continue
        category = spec.get("category")
        internal = spec.get("internal")
        # External VLAN: category="external" OR internal is explicitly False
        if category != "external" and internal is not False:
            continue
        self_ips = spec.get("selfip_v4s") or []
        prefix_len = spec.get("prefixlen_v4") or 24
        if not self_ips:
            continue
        try:
            net = ipaddress.ip_network(f"{self_ips[0]}/{prefix_len}", strict=False)
        except (ValueError, ipaddress.AddressValueError):
            continue
        # Pick .100 offset (4 hosts above the network address) — unlikely to
        # collide with self-IPs which are typically at the high end (.240+).
        # Skip network, broadcast, and any self-IP.
        candidate = net.network_address + 100
        if candidate not in net or candidate == net.network_address or candidate == net.broadcast_address:
            continue
        if str(candidate) in self_ips:
            continue
        return str(candidate)

    return None


def _inject_cluster_kubeconfig(
    variables: dict[str, Any],
    lib_module,  # ModuleLibrary | None
    first_cluster,  # KubernetesCluster | mock
) -> None:
    """Auto-inject kubeconfig_content for tofu modules that declare it.

    k8s-engine modules get their kubeconfig from engine_router; tofu
    modules must receive it as a variable. Inject from the project's
    registered cluster so users don't have to re-supply credentials
    forge already holds.

    Must be called AFTER Layer 5 (user overrides) are merged into
    ``variables`` — this guards on ``not variables.get("kubeconfig_content")``
    so a genuine user-supplied kubeconfig always wins, and only falls back to
    the registered cluster when the field is unset/blank (including legacy
    placeholder values like ``"<username>"``).
    """
    _module_schema = (lib_module.variables_schema or []) if lib_module else []
    _wants_kubeconfig = any(
        isinstance(v, dict) and v.get("name") == "kubeconfig_content" for v in _module_schema
    )
    if not _wants_kubeconfig:
        return
    if variables.get("kubeconfig_content"):
        return
    if not getattr(first_cluster, "kubeconfig_encrypted", None):
        return
    try:
        _kc = decrypt_value(first_cluster.kubeconfig_encrypted)
        if _kc:
            _kc = normalize_kubeconfig(_kc, source=NormalizationSource.INTERNAL_REREAD)
            variables["kubeconfig_content"] = _kc
            logger.debug(
                "Auto-injected kubeconfig_content from registered cluster %s",
                first_cluster.name,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to auto-inject kubeconfig_content: %s", exc)


def _inject_project_ssh_credential_variables(
    db: Session,
    module,
    variables: dict[str, Any],
) -> None:
    """Auto-wire project SSH credential values when module expects SSH inputs."""
    project = module.project
    lib_module = module.library_module
    if not project or not lib_module:
        return

    if not project.ssh_credential_id:
        return

    schema = lib_module.variables_schema or []
    declared_names = {
        str(v.get("name"))
        for v in schema
        if isinstance(v, dict) and v.get("name")
    }

    wanted = {"ssh_host", "ssh_user", "ssh_private_key_path"}
    needed = wanted.intersection(declared_names)
    if not needed:
        return

    cred = db.query(SSHCredential).filter(SSHCredential.id == project.ssh_credential_id).first()
    if not cred:
        logger.warning(
            "Project %s references missing ssh_credential_id=%s",
            project.id,
            project.ssh_credential_id,
        )
        return

    if "ssh_host" in needed and not variables.get("ssh_host"):
        variables["ssh_host"] = cred.host

    if "ssh_user" in needed and not variables.get("ssh_user"):
        variables["ssh_user"] = cred.username

    if "ssh_private_key_path" in needed and not variables.get("ssh_private_key_path"):
        if cred.private_key_encrypted:
            try:
                variables["ssh_private_key_path"] = decrypt_value(cred.private_key_encrypted)
            except Exception as exc:
                logger.warning(
                    "Failed to decrypt SSH private key for credential %s: %s",
                    cred.id,
                    exc,
                )


def _inject_rendered_bf_conf(
    db: Session,
    host,  # BareMetalHost
    module,  # ProjectModule
    variables: dict[str, Any],
) -> None:
    """Render the full bf.conf Jinja2 template and inject it into variables.

    Uses the same renderer as the DPU-tab flash flow (``bf_conf_renderer``).
    The join path from BareMetalHost to Dpu is:
        Dpu.project_id == host.project_id AND Dpu.host_node_ip == host.host_ip

    Does nothing (silently returns) if any prerequisite is missing:
      - No Dpu record for this host (DPU tab not used yet)
      - No ProjectDpuSettings for the project
      - No bf.conf template configured in settings
      - No DPU OS password available
    """
    from models.dpu import BfConfTemplate, Dpu, ProjectDpuSettings

    # Find the Dpu record associated with this host. For dual_dpu_obmc
    # hosts there are TWO Dpu rows sharing the same host_node_ip — one per
    # BF3 chip — so an unscoped `.first()` is non-deterministic and can
    # render bf.conf with the OTHER DPU's allocated IPs. When the host
    # carries a `deploy_dpu_pci_address`, narrow the query to the matching
    # PCI bus prefix.
    query = db.query(Dpu).filter(
        Dpu.project_id == host.project_id,
        Dpu.host_node_ip == host.host_ip,
    )
    deploy_pci_bus = (getattr(host, "deploy_dpu_pci_address", None) or "").rsplit(".", 1)[0]
    if deploy_pci_bus:
        query = query.filter(Dpu.pci_address == deploy_pci_bus)
    dpu = query.first()
    if dpu is None:
        logger.debug(
            "No Dpu record for host %s (ip=%s, deploy_pci_bus=%s) — skipping bf.conf render",
            host.name, host.host_ip, deploy_pci_bus or "<unset>",
        )
        return

    # Get project DPU settings (holds the bf.conf template choice)
    settings = (
        db.query(ProjectDpuSettings)
        .filter(ProjectDpuSettings.project_id == module.project_id)
        .first()
    )
    if settings is None or settings.bf_template_id is None:
        logger.debug("No DPU settings or bf.conf template for project %s — skipping render", module.project_id)
        return

    bf_template = db.query(BfConfTemplate).filter(BfConfTemplate.id == settings.bf_template_id).first()
    if bf_template is None:
        logger.debug("bf.conf template id=%s not found — skipping render", settings.bf_template_id)
        return

    # Resolve the DPU OS password — prefer the already-injected variable
    # (from the DPU credential block above), fall back to project settings.
    dpu_password = variables.get("dpu_password", "")
    if not dpu_password and settings.default_os_password_encrypted:
        try:
            dpu_password = decrypt_value(settings.default_os_password_encrypted)
        except Exception:
            logger.debug("Could not decrypt project DPU OS password — skipping render")
            return
    if not dpu_password:
        logger.debug("No DPU password available — skipping bf.conf render")
        return

    # Propagate the resolved password back so downstream modules (wait-dpu-ready,
    # setup-dpu-networking) can SSH into the DPU with the same password baked
    # into the bf.cfg. Without this, the password is only used for rendering
    # and modules fall back to "ubuntu" with no password.
    if "dpu_password" not in variables:
        variables["dpu_password"] = dpu_password

    # Resolve SSH credential for authorized_keys injection
    ssh_cred: SSHCredential | None = None
    if settings.default_os_ssh_credential_id is not None:
        ssh_cred = db.query(SSHCredential).filter(
            SSHCredential.id == settings.default_os_ssh_credential_id
        ).first()

    from services.bf_conf_renderer import build_render_context, render_bf_conf

    ctx = build_render_context(
        dpu=dpu,
        settings=settings,
        ssh_credential=ssh_cred,
        ubuntu_password_plaintext=dpu_password,
        decrypt=decrypt_value,
    )
    rendered = render_bf_conf(bf_template, ctx)
    variables["rendered_bf_conf"] = rendered

    # Also expose the structured VLAN list so downstream modules (notably
    # bare-metal/setup-dpu-networking on the dual_dpu_obmc path) can
    # render the symmetric host-side netplan: matching VLAN sub-interfaces
    # on the host's LAG PF, IPs derived from the DPU's subnet + host's
    # mgmt-IP last octet.
    variables["dpu_vlans"] = [
        {"name": v.name, "tag": v.tag, "uplink": v.uplink, "ip": v.ip}
        for v in ctx.host.vlans
    ]
    variables["dpu_mtu"] = ctx.dpu_mtu

    logger.info(
        "Injected rendered bf.conf (%d chars) + %d VLAN entries for host %s",
        len(rendered), len(ctx.host.vlans), host.name,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_variables(
    db: Session,
    module,  # ProjectModule
    secret_variables: dict[str, str] | None = None,
    *,
    operation: str = "apply",
) -> dict[str, Any]:
    """
    Build complete variable dict for module execution.

    Applies the 7-layer chain (lowest to highest precedence):
      1. Project-level/system variables
      2. Project Variables (user-defined project-wide)
      3. Dependency output wiring (from module.json metadata)
      4. Auto-wire common variables (match output names across applied modules)
      5. User-provided variables (module-level overrides)
      6. Project secrets (file paths and values)
      7. Variable mappings (rename/transform via VariableMapping records)

    Args:
        db: Database session
        module: ProjectModule with relationships loaded
        secret_variables: Pre-prepared secret values (from secrets_service)
        operation: "apply", "destroy", "plan", or "init" — destroy is lenient
                   about missing dependency outputs since modules may already
                   be torn down.

    Returns:
        Complete variable dict ready for execution
    """
    from models import CloudCredentialTemplate
    from services.defaults_service import get_default

    variables: dict[str, Any] = {}
    project = module.project
    lib_module = module.library_module

    # Validate region alignment with credential template
    if project.credential_template_id:
        template = db.query(CloudCredentialTemplate).filter(
            CloudCredentialTemplate.id == project.credential_template_id
        ).first()

        if template and project.cloud_provider == "aws" and template.region != project.region:
            logger.warning(
                f"Region mismatch for project '{project.name}': "
                f"Credential template '{template.name}' uses {template.region}, "
                f"but project deploys to {project.region}. "
                f"Ensure IAM policies allow multi-region operations."
            )

    # Layer 1: Project-level/system variables
    variables["project_name"] = project.name
    variables["project_name_aws_safe"] = slugify_aws_name(project.name)
    variables["project_name_s3_safe"] = slugify_s3_name(project.name)
    variables["project_name_gcp_safe"] = slugify_gcp_name(project.name)
    variables["environment"] = project.environment or "dev"
    variables["common_tags"] = {
        "Project": project.name,
        "Environment": project.environment or "dev",
        "ManagedBy": "BNK-Forge",
    }

    # Inject K8s cluster context when the project is bound to a cluster. Lets
    # modules declare cluster_name as source:auto and skip a user-input prompt.
    # Aliases cover the common naming conventions used across cloud-specific
    # modules (EKS / AKS / GKE).
    first_cluster = None
    if getattr(project, "k8s_clusters", None):
        first_cluster = project.k8s_clusters[0]
        cluster_name = getattr(first_cluster, "name", None)
        if cluster_name:
            variables["cluster_name"] = cluster_name
            variables["eks_cluster_name"] = cluster_name
            variables["aks_cluster_name"] = cluster_name
            variables["gke_cluster_name"] = cluster_name
            variables["roks_cluster_name"] = cluster_name
            variables["openshift_cluster_name"] = cluster_name

        # Best-effort: derive a BNK Gateway VIP from the cluster's external
        # F5SPKVlan. Gives blueprints like eks-bnk-smartllm-demo a
        # zero-input deploy against any cluster whose BNK external VLAN
        # isn't 10.0.10.0/24. User-level variables still override.
        try:
            bnk_vip = _pick_bnk_external_vip(db, first_cluster)
        except Exception as exc:  # noqa: BLE001 — auto-wire is best-effort
            bnk_vip = None
            logger.debug("gateway_vip auto-pick failed: %s", exc)
        if bnk_vip:
            variables["gateway_vip"] = bnk_vip
            variables["bnk_external_vip"] = bnk_vip

    # AWS-specific variables — only inject for AWS projects
    if project.cloud_provider == "aws":
        region = project.region or get_default(db, "cloud.aws.default_region")
        variables["region"] = region
        variables["aws_region"] = region  # backward compat with modules using var.aws_region

        # Add aws_profile if credential template uses profile auth
        if project.credential_template_id:
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project.credential_template_id
            ).first()
            if template and template.aws_auth_method == 'profile' and template.aws_profile:
                variables["aws_profile"] = template.aws_profile
            else:
                variables["aws_profile"] = "default"
        else:
            variables["aws_profile"] = "default"

    if project.cloud_provider == "ibm" and project.region:
        variables["region"] = project.region
        variables["ibm_region"] = project.region

        if project.credential_template_id:
            template = db.query(CloudCredentialTemplate).filter(
                CloudCredentialTemplate.id == project.credential_template_id
            ).first()
            if template and template.ibmcloud_resource_group:
                variables["ibmcloud_resource_group"] = template.ibmcloud_resource_group

    # Layer 2: Project Variables (user-defined project-wide variables)
    if project.project_variables:
        proj_vars = project.project_variables
        if isinstance(proj_vars, dict):
            if "variable_defaults" in proj_vars:
                variables.update(proj_vars["variable_defaults"])
                logger.debug(f"Applied {len(proj_vars['variable_defaults'])} project variables (legacy)")
            else:
                variables.update(proj_vars)
                logger.debug(f"Applied {len(proj_vars)} project variables")

    # Layer 2.5: Project context resolution (BM-020 blueprint service layer)
    # Injects canonical variables from ProjectDpuSettings, BareMetalHost,
    # BnkVersionProfile, and Dpu records. Low precedence — everything
    # downstream (dependency wiring, auto-wire, user overrides) can override.
    from services.execution.blueprint_context import (
        ProjectContext,
        resolve_project_context,
        transform_for_module,
    )

    _host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
    if not _host_id:
        _host_id = (module.variables or {}).get("bare_metal_host_id")

    # Skip the DPU/bare-metal augmenter for pure cloud-infra modules with no
    # bare-metal host. These modules (infra/aws/*, infra/gke/*, infra/azure/*)
    # never consume DPU/VLAN context, and unconditionally querying
    # ProjectDpuSettings here couples every cloud deploy to the bare-metal
    # schema — any drift in that table breaks unrelated cloud deploys.
    _path_for_gate = (lib_module.path if lib_module else None) or ""
    _is_pure_cloud_infra = _path_for_gate.startswith(("infra/aws/", "infra/gke/", "infra/azure/"))
    if _is_pure_cloud_infra and not _host_id:
        _project_ctx = ProjectContext()
    else:
        _project_ctx = resolve_project_context(db, project.id, bare_metal_host_id=_host_id)

    for _ctx_key, _ctx_val in _project_ctx.as_low_precedence_vars().items():
        if _ctx_key not in variables:
            variables[_ctx_key] = _ctx_val

    # Layer 2.6: Template defaults — apply module variable_overrides as low-precedence
    # defaults BEFORE dependency wiring. This prevents Layer 3 from hard-failing
    # when a dependency module (e.g. infra/aws/vpc) doesn't exist but the template
    # already provides a suitable default (e.g. external_subnet_cidrs: []).
    # Layer 5 re-applies these as high-precedence overrides later.
    _template_defaults = module.variable_overrides or module.variables or {}
    for _td_key, _td_val in _template_defaults.items():
        if _td_key not in variables:
            variables[_td_key] = _td_val

    # Layer 2.75: Early module transforms — run BEFORE dependency wiring so that
    # module-specific variable names (e.g. external_subnet_cidrs) are present when
    # Layer 3 checks for missing dependencies. Without this, Layer 3 crashes on
    # modules like k8s/network-setup whose pack_manifest wires from infra/aws/vpc
    # (which doesn't exist in bare-metal) before Layer 4.5 transforms can supply
    # the values from DPU settings.
    _module_path = (lib_module.path if lib_module else None) or ""
    _early_transform = transform_for_module(_module_path, variables, _project_ctx)
    for _tk, _tv in _early_transform.items():
        if _tk not in variables:
            variables[_tk] = _tv

    # Layer 3: Dependency output wiring (from module.json metadata — explicit wiring)
    if lib_module and lib_module.inputs_metadata:
        required_inputs = lib_module.inputs_metadata.get("required", [])
        optional_inputs = lib_module.inputs_metadata.get("optional", [])
        required_input_names = {inp.get("name") for inp in required_inputs}

        for inp in required_inputs + optional_inputs:
            if inp.get("source") == "module":
                from_module_path = inp.get("from_module")
                from_output = inp.get("from_output")
                input_name = inp.get("name")
                is_required = input_name in required_input_names

                # A source="module" input may omit from_module when a default or
                # transform supplies the value (e.g. bnk-flo's flo_namespace /
                # far_secret_name / cluster_issuer_name). With no path there is
                # nothing to wire — skip so later layers / the module's own
                # .get(name, default) apply it. (find_dependency_by_path would
                # otherwise raise on a None path.)
                if not from_module_path:
                    continue

                dep_module = find_dependency_by_path(
                    db,
                    module.project_id,
                    from_module_path,
                    stack_instance_id=getattr(module, "stack_instance_id", None),
                )
                if dep_module and dep_module.outputs:
                    value = dep_module.outputs.get(from_output)
                    if value is not None:
                        variables[input_name] = value
                        logger.debug(f"Wired {input_name} = {value} from {from_module_path}.{from_output}")
                    else:
                        # BUG-009: Fail if required dependency output is missing
                        # But during destroy, skip — deps may already be torn down
                        if is_required and operation != "destroy":
                            raise ValueError(
                                f"Required dependency output not available: "
                                f"{from_module_path}.{from_output} -> {input_name}"
                            )
                        logger.warning(
                            f"{'[destroy-lenient] ' if operation == 'destroy' else ''}"
                            f"Optional output {from_output} not found in {from_module_path}"
                        )
                else:
                    # Exact-path lookup missed or dep has no outputs yet.
                    # Fallback: search among actual declared dependencies for one whose
                    # outputs contain from_output.  Only runs when the path didn't match
                    # any module at all — if the dep exists but has no outputs yet, we
                    # let the error path below raise instead of wiring from an unrelated module.
                    fallback_value = (
                        _resolve_from_dependency_outputs(db, module, from_output)
                        if dep_module is None else None
                    )
                    if fallback_value is not None:
                        if input_name not in variables:
                            variables[input_name] = fallback_value
                            logger.info(
                                f"Layer-3 fallback: wired {input_name} = {fallback_value!r} "
                                f"from actual dependency output '{from_output}' "
                                f"(hint '{from_module_path}' did not match)"
                            )
                        else:
                            logger.info(
                                f"Layer-3 fallback skipped for {input_name}: already resolved earlier"
                            )
                    else:
                        # BUG-009: Fail if required dependency module is missing
                        # But during destroy, skip — deps may already be torn down.
                        # Also skip if the variable was already resolved by an earlier
                        # layer (e.g. Layer 2.5 ProjectContext) — the dependency module
                        # may not exist in this deployment context (e.g. infra/aws/vpc
                        # doesn't exist in bare-metal, but DPU settings provide the value).
                        # Also skip if the input has a default in its metadata — use it.
                        inp_default = inp.get("default")
                        if input_name not in variables and inp_default is not None:
                            variables[input_name] = inp_default
                            logger.info(
                                f"Dependency module {from_module_path} not found; "
                                f"using metadata default for {input_name}: {inp_default!r}"
                            )
                        elif is_required and operation != "destroy" and input_name not in variables:
                            raise ValueError(
                                f"Required dependency not available: "
                                f"{from_module_path} (needed for input '{input_name}')"
                            )
                        else:
                            logger.warning(
                                f"{'[destroy-lenient] ' if operation == 'destroy' else ''}"
                                f"Optional dependency {from_module_path} not found or has no outputs"
                        )

    # Layer 4: Auto-wire common variables
    auto_wire_common_variables(db, module, variables)

    # Layer 4.5: Module-specific transforms (BM-020 blueprint service layer)
    # Reshape canonical variables into module-expected format. Still lower
    # precedence than user overrides (Layer 5), but DOES override template
    # defaults from Layer 2.6 — transforms encode platform constraints
    # (e.g. DPU mode forces namespace=f5-bnk) that must beat module defaults.
    _module_path = (lib_module.path if lib_module else None) or ""
    _transform_result = transform_for_module(_module_path, variables, _project_ctx)
    variables.update(_transform_result)

    # Layer 5: User-provided variables (module-level overrides)
    user_vars = module.variable_overrides or module.variables or {}
    if user_vars:
        user_vars = _resolve_inherit_sentinels(module, user_vars)
        variables.update(user_vars)
        logger.debug(f"Applied user overrides: {list(user_vars.keys())}")

    # Layer 5b: Auto-wire SSH credential variables for modules that explicitly
    # declare SSH inputs. This keeps future SSH/bootstrap blueprints from needing
    # hardcoded template-slug wiring.
    _inject_project_ssh_credential_variables(db, module, variables)

    # Layer 5c: Auto-inject kubeconfig_content for tofu modules that declare
    # it, from the project's registered cluster. Must run after Layer 5 user
    # overrides are merged (above) so a genuine user-supplied kubeconfig wins;
    # `_inject_cluster_kubeconfig` guards on `not variables.get("kubeconfig_content")`
    # and only fills in when the field is blank/unset.
    if first_cluster is not None:
        _inject_cluster_kubeconfig(variables, lib_module, first_cluster)

    # Layer 6: Project secrets
    if secret_variables:
        variables.update(secret_variables)
        logger.debug(f"Injected {len(secret_variables)} secret variables (overrides user vars)")

    # Layer 7: Variable mappings (rename/transform via VariableMapping records)
    variables = apply_variable_mappings(db, module, variables)

    logger.info(f"Built {len(variables)} variables for module {module.id}")
    return variables


# Backward-compatible alias for K8s task path
build_variables_for_k8s = build_variables


def _resolve_bmc_credentials(db: Session, host) -> tuple[str, str]:
    """Resolve BMC credentials via the established chain.

    Resolution order:
    1. Per-host encrypted bmc_username/bmc_password (BareMetalHost columns)
    2. Project DPU settings defaults (ProjectDpuSettings.default_oob_*)
    3. NVIDIA factory default: root / 0penBmc
    """
    NVIDIA_DEFAULT_USER = "root"
    NVIDIA_DEFAULT_PASS = "0penBmc"

    # 1. Per-host credentials
    bmc_user = None
    bmc_pass = None
    if host.bmc_username_encrypted:
        bmc_user = decrypt_value_or_none(host.bmc_username_encrypted)
    if host.bmc_password_encrypted:
        bmc_pass = decrypt_value_or_none(host.bmc_password_encrypted)

    if bmc_user and bmc_pass:
        return bmc_user, bmc_pass

    # 2. Project DPU settings defaults
    from models.dpu import ProjectDpuSettings

    settings = db.query(ProjectDpuSettings).filter(
        ProjectDpuSettings.project_id == host.project_id
    ).first()
    if settings:
        if not bmc_user and settings.default_oob_username:
            bmc_user = settings.default_oob_username
        if not bmc_pass and settings.default_oob_password_encrypted:
            bmc_pass = decrypt_value_or_none(settings.default_oob_password_encrypted)

    # 3. NVIDIA factory default
    return bmc_user or NVIDIA_DEFAULT_USER, bmc_pass or NVIDIA_DEFAULT_PASS


def build_variables_for_ssh(
    db: Session,
    module,  # ProjectModule
    host=None,  # BareMetalHost (optional, loaded if not provided)
    *,
    secret_variables: dict[str, str] | None = None,
    operation: str = "apply",
) -> dict[str, Any]:
    """
    Build variables for SSH engine modules with host-source injection.

    Extends the standard 7-layer build_variables() with an additional
    layer that injects BareMetalHost discovery columns for inputs
    declaring source="host".

    Host-sourced values (nic_mode, mst_device, host_ip, etc.) are
    injected at low precedence — user overrides take priority.

    Args:
        db: Database session
        module: ProjectModule to build variables for
        host: Optional BareMetalHost. If None, resolved from
              module.variable_overrides["bare_metal_host_id"].
        secret_variables: Pre-prepared secret values (from secrets_service),
              injected at Layer 6 — mirrors the K8s task path. SSH BNK-layer
              modules (bnk-prerequisites, bnk-flo) require cne_pull_secret /
              jwt_token; without this they fail input validation.
        operation: "apply", "init", or "destroy"
    """
    # Standard 7-layer assembly (incl. Layer 6 project secrets)
    variables = build_variables(db, module, secret_variables, operation=operation)

    # Resolve host if not provided
    if host is None:
        host_id = (module.variable_overrides or {}).get("bare_metal_host_id")
        if not host_id:
            host_id = (module.variables or {}).get("bare_metal_host_id")
        if host_id:
            from models.bare_metal import BareMetalHost
            host = db.query(BareMetalHost).filter(BareMetalHost.id == int(host_id)).first()

    if host is None:
        return variables

    # Host fields are split into two groups:
    #
    # Authoritative fields — identity/classification data from the host DB row that must
    # never be silently overridden by stack template defaults (e.g., variable_overrides
    # containing topology=regular for a host that was actually detected as dual_dpu_obmc).
    # These are written unconditionally (overwrite whatever earlier layers set).
    #
    # Low-precedence fields — hardware hints the user might legitimately override via the
    # template's variable_overrides. These use the existing guard (only inject when not
    # already present in variables).
    host_authoritative_fields = {
        "host_ip": host.host_ip,
        "topology": host.topology,
        "rshim_source": host.rshim_source,
        "deploy_dpu_pci_address": host.deploy_dpu_pci_address,
        "deploy_dpu_index": host.deploy_dpu_index,
        "bmc_ip": host.bmc_ip,
        "bare_metal_host_id": host.id,
    }
    for key, value in host_authoritative_fields.items():
        if value is not None:
            variables[key] = value

    # Low-precedence fields: don't overwrite existing values (user overrides take priority)
    host_fields = {
        "mst_device": host.mst_device,
        "nic_mode": host.nic_mode,
        "rshim_present": host.rshim_present,
        "default_route_iface": host.default_route_iface,
        "vf_count": host.vf_count,
        "bond_mode": host.bond_mode,
    }
    for key, value in host_fields.items():
        if value is not None and key not in variables:
            variables[key] = value

    # BMC credential resolution for dual_dpu_obmc
    # Chain: per-host bmc_* columns → project DPU settings defaults → NVIDIA factory default
    if host.bmc_ip and host.rshim_source == "bmc":
        bmc_user, bmc_password = _resolve_bmc_credentials(db, host)
        variables["bmc_user"] = bmc_user
        variables["bmc_password"] = bmc_password

    # Inject DPU credentials for modules that need to connect to the DPU directly
    # (e.g., wait-dpu-ready which polls until the DPU is SSH-reachable).
    # These are in-memory only — never logged, stored in DB, or returned to the UI.
    if host.dpu_credential and "dpu_username" not in variables:
        from services.ssh.paramiko_utils import decrypt_ssh_credential
        try:
            dpu_cred = decrypt_ssh_credential(host.dpu_credential)
            if dpu_cred.get("username"):
                variables["dpu_username"] = dpu_cred["username"]
            if dpu_cred.get("password"):
                variables["dpu_password"] = dpu_cred["password"]
            if dpu_cred.get("private_key_content"):
                variables["dpu_private_key_content"] = dpu_cred["private_key_content"]
        except Exception as exc:
            logger.warning("Failed to inject DPU credentials into variables: %s", exc)

    # Render bf.conf from the full Jinja2 template if:
    #   1. A Dpu record exists for this host (matched by project + IP)
    #   2. Project has DPU settings with a bf.conf template configured
    # The rendered content is injected as a variable so flash-dpu can write
    # it to the host instead of falling back to the minimal bf.cfg.
    # Gracefully falls back to nothing if any piece is missing — the Dpu
    # record is created by the DPU tab, not the blueprint flow, so it may
    # not exist yet.
    if "rendered_bf_conf" not in variables:
        try:
            _inject_rendered_bf_conf(db, host, module, variables)
        except Exception as exc:
            logger.warning("bf.conf rendering failed, falling back to minimal bf.cfg: %s", exc)

    return variables


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------

def can_execute(db: Session, module) -> tuple[bool, list[str]]:
    """
    Check if module can be executed (all dependencies satisfied).

    A module can execute when all its declared dependencies have status
    "applied" and have captured outputs.

    Args:
        db: Database session
        module: ProjectModule to check

    Returns:
        Tuple of (can_execute: bool, missing_deps: List[str])
    """
    from models import ProjectModule

    missing: list[str] = []

    if not module.dependencies:
        return True, []

    # Single query with eager loading to avoid N+1 problem
    from models import ModuleLibrary
    deps = (
        db.query(ProjectModule)
        .options(
            joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.name,
            )
        )
        .filter(ProjectModule.id.in_(module.dependencies))
        .all()
    )

    dep_map = {dep.id: dep for dep in deps}

    for dep_id in module.dependencies:
        dep = dep_map.get(dep_id)
        if not dep:
            missing.append(f"Module ID {dep_id} not found")
        elif dep.status != "applied":
            dep_name = dep.library_module.name if dep.library_module else f"ID:{dep_id}"
            missing.append(f"{dep_name} (status: {dep.status}, needs: applied)")
        elif dep.outputs is None:
            # S14-020: Check for None specifically, not falsy (empty dict {} is valid)
            dep_name = dep.library_module.name if dep.library_module else f"ID:{dep_id}"
            missing.append(f"{dep_name} (no outputs captured)")

    return len(missing) == 0, missing


# ---------------------------------------------------------------------------
# Dependency resolution
# ---------------------------------------------------------------------------

def find_dependency_by_path(
    db: Session,
    project_id: int,
    module_path: str,
    stack_instance_id: int | None = None,
):
    """
    Find a ProjectModule by its library module path.

    Handles both full paths (infra/aws/vpc) and shortened paths (aws/vpc).
    If exact match fails, tries suffix matching and normalized matching.

    When ``stack_instance_id`` is provided the lookup prefers the module from
    the **same stack** before falling back to the project-wide search.  This
    prevents the wrong module being returned in projects that have multiple
    stacks containing a module with the same path (e.g. two ``kubeadm-init``
    modules from stack-15 and stack-16 — only one of which may be applied).

    Args:
        db: Database session
        project_id: Project ID to search in
        module_path: Module path (e.g., "infra/aws/vpc" or "aws/vpc")
        stack_instance_id: Optional stack to prefer when searching.

    Returns:
        ProjectModule if found, None otherwise
    """
    from models import ModuleLibrary, ProjectModule

    # ── Stack-scoped exact match (preferred when caller knows its stack) ──
    if stack_instance_id is not None:
        result = (
            db.query(ProjectModule)
            .join(ProjectModule.library_module)
            .filter(
                ProjectModule.project_id == project_id,
                ProjectModule.stack_instance_id == stack_instance_id,
                ModuleLibrary.path == module_path,
            )
            .first()
        )
        if result:
            return result

    # ── Project-wide exact match (original behaviour) ─────────────────────
    result = (
        db.query(ProjectModule)
        .join(ProjectModule.library_module)
        .filter(
            ProjectModule.project_id == project_id,
            ModuleLibrary.path == module_path,
        )
        .first()
    )
    if result:
        return result

    # ── Suffix / normalized matching ──────────────────────────────────────
    all_modules = (
        db.query(ProjectModule)
        .join(ProjectModule.library_module)
        .options(
            joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.path,
            )
        )
        .filter(ProjectModule.project_id == project_id)
        .all()
    )

    # When stack-scoped, check same-stack candidates first then fall back to
    # the full project-wide list (deduplication is handled by order of
    # iteration — same-stack modules appear in both passes but we return on
    # first match so the second pass is a no-op for those entries).
    candidate_lists: list[list] = []
    if stack_instance_id is not None:
        same_stack = [m for m in all_modules if m.stack_instance_id == stack_instance_id]
        candidate_lists.append(same_stack)
    candidate_lists.append(all_modules)

    def _suffix_or_normalized_match(pm) -> bool:
        lib_path = pm.library_module.path if pm.library_module else ""
        if lib_path.endswith("/" + module_path) or module_path.endswith("/" + lib_path):
            return True
        normalized_module_path = module_path.replace("infra/", "")
        normalized_lib_path = lib_path.replace("infra/", "")
        return normalized_module_path == normalized_lib_path

    seen_ids: set[int] = set()
    for candidates in candidate_lists:
        for pm in candidates:
            if pm.id in seen_ids:
                continue
            seen_ids.add(pm.id)
            if _suffix_or_normalized_match(pm):
                match_type = "suffix" if (
                    (pm.library_module.path if pm.library_module else "").endswith("/" + module_path)
                    or module_path.endswith("/" + (pm.library_module.path if pm.library_module else ""))
                ) else "normalized path"
                logger.info(
                    f"Matched dependency by {match_type}: {module_path} -> "
                    f"{pm.library_module.path if pm.library_module else ''}"
                )
                return pm

    return None


def _resolve_from_dependency_outputs(db: Session, module, output_name: str) -> Any:
    """Layer-3 fallback: find ``output_name`` in the module's actual dependencies.

    Used when a ``module.json`` input specifies ``from_module`` with a path that
    doesn't match any project module exactly (e.g. the hint says
    ``modules/eks-cluster-register`` but the blueprint used
    ``modules/eks-cluster-create`` which exposes the same outputs).

    Resolution rules (in precedence order):
    1. Among declared dependency IDs (``module.dependencies``), prefer the one
       in the same stack (``stack_instance_id`` match), then any declared dep.
    2. If no declared dependency provides the output, fall back to applied
       modules that expose it. When ``module.stack_instance_id`` is set, this
       fallback is scoped to that stack to avoid cross-stack mis-wiring; when
       stack is absent it remains project-wide. In either scope, fallback is
       used only when there is exactly one candidate (ambiguous case logs a
       warning and returns None).

    Returns the output value, or None if unresolvable.
    """
    from models import ProjectModule

    dep_ids = list(module.dependencies or [])
    stack_id = getattr(module, "stack_instance_id", None)

    # ── Search declared dependencies first ───────────────────────────────────
    if dep_ids:
        deps = (
            db.query(ProjectModule)
            .filter(ProjectModule.id.in_(dep_ids))
            .all()
        )
        # Prefer same-stack dep, then any dep
        same_stack = [d for d in deps if d.stack_instance_id == stack_id and d.outputs]
        any_dep = [d for d in deps if d.outputs]
        for candidates in (same_stack, any_dep):
            for dep in candidates:
                value = dep.outputs.get(output_name)
                if value is not None:
                    dep_path = dep.library_module.path if dep.library_module else f"id:{dep.id}"
                    logger.debug(
                        f"_resolve_from_dependency_outputs: found '{output_name}' "
                        f"in declared dep {dep_path}"
                    )
                    return value

    # ── Fallback: project-wide applied modules ────────────────────────────────
    applied_query = db.query(ProjectModule).filter(
        ProjectModule.project_id == module.project_id,
        ProjectModule.status == "applied",
    )
    if stack_id is not None:
        applied_query = applied_query.filter(ProjectModule.stack_instance_id == stack_id)
    applied = applied_query.all()
    providers = [m for m in applied if m.outputs and m.outputs.get(output_name) is not None]
    if len(providers) == 1:
        value = providers[0].outputs[output_name]
        dep_path = providers[0].library_module.path if providers[0].library_module else f"id:{providers[0].id}"
        logger.info(
            f"_resolve_from_dependency_outputs: unambiguous project-wide match for "
            f"'{output_name}' from {dep_path}"
        )
        return value
    if len(providers) > 1:
        paths = [
            (m.library_module.path if m.library_module else f"id:{m.id}") for m in providers
        ]
        logger.warning(
            f"_resolve_from_dependency_outputs: ambiguous — multiple modules produce "
            f"'{output_name}': {paths}. Skipping fallback to avoid mis-wiring."
        )
    return None


# ---------------------------------------------------------------------------
# Auto-wiring
# ---------------------------------------------------------------------------

def auto_wire_common_variables(db: Session, module, variables: dict[str, Any]) -> None:
    """
    Auto-wire common variables from deployed modules in the project.

    Scans all applied modules and wires matching output names to unset variables.
    Declared dependencies take priority when output names conflict.

    Args:
        db: Database session
        module: ProjectModule being deployed
        variables: Variables dict to update (modified in place)
    """
    from models import ModuleLibrary, Project, ProjectModule

    # Get all applied modules in this project (potential sources of outputs)
    applied_modules = (
        db.query(ProjectModule)
        .options(
            joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.name, ModuleLibrary.path,
                ModuleLibrary.inputs_metadata,
            )
        )
        .filter(
            ProjectModule.project_id == module.project_id,
            ProjectModule.status == "applied",
            ProjectModule.id != module.id,
            ProjectModule.outputs.isnot(None),
        )
        .all()
    )

    # Get declared dependency IDs for deterministic preference
    declared_dep_ids = set(module.dependencies or [])

    # Build output candidates grouped by output name.
    # Selection priority (highest -> lowest):
    #   1. Same stack instance as current module
    #   2. Declared dependency modules
    #   3. Any other applied module in project
    available_outputs: dict[str, tuple] = {}  # output_name -> (value, source_path, is_dep)
    output_candidates: dict[str, list[tuple[Any, str, bool, bool, int]]] = {}
    current_stack_instance_id = getattr(module, "stack_instance_id", None)

    for pm in applied_modules:
        if not pm.outputs or not pm.library_module:
            continue

        is_dep = pm.id in declared_dep_ids
        is_same_stack = (
            current_stack_instance_id is not None
            and getattr(pm, "stack_instance_id", None) == current_stack_instance_id
        )

        for output_name, output_value in pm.outputs.items():
            output_candidates.setdefault(output_name, []).append(
                (output_value, pm.library_module.path, is_dep, is_same_stack, pm.id)
            )

    def _candidate_priority(candidate: tuple[Any, str, bool, bool, int]) -> tuple[int, int, int]:
        _, _, is_dep, is_same_stack, pm_id = candidate
        if is_same_stack:
            # Within same stack, still prefer declared dependencies when present
            return (0, 0 if is_dep else 1, pm_id)
        if is_dep:
            return (1, 0, pm_id)
        return (2, 0, pm_id)

    for output_name, candidates in output_candidates.items():
        if not candidates:
            continue

        ordered = sorted(candidates, key=_candidate_priority)
        selected = ordered[0]
        selected_value, selected_source, selected_is_dep, selected_is_same_stack, _selected_id = selected

        # Preserve existing conflict visibility with clearer source-priority semantics.
        if len(ordered) > 1:
            second = ordered[1]
            _second_value, second_source, second_is_dep, second_is_same_stack, _second_id = second
            selected_rank = _candidate_priority(selected)
            second_rank = _candidate_priority(second)
            if selected_rank < second_rank:
                selected_reason = (
                    "same-stack precedence"
                    if selected_is_same_stack
                    else "declared dependency precedence"
                )
                logger.info(
                    f"Auto-wire conflict for '{output_name}': preferring '{selected_source}' "
                    f"over '{second_source}' due to {selected_reason}"
                )
            elif selected_is_dep == second_is_dep and selected_is_same_stack == second_is_same_stack:
                logger.warning(
                    f"Auto-wire conflict for '{output_name}': both '{selected_source}' and "
                    f"'{second_source}' produce this output (using '{selected_source}'). "
                    f"Consider explicit variable mapping to resolve ambiguity."
                )

        available_outputs[output_name] = (selected_value, selected_source, selected_is_dep)

    # AUTO-WIRE: For any variable not yet set, check if a matching output exists.
    # Some output names are too generic and cause cross-contamination between
    # modules with different namespace/release requirements. Skip those.
    _AUTO_WIRE_SKIP = {"namespace", "release_name", "name"}
    for output_name, (output_value, source_path, _is_dep) in available_outputs.items():
        if output_name in _AUTO_WIRE_SKIP:
            continue
        if variables.get(output_name):
            continue
        variables[output_name] = output_value
        log_value = str(output_value)[:50] + '...' if isinstance(output_value, str) and len(str(output_value)) > 50 else output_value
        logger.info(f"Auto-wired {output_name}='{log_value}' from {source_path}")

    # Auto-wire project-level variables
    project = db.query(Project).filter(Project.id == module.project_id).first()
    if project:
        if not variables.get('project_name'):
            variables['project_name'] = project.name
            logger.info(f"Auto-wired project_name='{project.name}' from project")
        if not variables.get('project_name_aws_safe'):
            variables['project_name_aws_safe'] = slugify_aws_name(project.name)
        if not variables.get('project_name_s3_safe'):
            variables['project_name_s3_safe'] = slugify_s3_name(project.name)
        if not variables.get('project_name_gcp_safe'):
            variables['project_name_gcp_safe'] = slugify_gcp_name(project.name)
        # Only auto-wire AWS region for AWS projects
        if project.cloud_provider == "aws" and project.region:
            if not variables.get('region'):
                variables['region'] = project.region
                logger.info(f"Auto-wired region='{project.region}' from project")
            if not variables.get('aws_region'):
                variables['aws_region'] = project.region
                logger.info(f"Auto-wired aws_region='{project.region}' from project")


# ---------------------------------------------------------------------------
# Variable mappings & transformations
# ---------------------------------------------------------------------------

def _looks_like_jwt(value: str) -> bool:
    """
    Heuristic check for JWT tokens (three dot-separated base64url segments).

    JWTs must never be base64-encoded — they are already base64url internally
    and double-encoding breaks signature verification.
    """
    parts = value.split(".")
    if len(parts) != 3:
        return False
    # JWT headers typically start with eyJ (base64url for '{"')
    return parts[0].startswith("eyJ")


def apply_transformation(value: Any, transform_type: str) -> Any:
    """
    Apply transformation to a variable value.

    Args:
        value: Original value
        transform_type: Type of transformation (none, uppercase, lowercase, json_encode, base64)

    Returns:
        Transformed value
    """
    if transform_type == "none" or not transform_type:
        return value

    str_value = str(value) if not isinstance(value, str) else value

    if transform_type == "uppercase":
        return str_value.upper()
    elif transform_type == "lowercase":
        return str_value.lower()
    elif transform_type == "json_encode":
        return json.dumps(value)
    elif transform_type == "base64":
        # S23-001 guard: Don't base64-encode JWTs — they are already base64url
        # internally.  Double-encoding breaks signature verification.
        if _looks_like_jwt(str_value):
            logger.warning(
                "Skipping base64 transform: value looks like a JWT "
                "(3 dot-separated segments starting with 'eyJ'). "
                "Returning raw value to avoid double-encoding."
            )
            return str_value
        return base64.b64encode(str_value.encode()).decode()
    else:
        logger.warning(f"Unknown transform type: {transform_type}, returning original value")
        return value


def _get_raw_encoding_vars(module) -> set:
    """
    Return the set of variable names that have ``encoding='raw'``.

    These must never be transformed (e.g. JWT tokens that are already
    base64url-encoded internally).

    Checks module.json-based modules (``inputs_metadata`` with ``"encoding": "raw"``).
    """
    raw_vars: set = set()

    # module.json-based modules (catalog source of truth)
    lib_module = module.library_module
    if lib_module and lib_module.inputs_metadata:
        for inp_list in (lib_module.inputs_metadata.get("required", []),
                         lib_module.inputs_metadata.get("optional", [])):
            for inp in inp_list:
                if inp.get("encoding") == "raw":
                    raw_vars.add(inp.get("name", ""))

    return raw_vars


def apply_variable_mappings(db: Session, module, variables: dict[str, Any]) -> dict[str, Any]:
    """
    Apply variable mappings to rename/transform variables.

    Maps source variable names to target names with optional transformations.
    S15-035: Defers source deletion to avoid breaking multi-mapping scenarios.
    S23-001: Skips transforms for variables marked ``encoding='raw'``.

    Args:
        db: Database session
        module: ProjectModule with mappings
        variables: Current variables dict

    Returns:
        Transformed variables dict
    """
    from models import VariableMapping

    mappings = db.query(VariableMapping).filter(
        VariableMapping.project_module_id == module.id,
        VariableMapping.is_active,
    ).all()

    if not mappings:
        return variables

    # S23-001: Collect variables that must never be transformed
    raw_vars = _get_raw_encoding_vars(module)

    mapped_vars: dict[str, Any] = {}
    sources_to_delete: set = set()  # S15-035: Collect for deferred deletion
    applied_count = 0

    for mapping in mappings:
        source_name = mapping.source_variable_name
        target_name = mapping.target_variable_name
        transform = mapping.transform_type

        if source_name in variables:
            original_value = variables[source_name]

            # S23-001: Skip transform for encoding="raw" variables
            if target_name in raw_vars or source_name in raw_vars:
                if transform and transform != "none":
                    logger.warning(
                        f"Skipping '{transform}' transform on '{source_name}' → "
                        f"'{target_name}': variable is marked encoding='raw'"
                    )
                transformed_value = original_value
            else:
                transformed_value = apply_transformation(original_value, transform)

            mapped_vars[target_name] = transformed_value
            applied_count += 1

            logger.debug(
                f"Mapped variable: {source_name} → {target_name} "
                f"(transform: {transform}, value: {original_value} → {transformed_value})"
            )

            # S15-035: Collect sources to delete AFTER all mappings are processed
            if source_name != target_name:
                sources_to_delete.add(source_name)

    # Merge mapped variables
    variables.update(mapped_vars)

    # S15-035: Now remove source variables that were mapped to different targets
    for source_name in sources_to_delete:
        if source_name in variables and source_name not in mapped_vars:
            del variables[source_name]

    if applied_count > 0:
        logger.info(f"Applied {applied_count} variable mappings for module {module.id}")

    return variables


# ---------------------------------------------------------------------------
# TMOS / AS3 variable assembly and declaration rendering (D-023 Phase 2)
# ---------------------------------------------------------------------------


def build_variables_for_tmos(
    db: Session,
    module,  # ProjectModule
    device=None,  # F5BIGIPDevice (optional, resolved from variable_overrides if not provided)
    *,
    operation: str = "apply",
) -> dict[str, Any]:
    """Build variables for TMOS engine modules.

    Extends the standard 7-layer build_variables() with device-source injection
    (device hostname, port, AS3 tenant) at low precedence — user overrides win.

    Args:
        db: Database session
        module: ProjectModule
        device: Optional F5BIGIPDevice; if None, resolved from variable_overrides.
        operation: "apply", "init", or "destroy"
    """
    # Standard 7-layer assembly
    variables = build_variables(db, module, operation=operation)

    # Resolve device if not provided
    if device is None:
        device_id = (module.variable_overrides or {}).get("f5_bigip_device_id")
        if not device_id:
            device_id = (module.variables or {}).get("f5_bigip_device_id")
        if device_id:
            try:
                from models.f5_device import F5BIGIPDevice
                device = db.query(F5BIGIPDevice).filter(F5BIGIPDevice.id == int(device_id)).first()
            except Exception as exc:
                logger.warning("Could not resolve F5BIGIPDevice for module %s: %s", module.id, exc)

    if device is None:
        return variables

    # Inject device-sourced values at low precedence (don't overwrite user overrides)
    device_fields = {
        "tmos_host": device.mgmt_host,
        "tmos_port": device.mgmt_port or 443,
        "f5_bigip_device_id": device.id,
        "f5_bigip_device_name": device.name,
    }
    for key, value in device_fields.items():
        if value is not None and key not in variables:
            variables[key] = value

    return variables


def render_as3(template: str | dict, variables: dict[str, Any]) -> dict:
    """Render an AS3 declaration from a Mustache/Jinja2 template and variables.

    Supports two template forms:
      1. dict — already a structured declaration; performs variable substitution
         on string values using simple {{var}} replacement.
      2. str — Mustache/Jinja2 template string; renders to JSON then parses.

    Returns:
        Parsed AS3 declaration dict.

    NEVER logs the rendered output (may contain TLS private keys).

    Raises:
        ValueError on render failure.
    """
    import json as _json
    import re

    def _substitute(text: str, vars_: dict[str, Any]) -> str:
        """Simple {{variable}} substitution for Mustache-style templates."""
        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            value = vars_.get(key)
            if value is None:
                return match.group(0)  # Leave unresolved placeholders in place
            return str(value)
        return re.sub(r"\{\{([^}]+)\}\}", _replace, text)

    try:
        if isinstance(template, dict):
            # Already parsed — substitute string values
            rendered_str = _json.dumps(template)
            rendered_str = _substitute(rendered_str, variables)
            return _json.loads(rendered_str)

        if isinstance(template, str):
            # Try Jinja2 first (if available), fall back to simple substitution
            try:
                from jinja2 import Environment, Undefined

                env = Environment(undefined=Undefined)
                rendered_str = env.from_string(template).render(**variables)
            except ImportError:
                rendered_str = _substitute(template, variables)
            except Exception:
                # Jinja2 render failed — fall back to simple substitution
                rendered_str = _substitute(template, variables)

            try:
                return _json.loads(rendered_str)
            except _json.JSONDecodeError as exc:
                raise ValueError(
                    f"AS3 template rendered to invalid JSON: {exc}"
                ) from exc

        raise ValueError(f"render_as3: unsupported template type {type(template).__name__}")

    except (ValueError, TypeError):
        raise
    except Exception as exc:
        raise ValueError(f"AS3 template render failed: {exc}") from exc
