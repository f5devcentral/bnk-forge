"""
Command Handlers — execute K8s operations locally on the cluster.

Each handler receives a payload dict and an async on_output callback for streaming.
Returns a result dict with at minimum {"success": bool}.

Handlers use kr8s (async K8s client) which authenticates via the pod's ServiceAccount.
Helm operations shell out to the helm CLI.
"""

import asyncio
import logging
import os
import subprocess
import json
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

import re

import kr8s
import yaml

logger = logging.getLogger("bnk-operator.handlers")

# ---------------------------------------------------------------------------
# BNK namespace discovery — Issue #139
# ---------------------------------------------------------------------------
# The operator runs inside the cluster and has no DB access. The set of BNK
# namespaces is therefore configured via:
#
#   1. BNK_NAMESPACES env-var (comma-separated) — set by the forge backend when
#      it has persisted discovered_namespaces from a prior scan; allows the
#      operator to query clusters where BNK is installed in a non-default
#      namespace without a full cluster-wide sweep.
#
#   2. Static default seed — "f5-bnk" (the canonical BNK install namespace).
#      This is the fallback for first-run / no env-var.
#
# All four call sites (~498-499, ~512, ~569-570, ~585) iterate _BNK_NS_LIST
# so that adding or changing the namespace set requires editing only this block.
# ---------------------------------------------------------------------------

_DEFAULT_BNK_NAMESPACE = "f5-bnk"

def _get_bnk_namespaces() -> list[str]:
    """Return the list of BNK namespaces to query.

    Reads BNK_NAMESPACES env-var (comma-separated).  Falls back to the static
    default ``f5-bnk`` when the env-var is absent or empty.
    """
    raw = os.environ.get("BNK_NAMESPACES", "").strip()
    if raw:
        ns_list = [ns.strip() for ns in raw.split(",") if ns.strip()]
        if ns_list:
            # Always ensure the canonical default is in the list
            if _DEFAULT_BNK_NAMESPACE not in ns_list:
                ns_list.insert(0, _DEFAULT_BNK_NAMESPACE)
            return ns_list
    return [_DEFAULT_BNK_NAMESPACE]


def _validate_cli_arg(name: str, value: Optional[str]) -> None:
    """Reject values that could be interpreted as CLI flags.

    Inline copy of ``utils.security.validate_cli_arg`` — the operator is a
    separate deployable (its own container image) and does not share the
    backend ``utils`` package.
    """
    if value and value.startswith("-"):
        raise ValueError(f"Invalid {name}: cannot start with '-'")


_HELM_TIMEOUT_RE = re.compile(r"^\d+[smh](\d+[smh])*$")


def _validate_helm_timeout(value: Optional[str]) -> None:
    """Reject timeout values that don't match ``<digits><unit>``."""
    if value is None:
        return
    _validate_cli_arg("timeout", value)
    if not _HELM_TIMEOUT_RE.match(value):
        raise ValueError(
            f"Invalid timeout format '{value}': expected <digits><unit> "
            f"(e.g. '5m', '300s', '1h30m')"
        )

# Type alias for the output callback
OutputCallback = Callable[[str], Coroutine]


