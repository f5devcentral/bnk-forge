"""
Tests for command_handlers.py — K8s operations executed locally on the cluster.

Covers:
  - apply_manifests: empty list, single manifest, multiple, namespace defaulting, errors, wait_for_ready
  - destroy_manifests: empty, single, reverse order, ignore_not_found, errors
  - install_helm: validation, missing args, OCI registry login, success, failure
  - uninstall_helm: validation, success, not-found, failure
  - scan_cluster: full scan with CRDs, FLO, TMM, error handling
  - get_health: full health report, FLO/TMM/gateways/routes, errors
  - get_resources: list pods, label selectors, errors
  - get_pod_logs: success, pod not found, missing pod_name
  - _validate_cli_arg / _validate_helm_timeout: security validation
"""

import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

operator_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if operator_root not in sys.path:
    sys.path.insert(0, operator_root)

# Import helpers from conftest — pytest makes them available as fixtures,
# but we also need them as regular functions in test code
from tests.conftest import (
    FakeK8sResource, make_crd, make_deployment, make_gateway,
    make_node, make_pod, make_route,
)


# =========================================================================
# Input Validation (security-critical)
# =========================================================================


class TestValidateCliArg:
    """Test _validate_cli_arg rejects CLI injection attempts."""

    def test_rejects_flag_prefix(self):
        from command_handlers import _validate_cli_arg
        with pytest.raises(ValueError, match="cannot start with '-'"):
            _validate_cli_arg("release_name", "--malicious")

    def test_accepts_normal_value(self):
        from command_handlers import _validate_cli_arg
        _validate_cli_arg("release_name", "my-release")  # should not raise

    def test_accepts_none(self):
        from command_handlers import _validate_cli_arg
        _validate_cli_arg("version", None)  # should not raise

    def test_accepts_empty_string(self):
        from command_handlers import _validate_cli_arg
        _validate_cli_arg("version", "")  # should not raise


class TestValidateHelmTimeout:
    """Test _validate_helm_timeout accepts valid formats and rejects bad ones."""

    def test_accepts_none(self):
        from command_handlers import _validate_helm_timeout
        _validate_helm_timeout(None)

    def test_accepts_5m(self):
        from command_handlers import _validate_helm_timeout
        _validate_helm_timeout("5m")

    def test_accepts_300s(self):
        from command_handlers import _validate_helm_timeout
        _validate_helm_timeout("300s")

    def test_accepts_1h(self):
        from command_handlers import _validate_helm_timeout
        _validate_helm_timeout("1h")

    def test_accepts_1h30m(self):
        from command_handlers import _validate_helm_timeout
        _validate_helm_timeout("1h30m")

    def test_rejects_plain_number(self):
        from command_handlers import _validate_helm_timeout
        with pytest.raises(ValueError, match="Invalid timeout format"):
            _validate_helm_timeout("300")

    def test_rejects_flag(self):
        from command_handlers import _validate_helm_timeout
        with pytest.raises(ValueError, match="cannot start with '-'"):
            _validate_helm_timeout("--timeout=5m")

    def test_rejects_arbitrary_string(self):
        from command_handlers import _validate_helm_timeout
        with pytest.raises(ValueError, match="Invalid timeout format"):
            _validate_helm_timeout("forever")


# =========================================================================
# apply_manifests
# =========================================================================


