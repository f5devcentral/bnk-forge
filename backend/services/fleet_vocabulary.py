"""Controlled vocabulary for assigned fleet labels.

Assigned labels are operator-set: env, team, criticality, owner, fault-domain.
The PUT endpoint rejects any key outside ASSIGNED_LABEL_KEYS with a 422 error.

Discovered label keys (vendor, region, k8s-version, …) may NOT appear in an
assigned-label PUT — the namespaces are kept strictly separate.
"""

from core.errors import ValidationError
from models.fleet import KNOWN_MEMBER_TYPES

# Allowed keys for operator-set assigned labels.
ASSIGNED_LABEL_KEYS: frozenset[str] = frozenset({
    "env",
    "team",
    "criticality",
    "owner",
    "fault-domain",
})

# Discovered-only keys — rejected if submitted as assigned labels.
# This set is the union of all keys emitted by the per-type mappers.
_DISCOVERED_LABEL_KEYS: frozenset[str] = frozenset({
    # universal (all types)
    "name",
    # cluster
    "vendor", "region", "k8s-version", "platform-profile", "platform-provider",
    "has-dpu", "status",
    # host
    "os", "os-version", "arch", "nic-mode", "bmc-vendor", "topology",
    # dpu
    "access-mode", "flash-status", "serial", "reachable",
})


def validate_assigned_labels(labels: dict[str, str]) -> None:
    """Raise ValidationError if any key is outside ASSIGNED_LABEL_KEYS.

    Also rejects keys that belong to the discovered namespace so operators
    can't shadow auto-detected values via the assigned endpoint.
    """
    unknown = set(labels.keys()) - ASSIGNED_LABEL_KEYS
    if unknown:
        raise ValidationError(
            "labels",
            f"Unknown assigned label key(s): {sorted(unknown)}. "
            f"Allowed: {sorted(ASSIGNED_LABEL_KEYS)}",
        )

    # Belt-and-suspenders: also reject discovered-namespace keys.
    shadowed = set(labels.keys()) & _DISCOVERED_LABEL_KEYS
    if shadowed:
        raise ValidationError(
            "labels",
            f"Label key(s) {sorted(shadowed)} are in the discovered namespace "
            "and cannot be set via the assigned-labels endpoint.",
        )


def validate_member_type(member_type: str) -> None:
    """Raise ValidationError if member_type is not in the known set."""
    if member_type not in KNOWN_MEMBER_TYPES:
        raise ValidationError(
            "member_type",
            f"Unknown member_type '{member_type}'. "
            f"Allowed: {sorted(KNOWN_MEMBER_TYPES)}",
        )
