"""
OpenTofu Runtime — core execution logic for OpenTofu operations.

Moved from services/execution_engine.py (P0 Phase 3).

Handles:
- Workspace preparation (cloning, config generation)
- OpenTofu subprocess execution (init, plan, apply, destroy, refresh)
- Output capture
- Destroy error analysis and retry logic

Delegated to other modules in the execution/ package:
- Variable assembly → variable_assembler.py
- Config file generation → config_writer.py
- Dependency checking → variable_assembler.can_execute()
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from sqlalchemy.orm import Session

from models import ModuleSource, ProjectModule
from services.defaults_service import check_required_configured
from services.git_auth_service import GitAuthService
from services.infrastructure_access_service import normalize_infrastructure_access_outputs

logger = logging.getLogger(__name__)


def _add_provider_lock_timeout_hint(output: str) -> str:
    """Annotate known provider install stalls with a concrete runtime hint."""
    normalized_output = (output or "").lower()
    has_provider_cache_context = ".terraform/providers" in normalized_output
    has_lock_signal = "lock" in normalized_output and (
        "waiting" in normalized_output or "cache directory" in normalized_output
    )
    if not (has_provider_cache_context and has_lock_signal):
        return output

    hint = (
        "\nDetected OpenTofu provider cache lock contention inside the module workspace.\n"
        "This usually means another tofu init/install process is holding .terraform/providers,\n"
        "or a stale provider lock file from a timed-out install was left behind.\n"
    )
    return output + hint


def _resolve_git_ref(inline_ref: str | None, module_source: "ModuleSource | None") -> str:
    """Resolve the git ref to clone.

    #321: precedence is inline ``?ref=`` > ``module_source.git_ref`` >
    ``module_source.branch`` > ``"main"``. The inline query string (when present)
    always wins; otherwise the ModuleSource's configured ref/branch is honoured.
    """
    if inline_ref:
        return inline_ref
    if module_source is not None:
        configured = getattr(module_source, "git_ref", None) or getattr(module_source, "branch", None)
        if configured:
            return configured
    return "main"


# Matches a terraform `source = "./x"` or `source = "../x"` assignment, capturing
# the relative path. Handles both double- and single-quoted strings.
_RELATIVE_SOURCE_RE = re.compile(
    r'\bsource\s*=\s*["\'](\.\.?/[^"\']*)["\']'
)

# Sentinel dir under work_dir where co-fetched siblings are materialised. Keeping
# co-fetched siblings under work_dir makes them execution-isolated and auto-cleaned
# with the workspace (no out-of-tree /tmp or /app/workspaces leak/clobber).
_FORGE_SIBLINGS_DIR = ".forge_siblings"


def _is_within(parent: str, child: str) -> bool:
    """Return True when ``child`` is ``parent`` or lives inside it."""
    parent_abs = os.path.realpath(parent)
    child_abs = os.path.realpath(child)
    try:
        return os.path.commonpath([parent_abs, child_abs]) == parent_abs
    except ValueError:  # pragma: no cover - different drives (Windows)
        return False


def _relpath_posix(target: str, start_dir: str) -> str:
    """Relative path from ``start_dir`` to ``target`` as a terraform local source.

    Always forward-slashed and prefixed with ``./`` when it does not already start
    with ``.`` so it is recognised by terraform as a local (not registry) source.
    """
    rel = os.path.relpath(target, start_dir).replace(os.sep, "/")
    # Terraform recognises a LOCAL source only when it starts with "./" or "../".
    # A path like ".forge_siblings/bar" must be prefixed so it isn't read as a
    # registry/module-namespace source.
    if not (rel.startswith("./") or rel.startswith("../")):
        rel = f"./{rel}"
    return rel


def _rewrite_relative_source(module_dir: str, old_rel: str, new_rel: str) -> None:
    """Rewrite ``source = "<old_rel>"`` → ``source = "<new_rel>"`` in ``module_dir``.

    Only rewrites exact-match local relative source strings in ``.tf`` files directly
    in ``module_dir`` (not recursive — each module dir is rewritten when scanned),
    preserving both quote styles and surrounding formatting. Registry / ``git::`` /
    other paths are untouched because the caller only passes resolved local refs.
    """
    if not os.path.isdir(module_dir):
        return
    # Match the exact relative path inside either quote style, capturing the quote.
    pattern = re.compile(
        r'(\bsource\s*=\s*)(["\'])' + re.escape(old_rel) + r'(["\'])'
    )

    def _sub(m: re.Match) -> str:
        # Preserve the opening quote char; close with the same kind it opened with.
        return f"{m.group(1)}{m.group(2)}{new_rel}{m.group(2)}"

    for name in os.listdir(module_dir):
        if not name.endswith(".tf"):
            continue
        path = os.path.join(module_dir, name)
        try:
            with open(path) as f:
                content = f.read()
        except OSError:
            continue
        new_content = pattern.sub(_sub, content)
        if new_content != content:
            try:
                with open(path, "w") as f:
                    f.write(new_content)
            except OSError as exc:  # pragma: no cover - defensive
                logger.warning("Failed to rewrite source in %s: %s", path, exc)


def _scan_relative_module_sources(module_dir: str) -> set[str]:
    """Return the set of relative ``source = "./x"`` / ``"../x"`` references in a dir.

    #322: scans every ``.tf`` file directly in ``module_dir`` (not recursive — each
    submodule is scanned separately when copied) for module source references that
    point at a sibling/parent directory in the same repo.
    """
    refs: set[str] = set()
    if not os.path.isdir(module_dir):
        return refs
    for name in os.listdir(module_dir):
        if not name.endswith(".tf"):
            continue
        try:
            with open(os.path.join(module_dir, name)) as f:
                content = f.read()
        except OSError:
            continue
        for match in _RELATIVE_SOURCE_RE.finditer(content):
            refs.add(match.group(1))
    return refs


def _inject_forge_kubeconfig_into_sibling(sibling_dir: str, project) -> None:
    """Inject ``locals { forge_kubeconfig }`` into a co-fetched sibling module dir.

    #323: the top-level injection (config_writer._inject_forge_kubeconfig_locals)
    only runs for the module workspace. Sibling/child modules copied in by #322 that
    reference ``local.forge_kubeconfig`` / ``var.forge_kubeconfig_content`` would not
    receive the local. This mirrors the top-level injection into each copied sibling,
    reusing the same helper (which self-gates on whether the module actually
    references forge_kubeconfig and keeps cloud-provider branching intact via the
    passed-in project's cloud_provider).
    """
    try:
        from services.execution.config_writer import _inject_forge_kubeconfig_locals
        _inject_forge_kubeconfig_locals(sibling_dir, None, project)
    except Exception as exc:  # pragma: no cover - defensive; injection is best-effort
        logger.warning("forge_kubeconfig injection into sibling %s failed: %s", sibling_dir, exc)


def _looks_like_private_key_payload(value: str) -> bool:
    """Return True when string appears to be inline SSH private key material."""
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    if "BEGIN OPENSSH PRIVATE KEY" in candidate:
        return True
    if "BEGIN RSA PRIVATE KEY" in candidate:
        return True
    if "BEGIN PRIVATE KEY" in candidate:
        return True
    return False


def _build_tofu_env(env: dict | None) -> dict:
    """Return subprocess env with optional shared provider cache wiring."""
    tofu_env = dict(env or {})
    plugin_cache_dir = os.getenv("TF_PLUGIN_CACHE_DIR")
    if plugin_cache_dir:
        tofu_env.setdefault("TF_PLUGIN_CACHE_DIR", plugin_cache_dir)
    return tofu_env


def _materialize_inline_ssh_key_file(work_dir: str, private_key_value: str) -> str:
    """Write inline SSH key payload to secure workspace file and return path."""
    secrets_dir = os.path.join(work_dir, "secrets")
    os.makedirs(secrets_dir, exist_ok=True)
    key_path = os.path.join(secrets_dir, "infra_ssh_private_key.pem")
    Path(key_path).write_text(private_key_value, encoding="utf-8")
    os.chmod(key_path, 0o600)
    return key_path


def _apply_known_module_hotfixes(work_dir: str, module_path: str | None) -> None:
    """Apply bounded runtime hotfixes for known module-contract drift."""
    normalized_module_path = (module_path or "").strip("/")
    if normalized_module_path.endswith("k8s/bnk-prerequisites"):
        download_manifest_script = os.path.join(work_dir, "scripts", "download-manifest.sh")
        if os.path.exists(download_manifest_script):
            script_content = Path(download_manifest_script).read_text(encoding="utf-8")
            updated_script_content = script_content

            # Fix 1: avoid SIGPIPE/141 from `tar | head` under `set -o pipefail`.
            updated_script_content = updated_script_content.replace(
                'MANIFEST_DIR=$(tar -tf "$MANIFEST_TAR" | head -n1 | cut -d\'/\' -f1)',
                'FIRST_TAR_ENTRY=""\nIFS= read -r FIRST_TAR_ENTRY < <(tar -tf "$MANIFEST_TAR")\nMANIFEST_DIR="${FIRST_TAR_ENTRY%%/*}"',
            )

            # Fix 2: prefer real manifest YAML (not Chart.yaml path-matched via dir name).
            updated_script_content = updated_script_content.replace(
                '# Dynamically find the manifest YAML file\nMANIFEST_FILE=$(find "$MANIFEST_DIR" -name "*.yaml" -type f | grep -E "(manifest|bigip)" | head -n1)\nif [ -z "$MANIFEST_FILE" ]; then\n    # Fallback: try any YAML file in the directory\n    MANIFEST_FILE=$(find "$MANIFEST_DIR" -name "*.yaml" -type f | head -n1)\nfi',
                '# Dynamically find the manifest YAML file\n# Prefer the versioned BNK manifest and avoid selecting Chart.yaml.\nfor candidate in "$MANIFEST_DIR"/*k8s-manifest*.yaml "$MANIFEST_DIR"/*manifest*.yaml "$MANIFEST_DIR"/*bigip*.yaml "$MANIFEST_DIR"/*.yaml; do\n    [ -f "$candidate" ] || continue\n    MANIFEST_FILE="$candidate"\n    case "$(basename "$MANIFEST_FILE")" in\n        Chart.yaml)\n            MANIFEST_FILE=""\n            continue\n            ;;\n    esac\n    break\ndone',
            )

            # Fix 3: resolve SERVICE_ACCOUNT_KEY_FILE to absolute path BEFORE cd.
            # The script receives a relative path like "./work/cne_pull_secret.json"
            # then does `cd "$WORK_DIR"` (./work).  After the cd, the relative path
            # resolves to ./work/work/cne_pull_secret.json which doesn't exist.
            # Insert path resolution right after the eval that reads terraform inputs.
            _sa_resolve_line = (
                '\n# Resolve SERVICE_ACCOUNT_KEY_FILE to absolute path before cd\n'
                'if [ -n "${SERVICE_ACCOUNT_KEY_FILE:-}" ] && [ -f "$SERVICE_ACCOUNT_KEY_FILE" ]; then\n'
                '    SERVICE_ACCOUNT_KEY_FILE="$(cd "$(dirname "$SERVICE_ACCOUNT_KEY_FILE")" && pwd)/$(basename "$SERVICE_ACCOUNT_KEY_FILE")"\n'
                'fi\n'
            )
            _eval_line = (
                'eval "$(jq -r \'@sh "MANIFEST_VERSION=\\(.manifest_version) '
                'WORK_DIR=\\(.work_dir) CHART_NAME=\\(.chart_name) '
                'SERVICE_ACCOUNT_KEY_FILE=\\(.service_account_key_file // empty)"\')"'
            )
            if _eval_line in updated_script_content and _sa_resolve_line not in updated_script_content:
                updated_script_content = updated_script_content.replace(
                    _eval_line,
                    _eval_line + _sa_resolve_line,
                )

            if updated_script_content != script_content:
                Path(download_manifest_script).write_text(updated_script_content, encoding="utf-8")
                logger.info(
                    "Applied hotfixes for k8s/bnk-prerequisites download-manifest.sh in %s",
                    download_manifest_script,
                )

    if not normalized_module_path.endswith("infra/ubuntu/kind"):
        return

    # Hotfix 1: remove deprecated hashicorp/terraform provider constraint that can
    # fail with plugin API mismatch on target runtimes.
    versions_tf = os.path.join(work_dir, "versions.tf")
    if os.path.exists(versions_tf):
        content = Path(versions_tf).read_text(encoding="utf-8")
        original_content = content
        content = re.sub(
            r'(?ms)^\s*terraform\s*=\s*\{[^{}]*?source\s*=\s*"hashicorp/terraform"[^{}]*?\}\s*,?\s*$\n?',
            "",
            content,
        )
        if content != original_content:
            # Keep file tidy after block removal
            content = re.sub(r"\n{3,}", "\n\n", content)
            Path(versions_tf).write_text(content, encoding="utf-8")
            logger.info("Applied hotfix: removed hashicorp/terraform provider from %s", versions_tf)

    # Hotfix 1b: ensure OpenSSH client is available in runtime image because the
    # module's local-exec step shells out to scp to retrieve kubeconfig.
    # If absent, replace scp command with equivalent ssh+cat redirection.
    has_scp = shutil.which("scp") is not None
    if not has_scp:
        main_tf_for_scp = os.path.join(work_dir, "main.tf")
        if os.path.exists(main_tf_for_scp):
            content = Path(main_tf_for_scp).read_text(encoding="utf-8")
            original_content = content
            ssh_cat_snippet = (
                "ssh -i '${var.ssh_private_key_path}' -o StrictHostKeyChecking=no "
                "-o UserKnownHostsFile=/dev/null '${var.ssh_user}@${var.ssh_host}' "
                "'cat ${local.remote_kubeconfig_path}' > '${local.local_kubeconfig_path}'"
            )
            # Support module variants by replacing the local-exec scp command
            # payload with an ssh+cat equivalent when scp is unavailable.
            content = re.sub(
                r"command\s*=\s*\"scp -i '\$\{var\.ssh_private_key_path\}'.*?\$\{local\.local_kubeconfig_path\}'\"",
                f'command = "{ssh_cat_snippet}"',
                content,
                flags=re.DOTALL,
            )
            if content != original_content:
                Path(main_tf_for_scp).write_text(content, encoding="utf-8")
                logger.info("Applied hotfix: replaced scp with ssh+cat in %s", main_tf_for_scp)

    # Hotfix 2: convert terraform_data validate helper to local_file guard so
    # plans do not require deprecated provider schema discovery.
    main_tf = os.path.join(work_dir, "main.tf")
    if os.path.exists(main_tf):
        content = Path(main_tf).read_text(encoding="utf-8")
        original_content = content

        # Hotfix 2b: some infra/ubuntu/kind variants upload kind-config.yaml to
        # ${local.remote_workspace_dir} before that directory is guaranteed to
        # exist, which can make file provisioner upload non-deterministically
        # fail. Use an always-existing /tmp path for transfer + cluster create.
        remote_kind_config_expr = "${local.remote_workspace_dir}/kind-config.yaml"
        tmp_kind_config_expr = "/tmp/bnk-forge-kind-config-${var.cluster_name}.yaml"
        content = content.replace(
            f'destination = "{remote_kind_config_expr}"',
            f'destination = "{tmp_kind_config_expr}"',
        )
        content = content.replace(
            f"--config '{remote_kind_config_expr}'",
            f"--config '{tmp_kind_config_expr}'",
        )

        if 'resource "terraform_data" "validate_remote_inputs"' in content:
            content = content.replace(
                'resource "terraform_data" "validate_remote_inputs" {',
                'resource "local_file" "remote_input_guard" {\n  filename = "${local.local_artifact_dir}/.remote-input-guard"\n  content  = "remote-inputs-validated"',
            )
            content = content.replace(
                'depends_on = [terraform_data.validate_remote_inputs]',
                'depends_on = [local_file.remote_input_guard]',
            )
        # ubuntu /bin/sh (dash) does not support pipefail; keep scripts POSIX-safe.
        content = content.replace('set -euo pipefail', 'set -eu')

        # Hotfix 3: make remote workspace/kubeconfig dir creation idempotent even
        # when stale files exist at those paths from prior failed runs.
        mkdir_line = '"$${SUDO} mkdir -p ${local.remote_workspace_dir} ${local.remote_kubeconfig_dir}",'
        if mkdir_line in content:
            content = content.replace(
                mkdir_line,
                '"if [ -f ${local.remote_workspace_dir} ]; then $${SUDO} rm -f ${local.remote_workspace_dir}; fi",\n'
                '      "if [ -f ${local.remote_kubeconfig_dir} ]; then $${SUDO} rm -f ${local.remote_kubeconfig_dir}; fi",\n'
                '      "$${SUDO} mkdir -p ${local.remote_workspace_dir} ${local.remote_kubeconfig_dir}",',
            )

        # Hotfix 4: preflight docker iptables chain before kind network creation.
        # Attempt bounded self-healing on hosts where Docker networking is
        # misconfigured (for example nft/legacy mismatch leaving DOCKER-FORWARD missing).
        kind_create_pattern = re.compile(
            r'^\s*"KUBECONFIG=\'.*?kind create cluster.*?--kubeconfig \'\$\{local\.remote_kubeconfig_path\}\'",\s*$',
            flags=re.MULTILINE,
        )

        old_preflight_line_escaped = (
            '"if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then '
            'echo \\\"DOCKER-FORWARD chain missing. Docker host networking is not ready for kind.\\\"; '
            'exit 1; fi",'
        )

        old_preflight_line_broken = (
            '"if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then '
            'echo "DOCKER-FORWARD chain missing. Docker host networking is not ready for kind."; '
            'exit 1; fi",'
        )

        remediation_preflight_line = (
            '"if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then '
            'echo \'DOCKER-FORWARD chain missing; attempting docker/iptables remediation...\'; '
            'if command -v update-alternatives >/dev/null 2>&1; then '
            'if update-alternatives --list iptables >/dev/null 2>&1 | grep -q iptables-legacy; then '
            '$${SUDO} update-alternatives --set iptables /usr/sbin/iptables-legacy >/dev/null 2>&1 || true; fi; '
            'if update-alternatives --list ip6tables >/dev/null 2>&1 | grep -q ip6tables-legacy; then '
            '$${SUDO} update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy >/dev/null 2>&1 || true; fi; '
            'fi; '
            '$${SUDO} systemctl restart docker >/dev/null 2>&1 || true; '
            'sleep 3; '
            'if ! $${SUDO} iptables -S DOCKER-FORWARD >/dev/null 2>&1; then '
            'echo \'DOCKER-FORWARD chain missing after remediation. Docker host networking is not ready for kind.\'; '
            'exit 1; fi; fi",'
        )
        remediation_preflight_line_indented = f"      {remediation_preflight_line}"

        if old_preflight_line_escaped in content:
            content = content.replace(old_preflight_line_escaped, remediation_preflight_line)
        if old_preflight_line_broken in content:
            content = content.replace(old_preflight_line_broken, remediation_preflight_line)
        elif 'DOCKER-FORWARD chain missing after remediation' not in content:
            content, _ = kind_create_pattern.subn(
                lambda m: f"{remediation_preflight_line_indented}\n{m.group(0)}",
                content,
                count=1,
            )
        if content != original_content:
            Path(main_tf).write_text(content, encoding="utf-8")
            logger.info("Applied hotfix: converted terraform_data guard in %s", main_tf)


def _reconcile_known_existing_k8s_resources(work_dir: str, module_path: str | None, env: dict) -> int:
    """Best-effort import for known rerun-idempotency resources.

    Existing-cluster reruns can hit AlreadyExists for namespaced primitives when
    the cluster already contains objects but module state does not. Pre-separation
    converge paths tolerated this via server-side apply semantics; in post-
    separation OpenTofu execution, we reconcile state by importing known resources
    before planning/apply.
    """
    normalized_module_path = (module_path or "").strip("/")
    module_import_targets: dict[str, list[tuple[str, str]]] = {
        "k8s/bnk-prerequisites": [
            ("kubernetes_namespace_v1.operator", "f5-operator"),
            ("kubernetes_namespace_v1.utils", "f5-utils"),
            ("kubernetes_namespace_v1.gateway", "bnk-gw"),
            ("kubernetes_secret_v1.far_secret_operator", "f5-operator/far-secret"),
            ("kubernetes_secret_v1.far_secret_utils", "f5-utils/far-secret"),
            ("kubernetes_secret_v1.far_secret_gateway", "bnk-gw/far-secret"),
        ],
        "k8s/network-setup": [
            ("kubernetes_manifest.external_nad", ""),
            ("kubernetes_manifest.internal_nad", ""),
        ],
        "k8s/cert-manager": [
            ("kubernetes_namespace_v1.cert_manager", ""),
        ],
    }

    import_targets: list[tuple[str, str]] = []
    for candidate_path, candidate_targets in module_import_targets.items():
        if normalized_module_path.endswith(candidate_path):
            import_targets = candidate_targets
            break

    if not import_targets:
        return 0

    def _requires_init(output: str) -> bool:
        normalized = (output or "").lower()
        return (
            "run \"tofu init\"" in normalized
            or "initialization required" in normalized
            or "backend initialization required" in normalized
            or "providers are not installed" in normalized
            or "no valid credential sources found" in normalized
            or "failed to refresh cached credentials" in normalized
            or "provider requires reinitialization" in normalized
        )

    def _read_workspace_file(name: str) -> str:
        path = os.path.join(work_dir, name)
        if not os.path.exists(path):
            return ""
        try:
            return Path(path).read_text(encoding="utf-8")
        except Exception:
            return ""

    def _manifest_import_id(namespace: str, name: str) -> str:
        return f"apiVersion=k8s.cni.cncf.io/v1,kind=NetworkAttachmentDefinition,namespace={namespace},name={name}"

    def _discover_import_targets() -> list[tuple[str, str]]:
        if normalized_module_path.endswith("k8s/bnk-prerequisites"):
            # Base targets are always present
            targets = list(import_targets)
            # DPU mode: instance_namespace (e.g. f5-bnk) gets its own ns + far-secret
            tfvars = _read_workspace_file("terraform.tfvars.json")
            if tfvars:
                try:
                    instance_ns = json.loads(tfvars).get("instance_namespace", "")
                    if instance_ns and instance_ns != "f5-operator":
                        targets.append(("kubernetes_namespace_v1.instance[0]", instance_ns))
                        targets.append(("kubernetes_secret_v1.far_secret_instance[0]", f"{instance_ns}/far-secret"))
                except Exception:
                    pass
            return targets

        if normalized_module_path.endswith("k8s/network-setup"):
            tfvars = _read_workspace_file("terraform.tfvars.json")
            namespace = "f5-operator"
            if tfvars:
                try:
                    namespace = json.loads(tfvars).get("namespace") or namespace
                except Exception:
                    pass
            return [
                ("kubernetes_manifest.external_nad", _manifest_import_id(namespace, "external-netdevice")),
                ("kubernetes_manifest.internal_nad", _manifest_import_id(namespace, "internal-netdevice")),
            ]

        if normalized_module_path.endswith("k8s/cert-manager"):
            main_tf = _read_workspace_file("main.tf")
            addresses: list[str] = []
            if 'resource "kubernetes_namespace_v1" "cert_manager"' in main_tf:
                if "count = var.create_namespace ? 1 : 0" in main_tf:
                    addresses.append("kubernetes_namespace_v1.cert_manager[0]")
                else:
                    addresses.append("kubernetes_namespace_v1.cert_manager")
            return [(address, "cert-manager") for address in addresses]

        return import_targets

    import_targets = _discover_import_targets()

    logger.info(
        "Reconcile: module_path=%s, discovered %d import targets: %s",
        normalized_module_path,
        len(import_targets),
        [(addr, imp_id[:80]) for addr, imp_id in import_targets],
    )

    if not import_targets:
        return 0

    def _run_init_for_reconcile() -> bool:
        try:
            init_result = subprocess.run(
                ["tofu", "init", "-no-color", "-input=false"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if init_result.returncode == 0:
                return True

            combined = f"{init_result.stdout or ''}\n{init_result.stderr or ''}".lower()
            return "no valid credential sources found" in combined or "failed to refresh cached credentials" in combined
        except Exception as exc:  # pragma: no cover - best effort safety
            logger.warning("Reconcile pre-init failed for BNK prereq workspace: %s", exc)
            return False

    managed_resources: set[str] = set()
    init_attempted = False
    tofu_env = _build_tofu_env(env)
    try:
        state_result = subprocess.run(
            ["tofu", "state", "list"],
            cwd=work_dir,
            env=tofu_env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if state_result.returncode != 0 and _requires_init(f"{state_result.stdout or ''}\n{state_result.stderr or ''}"):
            init_attempted = True
            if _run_init_for_reconcile():
                state_result = subprocess.run(
                    ["tofu", "state", "list"],
                    cwd=work_dir,
                    env=tofu_env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

        if state_result.returncode == 0:
            managed_resources = {
                line.strip()
                for line in (state_result.stdout or "").splitlines()
                if line.strip()
            }

        logger.info(
            "Reconcile: state list found %d managed resources, init_attempted=%s",
            len(managed_resources),
            init_attempted,
        )
    except Exception as exc:  # pragma: no cover - best effort safety
        logger.warning("State list check failed during BNK prereq reconcile: %s", exc)

    imported_count = 0

    for address, import_id in import_targets:
        if address in managed_resources:
            logger.info("Reconcile: %s already in state, skipping import", address)
            continue

        try:
            import_result = subprocess.run(
                ["tofu", "import", "-no-color", "-input=false", address, import_id],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Timed out importing %s (%s) for prereq reconcile", address, import_id)
            continue
        except Exception as exc:  # pragma: no cover - best effort safety
            logger.warning("Import command failed for %s (%s): %s", address, import_id, exc)
            continue

        if import_result.returncode != 0 and (not init_attempted):
            combined_output = f"{import_result.stdout or ''}\n{import_result.stderr or ''}"
            if _requires_init(combined_output):
                init_attempted = True
                if _run_init_for_reconcile():
                    import_result = subprocess.run(
                        ["tofu", "import", "-no-color", "-input=false", address, import_id],
                        cwd=work_dir,
                        env=tofu_env,
                        capture_output=True,
                        text=True,
                        timeout=45,
                    )

        if import_result.returncode == 0:
            logger.info("Imported existing BNK prereq resource into state: %s (%s)", address, import_id)
            imported_count += 1
            continue

        combined_output = f"{import_result.stdout or ''}\n{import_result.stderr or ''}".lower()
        if (
            "cannot import non-existent remote object" in combined_output
            or "not found" in combined_output
            or "does not exist" in combined_output
        ):
            # Object missing is fine — apply will create it.
            logger.info("Reconcile: %s does not exist on cluster, will be created by apply", address)
            continue

        logger.warning(
            "Unexpected import failure for %s (%s), proceeding with normal apply: %s",
            address,
            import_id,
            (import_result.stderr or import_result.stdout or "").strip()[:500],
        )

    return imported_count


class SystemDefaultsNotConfiguredError(Exception):
    """Raised when required system defaults are not configured."""
    def __init__(self, missing_settings: list):
        self.missing_settings = missing_settings
        missing_names = [s["description"] for s in missing_settings]
        message = (
            f"System defaults not configured. Please configure the following in System > Defaults: "
            f"{', '.join(missing_names)}"
        )
        super().__init__(message)


class OpenTofuRuntime:
    """
    OpenTofu runtime — subprocess management for tofu init/plan/apply/destroy.

    v2 architecture: Direct OpenTofu execution with runtime variable injection.
    Variables are stored in database and injected at runtime.
    """

    # Destroy timeout configuration (in seconds)
    # Different module types need different timeouts for cleanup
    DESTROY_TIMEOUTS = {
        "vpc": 45 * 60,      # 45 minutes - ENI cleanup can be slow
        "eks": 60 * 60,      # 60 minutes - node group + ENI cleanup
        "security": 60 * 60,  # 60 minutes - SG/ENI detach propagation can be slow
        "rds": 45 * 60,      # 45 minutes - snapshot creation
        "default": 30 * 60,  # 30 minutes - standard modules
    }

    # Destroy retry policy tuned for transient AWS dependency-release windows.
    DESTROY_RETRY_MAX = 8
    DESTROY_RETRY_INITIAL_DELAY_SECONDS = 5.0

    def __init__(self, db: Session):
        self.db = db
        self._validate_system_defaults()

    def _validate_system_defaults(self) -> None:
        """
        Validate that all required system defaults are configured.
        Raises SystemDefaultsNotConfiguredError if any are missing.
        """
        status = check_required_configured(self.db)
        if not status["all_configured"]:
            raise SystemDefaultsNotConfiguredError(status["missing"])

    def get_destroy_timeout(self, module: ProjectModule) -> int:
        """
        Get appropriate timeout for destroy operation based on module type.

        Args:
            module: ProjectModule being destroyed

        Returns:
            Timeout in seconds
        """
        if not module.library_module:
            return self.DESTROY_TIMEOUTS["default"]

        module_name = module.library_module.name.lower()

        # Check for exact match
        if module_name in self.DESTROY_TIMEOUTS:
            return self.DESTROY_TIMEOUTS[module_name]

        # Check for partial matches
        for key, timeout in self.DESTROY_TIMEOUTS.items():
            if key in module_name:
                return timeout

        return self.DESTROY_TIMEOUTS["default"]

    def parse_destroy_error(self, output: str, module: ProjectModule) -> str:
        """
        Parse destroy error output and provide actionable guidance.

        Args:
            output: Raw destroy command output
            module: Module being destroyed

        Returns:
            Enhanced error message with troubleshooting steps
        """
        error_guidance = []

        # Check for specific error patterns and provide guidance
        if re.search(r"DependencyViolation.*network interface", output, re.IGNORECASE):
            error_guidance.append(
                "\n🔍 ENI (Elastic Network Interface) Dependency Detected:\n"
                "- ENIs created by EKS/EC2 may persist after resource deletion\n"
                "- Manual cleanup: AWS Console → EC2 → Network Interfaces\n"
                "- Filter by VPC ID and delete unattached ENIs\n"
            )

        if re.search(r"DependencyViolation.*security group", output, re.IGNORECASE):
            error_guidance.append(
                "\n🔍 Security Group Dependency Detected:\n"
                "- Security groups may reference each other\n"
                "- Manual cleanup: AWS Console → EC2 → Security Groups\n"
                "- Remove all inbound/outbound rules first, then delete\n"
            )

        if re.search(r"DependencyViolation.*internet gateway", output, re.IGNORECASE):
            error_guidance.append(
                "\n🔍 Internet Gateway Dependency Detected:\n"
                "- IGW must be detached before VPC deletion\n"
                "- Manual cleanup: AWS Console → VPC → Internet Gateways\n"
                "- Detach from VPC, then delete\n"
            )

        if re.search(r"subnet.*still has dependencies", output, re.IGNORECASE):
            error_guidance.append(
                "\n🔍 Subnet Dependency Detected:\n"
                "- Subnets may have ENIs or other resources attached\n"
                "- Manual cleanup: Check for Lambda functions, RDS instances, or load balancers\n"
                "- Delete attached resources first\n"
            )

        if re.search(r"elastic.*ip.*still associated", output, re.IGNORECASE):
            error_guidance.append(
                "\n🔍 Elastic IP Dependency Detected:\n"
                "- EIPs must be disassociated before deletion\n"
                "- Manual cleanup: AWS Console → EC2 → Elastic IPs\n"
                "- Release unassociated EIPs\n"
            )

        # Module-specific guidance
        if module.library_module:
            module_name = module.library_module.name.lower()

            if "vpc" in module_name:
                tips = (
                    "\n💡 VPC Destroy Tips:\n"
                    "1. Ensure all child resources (EKS, RDS, EC2) are destroyed first\n"
                    "2. Check for orphaned ENIs in VPC\n"
                    "3. Verify NAT Gateways are deleted\n"
                )
                if module.project.cloud_provider == "aws":
                    tips += f"4. AWS Console: https://console.aws.amazon.com/vpc/home?region={module.project.region}#vpcs:\n"
                error_guidance.append(tips)

            if "eks" in module_name:
                tips = (
                    "\n💡 EKS Destroy Tips:\n"
                    "1. Ensure all workloads (Pods, Services, LoadBalancers) are deleted\n"
                    "2. Delete node groups manually if destroy fails\n"
                    "3. Check for orphaned ENIs created by pods\n"
                )
                if module.project.cloud_provider == "aws":
                    tips += f"4. AWS Console: https://console.aws.amazon.com/eks/home?region={module.project.region}#/clusters\n"
                error_guidance.append(tips)

        # Add summary if we found specific errors
        if error_guidance:
            summary = (
                "\n" + "="*60 + "\n"
                "DESTROY ERROR ANALYSIS & TROUBLESHOOTING\n"
                "="*60
            )
            return summary + "".join(error_guidance) + "\n" + "="*60 + "\n"

        return ""

    def can_execute(self, module: ProjectModule) -> tuple[bool, list[str]]:
        """Check if module can be executed — delegates to variable_assembler."""
        from services.execution.variable_assembler import can_execute
        return can_execute(self.db, module)

    def reconcile_known_existing_resources(self, work_dir: str, module: ProjectModule, env: dict) -> int:
        """Best-effort import for known existing resources before apply/plan.

        Returns number of successful imports.
        """
        module_path = module.library_module.path if module and module.library_module else None
        return _reconcile_known_existing_k8s_resources(work_dir, module_path, env)

    def _prepare_project_secrets(self, project_id: int, work_dir: str, module_path: str | None = None) -> dict[str, str]:
        """
        Prepare project secrets for module execution.

        Writes file secrets to workspace and returns value mappings.
        Only includes secrets that target the specified module (or have no target).

        Args:
            project_id: Project ID
            work_dir: Workspace directory
            module_path: Optional module path to filter secrets (e.g., "bnk/far-setup")

        Returns:
            Dict of variable_name -> secret_value (for injection into tfvars)
        """
        from services.secrets_service import SecretsService

        secrets_service = SecretsService(self.db)
        value_secrets, file_paths = secrets_service.prepare_secrets_for_execution(
            project_id, work_dir, module_path
        )

        logger.info(f"Prepared {len(value_secrets)} secret variables and {len(file_paths)} secret files for {module_path}")
        return value_secrets

    def build_variables(self, module: ProjectModule, secret_variables: dict | None = None, *, operation: str = "apply") -> dict:
        """Build complete variable dict — delegates to variable_assembler."""
        from services.execution.variable_assembler import build_variables
        return build_variables(self.db, module, secret_variables, operation=operation)

    def _strip_global_tfvar_secrets(self, variables: dict, lib_module) -> dict:
        """Remove jwt_token from variables if the module does not declare it.

        jwt_token (BNK LICENSE) is a globally-broadcast secret injected into
        tfvars.json for every module.  Modules that don't declare
        ``variable "jwt_token" {}`` produce a noisy "Value for undeclared
        variable" warning from tofu.  Strip it here so only modules that
        explicitly declare the variable receive it in tfvars.

        For modules that DO declare jwt_token (e.g. bnk/flo), the token stays
        in tfvars unchanged — env-injection is additive (see
        OpenTofuEngine._get_global_tfvar_env).

        # Future: if a second globally-broadcast secret appears, consider a
        # registry — see followup_jwt_token_undeclared_variable_warning.md
        """
        if "jwt_token" not in variables:
            return variables
        declared = {
            v.get("name")
            for v in (getattr(lib_module, "variables_schema", None) or [])
            if v.get("name")
        }
        if "jwt_token" in declared:
            return variables
        result = dict(variables)
        result.pop("jwt_token")
        logger.debug("Stripped jwt_token from tfvars (module does not declare it)")
        return result

    def prepare_workspace(self, module: ProjectModule, keep_workspace: bool = False) -> str:
        """
        Create temp workspace with module source and configuration.

        Args:
            module: ProjectModule to prepare workspace for
            keep_workspace: If True, don't auto-delete workspace (for debugging)

        Returns:
            Path to workspace directory
        """
        lib_module = module.library_module
        if not lib_module:
            raise ValueError(f"Module {module.id} has no library module")

        # Create temp directory
        work_dir = tempfile.mkdtemp(prefix="tofu-")
        logger.info(f"Created workspace: {work_dir}")

        try:
            # Clone module source (pass ModuleSource for auth token)
            self._clone_module_source(
                lib_module.git_source, work_dir, lib_module.source, project=module.project
            )

            _apply_known_module_hotfixes(work_dir, lib_module.path if lib_module else None)

            # Prepare project secrets (writes files, returns value mappings)
            # Pass module path to filter secrets to only those targeting this module
            module_path = lib_module.path if lib_module else None
            secret_variables = self._prepare_project_secrets(module.project_id, work_dir, module_path)

            # Build and write variables (includes secret_variables)
            variables = self.build_variables(module, secret_variables)

            # Strip jwt_token from tfvars for modules that don't declare it.
            # TF_VAR_jwt_token env-injection (OpenTofuEngine) covers the runtime value.
            variables = self._strip_global_tfvar_secrets(variables, lib_module)

            # Support modules that expect ssh_private_key_path while execution surfaces
            # an inline key secret (for example when project secret target variable is
            # set to ssh_private_key_path). Materialize to secure workspace file.
            ssh_key_path_value = variables.get("ssh_private_key_path")
            if _looks_like_private_key_payload(ssh_key_path_value):
                materialized_key_path = _materialize_inline_ssh_key_file(work_dir, ssh_key_path_value)
                variables["ssh_private_key_path"] = materialized_key_path
                logger.info(
                    "Materialized inline ssh_private_key_path secret to workspace file for module %s",
                    module.id,
                )

            # Filter variables to only include those declared by the module.
            # Auto-wiring injects ALL outputs from ALL deployed modules, but OpenTofu
            # rejects variables that aren't declared in variables.tf. Filter here to
            # only pass variables the module actually expects.
            # Secrets are always exempt — they were already filtered to this module
            # by _prepare_project_secrets and should never be stripped.
            if lib_module and lib_module.variables_schema:
                declared_vars = {v.get('name') for v in lib_module.variables_schema if v.get('name')}
                secret_keys = set(secret_variables.keys()) if secret_variables else set()
                if declared_vars:
                    allowed = declared_vars | secret_keys
                    filtered = {k: v for k, v in variables.items() if k in allowed}
                    removed = len(variables) - len(filtered)
                    if removed > 0:
                        logger.info(f"Filtered {removed} undeclared variables (module declares {len(declared_vars)} vars)")
                    variables = filtered

            module_overrides = dict(module.variable_overrides or {})
            persisted = False
            for key in ("project_name_aws_safe", "project_name_s3_safe"):
                if key in variables and module_overrides.get(key) != variables[key]:
                    module_overrides[key] = variables[key]
                    persisted = True
            if persisted:
                module.variable_overrides = module_overrides
                self.db.add(module)
                self.db.flush()

            self.write_tfvars(work_dir, variables)

            # Write backend configuration
            self.write_backend_config(work_dir, module)

            # Write encryption configuration (OpenTofu 1.8+)
            self.write_encryption_config(work_dir, module)

            # Write provider configuration for K8s/Helm modules (community module support)
            # Pass assembled variables so provider config can use cluster_name etc.
            self.write_provider_config(work_dir, module, variables)

            return work_dir

        except Exception:
            # Cleanup on failure (unless debugging)
            if not keep_workspace:
                shutil.rmtree(work_dir, ignore_errors=True)
            raise

    def prepare_persistent_workspace(self, module: ProjectModule, *, operation: str = "apply") -> str:
        """
        Prepare a persistent workspace for a module.

        Unlike prepare_workspace(), this:
        - Uses a persistent path (/app/workspaces/{project_id}/{module_id}/)
        - Only clones source if workspace doesn't exist or needs update
        - Preserves .terraform/ directory across operations
        - Always regenerates tfvars and config files

        Args:
            module: ProjectModule to prepare workspace for
            operation: "apply", "destroy", etc. — passed to build_variables for leniency

        Returns:
            Path to workspace directory
        """
        from services.workspace_manager import WorkspaceManager

        lib_module = module.library_module
        if not lib_module:
            raise ValueError(f"Module {module.id} has no library module")

        workspace = WorkspaceManager(self.db)
        work_dir = workspace.ensure_workspace(module)

        try:
            tf_files = [f for f in os.listdir(work_dir) if f.endswith(".tf")]
            source_exists = bool(tf_files)
            should_refresh_source = (lib_module.module_source_kind == "git_catalog") or (not source_exists)

            if should_refresh_source:
                # Deterministic source refresh for catalog modules and empty workspaces.
                # Keep expensive provider cache/state artifacts; only clear source files
                # and downloaded child module cache to force a clean source snapshot.
                for tf_file in tf_files:
                    os.remove(os.path.join(work_dir, tf_file))
                    logger.debug("Removed workspace source file before refresh: %s", tf_file)

                terraform_modules_dir = os.path.join(work_dir, ".terraform", "modules")
                if os.path.exists(terraform_modules_dir):
                    shutil.rmtree(terraform_modules_dir)
                    logger.debug("Cleared .terraform/modules cache before source refresh")

                logger.info("Refreshing module source in persistent workspace: %s", work_dir)
                git_source = lib_module.git_source or ""
                if git_source.startswith("builtin://"):
                    # Builtin modules: source lives in the shared blueprint repo.
                    # The subpath after builtin:// maps to a directory in the repo.
                    builtin_subpath = git_source[len("builtin://"):]
                    blueprint_repo = os.path.join(
                        workspace.get_blueprint_workspace_path(
                            module.project_id, module.stack_instance_id
                        ),
                        "repo",
                    )
                    if not os.path.isdir(blueprint_repo):
                        # Fall back: use the module catalog for builtin modules
                        # (builtin:// is a marker, not a real git URL — git clone would fail)
                        catalog_path = os.environ.get("MODULE_CATALOG_PATH", "/tmp/bnk-forge-modules")
                        blueprint_repo = catalog_path
                    src_path = os.path.join(blueprint_repo, builtin_subpath)
                    if os.path.isdir(src_path):
                        for item in os.listdir(src_path):
                            s = os.path.join(src_path, item)
                            d = os.path.join(work_dir, item)
                            if os.path.isdir(s):
                                if os.path.exists(d):
                                    shutil.rmtree(d)
                                shutil.copytree(s, d)
                            else:
                                shutil.copy2(s, d)
                        logger.info(
                            "Copied builtin module source from %s to workspace %s",
                            src_path,
                            work_dir,
                        )
                    else:
                        raise ValueError(
                            f"Builtin module source not found: {src_path}. "
                            f"Ensure the module library is synced."
                        )
                elif workspace.is_blueprint_eligible(module):
                    blueprint_repo = workspace.ensure_blueprint_repo(
                        module.project_id,
                        module.stack_instance_id,
                        lib_module.git_source,
                        ref="",
                        module_source=lib_module.source,
                    )
                    self._copy_source_from_shared_repo(lib_module.git_source, blueprint_repo, work_dir)
                else:
                    self._clone_module_source(
                        lib_module.git_source, work_dir, lib_module.source, project=module.project
                    )
            else:
                logger.info("Using existing non-catalog source in workspace: %s", work_dir)

            _apply_known_module_hotfixes(work_dir, lib_module.path if lib_module else None)

            # Always regenerate tfvars and config files (they may have changed)

            # Prepare project secrets (writes files, returns value mappings)
            module_path = lib_module.path if lib_module else None
            secret_variables = self._prepare_project_secrets(module.project_id, work_dir, module_path)

            # Build and write variables (includes secret_variables)
            variables = self.build_variables(module, secret_variables, operation=operation)

            # Strip jwt_token from tfvars for modules that don't declare it.
            # TF_VAR_jwt_token env-injection (OpenTofuEngine) covers the runtime value.
            variables = self._strip_global_tfvar_secrets(variables, lib_module)

            # Support modules that expect ssh_private_key_path while execution surfaces
            # an inline key secret (for example when project secret target variable is
            # set to ssh_private_key_path). Materialize to secure workspace file.
            ssh_key_path_value = variables.get("ssh_private_key_path")
            if _looks_like_private_key_payload(ssh_key_path_value):
                materialized_key_path = _materialize_inline_ssh_key_file(work_dir, ssh_key_path_value)
                variables["ssh_private_key_path"] = materialized_key_path
                logger.info(
                    "Materialized inline ssh_private_key_path secret to workspace file for module %s",
                    module.id,
                )

            # AWS naming safety: patch cloned module sources to use S3-safe/AWS-safe
            # derived names where raw project_name would break AWS resource naming
            # rules (e.g., S3 bucket names must be lowercase).  Also ensure the
            # safe-name variables are declared and passed through filtering.
            if lib_module and getattr(module.project, "cloud_provider", None) == "aws":
                from utils.naming import slugify_aws_name, slugify_s3_name
                s3_safe = variables.get("project_name_s3_safe") or slugify_s3_name(module.project.name)
                aws_safe = variables.get("project_name_aws_safe") or slugify_aws_name(module.project.name)
                variables["project_name_s3_safe"] = s3_safe
                variables["project_name_aws_safe"] = aws_safe

                for tf_file in [f for f in os.listdir(work_dir) if f.endswith('.tf')]:
                    tf_path = os.path.join(work_dir, tf_file)
                    try:
                        with open(tf_path) as f:
                            content = f.read()
                        patched = content

                        # Replace S3 bucket naming patterns that use raw project_name
                        # with the S3-safe variant to avoid uppercase/invalid chars
                        if '${var.project_name}-dpdk-scripts' in patched:
                            patched = patched.replace(
                                '${var.project_name}-dpdk-scripts',
                                '${var.project_name_s3_safe}-dpdk-scripts',
                            )
                        if '${var.project_name}-terraform-state' in patched:
                            patched = patched.replace(
                                '${var.project_name}-terraform-state',
                                '${var.project_name_s3_safe}-terraform-state',
                            )

                        if patched != content:
                            with open(tf_path, 'w') as f:
                                f.write(patched)
                            logger.info("Patched %s for S3-safe naming", tf_file)
                    except Exception:
                        continue

                # Ensure project_name_s3_safe variable is declared in the workspace
                vars_tf_path = os.path.join(work_dir, "variables.tf")
                if os.path.exists(vars_tf_path):
                    with open(vars_tf_path) as f:
                        vars_content = f.read()
                    declarations_added = []
                    if 'variable "project_name_s3_safe"' not in vars_content:
                        vars_content += '\nvariable "project_name_s3_safe" {\n  description = "S3-safe lowercase project name"\n  type        = string\n}\n'
                        declarations_added.append("project_name_s3_safe")
                    if 'variable "project_name_aws_safe"' not in vars_content:
                        vars_content += '\nvariable "project_name_aws_safe" {\n  description = "AWS-safe project name"\n  type        = string\n}\n'
                        declarations_added.append("project_name_aws_safe")
                    if declarations_added:
                        with open(vars_tf_path, 'w') as f:
                            f.write(vars_content)
                        logger.info("Added variable declarations to variables.tf: %s", declarations_added)

            # Filter variables to only include those declared by the module.
            # Secrets are always exempt — they were already filtered to this module
            # by _prepare_project_secrets and should never be stripped.
            if lib_module and lib_module.variables_schema:
                declared_vars = {v.get('name') for v in lib_module.variables_schema if v.get('name')}
                secret_keys = set(secret_variables.keys()) if secret_variables else set()
                if lib_module and getattr(module.project, "cloud_provider", None) == "aws":
                    for special in ('project_name_s3_safe', 'project_name_aws_safe'):
                        if special in variables:
                            declared_vars.add(special)
                if declared_vars:
                    allowed = declared_vars | secret_keys
                    filtered = {k: v for k, v in variables.items() if k in allowed}
                    removed = len(variables) - len(filtered)
                    if removed > 0:
                        logger.info(f"Filtered {removed} undeclared variables (module declares {len(declared_vars)} vars)")
                    variables = filtered

            self.write_tfvars(work_dir, variables)

            # Write backend configuration
            self.write_backend_config(work_dir, module)

            # Write encryption configuration (OpenTofu 1.8+)
            self.write_encryption_config(work_dir, module)

            # Write provider configuration for K8s/Helm modules
            self.write_provider_config(work_dir, module, variables)

            return work_dir

        except Exception as e:
            # Don't cleanup on failure - persistent workspace should persist
            logger.error(f"Failed to prepare persistent workspace: {e}")
            raise

    def _clone_module_source(
        self, git_source: str, work_dir: str, module_source: ModuleSource = None, project=None
    ):
        """
        Clone module source to workspace.

        Args:
            git_source: Git URL with optional path (e.g., "git::https://github.com/org/repo.git//path?ref=main")
            work_dir: Target directory
            module_source: Optional ModuleSource with auth credentials
            project: Optional Project — drives cloud-provider branching for #323 sibling
                forge_kubeconfig injection.
        """
        # Parse git source URL
        # Format: git::https://github.com/org/repo.git//path/to/module?ref=branch
        source = git_source

        # Remove git:: prefix if present
        if source.startswith("git::"):
            source = source[5:]

        # Extract ref if present.
        # #321: precedence is inline `?ref=` > module_source.git_ref/branch > "main".
        inline_ref = None
        if "?ref=" in source:
            source, inline_ref = source.rsplit("?ref=", 1)
        ref = _resolve_git_ref(inline_ref, module_source)

        # Extract subpath if present (look for // that's NOT part of https://)
        subpath = ""
        # Split on :// first to separate protocol from rest
        if "://" in source:
            protocol_part, rest = source.split("://", 1)
            # Now check if there's a // in the rest (indicating subpath)
            if "//" in rest:
                repo_part, subpath = rest.split("//", 1)
                source = f"{protocol_part}://{repo_part}"
            else:
                source = f"{protocol_part}://{rest}"
        elif "//" in source:
            # No protocol, just split on //
            source, subpath = source.split("//", 1)

        source = GitAuthService.strip_url_credentials(source)

        if module_source is not None:
            auth_ctx = GitAuthService.resolve_for_module_source(module_source, db=self.db)
        else:
            auth_ctx = GitAuthService.resolve_for_module_library_token_setting(self.db)

        logger.info(f"Cloning {source} (ref={ref} [resolved], subpath={subpath})")

        # Clone to temp location first
        clone_temp = tempfile.mkdtemp(prefix="tofu-clone-")
        env, cleanup_env = GitAuthService.build_git_environment(auth_ctx)
        try:
            # S15-032/S15-033: Use try/except to capture git stderr, add timeout
            try:
                subprocess.run(
                    ["git", "clone", "--depth=1", "--branch", ref, "--", source, clone_temp],
                    env=env,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=300  # S15-033: 5 minute timeout for git clone
                )
            except subprocess.CalledProcessError as e:
                # S15-032: Include git-specific stderr in error message
                stderr = GitAuthService.sanitize_error_text(e.stderr or "", secrets=[auth_ctx.secret])
                error_kind, guidance = GitAuthService.classify_git_failure(stderr)
                raise RuntimeError(
                    f"Git clone failed [{error_kind}] {guidance} (exit code {e.returncode}): {stderr.strip()}"
                ) from e
            except subprocess.TimeoutExpired:
                raise RuntimeError(
                    f"Git clone timed out after 300s for {source} (ref={ref})"
                )

            # Copy subpath to work_dir (or entire repo if no subpath)
            src_path = os.path.join(clone_temp, subpath) if subpath else clone_temp

            # S15-034: Validate subpath exists before attempting to copy
            if subpath and not os.path.exists(src_path):
                raise ValueError(
                    f"Module subpath '{subpath}' does not exist in repository. "
                    f"Available paths: {os.listdir(clone_temp)}"
                )

            # Copy contents to work_dir
            for item in os.listdir(src_path):
                src = os.path.join(src_path, item)
                dst = os.path.join(work_dir, item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)  # Remove existing before copytree
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            # #322: co-fetch sibling modules referenced via relative `source = "../..."`.
            # The isolated workspace only copies `subpath`; relative parent references
            # to peer dirs in the same repo would otherwise be missing at `tofu init`.
            if subpath:
                self._cofetch_relative_siblings(
                    clone_root=clone_temp,
                    subpath=subpath,
                    work_dir=work_dir,
                    project=project,
                )

            logger.info(f"Cloned module to {work_dir}")

        finally:
            cleanup_env()
            shutil.rmtree(clone_temp, ignore_errors=True)

    def _cofetch_relative_siblings(
        self, clone_root: str, subpath: str, work_dir: str, project=None
    ) -> None:
        """Co-fetch sibling modules referenced via relative ``source = "../..."``.

        #322: An isolated per-module workspace only copies ``subpath`` out of the clone.
        If the module's terraform references a peer directory via a relative source
        (e.g. ``module "x" { source = "../sibling" }``), that peer is not present and
        ``tofu init`` fails.

        Isolation/cleanup (reliability fix): each referenced sibling is copied
        **inside** ``work_dir`` under a dedicated sentinel directory
        (``work_dir/.forge_siblings/<name>``) and the relative ``source`` references in
        the referencing ``.tf`` files are rewritten to point at the in-tree copy
        (e.g. ``"../sibling"`` → ``"./.forge_siblings/sibling"``). Because everything
        lives under ``work_dir``:

          - Co-fetched siblings are **execution-isolated** — two modules in the same
            project (or two concurrent runs) that both reference ``../bar`` get their
            OWN copy inside their OWN ``work_dir``; no shared out-of-tree path is ever
            written, so there is no cross-module/cross-execution clobber.
          - They are **auto-cleaned** with ``work_dir`` on both success and failure;
            no out-of-tree ``/tmp/bar`` or ``/app/workspaces/{project}/bar`` leak.

        #323: each copied sibling that references ``local.forge_kubeconfig`` /
        ``var.forge_kubeconfig_content`` also receives the kubeconfig locals injection,
        mirroring the top-level behaviour in config_writer.

        Safety/bounds:
          - Only LOCAL relative references (``./`` or ``../``) are followed and
            rewritten; registry, ``git::`` and already-in-tree sources are ignored.
          - Resolved targets MUST stay within ``clone_root`` (path-traversal guard) —
            a reference that escapes the repo root is skipped (not rewritten).
          - Resolution is bounded: discovered siblings are themselves scanned so that
            a chain of relative references is satisfied, but each clone directory is
            handled at most once (``visited`` set), so the walk terminates.
        """
        clone_root_abs = os.path.realpath(clone_root)
        siblings_root = os.path.join(work_dir, _FORGE_SIBLINGS_DIR)
        # Clear any stale sentinel dir from a prior run on the same (persistent)
        # work_dir so co-fetched siblings are never reused across executions/refreshes
        # — each run gets a fresh, correctly-versioned snapshot.
        if os.path.exists(siblings_root):
            shutil.rmtree(siblings_root, ignore_errors=True)
        # The clone dir the module was copied FROM. References that resolve INSIDE
        # this tree (e.g. `./child`) were already copied wholesale into work_dir, so
        # they need neither a co-fetch nor a rewrite.
        subpath_clone_abs = os.path.realpath(os.path.join(clone_root, subpath))

        # Maps the realpath of a sibling dir inside the clone → the sentinel name it
        # was copied to under work_dir/.forge_siblings/. Ensures every distinct clone
        # dir gets a single, stable in-tree home (deterministic, collision-free).
        copied: dict[str, str] = {}
        used_names: set[str] = set()

        def _sentinel_name_for(target_clone_abs: str) -> str:
            """Pick a unique sentinel dir name for a co-fetched sibling.

            Prefers the directory's own basename; on collision (two different clone
            dirs share a basename) disambiguates with a short hash of the clone-relative
            path so distinct siblings never share an in-tree home.
            """
            existing = copied.get(target_clone_abs)
            if existing is not None:
                return existing
            base = os.path.basename(target_clone_abs.rstrip("/")) or "sibling"
            name = base
            if name in used_names:
                rel = os.path.relpath(target_clone_abs, clone_root_abs)
                digest = hashlib.sha1(rel.encode("utf-8")).hexdigest()[:8]
                name = f"{base}-{digest}"
            used_names.add(name)
            copied[target_clone_abs] = name
            return name

        visited: set[str] = set()

        def _scan(module_clone_dir: str, module_dir: str) -> None:
            """Scan ``module_dir`` (under work_dir) for relative sources.

            ``module_clone_dir`` is the matching directory inside the clone (used to
            resolve relative references); ``module_dir`` is the absolute on-disk
            location under ``work_dir`` whose ``.tf`` files are rewritten in place.
            """
            module_clone_abs = os.path.realpath(module_clone_dir)
            if module_clone_abs in visited:
                return
            visited.add(module_clone_abs)
            if not os.path.isdir(module_clone_dir):
                return

            for rel_source in _scan_relative_module_sources(module_clone_dir):
                target_clone = os.path.realpath(os.path.join(module_clone_dir, rel_source))
                # Path-traversal guard: never copy outside the clone root.
                if os.path.commonpath([clone_root_abs, target_clone]) != clone_root_abs:
                    logger.warning(
                        "Skipping sibling reference '%s' — resolves outside clone root", rel_source
                    )
                    continue
                # Explicit guard: never copy the clone root itself (e.g. source = "../.."
                # from a deeply nested module that resolves to the repo root would
                # otherwise shutil.copytree the entire working tree into .forge_siblings).
                if target_clone == clone_root_abs:
                    logger.warning(
                        "Skipping sibling reference '%s' — resolves to clone root", rel_source
                    )
                    continue
                # Also skip when the target is an ancestor of the current subpath
                # (e.g. source = ".." from clone/modules/foo resolves to clone/modules/,
                # which contains every sibling and would be copied wholesale).
                if _is_within(target_clone, subpath_clone_abs):
                    logger.warning(
                        "Skipping sibling reference '%s' — target is an ancestor of the module subpath",
                        rel_source,
                    )
                    continue
                if not os.path.isdir(target_clone):
                    logger.warning(
                        "Skipping sibling reference '%s' — not a directory in clone", rel_source
                    )
                    continue
                # Skip references that resolve inside the originally-copied subpath
                # tree (e.g. `./child`) — those are already present under work_dir and
                # their original relative reference still resolves there.
                if _is_within(subpath_clone_abs, target_clone):
                    continue

                already_copied = target_clone in copied
                sentinel = _sentinel_name_for(target_clone)
                dst_dir = os.path.join(siblings_root, sentinel)
                if not already_copied:
                    os.makedirs(siblings_root, exist_ok=True)
                    shutil.copytree(target_clone, dst_dir)
                    _inject_forge_kubeconfig_into_sibling(dst_dir, project)

                # Rewrite the referencing .tf files so the relative source points at
                # the in-tree copy, computed relative to module_dir's location.
                new_rel = _relpath_posix(dst_dir, module_dir)
                _rewrite_relative_source(module_dir, rel_source, new_rel)

                # Recurse so chained relative references inside the sibling are
                # satisfied too, rewriting the sibling's own .tf files in place.
                if not already_copied:
                    _scan(target_clone, dst_dir)

        # Seed: the module itself sits at work_dir, copied from clone/subpath.
        module_clone_dir = os.path.join(clone_root, subpath)
        _scan(module_clone_dir, work_dir)

    def _copy_source_from_shared_repo(self, git_source: str, repo_path: str, work_dir: str) -> None:
        """Copy module source subpath from shared blueprint repo into workspace."""
        from services.workspace_manager import WorkspaceManager

        _, _, subpath = WorkspaceManager._parse_git_source(git_source)

        src_path = os.path.join(repo_path, subpath) if subpath else repo_path
        if subpath and not os.path.exists(src_path):
            raise ValueError(
                f"Module subpath '{subpath}' does not exist in shared blueprint repository. "
                f"Available paths: {os.listdir(repo_path)}"
            )

        for item in os.listdir(src_path):
            src = os.path.join(src_path, item)
            dst = os.path.join(work_dir, item)
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)

        logger.info("Copied module source from shared blueprint repo: %s", repo_path)

    def write_tfvars(self, work_dir: str, variables: dict) -> str:
        """Generate terraform.tfvars.json — delegates to config_writer."""
        from services.execution.config_writer import write_tfvars
        return write_tfvars(work_dir, variables)

    def write_backend_config(self, work_dir: str, module: ProjectModule):
        """Generate backend configuration — delegates to config_writer."""
        from services.execution.config_writer import write_backend_config
        write_backend_config(work_dir, module, db=self.db)

    def write_encryption_config(self, work_dir: str, module: ProjectModule):
        """Generate state encryption configuration — delegates to config_writer."""
        from services.execution.config_writer import write_encryption_config
        write_encryption_config(work_dir, module, db=self.db)

    def write_provider_config(self, work_dir: str, module: ProjectModule, variables: dict = None):
        """Generate provider configuration — delegates to config_writer."""
        from services.execution.config_writer import write_provider_config
        write_provider_config(work_dir, module, variables, db=self.db)

    # S14-021: Subprocess timeout constants (in seconds)
    INIT_TIMEOUT = 15 * 60      # 15 minutes - downloading providers
    PLAN_TIMEOUT = 30 * 60      # 30 minutes - complex infrastructure plans
    APPLY_TIMEOUT = 90 * 60     # 90 minutes - long-running resource creation
    REFRESH_TIMEOUT = 30 * 60   # 30 minutes - state refresh

    def run_init(self, work_dir: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
        """
        Run tofu init.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Optional timeout in seconds (default: INIT_TIMEOUT)

        Returns:
            Tuple of (exit_code, output)
        """
        if timeout is None:
            timeout = self.INIT_TIMEOUT
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu init in {work_dir} (timeout: {timeout}s)")

        try:
            result = subprocess.run(
                ["tofu", "init", "-no-color", "-input=false"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = _add_provider_lock_timeout_hint(result.stdout + result.stderr)
            logger.info(f"tofu init completed with exit code {result.returncode}")

            return result.returncode, output

        except subprocess.TimeoutExpired as e:
            # S14-021: Handle timeout for init
            timeout_msg = (
                f"\nInit operation timed out after {timeout} seconds.\n"
                f"This may indicate network issues downloading providers.\n"
            )
            logger.error(f"Init timeout after {timeout}s")
            stdout = e.stdout
            stderr = e.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            output = _add_provider_lock_timeout_hint((stdout or "") + (stderr or "") + timeout_msg)
            return 1, output

    def run_plan(self, work_dir: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
        """
        Run tofu plan.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Optional timeout in seconds (default: PLAN_TIMEOUT)

        Returns:
            Tuple of (exit_code, output)
        """
        if timeout is None:
            timeout = self.PLAN_TIMEOUT
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu plan in {work_dir} (timeout: {timeout}s)")

        try:
            result = subprocess.run(
                ["tofu", "plan", "-no-color", "-input=false", "-out=plan.out"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr
            logger.info(f"tofu plan completed with exit code {result.returncode}")

            return result.returncode, output

        except subprocess.TimeoutExpired as e:
            # S14-021: Handle timeout for plan
            timeout_msg = (
                f"\nPlan operation timed out after {timeout} seconds.\n"
                f"This may indicate complex infrastructure or API rate limiting.\n"
            )
            logger.error(f"Plan timeout after {timeout}s")
            stdout = e.stdout
            stderr = e.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            output = (stdout or "") + (stderr or "") + timeout_msg
            return 1, output

    def run_plan_detailed(self, work_dir: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
        """
        Run tofu plan with detailed exit code for drift detection.

        Exit codes:
        - 0: No changes (no drift)
        - 1: Error
        - 2: Changes detected (drift!)

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Optional timeout in seconds (default: PLAN_TIMEOUT)

        Returns:
            Tuple of (exit_code, output)
        """
        if timeout is None:
            timeout = self.PLAN_TIMEOUT
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu plan (detailed) for drift detection in {work_dir} (timeout: {timeout}s)")

        try:
            result = subprocess.run(
                ["tofu", "plan", "-no-color", "-input=false", "-detailed-exitcode"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr
            logger.info(f"tofu plan (detailed) completed with exit code {result.returncode}")

            return result.returncode, output

        except subprocess.TimeoutExpired as e:
            # S14-021: Handle timeout for drift detection plan
            timeout_msg = (
                f"\nDrift detection plan timed out after {timeout} seconds.\n"
            )
            logger.error(f"Drift plan timeout after {timeout}s")
            stdout = e.stdout
            stderr = e.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            output = (stdout or "") + (stderr or "") + timeout_msg
            return 1, output  # Return error code (not 2 for changes)

    def run_apply(
        self,
        work_dir: str,
        env: dict,
        timeout: int | None = None,
        module: ProjectModule | None = None,
    ) -> tuple[int, str, dict]:
        """
        Run tofu apply and capture outputs.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Optional timeout in seconds (default: APPLY_TIMEOUT)

        Returns:
            Tuple of (exit_code, output, captured_outputs)
        """
        if timeout is None:
            timeout = self.APPLY_TIMEOUT
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu apply in {work_dir} (timeout: {timeout}s)")

        try:
            # Apply the plan
            result = subprocess.run(
                ["tofu", "apply", "-no-color", "-input=false", "-auto-approve", "plan.out"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr
            logger.info(f"tofu apply completed with exit code {result.returncode}")

            # Capture outputs if apply succeeded
            outputs = {}
            if result.returncode == 0:
                outputs = self._capture_outputs(work_dir, tofu_env)
                if module is not None:
                    normalized = normalize_infrastructure_access_outputs(
                        outputs,
                        project_id=module.project_id,
                        module_id=module.id,
                    )
                    outputs = normalized.outputs

            return result.returncode, output, outputs

        except subprocess.TimeoutExpired as e:
            # S14-021: Handle timeout for apply
            timeout_msg = (
                f"\nApply operation timed out after {timeout} seconds.\n"
                f"This may indicate long-running resource creation.\n"
                f"Check your cloud provider console for resource status — partial infrastructure may exist.\n"
            )
            logger.error(f"Apply timeout after {timeout}s")
            stdout = e.stdout
            stderr = e.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            output = (stdout or "") + (stderr or "") + timeout_msg
            return 1, output, {}

    def _capture_outputs(self, work_dir: str, env: dict) -> dict:
        """
        Capture outputs from OpenTofu state.

        Args:
            work_dir: Workspace directory
            env: Environment variables

        Returns:
            Dict of output_name -> value
        """
        logger.info("Capturing outputs")
        tofu_env = _build_tofu_env(env)

        try:
            result = subprocess.run(
                ["tofu", "output", "-json"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=60  # S15-033: 60 second timeout for output capture
            )
        except subprocess.TimeoutExpired:
            logger.warning("Timed out capturing outputs after 60s")
            return {}

        if result.returncode != 0:
            logger.warning(f"Failed to capture outputs: {result.stderr}")
            return {}

        try:
            raw_outputs = json.loads(result.stdout)
            # Flatten: {"vpc_id": {"value": "vpc-123", "type": "string"}} -> {"vpc_id": "vpc-123"}
            outputs = {k: v.get("value") for k, v in raw_outputs.items()}
            logger.info(f"Captured {len(outputs)} outputs: {list(outputs.keys())}")
            return outputs
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse outputs JSON: {e}")
            return {}

    def run_refresh(self, work_dir: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
        """
        Run tofu refresh to recover state from existing infrastructure.

        Used when state file was lost but infrastructure still exists.
        OpenTofu will connect to the cloud provider and rebuild state.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Optional timeout in seconds (default: REFRESH_TIMEOUT)

        Returns:
            Tuple of (exit_code, output)
        """
        if timeout is None:
            timeout = self.REFRESH_TIMEOUT
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu refresh in {work_dir} (timeout: {timeout}s)")

        try:
            result = subprocess.run(
                ["tofu", "refresh", "-no-color", "-input=false"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr
            logger.info(f"tofu refresh completed with exit code {result.returncode}")

            return result.returncode, output

        except subprocess.TimeoutExpired as e:
            timeout_msg = (
                f"\nRefresh operation timed out after {timeout} seconds.\n"
                f"This may indicate connectivity issues with the cloud provider.\n"
            )
            logger.error(f"Refresh timeout after {timeout}s")
            stdout = e.stdout
            stderr = e.stderr
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors='replace')
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors='replace')
            output = (stdout or "") + (stderr or "") + timeout_msg
            return 1, output

    def capture_outputs(self, work_dir: str, env: dict) -> dict:
        """Public wrapper for _capture_outputs"""
        return self._capture_outputs(work_dir, env)

    def normalize_outputs(self, outputs: dict, module: ProjectModule) -> dict:
        """Normalize captured outputs for durable/ truthful infrastructure access metadata."""
        normalized = normalize_infrastructure_access_outputs(
            outputs,
            project_id=module.project_id,
            module_id=module.id,
        )
        return normalized.outputs

    def run_destroy(self, work_dir: str, env: dict, timeout: int | None = None) -> tuple[int, str]:
        """
        Run tofu destroy.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            timeout: Timeout in seconds (default: 30 minutes)

        Returns:
            Tuple of (exit_code, output)
        """
        if timeout is None:
            timeout = 30 * 60  # 30 minutes default
        tofu_env = _build_tofu_env(env)

        logger.info(f"Running tofu destroy in {work_dir} (timeout: {timeout}s)")

        try:
            result = subprocess.run(
                ["tofu", "destroy", "-no-color", "-input=false", "-auto-approve"],
                cwd=work_dir,
                env=tofu_env,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            output = result.stdout + result.stderr
            logger.info(f"tofu destroy completed with exit code {result.returncode}")

            return result.returncode, output

        except subprocess.TimeoutExpired as e:
            timeout_msg = (
                f"\nDestroy operation timed out after {timeout} seconds.\n"
                f"This may indicate:\n"
                f"- Complex dependency chains requiring manual intervention\n"
                f"- Cloud provider API rate limiting or throttling\n"
                f"- Resources stuck in transitional states\n"
                f"\nCheck your cloud provider console for resource status and consider manual cleanup.\n"
            )
            logger.error(f"Destroy timeout after {timeout}s")
            # S14-019: text=True makes stdout/stderr strings, not bytes — don't call .decode()
            output = (e.stdout or "") + (e.stderr or "") + timeout_msg
            return 1, output  # Return error code

    def run_destroy_with_retry(
        self,
        work_dir: str,
        env: dict,
        module: ProjectModule | None = None,
        max_retries: int | None = None,
        initial_delay: float | None = None,
        timeout: int | None = None
    ) -> tuple[int, str]:
        """
        Run tofu destroy with retry logic for dependency violations.

        AWS resources like ENIs, EIPs, and security groups may not be immediately
        deletable after dependent resources are destroyed. This method implements
        exponential backoff to handle these transient failures.

        Args:
            work_dir: Workspace directory
            env: Environment variables
            module: ProjectModule being destroyed (for error analysis)
            max_retries: Maximum number of retry attempts (default: 5)
            initial_delay: Initial delay in seconds before first retry (default: 2.0)
            timeout: Timeout in seconds per attempt (default: module-specific)

        Returns:
            Tuple of (exit_code, combined_output_with_retry_info)
        """
        if max_retries is None:
            max_retries = self.DESTROY_RETRY_MAX
        if initial_delay is None:
            initial_delay = self.DESTROY_RETRY_INITIAL_DELAY_SECONDS

        # Patterns that indicate retryable errors
        retryable_patterns = [
            r"DependencyViolation",
            r"still has dependencies and cannot be deleted",
            r"has dependent resources and cannot be deleted",
            r"network interface.*is currently in use",
            r"deletion failed.*dependency",
        ]

        all_output = ""
        attempt = 0

        while attempt <= max_retries:
            if attempt > 0:
                delay = initial_delay * (2 ** (attempt - 1))  # Exponential backoff
                retry_msg = f"\n{'='*60}\nRETRY ATTEMPT {attempt}/{max_retries} after {delay}s delay\n{'='*60}\n"
                logger.info(f"Destroy failed with retryable error. Waiting {delay}s before retry {attempt}/{max_retries}")
                all_output += retry_msg
                time.sleep(delay)

            # Run destroy
            exit_code, output = self.run_destroy(work_dir, env, timeout=timeout)
            all_output += output

            # Success - return immediately
            if exit_code == 0:
                if attempt > 0:
                    success_msg = f"\n{'='*60}\nDESTROY SUCCEEDED ON RETRY {attempt}\n{'='*60}\n"
                    all_output += success_msg
                    logger.info(f"Destroy succeeded on retry attempt {attempt}")
                return exit_code, all_output

            # Check if error is retryable
            is_retryable = any(re.search(pattern, output, re.IGNORECASE) for pattern in retryable_patterns)

            if not is_retryable:
                # Non-retryable error - fail immediately
                logger.error(f"Destroy failed with non-retryable error (exit code {exit_code})")
                return exit_code, all_output

            # Retryable error - check if we have retries left
            if attempt >= max_retries:
                # Out of retries - add detailed error analysis
                failure_msg = (
                    f"\n{'='*60}\n"
                    f"DESTROY FAILED after {max_retries} retry attempts\n"
                    f"{'='*60}\n"
                )
                all_output += failure_msg

                # Add parsed error guidance if module is provided
                if module:
                    error_analysis = self.parse_destroy_error(all_output, module)
                    all_output += error_analysis
                else:
                    # Generic guidance if no module provided
                    all_output += (
                        "This typically indicates cloud resources with persistent dependencies:\n"
                        "- Network interfaces still attached\n"
                        "- Elastic/static IPs not released\n"
                        "- Security groups or firewall rules referencing other resources\n"
                        "\nManual cleanup may be required via your cloud provider console.\n"
                    )

                logger.error(f"Destroy failed after {max_retries} retries with DependencyViolation")
                return exit_code, all_output

            attempt += 1

        # Should never reach here, but return last result if we do
        return exit_code, all_output

    def cleanup_workspace(self, work_dir: str):
        """
        Clean up workspace directory.

        Args:
            work_dir: Workspace directory to remove
        """
        if work_dir and os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.info(f"Cleaned up workspace: {work_dir}")


# Backward-compat alias (used by opentofu_tasks.py and drift_tasks.py)
ExecutionEngine = OpenTofuRuntime
