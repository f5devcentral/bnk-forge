"""
Tests for services.execution.variable_assembler — variable assembly and transforms.

Covers apply_transformation, _looks_like_jwt, apply_variable_mappings,
can_execute, find_dependency_by_path, and build_variables layers.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest

from services.execution.variable_assembler import (
    _looks_like_jwt,
    _resolve_from_dependency_outputs,
    apply_transformation,
    apply_variable_mappings,
    auto_wire_common_variables,
    can_execute,
    find_dependency_by_path,
)

# ── apply_transformation ─────────────────────────────────────────────────

class TestApplyTransformation:
    def test_none_transform_returns_original(self):
        assert apply_transformation("hello", "none") == "hello"

    def test_empty_transform_returns_original(self):
        assert apply_transformation("hello", "") == "hello"

    def test_uppercase(self):
        assert apply_transformation("hello", "uppercase") == "HELLO"

    def test_lowercase(self):
        assert apply_transformation("HELLO", "lowercase") == "hello"

    def test_json_encode(self):
        result = apply_transformation({"key": "val"}, "json_encode")
        assert result == '{"key": "val"}'

    def test_base64_encode(self):
        result = apply_transformation("secret", "base64")
        assert result == base64.b64encode(b"secret").decode()

    def test_base64_skips_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
        result = apply_transformation(jwt, "base64")
        # Should return raw JWT, not double-encoded
        assert result == jwt

    def test_unknown_transform_returns_original(self):
        assert apply_transformation("val", "rot13") == "val"

    def test_non_string_value_coerced(self):
        assert apply_transformation(42, "uppercase") == "42"


# ── _looks_like_jwt ──────────────────────────────────────────────────────

class TestLooksLikeJwt:
    def test_valid_jwt_detected(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"
        assert _looks_like_jwt(jwt) is True

    def test_non_jwt_not_detected(self):
        assert _looks_like_jwt("not-a-jwt") is False

    def test_two_segment_not_jwt(self):
        assert _looks_like_jwt("eyJhbGci.eyJzdWIi") is False

    def test_three_segments_wrong_prefix(self):
        assert _looks_like_jwt("abc.def.ghi") is False


# ── apply_variable_mappings ──────────────────────────────────────────────

class TestApplyVariableMappings:
    def _make_mapping(self, source, target, transform="none"):
        m = MagicMock()
        m.source_variable_name = source
        m.target_variable_name = target
        m.transform_type = transform
        return m

    @patch("services.execution.variable_assembler._get_raw_encoding_vars", return_value=set())
    def test_renames_variable(self, mock_raw):
        db = MagicMock()
        module = MagicMock()
        module.id = 1
        db.query.return_value.filter.return_value.all.return_value = [
            self._make_mapping("old_name", "new_name"),
        ]
        variables = {"old_name": "value1", "other": "keep"}
        result = apply_variable_mappings(db, module, variables)
        assert result["new_name"] == "value1"
        assert "old_name" not in result
        assert result["other"] == "keep"

    @patch("services.execution.variable_assembler._get_raw_encoding_vars", return_value=set())
    def test_transform_during_mapping(self, mock_raw):
        db = MagicMock()
        module = MagicMock()
        module.id = 1
        db.query.return_value.filter.return_value.all.return_value = [
            self._make_mapping("name", "upper_name", "uppercase"),
        ]
        variables = {"name": "hello"}
        result = apply_variable_mappings(db, module, variables)
        assert result["upper_name"] == "HELLO"

    def test_no_mappings_returns_unchanged(self):
        db = MagicMock()
        module = MagicMock()
        module.id = 1
        db.query.return_value.filter.return_value.all.return_value = []
        variables = {"a": 1, "b": 2}
        result = apply_variable_mappings(db, module, variables)
        assert result == {"a": 1, "b": 2}


# ── can_execute ──────────────────────────────────────────────────────────

class TestCanExecute:
    def test_no_dependencies_can_execute(self):
        db = MagicMock()
        module = MagicMock()
        module.dependencies = []
        ok, missing = can_execute(db, module)
        assert ok is True
        assert missing == []

    def test_none_dependencies_can_execute(self):
        db = MagicMock()
        module = MagicMock()
        module.dependencies = None
        ok, missing = can_execute(db, module)
        assert ok is True
        assert missing == []

    @patch("services.execution.variable_assembler.joinedload")
    def test_missing_dependency_reported(self, mock_joinedload):
        db = MagicMock()
        module = MagicMock()
        module.dependencies = [99]
        # Return empty list — dependency not found
        db.query.return_value.options.return_value.filter.return_value.all.return_value = []
        ok, missing = can_execute(db, module)
        assert ok is False
        assert any("99" in m for m in missing)


# ── find_dependency_by_path ──────────────────────────────────────────────

class TestFindDependencyByPath:
    @patch("services.execution.variable_assembler.joinedload")
    def test_exact_match(self, mock_joinedload):
        db = MagicMock()
        expected = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = expected
        result = find_dependency_by_path(db, 1, "infra/aws/vpc")
        assert result is expected

    @patch("services.execution.variable_assembler.joinedload")
    def test_returns_none_when_not_found(self, mock_joinedload):
        db = MagicMock()
        db.query.return_value.join.return_value.filter.return_value.first.return_value = None
        db.query.return_value.join.return_value.options.return_value.filter.return_value.all.return_value = []
        result = find_dependency_by_path(db, 1, "nonexistent/path")
        assert result is None

    @patch("services.execution.variable_assembler.joinedload")
    def test_stack_scoped_returns_same_stack_on_exact_hit(self, mock_joinedload):
        """Stack-scoped query returns the same-stack module when the exact match succeeds."""
        db = MagicMock()
        expected = MagicMock()
        expected.id = 42

        # First filter call (stack-scoped) succeeds — should be returned immediately
        db.query.return_value.join.return_value.filter.return_value.first.return_value = expected

        result = find_dependency_by_path(db, 1, "bare-metal/kubeadm-init", stack_instance_id=10)
        assert result is expected

    @patch("services.execution.variable_assembler.joinedload")
    def test_stack_scoped_falls_back_when_not_in_stack(self, mock_joinedload):
        """Stack-scoped exact miss falls through to project-wide exact match."""
        db = MagicMock()
        fallback = MagicMock()
        fallback.id = 99
        fallback.stack_instance_id = 20

        # Stack-scoped first() returns None; project-wide first() returns fallback
        db.query.return_value.join.return_value.filter.return_value.first.side_effect = [
            None,       # stack-scoped exact miss
            fallback,   # project-wide exact hit
        ]

        result = find_dependency_by_path(db, 1, "bare-metal/kubeadm-init", stack_instance_id=10)
        assert result is fallback

    @patch("services.execution.variable_assembler.joinedload")
    def test_stack_scoped_suffix_prefers_same_stack(self, mock_joinedload):
        """Suffix matching prefers the same-stack module over cross-stack modules."""
        # Build two candidate modules with the same suffix match
        same_stack_pm = MagicMock()
        same_stack_pm.id = 1
        same_stack_pm.stack_instance_id = 5
        same_stack_pm.library_module = MagicMock()
        same_stack_pm.library_module.path = "bare-metal/kubeadm-init"

        cross_stack_pm = MagicMock()
        cross_stack_pm.id = 2
        cross_stack_pm.stack_instance_id = 99
        cross_stack_pm.library_module = MagicMock()
        cross_stack_pm.library_module.path = "bare-metal/kubeadm-init"

        db = MagicMock()
        # Both exact queries miss; suffix/normalised scan sees both modules
        db.query.return_value.join.return_value.filter.return_value.first.return_value = None
        db.query.return_value.join.return_value.options.return_value.filter.return_value.all.return_value = [
            cross_stack_pm,   # listed first — but same-stack should win
            same_stack_pm,
        ]

        result = find_dependency_by_path(
            db, 1, "kubeadm-init", stack_instance_id=5
        )
        assert result is same_stack_pm


# ── auto_wire_common_variables ────────────────────────────────────────────

class TestAutoWireCommonVariables:
    def _make_pm(self, pm_id, path, outputs, *, stack_instance_id, status="applied"):
        pm = MagicMock()
        pm.id = pm_id
        pm.status = status
        pm.outputs = outputs
        pm.stack_instance_id = stack_instance_id
        pm.library_module = MagicMock()
        pm.library_module.path = path
        return pm

    def _make_project(self, name="proj", region="us-east-1", cloud_provider="aws"):
        project = MagicMock()
        project.name = name
        project.region = region
        project.cloud_provider = cloud_provider
        return project

    def test_prefers_same_stack_outputs_over_cross_stack_and_dependencies(self):
        db = MagicMock()
        module = MagicMock()
        module.id = 100
        module.project_id = 1
        module.stack_instance_id = 10
        # Deliberately mark only cross-stack module as declared dependency.
        module.dependencies = [200]

        same_stack = self._make_pm(
            201,
            "infra/aws/security-same",
            {"security_group_id": "sg-same-stack"},
            stack_instance_id=10,
        )
        cross_stack_dep = self._make_pm(
            200,
            "infra/aws/security-dep",
            {"security_group_id": "sg-cross-stack-dep"},
            stack_instance_id=99,
        )

        applied_query = MagicMock()
        applied_query.options.return_value.filter.return_value.all.return_value = [
            cross_stack_dep,
            same_stack,
        ]

        project_query = MagicMock()
        project_query.filter.return_value.first.return_value = self._make_project()

        db.query.side_effect = [applied_query, project_query]

        variables = {}
        auto_wire_common_variables(db, module, variables)

        assert variables["security_group_id"] == "sg-same-stack"

    def test_falls_back_to_declared_dependency_when_same_stack_missing(self):
        db = MagicMock()
        module = MagicMock()
        module.id = 100
        module.project_id = 1
        module.stack_instance_id = 10
        module.dependencies = [200]

        cross_stack_dep = self._make_pm(
            200,
            "infra/aws/security-dep",
            {"security_group_id": "sg-dependency"},
            stack_instance_id=99,
        )
        cross_stack_other = self._make_pm(
            201,
            "infra/aws/security-other",
            {"security_group_id": "sg-other"},
            stack_instance_id=98,
        )

        applied_query = MagicMock()
        applied_query.options.return_value.filter.return_value.all.return_value = [
            cross_stack_other,
            cross_stack_dep,
        ]

        project_query = MagicMock()
        project_query.filter.return_value.first.return_value = self._make_project()

        db.query.side_effect = [applied_query, project_query]

        variables = {}
        auto_wire_common_variables(db, module, variables)

        assert variables["security_group_id"] == "sg-dependency"

    def test_preserves_project_level_fallback_when_no_same_stack_or_dependency(self):
        db = MagicMock()
        module = MagicMock()
        module.id = 100
        module.project_id = 1
        module.stack_instance_id = 10
        module.dependencies = []

        cross_stack_only = self._make_pm(
            300,
            "infra/aws/security-any",
            {"security_group_id": "sg-project-fallback"},
            stack_instance_id=77,
        )

        applied_query = MagicMock()
        applied_query.options.return_value.filter.return_value.all.return_value = [cross_stack_only]

        project_query = MagicMock()
        project_query.filter.return_value.first.return_value = self._make_project()

        db.query.side_effect = [applied_query, project_query]

        variables = {}
        auto_wire_common_variables(db, module, variables)

        assert variables["security_group_id"] == "sg-project-fallback"


# ── _resolve_from_dependency_outputs ─────────────────────────────────────────

class TestResolveFromDependencyOutputs:
    """FIX B: Layer-3 fallback when from_module hint mismatches the project's modules."""

    def _make_pm(self, pm_id, outputs, stack_instance_id=10):
        pm = MagicMock()
        pm.id = pm_id
        pm.status = "applied"
        pm.outputs = outputs
        pm.stack_instance_id = stack_instance_id
        pm.library_module = MagicMock()
        pm.library_module.path = f"modules/module-{pm_id}"
        return pm

    def _make_module(self, dep_ids, stack_instance_id=10):
        m = MagicMock()
        m.id = 99
        m.project_id = 1
        m.stack_instance_id = stack_instance_id
        m.dependencies = dep_ids
        return m

    def test_resolves_from_declared_dependency(self):
        """Declared dep produces the output -> value is returned."""
        dep = self._make_pm(10, {"vpc_id": "vpc-abc123"}, stack_instance_id=10)
        module = self._make_module([10])

        db = MagicMock()
        # Query for declared dep IDs
        db.query.return_value.filter.return_value.all.return_value = [dep]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")
        assert result == "vpc-abc123"

    def test_returns_none_when_no_dep_has_output(self):
        """No declared dep has the output, no applied modules either -> None."""
        dep = self._make_pm(10, {"other_output": "x"}, stack_instance_id=10)
        module = self._make_module([10])

        db = MagicMock()
        # First call: declared deps, second call: project-wide applied
        db.query.return_value.filter.return_value.all.side_effect = [
            [dep],       # declared deps (no vpc_id)
            [],          # project-wide applied (empty)
        ]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")
        assert result is None

    def test_no_declared_deps_falls_back_to_unambiguous_project_module(self):
        """No declared deps, exactly one project module produces the output -> returns it."""
        module = self._make_module([], stack_instance_id=None)  # no stack context -> project-wide fallback
        applied_module = self._make_pm(20, {"vpc_id": "vpc-fallback"})

        db = MagicMock()
        # Only ONE query runs (project-wide applied), because dep_ids=[] skips the first block
        db.query.return_value.filter.return_value.all.return_value = [applied_module]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")
        assert result == "vpc-fallback"

    def test_ambiguous_project_modules_returns_none(self):
        """Two applied modules produce the same output -> ambiguous, return None."""
        module = self._make_module([])
        pm1 = self._make_pm(20, {"vpc_id": "vpc-one"})
        pm2 = self._make_pm(21, {"vpc_id": "vpc-two"})

        db = MagicMock()
        db.query.return_value.filter.return_value.all.side_effect = [
            [],          # declared deps
            [pm1, pm2],  # project-wide applied
        ]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")
        assert result is None

    def test_prefers_same_stack_scope_for_project_fallback(self):
        """Fallback search is scoped to module.stack_instance_id when present."""
        module = self._make_module([], stack_instance_id=10)
        same_stack = self._make_pm(20, {"vpc_id": "vpc-stack-10"}, stack_instance_id=10)
        cross_stack = self._make_pm(21, {"vpc_id": "vpc-stack-20"}, stack_instance_id=20)

        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.all.return_value = [same_stack]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")

        assert result == "vpc-stack-10"

    def test_no_stack_scope_keeps_project_wide_ambiguity(self):
        """Without stack_instance_id, fallback remains project-wide and ambiguous matches return None."""
        module = self._make_module([], stack_instance_id=None)
        pm1 = self._make_pm(20, {"vpc_id": "vpc-one"}, stack_instance_id=10)
        pm2 = self._make_pm(21, {"vpc_id": "vpc-two"}, stack_instance_id=20)

        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [pm1, pm2]

        result = _resolve_from_dependency_outputs(db, module, "vpc_id")
        assert result is None
