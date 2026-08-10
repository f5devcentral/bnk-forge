"""Unit tests for WO-4 audit fixes that are pure functions (no DB).

Covers:
- FEAT-0139 / ERR-0022 — dependency-graph cycle detection + layer assignments.
- FEAT-0099 / ERR-0004 — list_account_roles returns a constructed role_arn.
"""

from unittest.mock import MagicMock, patch

import pytest

from utils.dependency_graph import get_dependency_graph


class TestDependencyGraphLayersAndCycles:
    def test_acyclic_graph_assigns_layers(self):
        modules = [
            {"id": 1, "name": "VPC", "dependencies": []},
            {"id": 2, "name": "Subnet", "dependencies": [1]},
            {"id": 3, "name": "EKS", "dependencies": [1, 2]},
        ]
        graph = get_dependency_graph(modules)

        assert graph["has_cycle"] is False
        assert "error" not in graph
        # Roots are layer 0; each dependent sits one layer below its deepest dep.
        assert graph["layers"] == {1: 0, 2: 1, 3: 2}
        by_id = {n["id"]: n for n in graph["nodes"]}
        assert by_id[1]["layer"] == 0
        assert by_id[3]["layer"] == 2

    def test_cyclic_graph_reports_error_and_no_layers(self):
        modules = [
            {"id": 1, "name": "A", "dependencies": [3]},
            {"id": 2, "name": "B", "dependencies": [1]},
            {"id": 3, "name": "C", "dependencies": [2]},
        ]
        graph = get_dependency_graph(modules)

        assert graph["has_cycle"] is True
        assert "error" in graph and "Circular dependency" in graph["error"]
        assert graph["layers"] == {}
        # Nodes still returned for visualization, but with no layer assigned.
        assert all(n["layer"] is None for n in graph["nodes"])


class TestListAccountRolesRoleArn:
    def test_role_arn_is_constructed(self):
        page = {"roleList": [{"roleName": "AdminRole", "accountId": "123456789012"}]}
        paginator = MagicMock()
        paginator.paginate.return_value = [page]
        sso_client = MagicMock()
        sso_client.get_paginator.return_value = paginator
        fake_boto3 = MagicMock()
        fake_boto3.client.return_value = sso_client

        with (
            patch("services.aws_auth_service._import_boto3", return_value=fake_boto3),
            patch(
                "services.aws_auth_service._import_botocore_exceptions",
                return_value=(Exception, Exception),
            ),
        ):
            from services.aws_auth_service import AWSAuthService

            roles = AWSAuthService().list_account_roles(
                access_token="tok", account_id="123456789012", region="us-east-1"
            )

        assert roles == [
            {
                "role_name": "AdminRole",
                "account_id": "123456789012",
                "role_arn": "arn:aws:iam::123456789012:role/AdminRole",
            }
        ]


class TestDpuProbeWaveDispatch:
    """FEAT-0165/0179 / ERR-0019/0020 — cap enforced at enqueue via waves."""

    def test_dispatch_chunks_into_capped_waves(self):
        from routes.dpus import MAX_PARALLEL_PROBES, _dispatch_probe_waves

        task = MagicMock()
        dpu_ids = list(range(25))  # 25 → 3 waves of 10/10/5 at cap=10

        with (
            patch("celery.group", side_effect=lambda sigs: list(sigs)) as mock_group,
            patch("celery.chain") as mock_chain,
        ):
            result = _dispatch_probe_waves(task, dpu_ids)

        assert MAX_PARALLEL_PROBES == 10
        assert result["enqueued"] == 25
        assert result["waves"] == 3
        assert result["max_parallel"] == 10
        assert len(result["tasks"]) == 25
        # One group per wave, and a single chained dispatch.
        assert mock_group.call_count == 3
        mock_chain.assert_called_once()
        assert len(mock_chain.call_args.args) == 3
        mock_chain.return_value.apply_async.assert_called_once()

    def test_dispatch_empty_does_not_enqueue(self):
        from routes.dpus import _dispatch_probe_waves

        task = MagicMock()
        with patch("celery.chain") as mock_chain:
            result = _dispatch_probe_waves(task, [])

        assert result["enqueued"] == 0
        assert result["waves"] == 0
        mock_chain.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
