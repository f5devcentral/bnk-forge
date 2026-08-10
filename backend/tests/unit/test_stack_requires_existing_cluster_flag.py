"""
Unit tests for #133 — requires_existing_cluster flag in stack_templates.json
replaces EXISTING_CLUSTER_BNK_SLUGS hardcoded set.

Covers:
- bnk-on-k8s has requires_existing_cluster: true in JSON
- Templates without the flag return False
- The helper functions in both stack_deployment_service and stack_service agree
- Legacy fallback still catches f5-bnk-2.2 (not in JSON but still in slug set)
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ── JSON-level assertions ─────────────────────────────────────────────


def _load_templates() -> list[dict]:
    path = Path(__file__).resolve().parents[2] / "data" / "stack_templates.json"
    return json.loads(path.read_text())


def _get_template(slug: str) -> dict:
    return next(t for t in _load_templates() if t.get("slug") == slug)


class TestJsonFlag:
    def test_bnk_on_k8s_has_flag_set_true(self):
        t = _get_template("bnk-on-k8s")
        assert t.get("requires_existing_cluster") is True

    def test_infrastructure_template_does_not_have_flag(self):
        t = _get_template("aws-k8s-foundation")
        assert not t.get("requires_existing_cluster", False)

    def test_demo_template_does_not_have_flag(self):
        t = _get_template("aws-eks-cluster-demo")
        assert not t.get("requires_existing_cluster", False)

    def test_no_template_has_flag_except_expected_slugs(self):
        expected_slugs = {"bnk-on-k8s"}
        flagged = [t["slug"] for t in _load_templates() if t.get("requires_existing_cluster")]
        assert set(flagged) == expected_slugs


# ── stack_deployment_service helper ──────────────────────────────────


class TestStackDeploymentServiceHelper:
    def test_bnk_on_k8s_returns_true(self):
        from services.stack_deployment_service import _template_requires_existing_cluster
        assert _template_requires_existing_cluster("bnk-on-k8s") is True

    def test_infrastructure_slug_returns_false(self):
        from services.stack_deployment_service import _template_requires_existing_cluster
        assert _template_requires_existing_cluster("aws-k8s-foundation") is False

    def test_unknown_slug_returns_false(self):
        from services.stack_deployment_service import _template_requires_existing_cluster
        assert _template_requires_existing_cluster("nonexistent-template-xyz") is False

    def test_f5_bnk_2_2_returns_true_via_legacy_fallback(self):
        # f5-bnk-2.2 is not in stack_templates.json but is in the legacy slug set
        from services.stack_deployment_service import _template_requires_existing_cluster
        assert _template_requires_existing_cluster("f5-bnk-2.2") is True

    def test_falls_back_to_legacy_on_json_read_error(self):
        from pathlib import Path

        from services.stack_deployment_service import _template_requires_existing_cluster
        with patch.object(Path, "read_text", side_effect=OSError("disk error")):
            # Legacy fallback: bnk-on-k8s is in the hardcoded set too
            assert _template_requires_existing_cluster("bnk-on-k8s") is True
            assert _template_requires_existing_cluster("aws-k8s-foundation") is False


# ── stack_service helper ──────────────────────────────────────────────


class TestStackServiceHelper:
    def test_bnk_on_k8s_returns_true(self):
        from services.stack_service import _template_requires_existing_cluster_guidance
        assert _template_requires_existing_cluster_guidance("bnk-on-k8s") is True

    def test_infrastructure_slug_returns_false(self):
        from services.stack_service import _template_requires_existing_cluster_guidance
        assert _template_requires_existing_cluster_guidance("aws-k8s-foundation") is False

    def test_both_helpers_agree_on_known_slugs(self):
        from services.stack_deployment_service import _template_requires_existing_cluster
        from services.stack_service import _template_requires_existing_cluster_guidance
        for t in _load_templates():
            slug = t["slug"]
            assert _template_requires_existing_cluster(slug) == _template_requires_existing_cluster_guidance(slug), \
                f"Helpers disagree for slug '{slug}'"
