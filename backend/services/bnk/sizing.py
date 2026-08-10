"""BNK deployment size → resource mapping.

Single source of truth for the F5-published relationship between a BNK
CNEInstance deployment size and the 2Mi HugePages count that must be
reserved on each TMM node.

Published values (F5 BNK 2.x deployment guide):

    Size      2Mi pages / node    HugePages memory / node
    small     1536                3 GiB
    medium    3072                6 GiB
    large     6144                12 GiB
    max       12288               24 GiB

Consumers: ``services.hugepages_deploy_service`` and any future pre-flight
validators that need to reconcile the chosen CNEInstance size against
node-level reservations.
"""

import copy
from typing import Any, Literal, get_args

BnkDeploymentSize = Literal["small", "medium", "large", "max"]

BNK_DEPLOYMENT_SIZES: tuple[BnkDeploymentSize, ...] = get_args(BnkDeploymentSize)

SIZE_TO_HUGEPAGES_2MI: dict[BnkDeploymentSize, int] = {
    "small": 1536,
    "medium": 3072,
    "large": 6144,
    "max": 12288,
}

_MIB_PER_2MI_PAGE = 2
_MIB_PER_GIB = 1024


def _normalize(size: str) -> BnkDeploymentSize:
    normalized = size.lower()
    if normalized not in SIZE_TO_HUGEPAGES_2MI:
        raise ValueError(
            f"unknown BNK deployment size {size!r}; "
            f"expected one of {BNK_DEPLOYMENT_SIZES}"
        )
    return normalized  # type: ignore[return-value]


def hugepages_2mi_count(size: BnkDeploymentSize) -> int:
    """2Mi HugePage count per TMM node for the given BNK deployment size."""
    return SIZE_TO_HUGEPAGES_2MI[_normalize(size)]


def hugepages_memory_gib(size: BnkDeploymentSize) -> float:
    """Physical memory reserved for HugePages per TMM node, in GiB."""
    pages = hugepages_2mi_count(size)
    return pages * _MIB_PER_2MI_PAGE / _MIB_PER_GIB


# ---------------------------------------------------------------------------
# Lab sizing profile (issue #387 part C)
#
# BNK's default f5-tmm pod requests (~3.1 CPU / 8Gi + 3Gi HugePages) don't
# schedule on a 12GB Docker-Desktop/kind lab VM. These field-validated
# overrides shrink f5-tmm's four resource-bearing containers so TMM can
# schedule on a lab box. NON-PRODUCTION ONLY — see LAB_PROFILE_WARNING.
# ---------------------------------------------------------------------------

LAB_PROFILE_WARNING = (
    "Lab sizing is NON-PRODUCTION — blobd/debug/observer are shrunk and TMM "
    "2Gi memory OOMs under real traffic."
)

LAB_PROFILE_RESOURCES: dict[str, dict[str, Any]] = {
    "tmm": {
        "resources": {
            "requests": {"cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi"},
            "limits": {"cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi"},
        },
    },
    "blobd": {
        "resources": {
            "requests": {"cpu": "100m", "memory": "512Mi"},
            "limits": {"cpu": "200m", "memory": "512Mi"},
        },
    },
    "debug": {
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "100m", "memory": "256Mi"},
        },
    },
    "observer": {
        "resources": {
            "requests": {"cpu": "100m", "memory": "256Mi"},
            "limits": {"cpu": "100m", "memory": "256Mi"},
        },
    },
}


def lab_profile_helm_values() -> dict[str, Any]:
    """f5-tmm helm values override tree for the lab sizing profile.

    Shaped to match the chart's values path so it can be merged directly
    into a values override (e.g. as ``suggested_variables["f5-tmm"]`` on a
    ``DeploymentPlan``, or into a helm ``-f`` overlay):

        f5-tmm.tmm.resources
        f5-tmm.blobd.resources
        f5-tmm.debug.resources
        f5-tmm.observer.resources
    """
    return {"f5-tmm": copy.deepcopy(LAB_PROFILE_RESOURCES)}
