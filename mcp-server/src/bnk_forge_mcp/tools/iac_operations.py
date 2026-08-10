"""
Infrastructure-as-Code (IaC) operations tools.

Projects, modules, stacks, and deployment operations.
Maps to: routes/projects.py, routes/project_modules.py, routes/project_execution.py,
         routes/project_orchestration.py, routes/project_deployments.py, routes/stacks.py
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register IaC operations tools with the MCP server."""

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_projects() -> str:
        """List all projects in BNK-Forge.

        Projects are the top-level organizational unit containing modules,
        clusters, and deployment history.
        """
        result = await client.get("/api/projects")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_project(project_id: int) -> str:
        """Get detailed information about a project.

        Includes modules, execution engine, variables, and deployment status.

        Args:
            project_id: The project ID
        """
        result = await client.get(f"/api/projects/{project_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def update_project(
        project_id: int,
        name: str = "",
        description: str = "",
        project_type: str = "",
        cloud_provider: str = "",
        environment: str = "",
        region: str = "",
        color: str = "",
        icon: str = "",
        credential_template_id: int = 0,
    ) -> str:
        """Update an existing project's mutable fields.

        Only non-empty fields are sent (empty strings / zero ints skipped), so this is
        safe to call with just the field(s) you want to change.

        Args:
            project_id: Project to update.
            name: New name (1-200 chars).
            description: New description.
            project_type: e.g. "cloud-aws", "cloud-azure", "kubernetes".
            cloud_provider: "aws", "azure", "gcp", "ibm", "on-prem", "none".
            environment: "dev" / "staging" / "prod" / etc.
            region: Cloud region.
            color: UI color theme.
            icon: UI icon identifier.
            credential_template_id: Bind or re-bind a credential template to this
                project. Pass the template id (e.g. 1). Pass 0 to leave unchanged.
        """
        body: dict[str, Any] = {}
        for k, v in (
            ("name", name), ("description", description),
            ("project_type", project_type), ("cloud_provider", cloud_provider),
            ("environment", environment), ("region", region),
            ("color", color), ("icon", icon),
        ):
            if v:
                body[k] = v
        if credential_template_id:
            body["credential_template_id"] = credential_template_id
        result = await client.put(f"/api/projects/{project_id}", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_project(project_id: int, force: bool = False) -> str:
        """Delete a project.

        Refuses by default if the project is active or the last project.
        Pass `force=True` to override (use with caution — irreversible).

        Args:
            project_id: Project to delete.
            force: Bypass safety checks (active/only-project).
        """
        result = await client.delete(
            f"/api/projects/{project_id}",
            params={"force": force},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def activate_project(project_id: int) -> str:
        """Mark a project as the active project.

        UI surfaces the active project prominently; some operations default
        to it when project_id is omitted.

        Args:
            project_id: Project to activate.
        """
        result = await client.post(f"/api/projects/{project_id}/activate")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_project(
        name: str,
        description: str = "",
        project_type: str = "",
        cloud_provider: str = "",
        environment: str = "dev",
        region: str = "",
        backend_type: str = "local",
        color: str = "#a8337a",
        icon: str = "",
        credential_template_id: int = 0,
        target_platform_profile: str = "",
    ) -> str:
        """Create a new project in BNK-Forge.

        A project is the top-level container for modules, clusters, and
        deployment history. The current user is set as the owner. This is the
        entry point for external tools (e.g. awsbnkctl) that need to register
        a deployment without using the REST API directly.

        Args:
            name: Project name (1-200 chars). For AWS-typed projects, must
                use AWS-safe characters only (letters, digits, +=,.@_-).
            description: Optional human-readable description.
            project_type: Project type, e.g. "cloud-aws", "cloud-azure",
                "cloud-gcp", "cloud-ibm", "kubernetes". Empty for unset.
            cloud_provider: Cloud provider: "aws", "azure", "gcp", "ibm",
                "on-prem", "none". Empty for unset. Drives region validation.
            environment: Environment label (default "dev"; e.g. "staging", "prod").
            region: AWS/cloud region or on-prem location label. Required for
                cloud_provider="aws" or project_type starting with "cloud-aws".
            backend_type: Terraform backend type (default "local").
            color: UI color theme (default forge purple).
            icon: UI icon identifier (empty for default).
            credential_template_id: Optional credential template to bind to
                this project. Pass 0 to leave unset.
            target_platform_profile: Optional declared target platform profile.
        """
        body: dict[str, Any] = {
            "name": name,
            "environment": environment,
            "backend_type": backend_type,
            "color": color,
            "icon": icon,
        }
        if description:
            body["description"] = description
        if project_type:
            body["project_type"] = project_type
        if cloud_provider:
            body["cloud_provider"] = cloud_provider
        if region:
            body["region"] = region
        if credential_template_id:
            body["credential_template_id"] = credential_template_id
        if target_platform_profile:
            body["target_platform_profile"] = target_platform_profile
        result = await client.post("/api/projects", json=body)
        # Normalize to {success, project, message} envelope.
        # Breaking change vs prior flat shape — coordinated with awsbnkctl client.
        if not isinstance(result, dict):
            return json.dumps({"success": False, "raw": result}, indent=2)
        if result.get("ok") is False and "error" in result:
            # Structured MCP error envelope from backend — pass through as-is.
            return json.dumps(result, indent=2)
        project_entity: dict = {}
        success = bool(result.get("success", True))
        message = result.get("message")
        # Map flat project_id → project.id; collect remaining non-meta keys.
        skip_keys = {"success", "message"}
        if "project_id" in result:
            project_entity["id"] = result["project_id"]
            skip_keys.add("project_id")
        for k, v in result.items():
            if k not in skip_keys:
                project_entity[k] = v
        envelope: dict = {"success": success, "project": project_entity}
        if message is not None:
            envelope["message"] = message
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    async def project_dependency_graph(project_id: int) -> str:
        """Get the dependency graph for a project's modules.

        Shows which modules depend on which, and the execution order.

        Args:
            project_id: The project ID
        """
        result = await client.get(f"/api/project-modules/project/{project_id}/dependency-graph")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_project_modules(project_id: int) -> str:
        """List all modules in a project.

        Args:
            project_id: The project ID
        """
        result = await client.get(f"/api/project-modules/project/{project_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_project_module(
        project_id: int,
        module_library_id: int,
        path_in_project: str,
        variable_overrides: dict[str, Any] | None = None,
        deployment_order: int = 0,
    ) -> str:
        """Add a module from the catalog to a project.

        Use this to register each IaC module a deployment depends on (one call
        per module). Pair with `list_module_catalog` to discover valid
        `module_library_id` values. After all modules are added, call
        `detect_eks_clusters` (for EKS deployments) or seed clusters
        directly via `create_cluster`.

        Args:
            project_id: Target project ID.
            module_library_id: Catalog module ID (positive integer; from `list_module_catalog`).
            path_in_project: Filesystem-style path the module will occupy
                inside the project (e.g. "infra/vpc", "eks/cluster"). Must
                match `^[a-zA-Z0-9/_-]+$` and may not contain ".." or start
                with "/".
            variable_overrides: Optional dict of variable name → value
                overrides. Variable names must be lowercase snake_case
                (`^[a-z_][a-z0-9_]*$`). Values are scanned for shell
                metacharacters; avoid `; | & ` $ ( )`.
            deployment_order: Integer 0–9999 controlling apply order
                (lower runs first). Default 0.
        """
        body: dict[str, Any] = {
            "module_library_id": module_library_id,
            "path_in_project": path_in_project,
            "variable_overrides": variable_overrides or {},
            "deployment_order": deployment_order,
        }
        result = await client.post(
            f"/api/project-modules/project/{project_id}/add",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def update_project_module(
        module_id: int,
        variable_overrides: dict[str, Any] | None = None,
        deployment_order: int | None = None,
        enabled: bool | None = None,
    ) -> str:
        """Update a project module's configuration.

        Args:
            module_id: Module to update.
            variable_overrides: Replace the module's variable overrides
                (None leaves unchanged; pass empty dict to clear).
            deployment_order: New deployment order (None leaves unchanged).
            enabled: True/False toggles whether this module participates
                in `project_deploy_all` runs (None leaves unchanged).
        """
        body: dict[str, Any] = {}
        if variable_overrides is not None:
            body["variable_overrides"] = variable_overrides
        if deployment_order is not None:
            body["deployment_order"] = deployment_order
        if enabled is not None:
            body["enabled"] = enabled
        result = await client.put(f"/api/project-modules/{module_id}", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def change_project_module_version(module_id: int, target_version: str) -> str:
        """Re-pin a project module to a different catalog version of its module path.

        The module catalog can hold multiple immutable versions of the same
        module path (see `list_module_catalog` — entries share a `path` and
        differ by `version`; `is_latest` marks the newest). A project module is
        pinned to the exact version it was created with; this explicit action
        re-pins it. Nothing is re-deployed by this call — the new version's
        manifest takes effect on the module's next plan/apply/destroy.

        Args:
            module_id: The project module ID (from `list_project_modules`).
            target_version: Catalog version to pin (e.g. "1.20.0"). On an
                unknown version the error lists the available versions.
        """
        result = await client.post(
            f"/api/project-modules/{module_id}/change-version",
            json={"target_version": target_version},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_project_module(module_id: int) -> str:
        """Remove a module from a project.

        Does NOT run `terraform destroy` — for that, call `project_destroy`
        first. This only removes forge's record of the module; any cloud
        resources it created will be orphaned.

        Args:
            module_id: Module to remove.
        """
        result = await client.delete(f"/api/project-modules/{module_id}")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Module Execution — Init / Deploy / Cancel / Retry
    # (plan / apply / destroy already exist; init is the missing first step)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def project_module_init(module_id: int) -> str:
        """Run 'terraform init' for a project module (the missing first step).

        Required before plan/apply on a newly-added module or after the
        backend config changes. Idempotent — safe to call on already-init'd
        modules.

        Args:
            module_id: Module to init.
        """
        result = await client.post(f"/api/project-modules/{module_id}/init")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_module_deploy(module_id: int) -> str:
        """One-shot init + plan + apply for a module.

        Convenience verb for "just make it happen" — wraps the three-step
        flow into a single async operation. Returns a task_id; poll via
        `get_task` for completion.

        Args:
            module_id: Module to deploy.
        """
        result = await client.post(f"/api/project-modules/{module_id}/deploy")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_module_cancel(module_id: int) -> str:
        """Cancel an in-progress plan / apply / destroy for a module.

        Cooperative cancel — sends a stop signal to the running task. The
        task may take a few seconds to settle; check `get_module_status`
        afterward.

        Args:
            module_id: Module whose operation to cancel.
        """
        result = await client.post(f"/api/project-modules/{module_id}/cancel")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_module_retry(module_id: int) -> str:
        """Retry the most recent failed apply/destroy for a module.

        Reuses the same parameters as the original attempt. Use this after
        a transient error (network blip, AWS API throttling) when you don't
        want to re-plan from scratch.

        Args:
            module_id: Module to retry.
        """
        result = await client.post(f"/api/project-modules/{module_id}/retry")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Module Diagnostics — Logs / State / Recovery
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_module_logs(module_id: int) -> str:
        """Fetch the operation logs for a module's most recent run.

        Critical for debugging: `project_apply` / `project_destroy` return
        a task_id but no inline output — the actual `terraform` / `tofu`
        stdout/stderr is here.

        Args:
            module_id: Module to fetch logs for.
        """
        result = await client.get(f"/api/project-modules/{module_id}/logs")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_module_state_info(module_id: int) -> str:
        """Get summary information about a module's Terraform state.

        Returns resource count, state version, last-modified timestamp,
        backend type. Use this to confirm state has been adopted / is
        non-empty before running `project_destroy`.

        Args:
            module_id: Module to inspect.
        """
        result = await client.get(f"/api/project-modules/{module_id}/state-info")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def recover_module_state(module_id: int) -> str:
        """Attempt to recover a module from a stuck or corrupted state.

        Runs a backend-specific recovery routine: unlock stale state locks,
        re-import drifted resources, repair the workspace directory. Use
        this when a module is wedged in `applying` / `destroying` past the
        timeout. Idempotent.

        Args:
            module_id: Module to recover.
        """
        result = await client.post(f"/api/project-modules/{module_id}/recover-state")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_module_state_history(module_id: int, limit: int = 50) -> str:
        """Return the audit log of status transitions for a project module.

        Use this to diagnose stuck or misbehaving deploys. The response is
        an ordered timeline of every status change for the module, including
        the task that drove it, the lock fence token (when under-lock), and
        the human-readable reason. Most-recent transition first.

        Each entry has: from_status, to_status, task_id, fence_token, reason, at.

        Phase 2 of the locking RFC (memory: rfc_robust_module_locking.md).

        Args:
            module_id: The project module ID.
            limit: Max entries to return (default 50, max 500).
        """
        result = await client.get(
            f"/api/project-modules/{module_id}/state-history",
            params={"limit": limit},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def list_module_catalog() -> str:
        """List available modules in the module catalog.

        Shows all built-in and user modules available for adding to projects.
        """
        result = await client.get("/api/module-library")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Execution (Plan / Apply / Destroy)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def project_plan(module_id: int) -> str:
        """Run 'plan' for a module (preview changes without applying).

        Equivalent to 'terraform plan'. Shows what would be created, modified, or destroyed.

        Args:
            module_id: The project module ID
        """
        result = await client.post(f"/api/project-modules/{module_id}/plan")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_apply(module_id: int) -> str:
        """Apply a module (deploy infrastructure changes).

        Equivalent to 'terraform apply'. Creates or modifies resources.
        This is an async operation — monitor via deployment history.

        Args:
            module_id: The project module ID
        """
        result = await client.post(
            f"/api/project-modules/{module_id}/apply",
            json={"auto_approve": False},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_destroy(module_id: int) -> str:
        """Destroy a module's infrastructure.

        Equivalent to 'terraform destroy'. Removes all resources created by this module.

        Args:
            module_id: The project module ID
        """
        result = await client.post(
            f"/api/project-modules/{module_id}/destroy",
            json={"auto_approve": False},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Parallel Orchestration
    # ------------------------------------------------------------------

    @mcp.tool()
    async def project_deploy_all(project_id: int) -> str:
        """Deploy all modules in a project in dependency order.

        Runs plan+apply for all modules, respecting dependency order.
        Independent modules run in parallel.

        Args:
            project_id: The project ID
        """
        result = await client.post(
            f"/api/projects/{project_id}/deploy-all",
            json={"parallel": True},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def project_destroy_all(project_id: int) -> str:
        """Destroy all modules in a project (reverse dependency order).

        Args:
            project_id: The project ID
        """
        result = await client.post(
            f"/api/projects/{project_id}/destroy-all",
            json={"parallel": True},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Deployment History
    # ------------------------------------------------------------------

    @mcp.tool()
    async def deployment_history(project_id: int, limit: int = 20) -> str:
        """Get deployment history for a project.

        Shows all plan, apply, and destroy operations with timestamps, status, and details.

        Args:
            project_id: The project ID
            limit: Maximum number of entries (default 20)
        """
        result = await client.get(f"/api/project-modules/project/{project_id}/deployments", params={"limit": limit})
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Stacks
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_stacks() -> str:
        """List available deployment stack templates.

        Stacks are pre-configured collections of modules for common deployment patterns.
        """
        result = await client.get("/api/stacks/templates")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_stack(slug: str) -> str:
        """Get detailed information about a deployment stack template.

        Args:
            slug: The stack template slug (e.g. "bnk-basic", "bnk-full")
        """
        result = await client.get(f"/api/stacks/templates/{slug}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def deploy_stack(project_id: int, stack_id: int) -> str:
        """Deploy a stack instance in a project.

        Instantiates all modules defined in the stack template.

        Args:
            project_id: Target project ID
            stack_id: The stack instance ID (create one first via the UI or API)
        """
        result = await client.post(f"/api/stacks/projects/{project_id}/stacks/{stack_id}/deploy")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def stack_template_preview(slug: str) -> str:
        """Preview the modules a stack template would instantiate.

        Use this before `create_stack_instance` to see exactly what
        will be created. Returns the module list with paths and variables.

        Args:
            slug: Stack template slug (from `list_stacks`).
        """
        result = await client.get(f"/api/stacks/templates/{slug}/preview")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def stack_template_required_inputs(slug: str) -> str:
        """List the required variables for a stack template.

        Returns variable name, type, description, and default (if any).
        Use this to know what `variables` to pass to `create_stack_instance`.

        Args:
            slug: Stack template slug.
        """
        result = await client.get(f"/api/stacks/templates/{slug}/required-inputs")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def stack_template_check_prerequisites(slug: str) -> str:
        """Check whether a stack template's prerequisites are satisfied.

        Examples of prereqs: existing cluster, AWS credentials configured,
        SSH credential available. Returns a per-prereq pass/fail.

        Args:
            slug: Stack template slug.
        """
        result = await client.get(f"/api/stacks/templates/{slug}/check-prerequisites")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_stack_instance(
        project_id: int,
        template_id: int,
        name: str,
        variables: dict[str, Any] | None = None,
    ) -> str:
        """Create a stack instance under a project (required before `deploy_stack`).

        This is the missing first step — the catalog previously had
        `deploy_stack` but no way to create the instance to deploy. After
        creating, call `deploy_stack` to instantiate the underlying modules.

        Args:
            project_id: Target project.
            template_id: Stack template ID (from `list_stacks`).
            name: Display name for this instance (1-255 chars, unique per project).
            variables: Optional variable values, keyed by template variable
                name. CIDR-typed variables are validated server-side.
        """
        body: dict[str, Any] = {"template_id": template_id, "name": name}
        if variables is not None:
            body["variables"] = variables
        result = await client.post(
            f"/api/stacks/projects/{project_id}/stacks",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_stack_instance(project_id: int, stack_id: int) -> str:
        """Get detail for a single stack instance — including its deployed modules.

        Args:
            project_id: Owning project.
            stack_id: Stack instance ID.
        """
        result = await client.get(f"/api/stacks/projects/{project_id}/stacks/{stack_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def stack_instance_status(project_id: int, stack_id: int) -> str:
        """Get the rollout status of a stack instance.

        Returns current step, total steps, error message (if any), and
        the deploy timeline. Use this to poll a `deploy_stack` until
        completion.

        Args:
            project_id: Owning project.
            stack_id: Stack instance ID.
        """
        result = await client.get(
            f"/api/stacks/projects/{project_id}/stacks/{stack_id}/status",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_stack_instance(project_id: int, stack_id: int) -> str:
        """Delete a stack instance.

        Removes the stack record and unlinks (does not destroy) the
        modules it created. Run `project_destroy` on each module first
        if you want the cloud resources gone.

        Args:
            project_id: Owning project.
            stack_id: Stack instance ID.
        """
        result = await client.delete(
            f"/api/stacks/projects/{project_id}/stacks/{stack_id}",
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Task Tracking (apply/destroy/deploy return task_ids — these track them)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def get_task(task_id: str) -> str:
        """Get the current state of an async task.

        Use this to poll any operation that returns a task_id:
        `project_apply`, `project_destroy`, `project_module_deploy`,
        `bnk_upgrade_execute`, etc. Returns status, progress, result,
        and (for failed tasks) the error detail.

        Args:
            task_id: The task UUID returned by the originating operation.
        """
        result = await client.get(f"/api/tasks/{task_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def cancel_task(task_id: str) -> str:
        """Request cancellation of an in-progress async task.

        Cooperative cancel — sends a stop signal; the task may take a few
        seconds to settle. Poll `get_task` to confirm.

        Args:
            task_id: The task UUID to cancel.
        """
        result = await client.post(f"/api/tasks/{task_id}/cancel")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Project Secrets
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_project_secrets(project_id: int) -> str:
        """List the secrets stored under a project.

        Returns metadata only (name, type, target module/variable) — the
        secret value or file content is never returned.

        Args:
            project_id: Project to list secrets for.
        """
        result = await client.get(f"/api/projects/{project_id}/secrets")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_project_secret_value(
        project_id: int,
        name: str,
        value: str,
        description: str = "",
        target_module_path: str = "",
        target_variable_name: str = "",
    ) -> str:
        """Create a value secret (API key, password, etc.) under a project.

        The value is encrypted at rest. At deploy time it's injected as a
        Terraform variable (when `target_variable_name` is set) or as an
        environment variable.

        Args:
            project_id: Target project.
            name: Secret name (unique per project).
            value: The secret material.
            description: Optional free-text description.
            target_module_path: Optional — bind the secret to a specific
                module path (e.g. "infra/vpc"). Empty applies project-wide.
            target_variable_name: Optional Terraform variable name to
                inject the secret as (must be lowercase snake_case).
        """
        body: dict[str, Any] = {"name": name, "value": value}
        if description:
            body["description"] = description
        if target_module_path:
            body["target_module_path"] = target_module_path
        if target_variable_name:
            body["target_variable_name"] = target_variable_name
        result = await client.post(
            f"/api/projects/{project_id}/secrets/value",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_project_secret(project_id: int, secret_id: int) -> str:
        """Delete a project secret.

        Removes the encrypted material immediately. Any module run that
        depended on this secret will fail until it is recreated.

        Args:
            project_id: Owning project.
            secret_id: Secret ID to delete.
        """
        result = await client.delete(
            f"/api/projects/{project_id}/secrets/{secret_id}",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def list_required_project_secrets(project_id: int) -> str:
        """List the secret names a project's modules expect.

        Returns the canonical "required" set computed from the project's
        modules (well-known secrets like `cne_pull_secret`, `jwt_token`,
        plus any module-declared sensitive variables). Use this to know
        what `create_project_secret_value` calls are needed before deploy.

        Args:
            project_id: Project to inspect.
        """
        result = await client.get(f"/api/projects/{project_id}/secrets/required")
        return json.dumps(result, indent=2)