class CommandHandlers:
    """
    Handles all commands sent from BNK-Forge.

    Uses kr8s (async) for native K8s API calls and subprocess for Helm CLI.
    """

    def __init__(self):
        self._api: Optional[kr8s.asyncio.Api] = None

    async def _get_api(self) -> kr8s.asyncio.Api:
        """Get or create kr8s API client (ServiceAccount auth)."""
        if self._api is None:
            self._api = await kr8s.asyncio.api()
        return self._api

    # ------------------------------------------------------------------
    # apply_manifests — server-side apply a list of K8s manifests
    # ------------------------------------------------------------------

    async def apply_manifests(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Apply K8s manifests via server-side apply.

        Payload:
          manifests: list[dict]    — K8s manifest dicts
          namespace: str           — default namespace (if not in manifest)
          wait_for_ready: bool     — wait for resources to be ready
          timeout: int             — seconds to wait (default 300)
        """
        manifests = payload.get("manifests", [])
        default_ns = payload.get("namespace", "default")
        wait_for_ready = payload.get("wait_for_ready", False)
        timeout = payload.get("timeout", 300)

        if not manifests:
            return {"success": True, "resources_created": 0, "message": "No manifests to apply"}

        api = await self._get_api()
        results = []
        errors = []

        for manifest in manifests:
            kind = manifest.get("kind", "Unknown")
            name = manifest.get("metadata", {}).get("name", "unnamed")
            ns = manifest.get("metadata", {}).get("namespace", default_ns)

            if on_output:
                await on_output(f"Applying {kind}/{name} in {ns}...")

            try:
                # Ensure namespace is set
                if "namespace" not in manifest.get("metadata", {}):
                    manifest.setdefault("metadata", {})["namespace"] = default_ns

                # Server-side apply
                resource = await api.async_apply(manifest, force=True)
                results.append({"kind": kind, "name": name, "namespace": ns, "action": "applied"})

                if on_output:
                    await on_output(f"  ✓ {kind}/{name} applied")

            except Exception as e:
                error_msg = f"{kind}/{name}: {e}"
                errors.append(error_msg)
                if on_output:
                    await on_output(f"  ✗ {error_msg}")
                logger.error(f"Apply failed for {kind}/{name}: {e}")

        # Wait for readiness if requested
        if wait_for_ready and not errors:
            for manifest in manifests:
                kind = manifest.get("kind", "")
                name = manifest.get("metadata", {}).get("name", "")
                ns = manifest.get("metadata", {}).get("namespace", default_ns)

                # Only wait for types that have readiness conditions
                if kind.lower() in ("deployment", "statefulset", "daemonset", "pod"):
                    if on_output:
                        await on_output(f"Waiting for {kind}/{name} to be ready...")

                    try:
                        resources = await api.async_get(kind, name, namespace=ns)
                        if resources:
                            resource = resources[0] if isinstance(resources, list) else resources
                            await asyncio.wait_for(
                                resource.async_wait("condition=Available", timeout=timeout),
                                timeout=timeout,
                            )
                            if on_output:
                                await on_output(f"  ✓ {kind}/{name} ready")
                    except asyncio.TimeoutError:
                        error_msg = f"{kind}/{name}: timed out waiting for readiness"
                        errors.append(error_msg)
                        if on_output:
                            await on_output(f"  ✗ {error_msg}")
                    except Exception as e:
                        # Non-fatal — resource might not support readiness
                        if on_output:
                            await on_output(f"  ⚠ {kind}/{name}: readiness check skipped ({e})")

        success = len(errors) == 0
        return {
            "success": success,
            "resources_created": len(results),
            "resources_failed": len(errors),
            "results": results,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # destroy_manifests — delete a list of K8s resources
    # ------------------------------------------------------------------

    async def destroy_manifests(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Delete K8s resources described by manifests.

        Payload:
          manifests: list[dict]     — K8s manifest dicts (need kind + metadata.name)
          namespace: str            — default namespace
          ignore_not_found: bool    — don't error on 404 (default true)
        """
        manifests = payload.get("manifests", [])
        default_ns = payload.get("namespace", "default")
        ignore_not_found = payload.get("ignore_not_found", True)

        if not manifests:
            return {"success": True, "resources_destroyed": 0}

        api = await self._get_api()
        destroyed = 0
        errors = []

        # Destroy in reverse order
        for manifest in reversed(manifests):
            kind = manifest.get("kind", "Unknown")
            name = manifest.get("metadata", {}).get("name", "unnamed")
            ns = manifest.get("metadata", {}).get("namespace", default_ns)

            if on_output:
                await on_output(f"Deleting {kind}/{name} from {ns}...")

            try:
                resources = await api.async_get(kind, name, namespace=ns)
                if resources:
                    resource = resources[0] if isinstance(resources, list) else resources
                    await resource.async_delete()
                    destroyed += 1
                    if on_output:
                        await on_output(f"  ✓ {kind}/{name} deleted")
                elif not ignore_not_found:
                    errors.append(f"{kind}/{name}: not found")
                    if on_output:
                        await on_output(f"  ⚠ {kind}/{name} not found")
                else:
                    if on_output:
                        await on_output(f"  ⚠ {kind}/{name} not found (ignored)")

            except Exception as e:
                if "not found" in str(e).lower() and ignore_not_found:
                    if on_output:
                        await on_output(f"  ⚠ {kind}/{name} not found (ignored)")
                else:
                    error_msg = f"{kind}/{name}: {e}"
                    errors.append(error_msg)
                    if on_output:
                        await on_output(f"  ✗ {error_msg}")

        return {
            "success": len(errors) == 0,
            "resources_destroyed": destroyed,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # install_helm — helm upgrade --install
    # ------------------------------------------------------------------

    async def install_helm(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Install or upgrade a Helm chart.

        Payload:
          chart_ref: str        — chart reference (oci://repo.f5.com/charts/f5-lifecycle-operator)
          release_name: str     — Helm release name
          namespace: str        — target namespace
          values: dict          — Helm values
          version: str          — chart version (optional)
          timeout: str          — timeout (default "10m")
          create_namespace: bool — create namespace if missing (default true)
          registry_auth: dict   — {url: str, username: str, password: str} for OCI auth
        """
        chart_ref = payload.get("chart_ref")
        release_name = payload.get("release_name")
        namespace = payload.get("namespace", "default")
        values = payload.get("values", {})
        version = payload.get("version")
        timeout = payload.get("timeout", "10m")
        create_namespace = payload.get("create_namespace", True)
        registry_auth = payload.get("registry_auth")

        if not chart_ref or not release_name:
            return {"success": False, "error_message": "chart_ref and release_name are required"}

        # Validate all values that will be interpolated into CLI commands
        _validate_cli_arg("chart_ref", chart_ref)
        _validate_cli_arg("release_name", release_name)
        _validate_cli_arg("namespace", namespace)
        _validate_cli_arg("version", version)
        _validate_helm_timeout(timeout)

        # OCI registry login if needed
        if registry_auth:
            login_url = registry_auth.get("url", "")
            _validate_cli_arg("registry_url", login_url)
            _validate_cli_arg("registry_username", registry_auth.get("username"))

            if on_output:
                await on_output(f"Logging into Helm registry {login_url}...")

            login_cmd = [
                "helm", "registry", "login", login_url,
                "--username", registry_auth.get("username", ""),
                "--password-stdin",
            ]
            proc = await asyncio.create_subprocess_exec(
                *login_cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate(
                input=registry_auth.get("password", "").encode()
            )
            if proc.returncode != 0:
                return {
                    "success": False,
                    "error_message": f"Helm registry login failed: {stderr.decode()}",
                }
            if on_output:
                await on_output(f"  ✓ Registry login successful")

        # Write values to temp file
        import tempfile
        import os
        values_file = None
        try:
            if values:
                fd, values_file = tempfile.mkstemp(suffix=".yaml")
                with os.fdopen(fd, "w") as f:
                    yaml.dump(values, f)

            # Build helm upgrade --install command
            cmd = [
                "helm", "upgrade", "--install", release_name, chart_ref,
                "--namespace", namespace,
                "--timeout", timeout,
                "--wait",
            ]
            if create_namespace:
                cmd.append("--create-namespace")
            if version:
                cmd.extend(["--version", version])
            if values_file:
                cmd.extend(["-f", values_file])

            if on_output:
                await on_output(f"Installing Helm chart: {chart_ref} as {release_name}...")

            # Execute
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            stdout_str = stdout.decode()
            stderr_str = stderr.decode()

            if on_output:
                for line in stdout_str.splitlines():
                    await on_output(f"  {line}")
                for line in stderr_str.splitlines():
                    if line.strip():
                        await on_output(f"  ⚠ {line}")

            if proc.returncode != 0:
                return {
                    "success": False,
                    "error_message": f"Helm install failed (exit {proc.returncode}): {stderr_str}",
                    "stdout": stdout_str,
                }

            if on_output:
                await on_output(f"  ✓ Helm release {release_name} installed/upgraded")

            return {
                "success": True,
                "release_name": release_name,
                "namespace": namespace,
                "stdout": stdout_str,
            }

        finally:
            if values_file and os.path.exists(values_file):
                os.unlink(values_file)

    # ------------------------------------------------------------------
    # uninstall_helm — helm uninstall
    # ------------------------------------------------------------------

    async def uninstall_helm(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Uninstall a Helm release.

        Payload:
          release_name: str
          namespace: str
        """
        release_name = payload.get("release_name")
        namespace = payload.get("namespace", "default")

        if not release_name:
            return {"success": False, "error_message": "release_name is required"}

        _validate_cli_arg("release_name", release_name)
        _validate_cli_arg("namespace", namespace)

        if on_output:
            await on_output(f"Uninstalling Helm release {release_name}...")

        cmd = ["helm", "uninstall", release_name, "--namespace", namespace]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_str = stderr.decode()
            if "not found" in stderr_str.lower():
                if on_output:
                    await on_output(f"  ⚠ Release {release_name} not found (already uninstalled)")
                return {"success": True, "message": "Release not found (already uninstalled)"}

            return {
                "success": False,
                "error_message": f"Helm uninstall failed: {stderr_str}",
            }

        if on_output:
            await on_output(f"  ✓ Helm release {release_name} uninstalled")

        return {"success": True, "release_name": release_name}

    # ------------------------------------------------------------------
    # scan_cluster — detect prerequisites and BNK components
    # ------------------------------------------------------------------

    async def scan_cluster(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Scan the cluster for prerequisites and BNK components.
        Lightweight version of backend/services/cluster_scanner.py.

        Returns cluster info, prerequisites status, BNK install status.
        """
        api = await self._get_api()

        if on_output:
            await on_output("Scanning cluster...")

        scan = {}

        try:
            # Cluster info
            version = await api.async_version()
            nodes = await api.async_get("nodes")
            ready_nodes = [n for n in nodes if _node_ready(n)]

            scan["cluster"] = {
                "kubernetes_version": version.get("gitVersion", "unknown"),
                "node_count": len(nodes),
                "nodes_ready": len(ready_nodes),
            }
            if on_output:
                await on_output(f"  Cluster: K8s {scan['cluster']['kubernetes_version']}, {len(nodes)} nodes")

            # CRDs
            crds = await api.async_get("customresourcedefinitions")
            crd_names = [c.metadata.get("name", "") for c in crds] if crds else []

            # Check cert-manager
            cert_manager_crds = [c for c in crd_names if "cert-manager.io" in c]
            scan["cert_manager"] = {
                "installed": len(cert_manager_crds) > 0,
                "crd_count": len(cert_manager_crds),
            }

            # Check Multus
            multus_installed = "network-attachment-definitions.k8s.cni.cncf.io" in crd_names
            scan["multus"] = {"installed": multus_installed}

            # Check Gateway API
            gateway_crds = [c for c in crd_names if "gateway.networking.k8s.io" in c]
            scan["gateway_api"] = {
                "installed": len(gateway_crds) > 0,
                "crd_count": len(gateway_crds),
            }

            # Check F5 BNK
            f5_crds = [c for c in crd_names if "f5.com" in c or "f5net.com" in c]
            scan["bnk"] = {
                "installed": len(f5_crds) > 0,
                "crd_count": len(f5_crds),
            }

            # Check for FLO deployment — query across all known BNK namespaces
            _bnk_ns = _get_bnk_namespaces()
            flo_results = []
            for _ns in _bnk_ns:
                try:
                    ns_deps = await api.async_get(
                        "deployments",
                        namespace=_ns,
                        label_selector="app.kubernetes.io/name=f5-lifecycle-operator",
                    )
                    if ns_deps:
                        flo_results.extend(ns_deps)
                except Exception:
                    pass
            if flo_results:
                scan["bnk"]["flo_installed"] = True
                scan["bnk"]["flo_ready"] = any(_deployment_ready(d) for d in flo_results)

            # Check for TMM pods — query across all known BNK namespaces
            all_tmm_pods = []
            for _ns in _bnk_ns:
                try:
                    ns_tmm = await api.async_get(
                        "pods", namespace=_ns, label_selector="app=f5-tmm"
                    )
                    if ns_tmm:
                        all_tmm_pods.extend(ns_tmm)
                except Exception:
                    pass
            scan["bnk"]["tmm_count"] = len(all_tmm_pods)
            scan["bnk"]["tmm_ready"] = sum(1 for p in all_tmm_pods if _pod_ready(p))

            if on_output:
                await on_output(f"  cert-manager: {'✓' if scan['cert_manager']['installed'] else '✗'}")
                await on_output(f"  Multus CNI:   {'✓' if scan['multus']['installed'] else '✗'}")
                await on_output(f"  Gateway API:  {'✓' if scan['gateway_api']['installed'] else '✗'}")
                await on_output(f"  F5 BNK:       {'✓' if scan['bnk']['installed'] else '✗'}")

            scan["success"] = True

        except Exception as e:
            logger.error(f"Cluster scan failed: {e}")
            scan["success"] = False
            scan["error_message"] = str(e)
            if on_output:
                await on_output(f"  ✗ Scan failed: {e}")

        return scan

    # ------------------------------------------------------------------
    # get_health — BNK health check (used by health_watcher)
    # ------------------------------------------------------------------

    async def get_health(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Get current BNK health status.
        Called periodically by HealthWatcher and on-demand via commands.
        """
        api = await self._get_api()
        health = {"timestamp": _now_iso()}

        try:
            # Cluster info
            version = await api.async_version()
            nodes = await api.async_get("nodes")
            health["cluster"] = {
                "kubernetes_version": version.get("gitVersion", "unknown"),
                "node_count": len(nodes),
                "nodes_ready": sum(1 for n in nodes if _node_ready(n)),
            }

            # BNK status
            bnk = {"installed": False}

            # FLO — query across all known BNK namespaces
            _bnk_ns = _get_bnk_namespaces()
            for _ns in _bnk_ns:
                try:
                    flo_deps = await api.async_get(
                        "deployments", namespace=_ns,
                        label_selector="app.kubernetes.io/name=f5-lifecycle-operator",
                    )
                    if flo_deps:
                        flo = flo_deps[0] if isinstance(flo_deps, list) else flo_deps
                        bnk["installed"] = True
                        bnk["flo_ready"] = _deployment_ready(flo)
                        bnk["flo_version"] = flo.metadata.get("labels", {}).get(
                            "app.kubernetes.io/version", "unknown"
                        )
                        break  # Found FLO — no need to check other namespaces
                except Exception:
                    pass

            # TMM — query across all known BNK namespaces
            all_tmm: list = []
            for _ns in _bnk_ns:
                try:
                    ns_pods = await api.async_get(
                        "pods", namespace=_ns, label_selector="app=f5-tmm"
                    )
                    if ns_pods:
                        all_tmm.extend(ns_pods)
                except Exception:
                    pass
            bnk["tmm_total"] = len(all_tmm)
            bnk["tmm_ready"] = sum(1 for p in all_tmm if _pod_ready(p))
            if all_tmm:
                # Container summary (e.g., "7/7")
                first_pod = all_tmm[0]
                container_statuses = first_pod.status.get("containerStatuses", [])
                ready_containers = sum(1 for c in container_statuses if c.get("ready"))
                total_containers = len(container_statuses)
                bnk["tmm_containers"] = f"{ready_containers}/{total_containers}"

            # Gateways — query across all known BNK namespaces
            all_gateways: list = []
            for _ns in _bnk_ns:
                try:
                    ns_gws = await api.async_get(
                        "gateways.gateway.networking.k8s.io", namespace=_ns
                    )
                    if ns_gws:
                        all_gateways.extend(ns_gws)
                except Exception:
                    pass
            try:
                bnk["gateways"] = [
                    {
                        "name": gw.metadata.get("name"),
                        "programmed": _has_condition(gw, "Programmed"),
                        "listeners": len(gw.spec.get("listeners", [])),
                    }
                    for gw in all_gateways
                ]
            except Exception:
                bnk["gateways"] = []

            # HTTPRoutes
            try:
                routes = await api.async_get(
                    "httproutes.gateway.networking.k8s.io",
                    namespace="",  # all namespaces
                )
                bnk["routes_count"] = len(routes) if routes else 0
            except Exception:
                bnk["routes_count"] = 0

            health["bnk"] = bnk
            health["success"] = True

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            health["success"] = False
            health["error_message"] = str(e)

        return health

    # ------------------------------------------------------------------
    # get_resources — list resources of a given kind
    # ------------------------------------------------------------------

    async def get_resources(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        List K8s resources.

        Payload:
          kind: str           — resource kind (e.g., "pods", "deployments")
          namespace: str      — namespace (default: all)
          label_selector: str — optional label selector
          api_version: str    — optional API version
        """
        kind = payload.get("kind", "pods")
        namespace = payload.get("namespace", "")
        label_selector = payload.get("label_selector", "")

        api = await self._get_api()

        try:
            kwargs = {"namespace": namespace} if namespace else {}
            if label_selector:
                kwargs["label_selector"] = label_selector

            resources = await api.async_get(kind, **kwargs)
            items = []
            for r in (resources or []):
                items.append({
                    "kind": r.kind,
                    "name": r.metadata.get("name"),
                    "namespace": r.metadata.get("namespace"),
                    "labels": r.metadata.get("labels", {}),
                    "creation_timestamp": r.metadata.get("creationTimestamp"),
                })

            return {"success": True, "items": items, "count": len(items)}

        except Exception as e:
            return {"success": False, "error_message": str(e)}

    # ------------------------------------------------------------------
    # get_pod_logs — stream pod logs
    # ------------------------------------------------------------------

    async def get_pod_logs(
        self,
        payload: Dict[str, Any],
        on_output: Optional[OutputCallback] = None,
    ) -> Dict[str, Any]:
        """
        Get pod logs.

        Payload:
          pod_name: str
          namespace: str
          container: str  — optional
          tail_lines: int — default 100
        """
        pod_name = payload.get("pod_name")
        namespace = payload.get("namespace", "default")
        container = payload.get("container")
        tail_lines = payload.get("tail_lines", 100)

        if not pod_name:
            return {"success": False, "error_message": "pod_name is required"}

        api = await self._get_api()

        try:
            pods = await api.async_get("pods", pod_name, namespace=namespace)
            if not pods:
                return {"success": False, "error_message": f"Pod {pod_name} not found"}

            pod = pods[0] if isinstance(pods, list) else pods
            logs = await pod.async_logs(container=container, tail_lines=tail_lines)

            if on_output:
                for line in logs.splitlines()[:50]:  # Stream first 50 lines
                    await on_output(line)

            return {"success": True, "logs": logs, "lines": len(logs.splitlines())}

        except Exception as e:
            return {"success": False, "error_message": str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _node_ready(node) -> bool:
    """Check if a K8s node is Ready."""
    conditions = node.status.get("conditions", [])
    for c in conditions:
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return False


def _pod_ready(pod) -> bool:
    """Check if all containers in a pod are ready."""
    conditions = pod.status.get("conditions", [])
    for c in conditions:
        if c.get("type") == "Ready":
            return c.get("status") == "True"
    return False


def _deployment_ready(dep) -> bool:
    """Check if a deployment has all replicas available."""
    status = dep.status
    desired = status.get("replicas", 0)
    available = status.get("availableReplicas", 0)
    return available >= desired and desired > 0


def _has_condition(resource, condition_type: str) -> bool:
    """Check if a resource has a specific condition set to True."""
    conditions = resource.status.get("conditions", [])
    for c in conditions:
        if c.get("type") == condition_type:
            return c.get("status") == "True"
    return False
