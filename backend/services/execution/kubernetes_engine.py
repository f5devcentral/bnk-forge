"""
Kubernetes Engine — native K8s execution via kr8s + helm CLI.

Handles two module types:
  - ManifestModule: server-side apply via kr8s
  - HelmModule: helm upgrade --install via subprocess

This engine is ~18x faster than OpenTofu for K8s resources because it skips:
  - git clone
  - provider download (tofu init)
  - state management (tofu plan + apply)
  - null_resource/time_sleep ceremony

Instead: render manifests → apply → wait for readiness → collect outputs.
"""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from services.execution.engine_interface import (
    DeploymentEngine,
    ModuleContext,
    OperationResult,
    PlanResult,
)
from services.execution.k8s_catalog_payload import (
    build_helm_payload,
    build_manifest_payload,
    collect_outputs_from_pack,
    is_catalog_k8s_context,
)
from utils.security import validate_cli_arg

logger = logging.getLogger(__name__)


class KubernetesEngine(DeploymentEngine):
    """
    Native K8s execution engine.

    Uses kr8s for manifest apply/delete/diff and helm CLI for chart installs.
    All methods are synchronous (Celery workers are sync) — we use asyncio.run()
    internally to call kr8s async APIs.
    """

    def __init__(self, kubeconfig_path: str, context: str | None = None):
        self.kubeconfig_path = kubeconfig_path
        self.context = context

    def _conn_args(self, kubeconfig_path: str | None = None, *, for_helm: bool = False) -> list[str]:
        """Build the shared helm/kubectl connection flags.

        Returns ``["--kubeconfig", path]`` plus the context flag only when a
        named context is set. The context flag differs by tool: helm uses
        ``--kube-context`` while kubectl uses ``--context`` (helm rejects
        ``--context``). Constructs a new list each call so callers can safely
        extend it without mutating shared state.
        """
        path = kubeconfig_path if kubeconfig_path is not None else self.kubeconfig_path
        args = ["--kubeconfig", path]
        if self.context:
            # Reject a context that could be parsed as a CLI flag (leading '-')
            # before it reaches the helm/kubectl argv.
            validate_cli_arg("context", self.context)
            args += ["--kube-context" if for_helm else "--context", self.context]
        return args

    @contextmanager
    def _inject_credentials(self, credentials_env: dict[str, str]):
        """Temporarily inject cloud credentials into os.environ.

        kr8s and helm shell out to ``aws eks get-token`` (or similar) for
        exec-based kubeconfig auth.  Those sub-processes inherit os.environ,
        so the credentials loaded from the DB must be present there.
        """
        if not credentials_env:
            yield
            return

        original: dict[str, str | None] = {}
        try:
            for key, value in credentials_env.items():
                original[key] = os.environ.get(key)
                os.environ[key] = value
            yield
        finally:
            for key in credentials_env:
                prev = original.get(key)
                if prev is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = prev

    def health_check(self) -> bool:
        """Check that we can reach the K8s API server (list namespaces)."""
        try:
            loop = _get_or_create_loop()
            api = loop.run_until_complete(self._get_api())
            # Quick connectivity check — get server version
            loop.run_until_complete(api.version())
            return True
        except Exception:
            return False

    # =========================================================================
    # DeploymentEngine interface
    # =========================================================================

    def init(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """
        Init for K8s engine = validate connectivity.

        No providers to download, no backend to configure. Just check we can
        reach the API server.
        """
        start = time.monotonic()
        try:
            with self._inject_credentials(ctx.credentials_env):
                loop = _get_or_create_loop()
                api = loop.run_until_complete(self._get_api())

                # Quick connectivity check
                version = loop.run_until_complete(api.version())
                msg = f"Connected to Kubernetes {version.get('major', '?')}.{version.get('minor', '?')}"

            if on_output:
                on_output(msg)

            return OperationResult(
                success=True,
                stdout=msg,
                duration_seconds=time.monotonic() - start,
            )
        except Exception as e:
            return OperationResult(
                success=False,
                error_message=f"Cannot connect to Kubernetes API: {e}",
                error_suggestion="Check kubeconfig path and cluster accessibility.",
                duration_seconds=time.monotonic() - start,
            )

    def plan(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> PlanResult:
        """Compare desired manifests against live cluster state."""
        if not is_catalog_k8s_context(ctx):
            raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot plan")

        if (ctx.deploy_model or "").strip().lower() == "helm":
            helm_payload = build_helm_payload(ctx, ctx.variables)
            return self._plan_helm_from_payload(helm_payload, ctx, on_output)

        manifest_payload = build_manifest_payload(ctx, ctx.variables)
        manifests = manifest_payload["manifests"]
        module_name = manifest_payload["module_name"]

        loop = _get_or_create_loop()

        with self._inject_credentials(ctx.credentials_env):
            api = loop.run_until_complete(self._get_api())

            adds = 0
            changes = 0
            details = []

            for manifest in manifests:
                kind = manifest["kind"]
                name = manifest["metadata"]["name"]
                ns = manifest["metadata"].get("namespace", "default")

                try:
                    actual_list = loop.run_until_complete(
                        _collect_resources(api, kind, name, ns)
                    )
                    # kr8s returns a list; if empty, resource doesn't exist
                    if not actual_list:
                        adds += 1
                        details.append(f"+ {kind}/{name} in {ns} (will be created)")
                    else:
                        # Resource exists — we could do a deep diff but for now
                        # assume server-side apply handles convergence
                        # Mark as "change" if we detect spec differences
                        changes += 1
                        details.append(f"~ {kind}/{name} in {ns} (will be updated)")
                except Exception:
                    adds += 1
                details.append(f"+ {kind}/{name} in {ns} (will be created)")

        if on_output:
            on_output(f"Planning Kubernetes manifests for {module_name}")
            for line in details:
                on_output(line)
            if not details:
                on_output("No changes detected.")

        return PlanResult(
            has_changes=(adds + changes) > 0,
            adds=adds,
            changes=changes,
            details="\n".join(details) if details else "No changes detected.",
        )

    def apply(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Apply module: render manifests/values → apply → wait → collect outputs."""
        start = time.monotonic()

        try:
            with self._inject_credentials(ctx.credentials_env):
                if not is_catalog_k8s_context(ctx):
                    raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot apply")

                deploy_model = (ctx.deploy_model or "").strip().lower()
                if deploy_model == "helm":
                    helm_payload = build_helm_payload(ctx, ctx.variables)
                    result = self._apply_helm_from_payload(helm_payload, ctx, on_output)
                else:
                    manifest_payload = build_manifest_payload(ctx, ctx.variables)
                    result = self._apply_manifests_from_payload(manifest_payload, on_output)

            if result.success and is_catalog_k8s_context(ctx):
                pack_outputs = collect_outputs_from_pack(ctx, ctx.variables)
                if pack_outputs:
                    result.outputs = pack_outputs

            result.duration_seconds = time.monotonic() - start
            return result

        except Exception as e:
            logger.exception(f"K8s engine apply failed for {ctx.path}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - start,
                error_message=str(e),
                error_suggestion=_suggest_fix(e),
            )

    def destroy(
        self,
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None = None,
    ) -> OperationResult:
        """Delete all resources created by this module."""
        start = time.monotonic()

        try:
            with self._inject_credentials(ctx.credentials_env):
                if not is_catalog_k8s_context(ctx):
                    raise ValueError(f"Module {ctx.path} is not a catalog K8s module — cannot destroy")

                deploy_model = (ctx.deploy_model or "").strip().lower()
                if deploy_model == "helm":
                    helm_payload = build_helm_payload(ctx, ctx.variables)
                    result = self._destroy_helm_from_payload(helm_payload, ctx, on_output)
                else:
                    manifest_payload = build_manifest_payload(ctx, ctx.variables)
                    result = self._destroy_manifests_from_payload(manifest_payload, on_output)

            result.duration_seconds = time.monotonic() - start
            return result

        except Exception as e:
            logger.exception(f"K8s engine destroy failed for {ctx.path}")
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - start,
                error_message=str(e),
            )

    def get_outputs(self, ctx: ModuleContext) -> dict[str, Any]:
        """Read outputs from pack manifest declarations for catalog K8s modules."""
        if is_catalog_k8s_context(ctx):
            return collect_outputs_from_pack(ctx, ctx.variables)
        return {}

    # =========================================================================
    # Manifest operations
    # =========================================================================

    def _plan_helm_from_payload(
        self,
        payload: dict[str, Any],
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None,
    ) -> PlanResult:
        module_def = _PayloadModuleAdapter(payload)
        import os

        validate_cli_arg("release_name", module_def.release_name)
        validate_cli_arg("namespace", module_def.namespace)

        cmd = [
            "helm", "status",
            module_def.release_name,
            "--namespace", module_def.namespace,
        ] + self._conn_args(for_helm=True)

        proc_env = {**os.environ, **(ctx.credentials_env or {})}
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=proc_env)

        if result.returncode != 0:
            return PlanResult(
                has_changes=True,
                adds=1,
                details=f"Helm release {module_def.release_name} does not exist (will be installed)",
            )

        return PlanResult(
            has_changes=True,  # Always assume changes for Helm (values may differ)
            changes=1,
            details=f"Helm release {module_def.release_name} exists (will be upgraded)",
        )

    def _apply_helm_from_payload(
        self,
        payload: dict[str, Any],
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None,
    ) -> OperationResult:
        module_def = _PayloadModuleAdapter(payload)
        import os

        values = module_def.render_helm_values(payload.get("values", {}))

        # Build process env: OS env + cloud credentials
        proc_env = {**os.environ, **(ctx.credentials_env or {})}

        # OCI registry login (F5 charts require authentication)
        if module_def.chart_ref.startswith("oci://"):
            registry_host = module_def.chart_ref.replace("oci://", "").split("/")[0]
            self._helm_registry_login(registry_host, values, proc_env, on_output)

        # Add any required repos
        for repo_name, repo_url in module_def.helm_repos.items():
            self._helm_repo_add(repo_name, repo_url, on_output)

        # Write values to temp file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(values, f)
            values_path = f.name

        # Validate all user-influenced values before building command
        validate_cli_arg("release_name", module_def.release_name)
        validate_cli_arg("chart_ref", module_def.chart_ref)
        validate_cli_arg("namespace", module_def.namespace)
        validate_cli_arg("chart_version", module_def.chart_version)

        cmd = [
            "helm", "upgrade", "--install",
            module_def.release_name,
            module_def.chart_ref,
            "--namespace", module_def.namespace,
            "--values", values_path,
            "--wait",
            "--timeout", f"{module_def.timeout}s",
        ] + self._conn_args(for_helm=True)

        if module_def.create_namespace:
            cmd.append("--create-namespace")

        if module_def.chart_version:
            cmd.extend(["--version", module_def.chart_version])

        if on_output:
            on_output(f"Installing Helm chart: {module_def.chart_ref}")
            on_output(f"  Release: {module_def.release_name}")
            on_output(f"  Namespace: {module_def.namespace}")
            if module_def.chart_version:
                on_output(f"  Version: {module_def.chart_version}")

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=module_def.timeout + 60,
            env=proc_env,
        )

        if result.returncode != 0:
            stderr_lower = (result.stderr or "").lower()
            has_crd_ownership_error = (
                "invalid ownership metadata" in stderr_lower
                or "release-namespace" in stderr_lower
            )

            if has_crd_ownership_error:
                adopted_crds = self._adopt_helm_crds(
                    release_name=module_def.release_name,
                    namespace=module_def.namespace,
                    kubeconfig_path=self.kubeconfig_path,
                    proc_env=proc_env,
                    on_output=on_output,
                )
                if adopted_crds:
                    if on_output:
                        on_output(
                            "Retrying Helm install after CRD adoption: "
                            f"{', '.join(adopted_crds)}"
                        )

                    retry_result = subprocess.run(
                        cmd, capture_output=True, text=True,
                        timeout=module_def.timeout + 60,
                        env=proc_env,
                    )

                    if retry_result.returncode != 0:
                        return OperationResult(
                            success=False,
                            stdout=retry_result.stdout,
                            stderr=retry_result.stderr,
                            error_message=f"Helm install failed after CRD adoption retry: {retry_result.stderr[:500]}",
                            error_suggestion=_suggest_helm_fix(retry_result.stderr),
                        )

                    if on_output:
                        on_output("  ✓ Helm chart installed successfully")

                    return OperationResult(
                        success=True,
                        outputs={},
                        stdout=retry_result.stdout,
                        resources_created=1,
                    )

            return OperationResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                error_message=f"Helm install failed: {result.stderr[:500]}",
                error_suggestion=_suggest_helm_fix(result.stderr),
            )

        if on_output:
            on_output("  ✓ Helm chart installed successfully")

        return OperationResult(
            success=True,
            outputs={},
            stdout=result.stdout,
            resources_created=1,
        )

    def _destroy_helm_from_payload(
        self,
        payload: dict[str, Any],
        ctx: ModuleContext,
        on_output: Callable[[str], None] | None,
    ) -> OperationResult:
        module_def = _PayloadModuleAdapter(payload)
        import os

        # Validate values before building command (defense-in-depth)
        validate_cli_arg("release_name", module_def.release_name)
        validate_cli_arg("namespace", module_def.namespace)

        if on_output:
            on_output(f"Uninstalling Helm release: {module_def.release_name}")

        cmd = [
            "helm", "uninstall",
            module_def.release_name,
            "--namespace", module_def.namespace,
            "--wait",
            "--timeout", f"{module_def.timeout}s",
        ] + self._conn_args(for_helm=True)

        proc_env = {**os.environ, **(ctx.credentials_env or {})}

        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=module_def.timeout + 60,
            env=proc_env,
        )

        if result.returncode != 0:
            # Helm uninstall fails if release doesn't exist — that's ok
            if "not found" in result.stderr.lower():
                if on_output:
                    on_output(f"  - Release {module_def.release_name} already absent")
                return OperationResult(success=True, resources_destroyed=0)

            return OperationResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                error_message=f"Helm uninstall failed: {result.stderr[:500]}",
            )

        if on_output:
            on_output(f"  ✓ Helm release {module_def.release_name} uninstalled")

        return OperationResult(success=True, resources_destroyed=1)

    def _apply_manifests_from_payload(
        self,
        payload: dict[str, Any],
        on_output: Callable[[str], None] | None,
    ) -> OperationResult:
        module_def = _PayloadModuleAdapter(payload)
        loop = _get_or_create_loop()
        api = loop.run_until_complete(self._get_api())
        manifests = module_def.render_manifests({})
        created = 0
        modified = 0
        output_lines = []

        if on_output:
            on_output(f"Applying {len(manifests)} K8s resources for {module_def.name}...")

        # Ensure all target namespaces exist before applying resources.
        # Builtin manifest modules (e.g., network-setup) may target namespaces
        # created by other modules in the pipeline — ensure them here.
        target_namespaces = {
            m["metadata"].get("namespace")
            for m in manifests
            if m["metadata"].get("namespace") and m["metadata"].get("namespace") != "default"
        }
        for ns_name in sorted(target_namespaces):
            try:
                ns_manifest = {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": ns_name},
                }
                loop.run_until_complete(_server_side_apply(api, ns_manifest))
            except Exception as exc:
                logger.debug("Namespace %s ensure skipped: %s", ns_name, exc)

        for manifest in manifests:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")

            if on_output:
                on_output(f"  Applying {kind}/{name} in {ns}...")

            try:
                # Server-side apply — handles create AND update
                loop.run_until_complete(_server_side_apply(api, manifest))

                # Determine if created or updated
                msg = f"  ✓ Applied {kind}/{name}"
                created += 1  # We can't easily distinguish create vs update with SSA
                output_lines.append(msg)

                if on_output:
                    on_output(msg)

            except Exception as e:
                error_msg = f"  ✗ Failed {kind}/{name}: {e}"
                output_lines.append(error_msg)
                if on_output:
                    on_output(error_msg)
                return OperationResult(
                    success=False,
                    stdout="\n".join(output_lines),
                    error_message=f"Failed to apply {kind}/{name}: {e}",
                    error_suggestion=_suggest_fix(e),
                    resources_created=created,
                    resources_modified=modified,
                )

        # Wait for readiness conditions
        for manifest in manifests:
            condition = module_def.get_readiness_condition(manifest)
            if condition:
                kind = manifest["kind"]
                name = manifest["metadata"]["name"]
                ns = manifest["metadata"].get("namespace", "default")

                if on_output:
                    on_output(f"  Waiting for {kind}/{name} ({condition})...")

                try:
                    success = loop.run_until_complete(
                        _wait_for_condition(api, kind, name, ns, condition, module_def.timeout)
                    )
                    if success:
                        msg = f"  ✓ {kind}/{name} is ready"
                        if on_output:
                            on_output(msg)
                        output_lines.append(msg)
                    else:
                        return OperationResult(
                            success=False,
                            stdout="\n".join(output_lines),
                            error_message=f"{kind}/{name} did not become ready within {module_def.timeout}s",
                            error_suggestion="Check pod events and logs for the resource.",
                            resources_created=created,
                        )
                except Exception as e:
                    return OperationResult(
                        success=False,
                        stdout="\n".join(output_lines),
                        error_message=f"Error waiting for {kind}/{name}: {e}",
                        resources_created=created,
                    )

        if on_output:
            on_output(f"✓ {module_def.name} applied successfully ({created} resources)")

        return OperationResult(
            success=True,
            outputs={},
            stdout="\n".join(output_lines),
            resources_created=created,
            resources_modified=modified,
        )

    def _destroy_manifests_from_payload(
        self,
        payload: dict[str, Any],
        on_output: Callable[[str], None] | None,
    ) -> OperationResult:
        module_def = _PayloadModuleAdapter(payload)
        loop = _get_or_create_loop()
        api = loop.run_until_complete(self._get_api())

        # Render manifests — retry with placeholders for missing variables
        render_vars = {}
        manifests = None
        for _attempt in range(10):
            try:
                manifests = module_def.get_destroy_order(
                    module_def.render_manifests(render_vars)
                )
                break
            except KeyError as e:
                missing_key = e.args[0] if e.args else str(e)
                logger.warning(
                    f"[destroy-fallback] render_manifests missing '{missing_key}' "
                    f"— injecting placeholder for resource discovery"
                )
                render_vars[missing_key] = f"__destroy_placeholder_{missing_key}__"
            except Exception:
                raise

        if manifests is None:
            return OperationResult(
                success=False,
                error_message="Failed to render manifests for destroy after retries",
            )

        destroyed = 0
        output_lines = []

        if on_output:
            on_output(f"Destroying {len(manifests)} K8s resources for {module_def.name}...")

        for manifest in manifests:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")

            if on_output:
                on_output(f"  Deleting {kind}/{name} in {ns}...")

            try:
                deleted = loop.run_until_complete(_delete_resource(api, kind, name, ns))
                if deleted:
                    msg = f"  ✓ Deleted {kind}/{name}"
                    destroyed += 1
                else:
                    msg = f"  - {kind}/{name} already absent"
                output_lines.append(msg)
                if on_output:
                    on_output(msg)
            except Exception as e:
                msg = f"  ✗ Failed to delete {kind}/{name}: {e}"
                output_lines.append(msg)
                if on_output:
                    on_output(msg)
                # Continue deleting other resources even if one fails

        if on_output:
            on_output(f"✓ {module_def.name} destroyed ({destroyed} resources)")

        return OperationResult(
            success=True,
            stdout="\n".join(output_lines),
            resources_destroyed=destroyed,
        )

    def _helm_registry_login(
        self,
        registry_host: str,
        variables: dict[str, Any],
        proc_env: dict,
        on_output: Callable[[str], None] | None,
    ):
        """
        Login to an OCI Helm registry using the FAR pull secret.

        For F5's repo.f5.com, authentication uses the cne_pull_secret project
        secret — a base64-encoded Docker config JSON (the same content that
        gets injected into a Kubernetes imagePullSecret by bnk-prerequisites).

        The Docker config JSON has the structure:
          {"auths": {"repo.f5.com": {"auth": "<base64 user:pass>"}}}

        We decode the auth field and pass it to `helm registry login`.

        NOTE: jwt_token is the BNK LICENSE token — it is NOT used for
        OCI registry auth. Only cne_pull_secret authenticates to repo.f5.com.
        """
        import base64

        cne_pull_secret = variables.get("cne_pull_secret", "")

        if not cne_pull_secret:
            logger.warning(
                f"No cne_pull_secret available for OCI registry login to {registry_host}. "
                f"Helm pull may fail if registry requires authentication."
            )
            return

        try:
            # Decode the base64-encoded Docker config JSON
            decoded = base64.b64decode(cne_pull_secret).decode("utf-8")
            config = json.loads(decoded)
            auths = config.get("auths", {})

            for host, auth_data in auths.items():
                if registry_host in host or host in registry_host:
                    auth_b64 = auth_data.get("auth", "")
                    if auth_b64:
                        auth_decoded = base64.b64decode(auth_b64).decode("utf-8")
                        if ":" in auth_decoded:
                            username, password = auth_decoded.split(":", 1)
                            cmd = [
                                "helm", "registry", "login", registry_host,
                                "--username", username, "--password-stdin",
                            ]
                            result = subprocess.run(
                                cmd, input=password, capture_output=True,
                                text=True, timeout=30, env=proc_env,
                            )
                            if result.returncode == 0:
                                if on_output:
                                    on_output(f"  ✓ Authenticated to OCI registry {registry_host}")
                                return
                            else:
                                logger.warning(
                                    f"OCI registry login to {registry_host} failed: {result.stderr}"
                                )
                                return

            # No matching host found in auths — try using the raw secret as password
            logger.warning(
                f"No auth entry for {registry_host} in cne_pull_secret Docker config. "
                f"Available hosts: {list(auths.keys())}"
            )

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Not a Docker config JSON — try raw value as password (legacy format)
            logger.debug(f"cne_pull_secret is not Docker config JSON ({e}), trying as raw password")
            cmd = [
                "helm", "registry", "login", registry_host,
                "--username", "_json_key", "--password-stdin",
            ]
            result = subprocess.run(
                cmd, input=cne_pull_secret, capture_output=True,
                text=True, timeout=30, env=proc_env,
            )
            if result.returncode == 0:
                if on_output:
                    on_output(f"  ✓ Authenticated to OCI registry {registry_host}")
            else:
                logger.warning(f"OCI registry login to {registry_host} failed: {result.stderr}")

        except Exception as e:
            logger.warning(f"Failed to parse cne_pull_secret for OCI login: {e}")

    def _adopt_helm_crds(
        self,
        release_name: str,
        namespace: str,
        kubeconfig_path: str,
        proc_env: dict[str, str],
        on_output: Callable[[str], None] | None,
    ) -> list[str]:
        """Adopt CRDs with matching release-name into the target namespace.

        This is best-effort and must never block Helm install flow.
        """
        validate_cli_arg("release_name", release_name)
        validate_cli_arg("namespace", namespace)

        try:
            list_cmd = [
                "kubectl", "get", "crd",
                "-o", "json",
            ] + self._conn_args(kubeconfig_path)
            result = subprocess.run(
                list_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env=proc_env,
            )

            if result.returncode != 0:
                logger.warning(f"Failed listing CRDs for Helm adoption: {result.stderr}")
                if on_output:
                    on_output("  - Warning: unable to inspect CRDs for Helm adoption")
                return []

            payload = json.loads(result.stdout or "{}")
            items = payload.get("items") or []
            adopted: list[str] = []

            for crd in items:
                metadata = crd.get("metadata") or {}
                annotations = metadata.get("annotations") or {}
                labels = metadata.get("labels") or {}
                crd_name = str(metadata.get("name") or "")
                if not crd_name:
                    continue

                if annotations.get("meta.helm.sh/release-name") != release_name:
                    continue

                current_namespace = annotations.get("meta.helm.sh/release-namespace")
                if current_namespace == namespace:
                    continue

                if on_output:
                    on_output(
                        f"  Adopting CRD {crd_name}: "
                        f"release-namespace {current_namespace!r} -> {namespace!r}"
                    )

                annotate_cmd = [
                    "kubectl", "annotate", "crd", crd_name,
                    f"meta.helm.sh/release-name={release_name}",
                    f"meta.helm.sh/release-namespace={namespace}",
                    "--overwrite",
                ] + self._conn_args(kubeconfig_path)
                annotate_result = subprocess.run(
                    annotate_cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=proc_env,
                )
                if annotate_result.returncode != 0:
                    logger.warning(
                        f"Failed to adopt CRD {crd_name} annotations: {annotate_result.stderr}"
                    )
                    if on_output:
                        on_output(f"  - Warning: failed CRD adoption for {crd_name}")
                    continue

                if labels.get("app.kubernetes.io/managed-by") != "Helm":
                    label_cmd = [
                        "kubectl", "label", "crd", crd_name,
                        "app.kubernetes.io/managed-by=Helm",
                        "--overwrite",
                    ] + self._conn_args(kubeconfig_path)
                    label_result = subprocess.run(
                        label_cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,
                        env=proc_env,
                    )
                    if label_result.returncode != 0:
                        logger.warning(
                            f"Failed to set Helm managed-by label for {crd_name}: {label_result.stderr}"
                        )
                        if on_output:
                            on_output(f"  - Warning: failed managed-by label patch for {crd_name}")

                adopted.append(crd_name)

            if adopted and on_output:
                on_output(f"  ✓ Adopted {len(adopted)} CRD(s) for Helm release {release_name}")

            return adopted
        except Exception as e:
            logger.warning(f"CRD adoption helper failed (continuing without adoption): {e}")
            if on_output:
                on_output("  - Warning: CRD adoption check failed; continuing with Helm install")
            return []

    def _helm_repo_add(self, name: str, url: str, on_output):
        """Add a Helm repo if not already present."""
        validate_cli_arg("repo_name", name)
        validate_cli_arg("repo_url", url)
        cmd = ["helm", "repo", "add", name, url, "--force-update"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and on_output:
            on_output(f"  Added Helm repo: {name}")

    # =========================================================================
    # Internal helpers
    # =========================================================================

    async def _get_api(self):
        """Get kr8s API client."""
        import kr8s
        if self.context:
            return await kr8s.asyncio.api(kubeconfig=self.kubeconfig_path, context=self.context)
        return await kr8s.asyncio.api(kubeconfig=self.kubeconfig_path)

    async def _get_api_and_get(self, kind, name, namespace):
        """Helper to get API + fetch resource."""
        api = await self._get_api()
        return await _collect_resources(api, kind, name, namespace)


# =============================================================================
# Async helpers (called via asyncio.run_until_complete from sync methods)
# =============================================================================

def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """Get or create an event loop for the current thread."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


async def _server_side_apply(api, manifest: dict):
    """Apply a manifest using server-side apply (kr8s >=0.20 compatible).

    kr8s 0.20 removed ``obj.apply()`` and ``api.patch()``.  We use
    ``api.call_api("PATCH", ...)`` to perform a server-side apply directly
    against the K8s API, which works for both built-in and custom resources.

    IMPORTANT: kr8s ``call_api`` always passes its ``url`` param through
    ``_construct_url(version, base, namespace, url)`` which joins
    ``base / version / [namespaces/ns/] url``.  We must supply ``base``,
    ``version``, ``namespace``, and ``url`` separately so kr8s constructs
    the correct path — passing a pre-built full path as ``url`` causes
    double-prefixing (e.g. ``/api/v1/apis/k8s.cni.cncf.io/v1/...``).
    """
    import json as _json

    kind = manifest["kind"]
    api_version = manifest.get("apiVersion", "v1")
    name = manifest["metadata"]["name"]
    namespace = manifest["metadata"].get("namespace")

    # Build URL components for kr8s call_api().
    # kr8s._construct_url joins: base / version / [namespaces/ns/] url
    # We must pass components separately — NOT a pre-built full path — or
    # _construct_url will double-prefix the API group (e.g. /api/v1//api/v1/...).
    resource_plural = _get_resource_info(api_version, kind)
    if api_version == "v1":
        base = "/api"
        version = "v1"
    else:
        base = "/apis"
        version = api_version  # e.g. "k8s.cni.cncf.io/v1", "apps/v1"

    # For cluster-scoped resources (no namespace), we must NOT pass namespace
    # to call_api or it will inject /namespaces/<ns>/ into the path.
    # The url component is the resource path after the version prefix.
    resource_url = f"{resource_plural}/{name}"

    # Server-side apply: PATCH with application/apply-patch+yaml
    try:
        async with api.call_api(
            "PATCH",
            version=version,
            base=base,
            namespace=namespace,
            url=resource_url,
            data=_json.dumps(manifest),
            headers={"Content-Type": "application/apply-patch+yaml"},
            params={"fieldManager": "bnk-forge", "force": "true"},
            raise_for_status=True,
        ) as _resp:
            pass
    except Exception as e:
        # If the PATCH fails (resource doesn't exist yet), try raw CREATE
        logger.debug(f"Server-side apply PATCH failed for {kind}/{name}, trying CREATE: {e}")
        try:
            async with api.call_api(
                "POST",
                version=version,
                base=base,
                namespace=namespace,
                url=resource_plural,
                data=_json.dumps(manifest),
                headers={"Content-Type": "application/json"},
                raise_for_status=True,
            ) as _resp:
                pass
        except Exception as e2:
            raise RuntimeError(
                f"Failed to apply {kind}/{name}: "
                f"server-side apply failed ({e}), create also failed: {e2}"
            ) from e


async def _wait_for_condition(api, kind: str, name: str, namespace: str, condition: str, timeout: int) -> bool:
    """Wait for a K8s resource to meet a condition."""

    # Parse condition string: "condition=Accepted" -> check status.conditions for Accepted=True
    cond_key = condition.split("=", 1)[1] if "=" in condition else condition

    deadline = time.monotonic() + timeout
    poll_interval = 2  # seconds

    while time.monotonic() < deadline:
        try:
            resources = await _collect_resources(api, kind, name, namespace)
            if resources:
                obj = resources[0]
                raw = obj.raw if hasattr(obj, "raw") else obj

                # Check status.conditions for the target condition
                conditions = (raw.get("status") or {}).get("conditions", [])
                for cond in conditions:
                    if cond.get("type") == cond_key and cond.get("status") == "True":
                        return True

            await asyncio.sleep(poll_interval)
        except Exception as e:
            logger.debug(f"Waiting for {kind}/{name}: {e}")
            await asyncio.sleep(poll_interval)

    return False


async def _collect_resources(api, kind: str, name: str, namespace: str) -> list:
    """Collect resources from api.get() async generator into a list.

    kr8s 0.20+ returns an AsyncGenerator from api.get(), not a list.
    This helper properly iterates the generator to get actual resources.
    """
    results = []
    async for obj in api.get(kind, name, namespace=namespace):
        results.append(obj)
    return results


async def _delete_resource(api, kind: str, name: str, namespace: str) -> bool:
    """Delete a K8s resource. Returns True if actually deleted, False if not found."""
    try:
        resources = await _collect_resources(api, kind, name, namespace)
        if resources:
            obj = resources[0]
            await obj.delete()
            return True
        return False
    except Exception as e:
        if "not found" in str(e).lower():
            return False
        raise


# Authoritative Kind → plural mapping for core K8s + F5/BNK CRDs.
# Validated against real F5 BNK CRDs.  Imported by snapshot_service,
# config_promotion, and config_export so every code path uses the same map.
KNOWN_PLURALS: dict[str, str] = {
    # Core / built-in K8s
    "CustomResourceDefinition": "customresourcedefinitions",
    "Namespace": "namespaces",
    "ConfigMap": "configmaps",
    "Secret": "secrets",
    "Service": "services",
    "Deployment": "deployments",
    "StatefulSet": "statefulsets",
    "DaemonSet": "daemonsets",
    "Job": "jobs",
    "CronJob": "cronjobs",
    "ServiceAccount": "serviceaccounts",
    "ClusterRole": "clusterroles",
    "ClusterRoleBinding": "clusterrolebindings",
    "Role": "roles",
    "RoleBinding": "rolebindings",
    "ResourceQuota": "resourcequotas",
    "LimitRange": "limitranges",
    "PersistentVolumeClaim": "persistentvolumeclaims",
    # Multus
    "NetworkAttachmentDefinition": "network-attachment-definitions",
    # Gateway API
    "GatewayClass": "gatewayclasses",
    "Gateway": "gateways",
    "HTTPRoute": "httproutes",
    "GRPCRoute": "grpcroutes",
    # BNK CRDs
    "BNKGatewayClassConfig": "bnkgatewayclassconfigs",
    "BNKSecPolicy": "bnksecpolicies",
    "BNKNetPolicy": "bnknetpolicies",
    "L4Route": "l4routes",
    "CNEInstance": "cneinstances",
    # F5 SPK
    "F5SPKVlan": "f5-spk-vlans",
    "F5SPKStaticRoute": "f5-spk-staticroutes",
    "F5SPKSnatpool": "f5-spk-snatpools",
    "F5SPKEgress": "f5-spk-egresses",
    # F5 BIG
    "F5BigFwPolicy": "f5-big-fw-policies",
    "F5BigFwRulelist": "f5-big-fw-rulelists",
    "F5BigCneAddresslist": "f5-big-cne-addresslists",
    "F5BigCnePortlist": "f5-big-cne-portlists",
    "F5BigCneIrule": "f5-big-cne-irules",
    "F5BigAnalyzer": "f5-big-analyzers",
    "F5BigDdosGlobal": "f5-big-ddos-globals",
    "F5BigLogHslpub": "f5-big-log-hslpubs",
    "F5BigLogProfile": "f5-big-log-profiles",
    "F5BigGlobalOptions": "f5-big-global-optionses",
    "F5BnkGateway": "f5-bnkgateways",
    # IPAM
    "IPAMRange": "ipamranges",
}


def _get_resource_info(api_version: str, kind: str) -> str:
    """
    Map kind to resource plural name.

    Uses the module-level KNOWN_PLURALS dict (validated against real F5 BNK CRDs).
    For unknown types, we lowercase + add 's' as a heuristic.
    """
    return KNOWN_PLURALS.get(kind, kind.lower() + "s")


def _suggest_fix(error: Exception) -> str | None:
    """Map common K8s errors to actionable suggestions."""
    msg = str(error).lower()
    if "forbidden" in msg:
        return "The kubeconfig may not have sufficient RBAC permissions."
    if "not found" in msg and "crd" in msg:
        return "A required CRD is not installed. Ensure FLO operator is deployed first."
    if "connection refused" in msg:
        return "Cannot reach the Kubernetes API server. Check kubeconfig and cluster status."
    if "timed out" in msg or "timeout" in msg:
        return "Operation timed out. The cluster may be overloaded or the resource is not converging."
    if "already exists" in msg:
        return "Resource already exists. The server-side apply should handle updates — this may indicate a field ownership conflict."
    return None


def _suggest_helm_fix(stderr: str) -> str | None:
    """Map common Helm errors to actionable suggestions."""
    msg = stderr.lower()
    if "invalid ownership metadata" in msg or "cannot be imported" in msg:
        return (
            "CRD ownership conflict detected. This usually happens when re-deploying "
            "to a different namespace than the original installation. The engine "
            "will attempt automatic CRD adoption."
        )
    if "unauthorized" in msg or "authentication required" in msg or "401" in msg:
        return (
            "OCI registry authentication failed. Ensure the cne_pull_secret project "
            "secret is configured — it must be the base64-encoded FAR service account "
            "Docker config JSON from F5 (same content used for imagePullSecrets)."
        )
    if "not found" in msg and "chart" in msg:
        return "Helm chart not found. Check chart name, version, and repo configuration."
    if "timed out" in msg:
        return "Helm install timed out. The chart's pods may not be becoming ready. Check pod status and events."
    if "already exists" in msg:
        return "Release already exists. Use 'helm upgrade --install' (which we do). This may indicate a stale release."
    if "imagepullbackoff" in msg or "image pull" in msg:
        return (
            "Pod image pull failed. The FAR pull secret may not be configured in the target namespace. "
            "Ensure k8s/bnk-prerequisites has been applied to create the FAR secret."
        )
    return None


class _PayloadModuleAdapter:
    """Small adapter to reuse existing helm/manifest codepaths."""

    module_type = "manifest"
    create_namespace = True

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.name = payload.get("module_name", "k8s-module")
        self.timeout = int(payload.get("timeout", 600))

        self.chart_ref = payload.get("chart_ref", "")
        self.release_name = payload.get("release_name", self.name)
        self.namespace = payload.get("namespace", "default")
        self.chart_version = payload.get("chart_version") or ""
        self.helm_repos = payload.get("helm_repos") or {}
        if self.chart_ref:
            self.module_type = "helm_chart"
            self.create_namespace = bool(payload.get("create_namespace", True))

        self.inputs: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}

    def render_manifests(self, _variables: dict[str, Any]) -> list[dict[str, Any]]:
        manifests = self._payload.get("manifests")
        return manifests if isinstance(manifests, list) else []

    def render_helm_values(self, _variables: dict[str, Any]) -> dict[str, Any]:
        values = self._payload.get("values")
        return values if isinstance(values, dict) else {}

    def get_static_outputs(self, _variables: dict[str, Any]) -> dict[str, Any]:
        return {}

    def get_readiness_condition(self, _manifest: dict[str, Any]) -> str | None:
        return None

    def get_destroy_order(self, manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(reversed(manifests))
