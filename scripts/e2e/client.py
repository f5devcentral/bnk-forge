"""Thin REST client for bnk-forge.

One method per endpoint the e2e steps actually call. Each returns
the parsed JSON body (or `None` on 204). Errors raise
`BnkForgeApiError` with the backend's `detail`/`message` extracted —
the same surface the React frontend gets.

Self-signed certs on the local server are accepted (`verify=False`)
since the script is meant to run on a box with line-of-sight to
bnk-forge, not from the public internet.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests
import urllib3
from requests import Response

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("e2e.client")


class BnkForgeApiError(Exception):
    def __init__(self, status: int, message: str, payload: Any = None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message
        self.payload = payload


class BnkForgeClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        self.timeout = timeout
        self._token: str | None = None

    # ── Auth ───────────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> dict[str, Any]:
        body = self._post(
            "/api/auth/login",
            {"username": username, "password": password},
            authed=False,
        )
        token = body.get("token")
        if not isinstance(token, str):
            raise BnkForgeApiError(200, "login response missing 'token'", body)
        self._token = token
        return body

    def change_password(self, current: str, new: str) -> dict[str, Any]:
        """POST /api/auth/change-password — clears `must_change_password`
        on a freshly-seeded admin so the rest of the harness can run."""
        return self._post(
            "/api/auth/change-password",
            {"current_password": current, "new_password": new},
        )

    # ── SSH credentials ────────────────────────────────────────────────────

    def list_ssh_credentials(self) -> list[dict[str, Any]]:
        return self._get("/api/ssh-credentials") or []

    def create_ssh_credential(
        self,
        *,
        name: str,
        host: str,
        port: int,
        username: str,
        private_key: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        return self._post(
            "/api/ssh-credentials",
            {
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "auth_type": "key",
                "private_key": private_key,
                "description": description,
            },
        )

    # ── Projects ───────────────────────────────────────────────────────────

    def create_project(
        self,
        *,
        name: str,
        description: str | None,
        project_type: str,
    ) -> dict[str, Any]:
        return self._post(
            "/api/projects",
            {
                "name": name,
                "description": description,
                "project_type": project_type,
            },
        )

    def update_project(
        self, project_id: int, **fields: Any,
    ) -> dict[str, Any]:
        return self._put(f"/api/projects/{project_id}", fields)

    # ── Project DPU settings ───────────────────────────────────────────────

    def get_dpu_settings(self, project_id: int) -> dict[str, Any]:
        return self._get(f"/api/projects/{project_id}/dpu-settings")

    def patch_dpu_settings(
        self, project_id: int, **fields: Any,
    ) -> dict[str, Any]:
        return self._patch(
            f"/api/projects/{project_id}/dpu-settings", fields,
        )

    # ── BFB images + bf.conf templates ─────────────────────────────────────

    def list_bfb_images(self) -> list[dict[str, Any]]:
        # Returns a flat list (not wrapped) — the route is unwrapped:
        # `response_model=list[BluefieldSoftwareImageResponse]`.
        body = self._get("/api/bluefield-images")
        return body if isinstance(body, list) else []

    def list_bf_templates(self) -> list[dict[str, Any]]:
        body = self._get("/api/bf-conf-templates")
        # Route returns `BfConfTemplateListResponse{templates: [...]}`.
        if isinstance(body, dict) and "templates" in body:
            return body["templates"]
        return body if isinstance(body, list) else []

    # ── Discovery ──────────────────────────────────────────────────────────

    def trigger_discovery(
        self,
        project_id: int,
        ips: list[str],
        *,
        ssh_credential_id: int | None = None,
        ssh_username: str | None = None,
        ssh_private_key: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "nodes": [{"ip_address": ip} for ip in ips],
            "ssh_port": 22,
        }
        if ssh_credential_id is not None:
            body["ssh_credential_id"] = ssh_credential_id
        if ssh_username:
            body["ssh_username"] = ssh_username
            body["ssh_auth_type"] = "key"
        if ssh_private_key:
            body["ssh_private_key"] = ssh_private_key
        return self._post(
            f"/api/projects/{project_id}/discovery", body,
        )

    def get_discovery_job(self, project_id: int, job_id: int) -> dict[str, Any]:
        return self._get(f"/api/projects/{project_id}/discovery/{job_id}")

    def register_all_hosts(self, project_id: int, job_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/discovery/{job_id}/register-all-hosts",
            {},
        )

    def register_node_as_dpu(
        self,
        project_id: int,
        node_id: int,
        *,
        bmc_ip: str | None = None,
        bmc_username: str | None = None,
        bmc_password: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """POST register-as-dpu — backend dispatches by detection flags
        (is_dpu_host → in-band rows per pci_address; is_dpu_bmc → BMC row;
        is_dpu_node → BMC row but bmc_ip required)."""
        body: dict[str, Any] = {}
        if bmc_ip is not None:
            body["bmc_ip"] = bmc_ip
        if bmc_username is not None:
            body["bmc_username"] = bmc_username
        if bmc_password is not None:
            body["bmc_password"] = bmc_password
        if name is not None:
            body["name"] = name
        return self._post(
            f"/api/projects/{project_id}/discovery/nodes/{node_id}/register-as-dpu",
            body,
        )

    # ── DPUs ───────────────────────────────────────────────────────────────

    def list_dpus(self, project_id: int) -> list[dict[str, Any]]:
        return (self._get(f"/api/projects/{project_id}/dpus") or {}).get(
            "dpus", [],
        )

    def discover_all_dpus(self, project_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/discover-all", {},
        )

    def probe_dpu_os_all(self, project_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/probe-dpu-os-all", {},
        )

    def auto_assign_vlan_ips(self, project_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/auto-assign-vlan-ips", {},
        )

    def flash_dpu(self, project_id: int, dpu_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/{dpu_id}/flash", {},
        )

    def flash_all_dpus(self, project_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/flash-all", {},
        )

    def run_connectivity_matrix(self, project_id: int) -> dict[str, Any]:
        return self._post(
            f"/api/projects/{project_id}/dpus/connectivity-matrix/run", {},
        )

    def get_connectivity_matrix(self, project_id: int) -> dict[str, Any]:
        return self._get(
            f"/api/projects/{project_id}/dpus/connectivity-matrix",
        )

    # ── Cloud blueprint ────────────────────────────────────────────────────

    def list_blueprint_releases(self) -> list[dict[str, Any]]:
        """GET /api/blueprint-catalog/releases — all releases."""
        body = self._get("/api/blueprint-catalog/releases")
        return body if isinstance(body, list) else []

    def get_release(self, release_id: int) -> dict[str, Any]:
        """GET /api/blueprint-catalog/releases/{id}."""
        return self._get(f"/api/blueprint-catalog/releases/{release_id}")

    def get_release_required_inputs(self, release_id: int) -> dict[str, Any]:
        """GET /api/stacks/releases/{id}/required-inputs."""
        return self._get(f"/api/stacks/releases/{release_id}/required-inputs")

    def create_project_from_release(
        self,
        release_id: int,
        *,
        cloud_provider: str | None,
        region: str | None,
        credential_template_id: int | None,
        variables: dict[str, Any],
        name: str,
    ) -> dict[str, Any]:
        """POST /api/stacks/releases/{id}/projects.

        Response shape: ImportedBlueprintProjectCreateResponse
          { success, project_id, project_name, blueprint_release_id,
            module_count, created_module_ids, message }
        """
        return self._post(
            f"/api/stacks/releases/{release_id}/projects",
            {
                "name": name,
                "cloud_provider": cloud_provider,
                "region": region,
                "credential_template_id": credential_template_id,
                "variables": variables,
            },
        )

    def deploy_all(self, project_id: int) -> dict[str, Any]:
        """POST /api/projects/{project_id}/deploy-all.

        Response carries `orchestrator_task_id` (Celery UUID, NOT the poll id).
        To poll progress, list parallel-executions and capture the integer `id`.
        """
        return self._post(f"/api/projects/{project_id}/deploy-all", {})

    def get_parallel_executions(self, project_id: int) -> list[dict[str, Any]]:
        """GET /api/projects/{project_id}/parallel-executions.

        Returns a list of recent execution records (newest first).
        Each record has fields: id (int), action, status, current_layer,
        total_layers, started_at, completed_at, duration_seconds,
        successful_modules (int count), failed_modules (int count), triggered_by.
        Status values: 'pending' | 'in_progress' | 'completed' | 'failed'.
        """
        body = self._get(f"/api/projects/{project_id}/parallel-executions")
        return body if isinstance(body, list) else []

    def get_parallel_execution_status(
        self, project_id: int, exec_id: int,
    ) -> dict[str, Any]:
        """GET /api/projects/{project_id}/parallel-executions/{exec_id}.

        exec_id is an integer DB row id (NOT the Celery orchestrator_task_id).
        Returns full execution record including: id, project_id, action,
        status, current_layer, total_layers, layers_data, execution_mode,
        successful_modules (list), failed_modules (list), skipped_modules (list),
        error_message, started_at, completed_at, duration_seconds,
        estimated_duration_minutes, progress_percent, triggered_by, created_at.
        Status values: 'pending' | 'in_progress' | 'completed' | 'failed'.
        """
        return self._get(
            f"/api/projects/{project_id}/parallel-executions/{exec_id}",
        )

    def destroy_all(self, project_id: int) -> dict[str, Any]:
        """POST /api/projects/{project_id}/destroy-all.

        Response carries `orchestrator_task_id` (Celery UUID, NOT the poll id).
        To poll progress, list parallel-executions and capture the integer `id`.
        """
        return self._post(f"/api/projects/{project_id}/destroy-all", {})

    def delete_stack(
        self, project_id: int, stack_id: int, force: bool = False,
    ) -> Any:
        """DELETE /api/stacks/projects/{project_id}/stacks/{stack_id}."""
        path = f"/api/stacks/projects/{project_id}/stacks/{stack_id}"
        if force:
            path += "?force=true"
        return self._request("DELETE", path)

    def delete_project(self, project_id: int) -> Any:
        """DELETE /api/projects/{project_id}."""
        return self._request("DELETE", f"/api/projects/{project_id}")

    def get_bnk_health(self, cluster_id: int) -> dict[str, Any]:
        """GET /api/k8s/clusters/{cluster_id}/f5bnk/health.

        Returns health analysis with `overall`, `platform`, `dataPlane`, `counts`.
        """
        return self._get(f"/api/k8s/clusters/{cluster_id}/f5bnk/health")

    def get_license_status(self, cluster_id: int) -> dict[str, Any]:
        """GET /api/licensing/{cluster_id}/status.

        Relevant field: `license_state` (string, e.g. 'Active').
        """
        return self._get(f"/api/licensing/{cluster_id}/status")

    def activate_license(self, cluster_id: int, jwt: str) -> dict[str, Any]:
        """POST /api/licensing/{cluster_id}/activate — raw JWT string."""
        return self._post(
            f"/api/licensing/{cluster_id}/activate",
            {"jwt": jwt},
        )

    def get_cwc_status(self, cluster_id: int) -> dict[str, Any]:
        """GET /api/licensing/{cluster_id}/cwc-status."""
        return self._get(f"/api/licensing/{cluster_id}/cwc-status")

    # ── HTTP helpers ───────────────────────────────────────────────────────

    def _headers(self, *, authed: bool = True) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if authed and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _request(
        self, method: str, path: str, json: Any = None, *, authed: bool = True,
    ) -> Any:
        url = f"{self.base_url}{path}"
        attempts = 3
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                resp: Response = self.session.request(
                    method, url, json=json, timeout=self.timeout,
                    headers=self._headers(authed=authed),
                )
                return _parse_response(resp)
            except requests.RequestException as exc:
                last_exc = exc
                if i + 1 == attempts:
                    raise
                time.sleep(1.5 ** i)
        raise RuntimeError(f"unreachable: {last_exc}")

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, json: Any, *, authed: bool = True) -> Any:
        return self._request("POST", path, json, authed=authed)

    def _put(self, path: str, json: Any) -> Any:
        return self._request("PUT", path, json)

    def _patch(self, path: str, json: Any) -> Any:
        return self._request("PATCH", path, json)


def _parse_response(resp: Response) -> Any:
    if resp.status_code == 204:
        return None
    body: Any
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    if 200 <= resp.status_code < 300:
        return body
    # FastAPI surfaces the structured error under `error.message` /
    # `detail` / `message`. Pull the most specific one we can find.
    message = "unknown error"
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else {}
        message = (
            err.get("message")
            or body.get("detail")
            or body.get("message")
            or str(body)
        )
    elif isinstance(body, str) and body:
        message = body
    raise BnkForgeApiError(resp.status_code, str(message), body)
