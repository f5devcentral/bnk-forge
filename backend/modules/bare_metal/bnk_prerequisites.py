"""
SSH port of catalog module 18 — k8s/bnk-prerequisites (bare-metal/bnk-prerequisites).

Creates the BNK namespaces and the FAR image-pull secret (``far-secret``) in each.
DPU case (via d019 _transform_bnk_prerequisites): also creates the ``f5-bnk``
instance namespace + its far-secret. Maps poc-deployer 00-namespaces.yaml +
30-create-far-secret.sh.

Version resolution: like the catalog tofu module, this downloads the BNK manifest
from repo.f5.com (``helm pull``) and parses component versions, producing
``flo_version`` / ``manifest_version`` / ``component_versions`` for downstream
modules. If ``flo_version`` is already supplied (e.g. from a BnkDeployableRelease
via the transforms), the download is skipped. If neither ``flo_version`` nor
``bnk_manifest_version`` is set, execution fails fast — a catalog release must be
assigned to the host. Maps poc-deployer download-manifest.sh + parse-versions.sh.

dockerconfigjson construction mirrors the tofu module exactly:
``auth = base64("_json_key_base64:" + cne_pull_secret)`` (Format A), with Format B
(pre-built dockerconfigjson) auto-detected and rebuilt.
"""

from __future__ import annotations

import base64
import json
import shlex
import time
from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec

_NS_LABELS = {
    "app.kubernetes.io/managed-by": "bnk-forge",
    "f5.com/product": "bnk",
}

# Manifest chart pulled from repo.f5.com to resolve component versions.
_MANIFEST_CHART = "oci://repo.f5.com/release/f5-bigip-k8s-manifest"


def parse_component_versions(text: str) -> dict[str, str]:
    """Parse ``charts/<name>=<version>`` lines (from the on-host awk) into a dict.

    Mirrors poc-deployer parse-versions.sh output. Keys keep their ``charts/`` /
    ``images/`` prefix (e.g. ``charts/f5-lifecycle-operator``).
    """
    versions: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            versions[key] = value
    return versions


def build_docker_config_json(cne_pull_secret: str) -> str:
    """Build the .dockerconfigjson string for repo.f5.com from a FAR credential.

    Format A (bare base64 SA key): auth = base64("_json_key_base64:" + secret).
    Format B (pre-built dockerconfigjson): extract the raw key, then rebuild as
    base64("_json_key_base64:" + base64(raw_key)) — matching the tofu module.
    """
    secret = cne_pull_secret.strip()
    raw_key_for_b: str | None = None
    try:
        decoded = base64.b64decode(secret).decode("utf-8", errors="strict")
        parsed = json.loads(decoded)
        if isinstance(parsed, dict) and "auths" in parsed:
            inner_auth = parsed["auths"]["repo.f5.com"]["auth"]
            decoded_auth = base64.b64decode(inner_auth).decode("utf-8", errors="replace")
            # password is everything after the first ":"
            raw_key_for_b = decoded_auth.split(":", 1)[1] if ":" in decoded_auth else decoded_auth
    except Exception:
        raw_key_for_b = None

    if raw_key_for_b is not None:
        password = base64.b64encode(raw_key_for_b.encode()).decode()
    else:
        password = secret  # Format A: secret IS the base64-encoded SA key

    auth = base64.b64encode(f"_json_key_base64:{password}".encode()).decode()
    return json.dumps({"auths": {"repo.f5.com": {"auth": auth}}}, separators=(",", ":"))


