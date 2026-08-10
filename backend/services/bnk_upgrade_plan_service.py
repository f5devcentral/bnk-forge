"""
BNK upgrade plan mixin — pre-checks and plan generation.

Extracted from bnk_upgrade_service.py (R4-012) to keep the monolith under control.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Import version helpers and constants from the main module
from services.bnk_upgrade_service import (
    STEP_CRD_WAIT,
    STEP_HEALTH_GATE,
    STEP_HELM_UPGRADE,
    STEP_MANIFEST_APPLY,
    get_known_version_info,
    parse_version,
    version_eq,
    version_gt,
)


class BnkUpgradePlanMixin:
    """
    Mixin providing upgrade pre-checks and plan generation.

    Expects the host class to provide:
        - self.db  (SQLAlchemy Session)
    """

    def _run_pre_checks(
        self,
        bnk_install: dict,
        cluster_info: dict,
        current_version: str | None,
        target_version: str,
    ) -> list[dict]:
        """Run pre-upgrade validation checks."""
        checks = []

        # Install shape is computed by the scanner (scanner/bnk_install.py) and
        # already drives graceful FLO-absence handling elsewhere (bnk/health.py,
        # routes/k8s/recovery.py). "flo" is the default so fixtures/callers that
        # don't set it explicitly keep the original FLO-centric behavior (#389).
        install_shape = bnk_install.get("install_shape", "flo")

        # 1. BNK must be installed
        bnk_status = bnk_install.get("status", "not_installed")
        checks.append({
            "name": "bnk_installed",
            "label": "BNK Installation",
            "status": "pass" if bnk_status == "installed" else "fail",
            "detail": (
                f"BNK is {bnk_status}"
                if bnk_status != "installed"
                else f"BNK is installed (version {current_version or 'unknown'})"
            ),
            "critical": True,
        })

        # 2. Current version must be detected — install-shape-aware (#389).
        # Version comes from FLO's pod image when present, or the Helm release
        # chart version on a helm/manual install (see
        # bnk_upgrade_service.detect_current_bnk_version). Only "detected by
        # neither method" is treated as a blocking failure.
        checks.append({
            "name": "version_detected",
            "label": "Current Version Detection",
            "status": "pass" if current_version else "fail",
            "detail": (
                f"Current version: {current_version}"
                if current_version
                else "Cannot detect current BNK version — checked FLO pods and Helm release chart"
            ),
            "critical": True,
        })

        # 3. Target version must be different from current
        if current_version and target_version:
            is_upgrade = version_gt(target_version, current_version)
            is_same = version_eq(target_version, current_version)
            if is_same:
                checks.append({
                    "name": "version_change",
                    "label": "Version Change",
                    "status": "fail",
                    "detail": f"Target version ({target_version}) is the same as current ({current_version})",
                    "critical": True,
                })
            elif not is_upgrade:
                checks.append({
                    "name": "version_change",
                    "label": "Version Change (Downgrade)",
                    "status": "warn",
                    "detail": f"Target version ({target_version}) is older than current ({current_version}). This is a downgrade.",
                    "critical": False,
                })
            else:
                checks.append({
                    "name": "version_change",
                    "label": "Version Change",
                    "status": "pass",
                    "detail": f"Upgrade: {current_version} → {target_version}",
                    "critical": True,
                })

        # 4. BNK health should be healthy
        # Scan values can be explicit None (K8s API returns null, not missing
        # keys) so `.get(k, {})` isn't enough — use `or {}` / `or []`.
        bnk_install.get("health")
        flo_info = bnk_install.get("flo") or {}
        tmm_info = bnk_install.get("tmm") or {}

        flo_running = flo_info.get("running", 0)
        flo_pods = flo_info.get("pods", 0)
        tmm_running = tmm_info.get("running", 0)
        tmm_pods = tmm_info.get("pods", 0)

        if install_shape == "helm":
            # FLO isn't part of a direct-helm/manual install — its absence
            # isn't a health signal here (mirrors bnk/health.py's rollup gate).
            checks.append({
                "name": "flo_health",
                "label": "FLO Health",
                "status": "pass",
                "detail": "FLO not used on a helm/manual install (install shape: helm) — check skipped",
                "critical": False,
            })
        else:
            checks.append({
                "name": "flo_health",
                "label": "FLO Health",
                "status": "pass" if flo_running == flo_pods and flo_pods > 0 else "warn",
                "detail": f"FLO pods: {flo_running}/{flo_pods} running",
                "critical": False,
            })
        checks.append({
            "name": "tmm_health",
            "label": "TMM Health",
            "status": "pass" if tmm_running == tmm_pods and tmm_pods > 0 else "warn",
            "detail": f"TMM pods: {tmm_running}/{tmm_pods} running",
            "critical": False,
        })

        # 5. VLANs should be programmed
        vlans = bnk_install.get("vlans") or []
        all_programmed = all(v.get("programmed", False) for v in vlans) if vlans else True
        checks.append({
            "name": "vlans_programmed",
            "label": "VLAN Status",
            "status": "pass" if all_programmed else "warn",
            "detail": (
                f"All {len(vlans)} VLANs programmed"
                if all_programmed
                else "Some VLANs not programmed — upgrade may cause transient connectivity loss"
            ),
            "critical": False,
        })

        # 6. Kubernetes version compatibility
        k8s_version_str = cluster_info.get("version", "")
        target_info = get_known_version_info(target_version)
        if target_info and k8s_version_str:
            # Extract major.minor from k8s version
            k8s_match = re.match(r"v?(\d+\.\d+)", k8s_version_str)
            if k8s_match:
                k8s_ver = k8s_match.group(1)
                min_k8s = target_info.get("min_k8s", "1.29")
                max_k8s = target_info.get("max_k8s", "1.33")

                # Numeric (major, minor) comparison, not lexical string compare (#390) —
                # "1.30" <= "1.9" is True lexically, and "1.36" mis-sorts against "1.31".
                k8s_tuple = parse_version(k8s_ver)
                min_tuple = parse_version(min_k8s)
                max_tuple = parse_version(max_k8s)

                if k8s_tuple < min_tuple:
                    status = "fail"
                    critical = True
                    detail = f"K8s {k8s_ver} is older than the minimum supported version ({min_k8s}) for target BNK version"
                elif k8s_tuple > max_tuple:
                    # Newer-than-tested is a warning, not a hard fail — k8s is
                    # backward-compatible for BNK's purposes.
                    status = "warn"
                    critical = False
                    detail = f"K8s {k8s_ver} is newer than the validated range ({min_k8s}-{max_k8s}) — likely compatible but not explicitly tested"
                else:
                    status = "pass"
                    critical = True
                    detail = f"K8s {k8s_ver} is compatible with target BNK version (requires {min_k8s}-{max_k8s})"

                checks.append({
                    "name": "k8s_compat",
                    "label": "Kubernetes Compatibility",
                    "status": status,
                    "detail": detail,
                    "critical": critical,
                })

        # 7. Helm release exists (for rollback) — install-shape-aware wording (#389).
        # On a helm/manual install this is the discovered release backing the
        # running controller/ingress, not necessarily FLO.
        helm_release = flo_info.get("helm_release") or {}
        if helm_release:
            detail = f"Helm release '{helm_release.get('name', 'flo')}' found (revision available for rollback)"
        elif install_shape == "flo":
            detail = "No Helm release found — FLO may have been installed outside BNK-Forge"
        else:
            detail = "No Helm release discovered for this install — rollback via Helm may not be available"
        checks.append({
            "name": "helm_release",
            "label": "Helm Release",
            "status": "pass" if helm_release else "warn",
            "detail": detail,
            "critical": False,
        })

        return checks

    def _build_plan(
        self,
        bnk_install: dict,
        target_version: str,
    ) -> list[dict]:
        """
        Generate an ordered upgrade plan.

        Upgrade order:
          1. Capture pre-upgrade health snapshot
          2. Upgrade FLO (Helm upgrade — deploys new CRDs, controller, TMM images)
          3. Wait for CRD migrations (FLO's crd-installer runs)
          4. Health gate: verify FLO pods ready
          5. Re-apply CNEInstance (pick up new CRD schema, trigger TMM rolling restart)
          6. Health gate: verify TMM ready (all containers)
          7. Re-apply VLANs (pick up any VLAN CRD schema changes)
          8. Re-apply GatewayClass (pick up any Gateway API changes)
          9. Post-upgrade health verification
        """
        plan = []
        step = 0

        # Install shape drives the fallback strategy below (#389) — a FLO
        # install has a well-known canonical OCI chart to fall back to; a
        # helm/manual install does not (there's no way to guess what chart
        # backs an arbitrary discovered release). Default "flo" keeps
        # fixtures/callers that don't set it explicitly on the original,
        # byte-for-byte FLO behavior.
        install_shape = bnk_install.get("install_shape", "flo")

        # Step 1: Pre-health snapshot
        step += 1
        plan.append({
            "step": step,
            "action": STEP_HEALTH_GATE,
            "label": "Capture pre-upgrade health snapshot",
            "module": None,
            "phase": "pre_upgrade",
            "timeout": 30,
        })

        # Step 2: Helm upgrade — FLO on a FLO install, or the discovered
        # controller/ingress Helm release on a helm/manual install. Both
        # shapes read the same bnk_install["flo"]["helm_release"] slot;
        # on a helm/manual install this is the release the scanner actually
        # discovered (e.g. an ingress controller chart), not FLO.
        flo_info = bnk_install.get("flo") or {}
        helm_release = flo_info.get("helm_release") or {}
        flo_chart = helm_release.get("chart")
        flo_namespace = helm_release.get("namespace")
        release_name = helm_release.get("name")

        # Degrade gracefully when the Helm secret couldn't be decoded — the chart
        # format varies per cluster (gzip/JSON/base64) and may fail silently.
        # On a FLO install, fall back to the canonical F5 OCI registry path.
        # On a helm/manual install there's no equivalent well-known chart to
        # assume, so leave it unresolved and surface a blocking warning instead
        # of guessing at a chart reference that would be actively wrong.
        chart_ref_fallback = False
        namespace_fallback = False
        if not flo_chart:
            chart_ref_fallback = True
            if install_shape == "flo":
                release_name_for_chart = release_name or "f5-lifecycle-operator"
                flo_chart = f"oci://repo.f5.com/charts/{release_name_for_chart}"
                logger.warning(
                    "FLO helm_release.chart missing — falling back to %s. "
                    "Airgapped-registry override may be required.",
                    flo_chart,
                )
            else:
                logger.warning(
                    "Helm release chart missing for install_shape=%s — no fallback chart available.",
                    install_shape,
                )
        if not flo_namespace:
            namespace_fallback = True
            if install_shape == "flo":
                # Use the namespace from flo pods if we have it; default to f5-cne-core
                # (the namespace seen in modern BNK 2.3+ clusters).
                flo_namespace = "f5-cne-core"
                logger.warning(
                    "FLO helm_release.namespace missing — falling back to %s.",
                    flo_namespace,
                )
            else:
                logger.warning(
                    "Helm release namespace missing for install_shape=%s — no fallback namespace available.",
                    install_shape,
                )

        if chart_ref_fallback or namespace_fallback:
            missing_fields = []
            if chart_ref_fallback:
                missing_fields.append(
                    f"chart (using fallback: {flo_chart})" if flo_chart else "chart (no fallback available)"
                )
            if namespace_fallback:
                missing_fields.append(
                    f"namespace (using fallback: {flo_namespace})" if flo_namespace else "namespace (no fallback available)"
                )
            step += 1
            plan.append({
                "step": step,
                "action": "warn",
                "label": "Helm release reference could not be fully read",
                "module": None,
                "phase": "pre_upgrade",
                "detail": (
                    "The installed Helm release secret could not be fully decoded "
                    f"(missing: {', '.join(missing_fields)}). "
                    + (
                        "The plan uses a best-effort fallback. If this cluster uses an "
                        "airgapped registry, override the chart reference before executing."
                        if install_shape == "flo"
                        else "This install shape has no well-known chart to fall back to — "
                        "confirm the chart/namespace before executing this step."
                    )
                ),
                "severity": "warn",
                "timeout": 0,
            })

        step += 1
        plan.append({
            "step": step,
            "action": STEP_HELM_UPGRADE,
            "label": f"Upgrade {'FLO' if install_shape == 'flo' else (release_name or 'BNK release')} to {target_version}",
            "module": "bnk/flo",
            "release_name": release_name or "flo",
            "namespace": flo_namespace,
            "chart": flo_chart,
            "version": target_version,
            "phase": "flo_upgrade",
            "timeout": 600,
        })

        # Step 3: Wait for CRD migration
        step += 1
        plan.append({
            "step": step,
            "action": STEP_CRD_WAIT,
            "label": "Wait for CRD installer to complete",
            "module": None,
            "phase": "crd_migration",
            "timeout": 120,
        })

        # Step 4: Health gate — FLO pods ready
        step += 1
        plan.append({
            "step": step,
            "action": STEP_HEALTH_GATE,
            "label": "Verify FLO pods healthy",
            "module": None,
            "phase": "flo_health",
            "checks": ["flo_pods_ready", "controller_pods_ready"],
            "timeout": 120,
        })

        # Step 5: Re-apply CNEInstance
        # cne_instance is explicit None on a helm/manual scan (no CNEInstance
        # CR), so `.get(k, {})` isn't enough — the key exists with value None.
        cne = bnk_install.get("cne_instance") or {}
        if cne.get("name"):
            step += 1
            plan.append({
                "step": step,
                "action": STEP_MANIFEST_APPLY,
                "label": "Re-apply CNEInstance (triggers TMM rolling restart)",
                "module": "bnk/cneinstance",
                "phase": "cneinstance_update",
                "timeout": 300,
            })

        # Step 6: Health gate — TMM ready
        step += 1
        plan.append({
            "step": step,
            "action": STEP_HEALTH_GATE,
            "label": "Verify TMM pods healthy (all containers ready)",
            "module": None,
            "phase": "tmm_health",
            "checks": ["tmm_pods_ready", "tmm_containers_ready"],
            "timeout": 300,
        })

        # Step 7: Re-apply VLANs (pick up CRD schema changes)
        vlans = bnk_install.get("vlans") or []
        if vlans:
            step += 1
            plan.append({
                "step": step,
                "action": STEP_MANIFEST_APPLY,
                "label": "Re-apply VLANs (pick up CRD schema changes)",
                "module": "bnk/bnk-vlans",
                "phase": "vlans_update",
                "timeout": 120,
            })

        # Step 8: Re-apply GatewayClass
        step += 1
        plan.append({
            "step": step,
            "action": STEP_MANIFEST_APPLY,
            "label": "Re-apply GatewayClass",
            "module": "bnk/bnk-gatewayclass",
            "phase": "gatewayclass_update",
            "timeout": 60,
        })

        # Step 9: Post-upgrade health verification
        step += 1
        plan.append({
            "step": step,
            "action": STEP_HEALTH_GATE,
            "label": "Post-upgrade health verification",
            "module": None,
            "phase": "post_upgrade",
            "checks": [
                "flo_pods_ready", "tmm_pods_ready", "tmm_containers_ready",
                "vlans_programmed", "gatewayclass_accepted",
            ],
            "timeout": 120,
        })

        return plan
