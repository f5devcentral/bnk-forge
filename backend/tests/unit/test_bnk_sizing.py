"""Unit tests for BNK deployment-size constants and helpers.

Covers the single source of truth for BNK size → hugepages mapping that F5
publishes for CNEInstance deployments.
"""

import pytest

from services.bnk.sizing import (
    BNK_DEPLOYMENT_SIZES,
    LAB_PROFILE_WARNING,
    SIZE_TO_HUGEPAGES_2MI,
    BnkDeploymentSize,
    hugepages_2mi_count,
    hugepages_memory_gib,
    lab_profile_helm_values,
)


class TestSizeMapping:
    def test_all_sizes_are_enumerated(self):
        assert BNK_DEPLOYMENT_SIZES == ("small", "medium", "large", "max")

    def test_hugepages_2mi_counts_match_f5_recommendation(self):
        # Per F5 BNK 2.x deployment guide:
        # Small=1536, Medium=3072, Large=6144, Max=12288 × 2Mi pages per TMM node.
        assert SIZE_TO_HUGEPAGES_2MI == {
            "small": 1536,
            "medium": 3072,
            "large": 6144,
            "max": 12288,
        }

    def test_every_enumerated_size_has_a_count(self):
        # Guard against a size being added to the tuple but not the map.
        assert set(BNK_DEPLOYMENT_SIZES) == set(SIZE_TO_HUGEPAGES_2MI.keys())


class TestHugepages2MiCount:
    @pytest.mark.parametrize(
        "size,expected",
        [
            ("small", 1536),
            ("medium", 3072),
            ("large", 6144),
            ("max", 12288),
        ],
    )
    def test_known_sizes(self, size: BnkDeploymentSize, expected: int):
        assert hugepages_2mi_count(size) == expected

    def test_is_case_insensitive(self):
        # F5 docs use "Small"/"Medium"/"Large"/"Max" with initial caps; the
        # UI and stack_templates.json use the same. Be permissive on input.
        assert hugepages_2mi_count("Small") == 1536  # type: ignore[arg-type]
        assert hugepages_2mi_count("MEDIUM") == 3072  # type: ignore[arg-type]

    def test_rejects_unknown_size(self):
        with pytest.raises(ValueError, match="unknown BNK deployment size"):
            hugepages_2mi_count("xlarge")  # type: ignore[arg-type]


class TestHugepagesMemoryGib:
    @pytest.mark.parametrize(
        "size,expected_gib",
        [
            # 2Mi pages → MiB = count × 2 → GiB = MiB / 1024
            ("small", 3.0),    # 1536 × 2 / 1024 = 3
            ("medium", 6.0),   # 3072 × 2 / 1024 = 6
            ("large", 12.0),   # 6144 × 2 / 1024 = 12
            ("max", 24.0),     # 12288 × 2 / 1024 = 24
        ],
    )
    def test_gib_preview_matches_physical_reservation(self, size: BnkDeploymentSize, expected_gib: float):
        assert hugepages_memory_gib(size) == expected_gib


class TestLabProfileHelmValues:
    """Field-validated NON-PRODUCTION lab overrides (issue #387 part C)."""

    def test_returns_f5_tmm_keyed_tree(self):
        values = lab_profile_helm_values()
        assert set(values.keys()) == {"f5-tmm"}
        assert set(values["f5-tmm"].keys()) == {"tmm", "blobd", "debug", "observer"}

    def test_tmm_resources(self):
        tmm = lab_profile_helm_values()["f5-tmm"]["tmm"]["resources"]
        assert tmm["requests"] == {"cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi"}
        assert tmm["limits"] == {"cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi"}

    def test_blobd_resources(self):
        blobd = lab_profile_helm_values()["f5-tmm"]["blobd"]["resources"]
        assert blobd["requests"] == {"cpu": "100m", "memory": "512Mi"}
        assert blobd["limits"] == {"cpu": "200m", "memory": "512Mi"}

    def test_debug_resources(self):
        debug = lab_profile_helm_values()["f5-tmm"]["debug"]["resources"]
        assert debug["requests"] == {"cpu": "100m", "memory": "256Mi"}
        assert debug["limits"] == {"cpu": "100m", "memory": "256Mi"}

    def test_observer_resources(self):
        observer = lab_profile_helm_values()["f5-tmm"]["observer"]["resources"]
        assert observer["requests"] == {"cpu": "100m", "memory": "256Mi"}
        assert observer["limits"] == {"cpu": "100m", "memory": "256Mi"}

    def test_warning_mentions_non_production_and_oom(self):
        assert "NON-PRODUCTION" in LAB_PROFILE_WARNING
        assert "OOM" in LAB_PROFILE_WARNING

    def test_returns_a_fresh_copy_each_call(self):
        # Guard against accidental shared-mutable-state bugs downstream
        # (e.g. a caller mutating the dict it got back).
        a = lab_profile_helm_values()
        a["f5-tmm"]["tmm"]["resources"]["requests"]["cpu"] = "999"
        b = lab_profile_helm_values()
        assert b["f5-tmm"]["tmm"]["resources"]["requests"]["cpu"] == "1"