class TestApplyManifests:
    """Test apply_manifests command handler."""

    @pytest.mark.asyncio
    async def test_empty_manifests(self, handlers):
        """Empty manifest list should succeed with 0 resources."""
        result = await handlers.apply_manifests({"manifests": []})
        assert result["success"] is True
        assert result["resources_created"] == 0

    @pytest.mark.asyncio
    async def test_single_manifest_applied(self, handlers, mock_kr8s_api):
        """Single manifest should be applied via server-side apply."""
        manifest = {
            "kind": "ConfigMap",
            "metadata": {"name": "test-cm", "namespace": "default"},
        }
        result = await handlers.apply_manifests({"manifests": [manifest]})

        assert result["success"] is True
        assert result["resources_created"] == 1
        assert result["resources_failed"] == 0
        mock_kr8s_api.async_apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_manifests(self, handlers, mock_kr8s_api):
        """Multiple manifests should all be applied."""
        manifests = [
            {"kind": "ConfigMap", "metadata": {"name": f"cm-{i}"}} for i in range(3)
        ]
        result = await handlers.apply_manifests({"manifests": manifests})

        assert result["success"] is True
        assert result["resources_created"] == 3
        assert mock_kr8s_api.async_apply.call_count == 3

    @pytest.mark.asyncio
    async def test_default_namespace_injected(self, handlers, mock_kr8s_api):
        """Manifest without namespace should get the default namespace."""
        manifest = {"kind": "ConfigMap", "metadata": {"name": "no-ns"}}
        await handlers.apply_manifests({
            "manifests": [manifest],
            "namespace": "my-ns",
        })

        # The manifest should have been mutated to include namespace
        assert manifest["metadata"]["namespace"] == "my-ns"

    @pytest.mark.asyncio
    async def test_apply_error_recorded(self, handlers, mock_kr8s_api):
        """Failed apply should record error but continue."""
        mock_kr8s_api.async_apply.side_effect = Exception("conflict")

        manifest = {"kind": "ConfigMap", "metadata": {"name": "bad"}}
        result = await handlers.apply_manifests({"manifests": [manifest]})

        assert result["success"] is False
        assert result["resources_failed"] == 1
        assert "conflict" in result["errors"][0]

    @pytest.mark.asyncio
    async def test_output_callback_called(self, handlers, mock_kr8s_api):
        """on_output callback should receive progress messages."""
        output_lines = []

        async def capture(line):
            output_lines.append(line)

        manifest = {"kind": "Deployment", "metadata": {"name": "web"}}
        await handlers.apply_manifests(
            {"manifests": [manifest]},
            on_output=capture,
        )

        assert any("Applying" in line for line in output_lines)
        assert any("applied" in line for line in output_lines)

    @pytest.mark.asyncio
    async def test_wait_for_ready_deployment(self, handlers, mock_kr8s_api):
        """wait_for_ready should wait for deployments."""
        dep = FakeK8sResource(kind="Deployment", name="web")
        mock_kr8s_api.async_get.return_value = [dep]

        manifest = {
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": "default"},
        }
        result = await handlers.apply_manifests({
            "manifests": [manifest],
            "wait_for_ready": True,
            "timeout": 5,
        })

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_wait_for_ready_timeout(self, handlers, mock_kr8s_api):
        """wait_for_ready should handle timeout gracefully."""
        dep = FakeK8sResource(kind="Deployment", name="web")
        dep.async_wait = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_kr8s_api.async_get.return_value = [dep]

        manifest = {
            "kind": "Deployment",
            "metadata": {"name": "web", "namespace": "default"},
        }
        result = await handlers.apply_manifests({
            "manifests": [manifest],
            "wait_for_ready": True,
            "timeout": 1,
        })

        assert result["success"] is False
        assert any("timed out" in e for e in result["errors"])

    @pytest.mark.asyncio
    async def test_wait_skips_non_waitable_kinds(self, handlers, mock_kr8s_api):
        """wait_for_ready should skip ConfigMaps, Secrets, etc."""
        manifest = {
            "kind": "ConfigMap",
            "metadata": {"name": "cfg", "namespace": "default"},
        }
        result = await handlers.apply_manifests({
            "manifests": [manifest],
            "wait_for_ready": True,
        })

        assert result["success"] is True
        # async_get should NOT be called for ConfigMap readiness
        mock_kr8s_api.async_get.assert_not_called()


# =========================================================================
# destroy_manifests
# =========================================================================


