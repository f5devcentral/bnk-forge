"""
bare-metal/bnk-license — SSH module that creates the BNK License CR (ADR-478).

In BNK 2.3.1, CWC (spk-cwc) is licensed by a ``License`` CR
(``apiVersion: k8s.f5net.com/v1``, ``kind: License``). Without it, CWC logs
"cpcl-config-secret not found … waiting for License CR", TMM is placed in
STANDBY, and F5SPKVlans never reach ``Programmed``. Proved live on dpu-server-2
2026-07-24 — hand-applying this CR immediately licensed the cluster, TMM went
ACTIVE, and both F5SPKVlans reached Programmed=True.

This module is RELEASE-GATED on ``manifest_version``:
  - 2.3+  → applies the License CR, waits for LicenseActive condition.
  - <2.3  → CLEAN NO-OP. FLO helm ``license.*`` values carry licensing in 2.2,
            and the ``licenses.k8s.f5net.com`` CRD does not exist in 2.2 — so
            the CRD gate would block forever on a 2.2 deploy.

``manifest_version`` flows from ``bare-metal/bnk-prerequisites`` via auto-wiring
(e.g. "2.3.1-3.2598.3-0.0.304" or "2.2.1-3.2226.0-0.0.511"). Empty version
→ safe no-op.
"""

from __future__ import annotations

import time
from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


def _parse_major_minor(manifest_version: str) -> tuple[int, int]:
    """Parse (major, minor) from a BNK manifest version string.

    "2.3.1-3.2598.3-0.0.304"  → (2, 3)
    "2.2.1-3.2226.0-0.0.511"  → (2, 2)
    Returns (0, 0) on any parse failure or empty input.
    """
    if not manifest_version:
        return (0, 0)
    first_segment = str(manifest_version).split("-")[0]
    parts = first_segment.split(".")
    try:
        major = int(parts[0]) if parts else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return (0, 0)


class BnkLicenseSSHModule(BnkSSHModule):
    name = "BNK License CR [SSH]"
    path = "bare-metal/bnk-license"
    description = "Create License CR for CWC (BNK 2.3+); no-op for 2.2 — FLO helm path (ADR-478)"
    version = "1.0.0"
    estimated_duration = 60
    timeout = 600

    # CWC (f5-spk-cwc) is created by FLO reconciling the CNEInstance; the
    # licenses.k8s.f5net.com CRD and CWC deployment only exist after that.
    dependencies = ["bare-metal/bnk-cneinstance"]

    namespace_var = "namespace"
    default_namespace = "f5-operator"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "jwt_token": InputSpec(name="jwt_token", source="user", required=True, sensitive=True),
        "license_mode": InputSpec(name="license_mode", source="profile", default="connected"),
        "namespace": InputSpec(name="namespace", source="profile", default="f5-operator"),
        "license_cr_name": InputSpec(name="license_cr_name", source="profile", default="bnk-license"),
        # manifest_version is auto-wired from bare-metal/bnk-prerequisites outputs;
        # not required because this module falls back to a safe no-op when unset.
        "manifest_version": InputSpec(
            name="manifest_version", source="module", required=False, default="",
            from_module="bare-metal/bnk-prerequisites", from_output="manifest_version",
        ),
    }

    outputs = {
        "license_active": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(v)
        name = str(v.get("license_cr_name", "bnk-license"))
        jwt = str(v.get("jwt_token", ""))
        mode = str(v.get("license_mode", "connected"))
        # Teem URLs match the static values in _flo_license_static.py
        # (teemCertUrl uses product.apis; the two others use product-s.apis).
        return [
            {
                "apiVersion": "k8s.f5net.com/v1",
                "kind": "License",
                "metadata": {"name": name, "namespace": ns},
                "spec": {
                    "jwt": jwt,
                    "operationMode": mode,
                    "teemCertUrl": "https://product.apis.f5.com/ee/v1",
                    "teemEntitlementUrl": "https://product-s.apis.f5.com/ee/v1",
                    "teemInitialConfigUrl": "https://product-s.apis.f5.com/ee/v1",
                },
            }
        ]

    def get_required_crds(self, v: dict[str, Any]) -> list[str]:
        return ["licenses.k8s.f5net.com"]

    def get_required_deployments(self, v: dict[str, Any]) -> list[dict[str, str]]:
        return [{"name": "f5-spk-cwc", "namespace": self.resolve_namespace(v)}]

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(v)
        name = str(v.get("license_cr_name", "bnk-license"))
        return [
            {
                "kind": "licenses.k8s.f5net.com",
                "name": name,
                "namespace": ns,
                "condition": "condition=LicenseActive",
                "timeout": 600,
            }
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {"license_active": True}

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        t0 = time.monotonic()
        tag = "[bnk-license]"

        mv = str(variables.get("manifest_version") or "")
        major, minor = _parse_major_minor(mv)

        if (major, minor) < (2, 3):
            # Pre-2.3: licenses.k8s.f5net.com CRD does not exist and CWC is
            # licensed via FLO helm chart values. Skip ALL gates — letting the
            # CRD gate run would block forever on a 2.2 deploy.
            on_output(
                f"{tag} manifest_version={mv!r} is pre-2.3 (parsed {major}.{minor}); "
                "License CR not required — CWC licensing is via FLO helm chart. Skipping."
            )
            return {
                "license_active": True,
                "execution_duration_seconds": round(time.monotonic() - t0, 1),
            }

        # 2.3+ path: apply License CR via base class
        # (CRD gate + CWC deployment gate + manifest apply + LicenseActive wait)
        return super().execute(session, variables, on_output)