class BnkPrerequisitesSSHModule(BnkSSHModule):
    name = "BNK Prerequisites [SSH]"
    path = "bare-metal/bnk-prerequisites"
    description = "Create BNK namespaces + FAR pull secret over SSH"
    version = "1.0.0"
    estimated_duration = 90
    timeout = 300

    # Used by the base _helm_registry_login() for the manifest pull (not a helm
    # install — chart_ref stays empty, so this module is not treated as helm).
    oci_registry = "repo.f5.com"

    dependencies = ["bare-metal/kubeadm-init"]

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "cne_pull_secret": InputSpec(
            name="cne_pull_secret", source="project_secret", required=True, sensitive=True
        ),
        "operator_namespace": InputSpec(name="operator_namespace", source="profile", default="f5-operator"),
        "utils_namespace": InputSpec(name="utils_namespace", source="profile", default="f5-utils"),
        "gateway_namespace": InputSpec(name="gateway_namespace", source="profile", default="bnk-gw"),
        "instance_namespace": InputSpec(name="instance_namespace", source="profile", required=False, default=""),
        "bnk_manifest_version": InputSpec(
            name="bnk_manifest_version", source="profile", required=False, default=None
        ),
        "flo_version": InputSpec(name="flo_version", source="profile", required=False, default=""),
    }

    outputs = {
        "operator_namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "utils_namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "gateway_namespace": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "far_secret_name": OutputSpec(resource_kind="", resource_name="", static_value="far-secret"),
        "flo_version": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "manifest_version": OutputSpec(resource_kind="", resource_name="", field_path=""),
        "prerequisites_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def _namespaces(self, v: dict[str, Any]) -> list[str]:
        ns = [
            str(v.get("operator_namespace", "f5-operator")),
            str(v.get("utils_namespace", "f5-utils")),
            str(v.get("gateway_namespace", "bnk-gw")),
        ]
        instance = str(v.get("instance_namespace", "") or "")
        if instance and instance not in ns:
            ns.append(instance)
        return ns

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        secret = str(v.get("cne_pull_secret", "") or "")
        docker_config = build_docker_config_json(secret) if secret else ""
        manifests: list[dict[str, Any]] = []
        # Namespaces first, then the far-secret in each — apply order matters.
        for name in self._namespaces(v):
            manifests.append({
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": name, "labels": dict(_NS_LABELS)},
            })
        for name in self._namespaces(v):
            manifests.append({
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {"name": "far-secret", "namespace": name, "labels": dict(_NS_LABELS)},
                "type": "kubernetes.io/dockerconfigjson",
                "stringData": {".dockerconfigjson": docker_config},
            })
        return manifests

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        t0 = time.monotonic()
        tag = "[bnk-prerequisites]"

        # 1. Apply namespaces + far-secrets.
        manifests = self.render_manifests(variables)
        on_output(f"{tag} Applying {len(manifests)} manifest document(s) (namespaces + far-secret)...")
        self._apply_manifests(session, manifests, on_output)

        # 2. Resolve component versions. Prefer provided flo_version; otherwise
        #    download the BNK manifest from repo.f5.com and parse them (the catalog
        #    k8s/bnk-prerequisites behaviour).
        flo_version = str(variables.get("flo_version") or "")
        manifest_version = str(variables.get("bnk_manifest_version") or "")
        component_versions: dict[str, str] = {}
        if not flo_version:
            if not manifest_version:
                raise RuntimeError(
                    f"{tag} Neither flo_version nor bnk_manifest_version is set — cannot resolve "
                    "component versions. Assign a BnkDeployableRelease to this host."
                )
            component_versions = self._download_versions(session, variables, manifest_version, on_output)
            flo_version = component_versions.get("charts/f5-lifecycle-operator", "")
            if not flo_version:
                raise RuntimeError(
                    f"{tag} Could not resolve flo_version from manifest {manifest_version} "
                    "(charts/f5-lifecycle-operator missing). FLO install requires a chart version."
                )

        outputs = self.collect_outputs(session, variables)
        outputs["flo_version"] = flo_version
        outputs["manifest_version"] = manifest_version
        outputs["component_versions"] = component_versions
        outputs["execution_duration_seconds"] = round(time.monotonic() - t0, 1)
        on_output(f"{tag} Complete (flo_version={flo_version}, {outputs['execution_duration_seconds']}s)")
        return outputs

    def _download_versions(
        self, session: Any, variables: dict[str, Any], manifest_version: str, on_output: Any,
    ) -> dict[str, str]:
        """helm pull the BNK manifest, extract it, and parse component versions.

        Ports poc-deployer download-manifest.sh + parse-versions.sh. The awk
        emits ``charts/<name>=<version>`` lines; we parse them in Python.
        """
        on_output(f"[bnk-prerequisites] Resolving component versions from manifest {manifest_version}...")
        self._helm_registry_login(session, variables, on_output)  # repo.f5.com via FAR creds
        script = (
            "set -o pipefail; WD=$(mktemp -d); cd \"$WD\"; "
            f"{self.HELM} pull {_MANIFEST_CHART} --version {shlex.quote(manifest_version)} >/dev/null 2>&1 "
            "|| { echo PULL_FAILED; exit 1; }; "
            "TGZ=$(ls -t *.tgz 2>/dev/null | head -1); "
            "[ -z \"$TGZ\" ] && { echo NO_TGZ; exit 1; }; "
            "tar xf \"$TGZ\"; "
            # The component list lives in the manifest DATA file
            # (e.g. bigip-k8s-manifest-<ver>.yaml), NOT Chart.yaml — both share the
            # chart's 'bigip'/'manifest' directory path, so match the basename and
            # exclude Chart.yaml/values.yaml so we don't parse the chart's own
            # name/version instead of the components.
            "MF=$(find . -name '*manifest*.yaml' -type f ! -name 'Chart.yaml' | head -1); "
            "[ -z \"$MF\" ] && MF=$(find . -name '*.yaml' -type f ! -name 'Chart.yaml' ! -name 'values.yaml' | head -1); "
            "[ -z \"$MF\" ] && { echo NO_MANIFEST; exit 1; }; "
            "awk '/name:/{for(i=1;i<=NF;i++)if($i==\"name:\"){k=$(i+1);f=1;next}} "
            "f&&/version:/{for(i=1;i<=NF;i++)if($i==\"version:\"){print k\"=\"$(i+1);f=0;next}} "
            "{f=0}' \"$MF\"; "
            "cd /; rm -rf \"$WD\""
        )
        r = session.execute(script, timeout=self.timeout)
        if r.exit_code != 0 or "PULL_FAILED" in r.stdout or "NO_MANIFEST" in r.stdout:
            raise RuntimeError(
                f"[bnk-prerequisites] manifest download/parse failed "
                f"(exit {r.exit_code}): {(r.stdout + r.stderr)[:300]}"
            )
        return parse_component_versions(r.stdout)

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "operator_namespace": v.get("operator_namespace", "f5-operator"),
            "utils_namespace": v.get("utils_namespace", "f5-utils"),
            "gateway_namespace": v.get("gateway_namespace", "bnk-gw"),
            "far_secret_name": "far-secret",
            "flo_version": v.get("flo_version", ""),
            "manifest_version": v.get("bnk_manifest_version", ""),
            "prerequisites_ready": True,
        }

    def destroy(self, session: Any, v: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Clean up F5 webhooks/finalizers/CRDs, then delete secrets + namespaces.

        Ports the tofu null_resource.bnk_cleanup: BNK installs validating/mutating
        webhooks and CRD instances with finalizers that block namespace deletion.
        Without this, `kubectl delete namespace` hangs forever.
        """
        ns_list = " ".join(shlex.quote(n) for n in self._namespaces(v))
        cleanup = (
            f"KC='{self.KUBECTL}'; "
            "for wh in $($KC get validatingwebhookconfiguration -o name 2>/dev/null | grep f5); do "
            "$KC delete $wh --timeout=10s 2>/dev/null || true; done; "
            "for wh in $($KC get mutatingwebhookconfiguration -o name 2>/dev/null | grep f5); do "
            "$KC delete $wh --timeout=10s 2>/dev/null || true; done; "
            "F5_CRDS=$($KC get crd -o name 2>/dev/null | grep -E 'k8s\\.f5\\.(com|net\\.com)' | sed 's|.*/||'); "
            f"for NS in {ns_list}; do for crd in $F5_CRDS; do "
            "for res in $($KC get $crd -n $NS -o name 2>/dev/null); do "
            "$KC patch $res -n $NS --type=merge -p '{\"metadata\":{\"finalizers\":null}}' 2>/dev/null || true; "
            "done; done; done; "
            "for crd in $F5_CRDS; do $KC delete crd $crd --timeout=30s 2>/dev/null || true; done; "
            "for crd in $($KC get crd -o name 2>/dev/null | grep 'fic\\.f5\\.com' | sed 's|.*/||'); do "
            "$KC delete crd $crd --timeout=30s 2>/dev/null || true; done"
        )
        on_output("[bnk-prerequisites] Cleaning up F5 webhooks/finalizers/CRDs before namespace delete")
        session.execute(cleanup, timeout=self.timeout)
        # Then delete the rendered manifests (secrets, namespaces) in reverse.
        return super().destroy(session, v, on_output)