class TestDestroyManifests:
    """Test destroy_manifests command handler."""

    @pytest.mark.asyncio
    async def test_empty_manifests(self, handlers):
        result = await handlers.destroy_manifests({"manifests": []})
        assert result["success"] is True
        assert result["resources_destroyed"] == 0

    @pytest.mark.asyncio
    async def test_single_resource_deleted(self, handlers, mock_kr8s_api):
        """Single resource should be deleted."""
        resource = FakeK8sResource(kind="ConfigMap", name="old")
        mock_kr8s_api.async_get.return_value = [resource]

        manifest = {"kind": "ConfigMap", "metadata": {"name": "old"}}
        result = await handlers.destroy_manifests({"manifests": [manifest]})

        assert result["success"] is True
        assert result["resources_destroyed"] == 1

    @pytest.mark.asyncio
    async def test_reverse_order(self, handlers, mock_kr8s_api):
        """Manifests should be destroyed in reverse order."""
        call_order = []

        async def track_get(kind, name=None, namespace=None, **kw):
            resource = FakeK8sResource(kind=kind, name=name or "x")
            original_delete = resource.async_delete

            async def tracked_delete():
                call_order.append(name)
                return await original_delete()

            resource.async_delete = tracked_delete
            return [resource]

        mock_kr8s_api.async_get.side_effect = track_get

        manifests = [
            {"kind": "ConfigMap", "metadata": {"name": "first"}},
            {"kind": "ConfigMap", "metadata": {"name": "second"}},
            {"kind": "ConfigMap", "metadata": {"name": "third"}},
        ]
        await handlers.destroy_manifests({"manifests": manifests})

        assert call_order == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_ignore_not_found_default(self, handlers, mock_kr8s_api):
        """Not-found resources should be silently ignored by default."""
        mock_kr8s_api.async_get.return_value = []  # Not found

        manifest = {"kind": "ConfigMap", "metadata": {"name": "gone"}}
        result = await handlers.destroy_manifests({"manifests": [manifest]})

        assert result["success"] is True
        assert result["resources_destroyed"] == 0

    @pytest.mark.asyncio
    async def test_not_found_error_when_not_ignored(self, handlers, mock_kr8s_api):
        """Not-found should be an error when ignore_not_found=False."""
        mock_kr8s_api.async_get.return_value = []

        manifest = {"kind": "ConfigMap", "metadata": {"name": "gone"}}
        result = await handlers.destroy_manifests({
            "manifests": [manifest],
            "ignore_not_found": False,
        })

        assert result["success"] is False
        assert len(result["errors"]) == 1

    @pytest.mark.asyncio
    async def test_exception_not_found_ignored(self, handlers, mock_kr8s_api):
        """Exception containing 'not found' should be ignored when flag is set."""
        mock_kr8s_api.async_get.side_effect = Exception("resource not found in cluster")

        manifest = {"kind": "ConfigMap", "metadata": {"name": "gone"}}
        result = await handlers.destroy_manifests({"manifests": [manifest]})

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_real_error_recorded(self, handlers, mock_kr8s_api):
        """Non-404 errors should be recorded."""
        mock_kr8s_api.async_get.side_effect = Exception("connection refused")

        manifest = {"kind": "ConfigMap", "metadata": {"name": "bad"}}
        result = await handlers.destroy_manifests({"manifests": [manifest]})

        assert result["success"] is False
        assert "connection refused" in result["errors"][0]


# =========================================================================
# install_helm
# =========================================================================


class TestInstallHelm:
    """Test install_helm command handler."""

    @pytest.mark.asyncio
    async def test_missing_chart_ref(self, handlers):
        result = await handlers.install_helm({"release_name": "x"})
        assert result["success"] is False
        assert "chart_ref" in result["error_message"]

    @pytest.mark.asyncio
    async def test_missing_release_name(self, handlers):
        result = await handlers.install_helm({"chart_ref": "oci://example.com/chart"})
        assert result["success"] is False
        assert "release_name" in result["error_message"]

    @pytest.mark.asyncio
    async def test_rejects_flag_in_chart_ref(self, handlers):
        with pytest.raises(ValueError, match="cannot start with '-'"):
            await handlers.install_helm({
                "chart_ref": "--inject",
                "release_name": "test",
            })

    @pytest.mark.asyncio
    async def test_rejects_bad_timeout(self, handlers):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "test",
                "timeout": "forever",
            })

    @pytest.mark.asyncio
    async def test_successful_install(self, handlers):
        """Helm install should shell out to helm CLI."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"installed\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "my-release",
                "namespace": "test-ns",
            })

        assert result["success"] is True
        assert result["release_name"] == "my-release"
        assert result["namespace"] == "test-ns"

    @pytest.mark.asyncio
    async def test_install_with_values(self, handlers):
        """Helm install with values should write a temp file."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc) as mock_exec:
            result = await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "my-release",
                "values": {"replicaCount": 3},
            })

        assert result["success"] is True
        # The call should include -f <values_file>
        call_args = mock_exec.call_args
        cmd_list = call_args[0]
        assert "-f" in cmd_list

    @pytest.mark.asyncio
    async def test_install_with_version(self, handlers):
        """Version flag should be passed to helm."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc) as mock_exec:
            await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "my-release",
                "version": "1.2.3",
            })

        cmd_list = mock_exec.call_args[0]
        assert "--version" in cmd_list
        assert "1.2.3" in cmd_list

    @pytest.mark.asyncio
    async def test_install_failure(self, handlers):
        """Failed helm install should return error."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(return_value=(b"", b"Error: chart not found\n"))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "bad-release",
            })

        assert result["success"] is False
        assert "chart not found" in result["error_message"]

    @pytest.mark.asyncio
    async def test_registry_login(self, handlers):
        """OCI registry login should be attempted before install."""
        # Login proc succeeds, install proc succeeds
        login_proc = AsyncMock()
        login_proc.returncode = 0
        login_proc.communicate = AsyncMock(return_value=(b"Login succeeded\n", b""))

        install_proc = AsyncMock()
        install_proc.returncode = 0
        install_proc.communicate = AsyncMock(return_value=(b"installed\n", b""))

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return login_proc
            return install_proc

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            result = await handlers.install_helm({
                "chart_ref": "oci://registry.example.com/chart",
                "release_name": "my-release",
                "registry_auth": {
                    "url": "registry.example.com",
                    "username": "user",
                    "password": "pass",
                },
            })

        assert result["success"] is True
        assert call_count == 2  # login + install

    @pytest.mark.asyncio
    async def test_registry_login_failure(self, handlers):
        """Failed registry login should abort install."""
        login_proc = AsyncMock()
        login_proc.returncode = 1
        login_proc.communicate = AsyncMock(return_value=(b"", b"unauthorized\n"))

        with patch("asyncio.create_subprocess_exec", return_value=login_proc):
            result = await handlers.install_helm({
                "chart_ref": "oci://registry.example.com/chart",
                "release_name": "my-release",
                "registry_auth": {
                    "url": "registry.example.com",
                    "username": "user",
                    "password": "bad-pass",
                },
            })

        assert result["success"] is False
        assert "login failed" in result["error_message"].lower()

    @pytest.mark.asyncio
    async def test_no_create_namespace(self, handlers):
        """create_namespace=False should omit --create-namespace flag."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"ok\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc) as mock_exec:
            await handlers.install_helm({
                "chart_ref": "oci://example.com/chart",
                "release_name": "my-release",
                "create_namespace": False,
            })

        cmd_list = mock_exec.call_args[0]
        assert "--create-namespace" not in cmd_list


# =========================================================================
# uninstall_helm
# =========================================================================


class TestUninstallHelm:
    """Test uninstall_helm command handler."""

    @pytest.mark.asyncio
    async def test_missing_release_name(self, handlers):
        result = await handlers.uninstall_helm({})
        assert result["success"] is False
        assert "release_name" in result["error_message"]

    @pytest.mark.asyncio
    async def test_rejects_flag_in_release_name(self, handlers):
        with pytest.raises(ValueError, match="cannot start with '-'"):
            await handlers.uninstall_helm({"release_name": "--flag"})

    @pytest.mark.asyncio
    async def test_successful_uninstall(self, handlers):
        fake_proc = AsyncMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"release uninstalled\n", b""))

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await handlers.uninstall_helm({
                "release_name": "my-release",
                "namespace": "test-ns",
            })

        assert result["success"] is True
        assert result["release_name"] == "my-release"

    @pytest.mark.asyncio
    async def test_uninstall_not_found(self, handlers):
        """Uninstall of non-existent release should succeed with message."""
        fake_proc = AsyncMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(
            return_value=(b"", b'Error: uninstall: Release not found: my-release\n')
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await handlers.uninstall_helm({"release_name": "my-release"})

        assert result["success"] is True
        assert "not found" in result.get("message", "").lower()

    @pytest.mark.asyncio
    async def test_uninstall_failure(self, handlers):
        fake_proc = AsyncMock()
        fake_proc.returncode = 1
        fake_proc.communicate = AsyncMock(
            return_value=(b"", b"Error: something went wrong\n")
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_proc):
            result = await handlers.uninstall_helm({"release_name": "my-release"})

        assert result["success"] is False
        assert "something went wrong" in result["error_message"]


# =========================================================================
# scan_cluster
# =========================================================================


class TestScanCluster:
    """Test scan_cluster command handler."""

    @pytest.mark.asyncio
    async def test_full_scan(self, handlers, mock_kr8s_api):
        """Full cluster scan should return structured data."""
        nodes = [make_node("n1", ready=True), make_node("n2", ready=False)]
        crds = [
            make_crd("certificates.cert-manager.io"),
            make_crd("issuers.cert-manager.io"),
            make_crd("network-attachment-definitions.k8s.cni.cncf.io"),
            make_crd("gateways.gateway.networking.k8s.io"),
            make_crd("f5bigips.f5.com"),
        ]
        flo_dep = make_deployment("flo", ready=True)
        tmm_pods = [make_pod("tmm-1", ready=True)]

        call_count = 0

        async def mock_get(kind, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if kind == "nodes":
                return nodes
            if kind == "customresourcedefinitions":
                return crds
            if kind == "deployments":
                return [flo_dep]
            if kind == "pods":
                return tmm_pods
            return []

        mock_kr8s_api.async_get.side_effect = mock_get

        result = await handlers.scan_cluster({})

        assert result["success"] is True
        assert result["cluster"]["kubernetes_version"] == "v1.29.0"
        assert result["cluster"]["node_count"] == 2
        assert result["cluster"]["nodes_ready"] == 1
        assert result["cert_manager"]["installed"] is True
        assert result["cert_manager"]["crd_count"] == 2
        assert result["multus"]["installed"] is True
        assert result["gateway_api"]["installed"] is True
        assert result["bnk"]["installed"] is True

    @pytest.mark.asyncio
    async def test_scan_error(self, handlers, mock_kr8s_api):
        """Scan failure should return error message."""
        mock_kr8s_api.async_version.side_effect = Exception("connection refused")

        result = await handlers.scan_cluster({})

        assert result["success"] is False
        assert "connection refused" in result["error_message"]


# =========================================================================
# get_health
# =========================================================================


class TestGetHealth:
    """Test get_health command handler."""

    @pytest.mark.asyncio
    async def test_full_health_report(self, handlers, mock_kr8s_api):
        """Health report should include cluster, BNK, gateways, routes."""
        nodes = [make_node("n1", ready=True)]
        flo_dep = make_deployment("flo", ready=True)
        tmm_pods = [make_pod("tmm-1", ready=True, containers=7)]
        gateways = [make_gateway("gw-1", programmed=True, listeners=2)]
        routes = [make_route("r1"), make_route("r2")]

        async def mock_get(kind, *args, **kwargs):
            if kind == "nodes":
                return nodes
            if kind == "deployments":
                return [flo_dep]
            if kind == "pods":
                return tmm_pods
            if kind == "gateways.gateway.networking.k8s.io":
                return gateways
            if kind == "httproutes.gateway.networking.k8s.io":
                return routes
            return []

        mock_kr8s_api.async_get.side_effect = mock_get

        result = await handlers.get_health({})

        assert result["success"] is True
        assert "timestamp" in result
        assert result["cluster"]["nodes_ready"] == 1
        assert result["bnk"]["installed"] is True
        assert result["bnk"]["flo_ready"] is True
        assert result["bnk"]["tmm_total"] == 1
        assert result["bnk"]["tmm_ready"] == 1
        assert result["bnk"]["tmm_containers"] == "7/7"
        assert len(result["bnk"]["gateways"]) == 1
        assert result["bnk"]["gateways"][0]["programmed"] is True
        assert result["bnk"]["routes_count"] == 2

    @pytest.mark.asyncio
    async def test_health_no_bnk(self, handlers, mock_kr8s_api):
        """Health report when BNK is not installed."""
        nodes = [make_node("n1")]

        async def mock_get(kind, *args, **kwargs):
            if kind == "nodes":
                return nodes
            return []

        mock_kr8s_api.async_get.side_effect = mock_get

        result = await handlers.get_health({})

        assert result["success"] is True
        assert result["bnk"]["installed"] is False

    @pytest.mark.asyncio
    async def test_health_error(self, handlers, mock_kr8s_api):
        """Health check failure should return error."""
        mock_kr8s_api.async_version.side_effect = Exception("timeout")

        result = await handlers.get_health({})

        assert result["success"] is False
        assert "timeout" in result["error_message"]


# =========================================================================
# get_resources
# =========================================================================


class TestGetResources:
    """Test get_resources command handler."""

    @pytest.mark.asyncio
    async def test_list_pods(self, handlers, mock_kr8s_api):
        pods = [make_pod("p1"), make_pod("p2")]
        mock_kr8s_api.async_get.return_value = pods

        result = await handlers.get_resources({"kind": "pods", "namespace": "default"})

        assert result["success"] is True
        assert result["count"] == 2
        assert result["items"][0]["name"] == "p1"

    @pytest.mark.asyncio
    async def test_list_with_label_selector(self, handlers, mock_kr8s_api):
        mock_kr8s_api.async_get.return_value = [make_pod("p1")]

        result = await handlers.get_resources({
            "kind": "pods",
            "namespace": "f5-bnk",
            "label_selector": "app=f5-tmm",
        })

        assert result["success"] is True
        mock_kr8s_api.async_get.assert_called_with(
            "pods", namespace="f5-bnk", label_selector="app=f5-tmm"
        )

    @pytest.mark.asyncio
    async def test_list_all_namespaces(self, handlers, mock_kr8s_api):
        """Empty namespace should list across all namespaces."""
        mock_kr8s_api.async_get.return_value = []

        await handlers.get_resources({"kind": "deployments", "namespace": ""})

        # Should NOT pass namespace kwarg
        mock_kr8s_api.async_get.assert_called_with("deployments")

    @pytest.mark.asyncio
    async def test_list_error(self, handlers, mock_kr8s_api):
        mock_kr8s_api.async_get.side_effect = Exception("forbidden")

        result = await handlers.get_resources({"kind": "secrets"})

        assert result["success"] is False
        assert "forbidden" in result["error_message"]


# =========================================================================
# get_pod_logs
# =========================================================================


class TestGetPodLogs:
    """Test get_pod_logs command handler."""

    @pytest.mark.asyncio
    async def test_missing_pod_name(self, handlers):
        result = await handlers.get_pod_logs({})
        assert result["success"] is False
        assert "pod_name" in result["error_message"]

    @pytest.mark.asyncio
    async def test_pod_not_found(self, handlers, mock_kr8s_api):
        mock_kr8s_api.async_get.return_value = []

        result = await handlers.get_pod_logs({"pod_name": "ghost"})

        assert result["success"] is False
        assert "not found" in result["error_message"].lower()

    @pytest.mark.asyncio
    async def test_successful_log_retrieval(self, handlers, mock_kr8s_api):
        pod = make_pod("web-1")
        mock_kr8s_api.async_get.return_value = [pod]

        result = await handlers.get_pod_logs({
            "pod_name": "web-1",
            "namespace": "default",
            "tail_lines": 50,
        })

        assert result["success"] is True
        assert result["lines"] > 0
        assert "line1" in result["logs"]

    @pytest.mark.asyncio
    async def test_log_retrieval_error(self, handlers, mock_kr8s_api):
        pod = make_pod("web-1")
        pod.async_logs = AsyncMock(side_effect=Exception("container not running"))
        mock_kr8s_api.async_get.return_value = [pod]

        result = await handlers.get_pod_logs({"pod_name": "web-1"})

        assert result["success"] is False
        assert "container not running" in result["error_message"]


# =========================================================================
# Helper functions
# =========================================================================


class TestHelpers:
    """Test module-level helper functions."""

    def test_node_ready_true(self):
        from command_handlers import _node_ready
        node = make_node("n1", ready=True)
        assert _node_ready(node) is True

    def test_node_ready_false(self):
        from command_handlers import _node_ready
        node = make_node("n1", ready=False)
        assert _node_ready(node) is False

    def test_node_ready_no_conditions(self):
        from command_handlers import _node_ready
        node = FakeK8sResource(status={"conditions": []})
        assert _node_ready(node) is False

    def test_pod_ready_true(self):
        from command_handlers import _pod_ready
        pod = make_pod("p1", ready=True)
        assert _pod_ready(pod) is True

    def test_pod_ready_false(self):
        from command_handlers import _pod_ready
        pod = make_pod("p1", ready=False)
        assert _pod_ready(pod) is False

    def test_deployment_ready(self):
        from command_handlers import _deployment_ready
        dep = make_deployment("d1", ready=True, replicas=3)
        assert _deployment_ready(dep) is True

    def test_deployment_not_ready(self):
        from command_handlers import _deployment_ready
        dep = make_deployment("d1", ready=False, replicas=3)
        assert _deployment_ready(dep) is False

    def test_deployment_zero_replicas(self):
        from command_handlers import _deployment_ready
        dep = FakeK8sResource(status={"replicas": 0, "availableReplicas": 0})
        assert _deployment_ready(dep) is False

    def test_has_condition_true(self):
        from command_handlers import _has_condition
        gw = make_gateway("gw", programmed=True)
        assert _has_condition(gw, "Programmed") is True

    def test_has_condition_false(self):
        from command_handlers import _has_condition
        gw = make_gateway("gw", programmed=False)
        assert _has_condition(gw, "Programmed") is False

    def test_has_condition_missing(self):
        from command_handlers import _has_condition
        resource = FakeK8sResource(status={"conditions": []})
        assert _has_condition(resource, "Ready") is False

    def test_now_iso(self):
        from command_handlers import _now_iso
        ts = _now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts  # timezone-aware
