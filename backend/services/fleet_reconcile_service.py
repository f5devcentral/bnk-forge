"""Fleet reconcile service — maps siloed per-type facts into discovered labels.

D-022 Phase 1: reads already-captured facts from KubernetesCluster,
BareMetalHost, and Dpu and upserts a FleetMember row. Never overwrites
assigned_labels on re-reconcile.

D-022 Phase 4: adds per-type lifecycle_state derivation (same reconcile pass,
no new reads or runtime). Lifecycle is DERIVED from existing status columns —
it is not driven by any new provisioning engine.

Label keys follow kebab-case convention; values are lowercase strings.
None/empty labels are omitted (never key="").
"""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models.fleet import (
    KNOWN_MEMBER_TYPES,
    LIFECYCLE_STATE_ACTIVE,
    LIFECYCLE_STATE_DECOMMISSIONED,
    LIFECYCLE_STATE_PROVISIONING,
    LIFECYCLE_STATE_UNKNOWN,
    FleetMember,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-type lifecycle mappers (pure functions — no DB I/O)
#
# Each mapper receives the loaded target row and returns one of the
# LIFECYCLE_STATES strings. "draining" is kept in vocab but no mapper
# produces it in P4 — drain EXECUTION is out of scope (D-022 task desc).
# ---------------------------------------------------------------------------

def _lifecycle_for_cluster(cluster) -> str:
    """Map KubernetesCluster.status → unified lifecycle_state.

    connecting   → provisioning
    active       → active
    inactive     → decommissioned
    error        → unknown  (degraded state, not a lifecycle phase)
    NULL / other → unknown
    """
    status = (cluster.status or "").strip().lower()
    if status == "connecting":
        return LIFECYCLE_STATE_PROVISIONING
    if status == "active":
        return LIFECYCLE_STATE_ACTIVE
    if status == "inactive":
        return LIFECYCLE_STATE_DECOMMISSIONED
    return LIFECYCLE_STATE_UNKNOWN


def _lifecycle_for_host(host, latest_deployment=None) -> str:
    """Map BareMetalHost + optional latest BareMetalDeployment → lifecycle_state.

    Deployment status (wins when a deployment row exists):
      pending / discovering / planning / in_progress → provisioning
      completed                                       → active
      cancelled                                       → decommissioned

    Host fallback (no deployment, or deployment status unrecognised):
      pending / ssh_probe / bmc_probe / dpu_probe / assessment → provisioning
      completed                                                  → active
      failed                                                     → unknown
      NULL / other                                               → unknown
    """
    if latest_deployment is not None:
        dep_status = (latest_deployment.status or "").strip().lower()
        if dep_status in {"pending", "discovering", "planning", "in_progress"}:
            return LIFECYCLE_STATE_PROVISIONING
        if dep_status == "completed":
            return LIFECYCLE_STATE_ACTIVE
        if dep_status == "cancelled":
            return LIFECYCLE_STATE_DECOMMISSIONED
        # Fall through to host status if deployment status is unrecognised.

    host_status = (host.last_discovery_status or "").strip().lower()
    if host_status in {"pending", "ssh_probe", "bmc_probe", "dpu_probe", "assessment"}:
        return LIFECYCLE_STATE_PROVISIONING
    if host_status == "completed":
        return LIFECYCLE_STATE_ACTIVE
    if host_status == "failed":
        return LIFECYCLE_STATE_UNKNOWN
    return LIFECYCLE_STATE_UNKNOWN


def _lifecycle_for_dpu(dpu) -> str:
    """Map Dpu.flash_status (primary) + reachability fallback → lifecycle_state.

    uploading / waiting_reboot → provisioning
    os_online                  → active
    cancelled                  → decommissioned
    failed                     → unknown
    NULL but dpu_os_reachable  → active  (reachable means it's up)
    else                       → unknown
    """
    flash = (dpu.flash_status or "").strip().lower()
    if flash in {"uploading", "waiting_reboot"}:
        return LIFECYCLE_STATE_PROVISIONING
    if flash == "os_online":
        return LIFECYCLE_STATE_ACTIVE
    if flash == "cancelled":
        return LIFECYCLE_STATE_DECOMMISSIONED
    if flash == "failed":
        return LIFECYCLE_STATE_UNKNOWN
    # flash_status is NULL or unrecognised — fall back to reachability
    if dpu.dpu_os_reachable:
        return LIFECYCLE_STATE_ACTIVE
    return LIFECYCLE_STATE_UNKNOWN


# Dispatcher: member_type → mapper.  Any unregistered type → unknown (D-017).
_LIFECYCLE_MAPPERS = {
    "cluster": _lifecycle_for_cluster,
    "bare-metal-host": _lifecycle_for_host,
    "dpu": _lifecycle_for_dpu,
}


# ---------------------------------------------------------------------------
# Per-type fact → label mappers (pure functions — no DB I/O)
# ---------------------------------------------------------------------------

def _discovered_labels_for_cluster(cluster) -> dict[str, str]:
    """Map KubernetesCluster facts → discovered labels."""
    labels: dict[str, str] = {}

    # name label: makes `name=<cluster-name>` a valid selector expression.
    # Value is the display name as stored (not lowercased) so the label value
    # round-trips identically to what users see in the inventory list.
    if cluster.name:
        labels["name"] = cluster.name

    v = (cluster.cloud_provider or "").strip().lower()
    if v:
        labels["vendor"] = v

    v = (cluster.region or "").strip().lower()
    if v:
        labels["region"] = v

    v = (cluster.version or "").strip().lower()
    if v:
        labels["k8s-version"] = v

    v = (cluster.detected_platform_profile or "").strip().lower()
    if v:
        labels["platform-profile"] = v

    v = (cluster.detected_platform_provider or "").strip().lower()
    if v:
        labels["platform-provider"] = v

    v = (cluster.status or "").strip().lower()
    if v:
        labels["status"] = v

    # has-dpu: check platform_capabilities if available
    caps = cluster.platform_capabilities or {}
    if isinstance(caps, dict):
        # Use explicit key lookup (not `or`) to preserve False correctly.
        if "has_dpu" in caps:
            labels["has-dpu"] = "true" if caps["has_dpu"] else "false"
        elif "hasDpu" in caps:
            labels["has-dpu"] = "true" if caps["hasDpu"] else "false"

    return labels


def _discovered_labels_for_host(host) -> dict[str, str]:
    """Map BareMetalHost facts → discovered labels."""
    labels: dict[str, str] = {}

    # name label — enables `name=<host-name>` selectors.
    if host.name:
        labels["name"] = host.name

    os_info = host.os_info or {}
    if isinstance(os_info, dict):
        v = (os_info.get("os_type") or "").strip().lower()
        if v:
            labels["os"] = v

        v = (os_info.get("os_version") or "").strip().lower()
        if v:
            labels["os-version"] = v

        v = (os_info.get("arch") or "").strip().lower()
        if v:
            labels["arch"] = v

    k8s_info = host.k8s_info or {}
    if isinstance(k8s_info, dict):
        v = (k8s_info.get("version") or "").strip().lower()
        if v:
            labels["k8s-version"] = v

    # has-dpu: rshim_present column is the authoritative indicator
    if host.rshim_present is not None:
        labels["has-dpu"] = "true" if host.rshim_present else "false"

    v = (host.nic_mode or "").strip().lower()
    if v:
        labels["nic-mode"] = v

    v = (host.bmc_vendor or "").strip().lower()
    if v:
        labels["bmc-vendor"] = v

    v = (host.topology or "").strip().lower()
    if v:
        labels["topology"] = v

    return labels


def _discovered_labels_for_dpu(dpu) -> dict[str, str]:
    """Map Dpu facts → discovered labels."""
    labels: dict[str, str] = {}

    # name label — uses the resolved name (same logic as _load_target for DPU).
    # We use the same priority: name → bmc_ip → serial → "dpu-{id}".
    # Note: dpu.id may not be available in the pure mapper context so we use
    # what's accessible (name/bmc_ip/serial_number) without the fallback id.
    dpu_name = dpu.name or dpu.bmc_ip or dpu.serial_number or None
    if dpu_name:
        labels["name"] = dpu_name

    # DPUs are always NVIDIA BlueField at P1.
    labels["vendor"] = "nvidia-bluefield"

    v = (dpu.access_mode or "").strip().lower()
    if v:
        labels["access-mode"] = v

    v = (dpu.flash_status or "").strip().lower()
    if v:
        labels["flash-status"] = v

    v = (dpu.serial_number or "").strip().lower()
    if v:
        labels["serial"] = v

    if dpu.dpu_os_reachable is not None:
        labels["reachable"] = "true" if dpu.dpu_os_reachable else "false"

    return labels


# ---------------------------------------------------------------------------
# Reconcile dispatcher
# ---------------------------------------------------------------------------

def reconcile_fleet_member(db: Session, member_type: str, member_id: int) -> FleetMember | None:
    """Upsert a FleetMember record from the existing per-type facts.

    - Creates the membership row if absent.
    - Overwrites discovered_labels, name, project_id, health_status,
      lifecycle_state, last_reconciled_at.
    - NEVER overwrites assigned_labels.
    - Raises ValueError on unknown member_type (never silently no-ops).
    """
    if member_type not in KNOWN_MEMBER_TYPES:
        raise ValueError(
            f"Unknown member_type '{member_type}'. "
            f"Allowed: {sorted(KNOWN_MEMBER_TYPES)}"
        )

    target, discovered_labels, name, project_id, health_status, extra = _load_target(
        db, member_type, member_id
    )

    if target is None:
        logger.warning(
            "reconcile_fleet_member: target not found "
            "(member_type=%s member_id=%s) — skipping",
            member_type,
            member_id,
        )
        return None

    # Derive lifecycle_state using the per-type mapper; unregistered types → unknown.
    mapper = _LIFECYCLE_MAPPERS.get(member_type)
    if mapper is None:
        lifecycle_state = LIFECYCLE_STATE_UNKNOWN
    elif member_type == "bare-metal-host":
        # Host mapper needs the optional latest deployment row (loaded in extra).
        lifecycle_state = mapper(target, extra.get("latest_deployment"))
    else:
        lifecycle_state = mapper(target)

    existing = (
        db.query(FleetMember)
        .filter(
            FleetMember.member_type == member_type,
            FleetMember.member_id == member_id,
        )
        .first()
    )

    now = datetime.now(UTC)

    if existing is None:
        member = FleetMember(
            member_type=member_type,
            member_id=member_id,
            project_id=project_id,
            name=name,
            discovered_labels=discovered_labels,
            assigned_labels={},
            health_status=health_status,
            lifecycle_state=lifecycle_state,
            last_reconciled_at=now,
        )
        db.add(member)
        db.flush()
        db.refresh(member)
        logger.debug(
            "reconcile_fleet_member: created member id=%s (%s/%s) lifecycle=%s",
            member.id,
            member_type,
            member_id,
            lifecycle_state,
        )
    else:
        prev_lifecycle = existing.lifecycle_state
        existing.project_id = project_id
        existing.name = name
        existing.discovered_labels = discovered_labels
        existing.health_status = health_status
        existing.lifecycle_state = lifecycle_state
        existing.last_reconciled_at = now
        # NOTE: assigned_labels is deliberately NOT updated here.
        db.flush()
        db.refresh(existing)
        member = existing
        if prev_lifecycle != lifecycle_state:
            logger.info(
                "reconcile_fleet_member: lifecycle transition id=%s (%s/%s) %s → %s",
                member.id,
                member_type,
                member_id,
                prev_lifecycle,
                lifecycle_state,
            )
        else:
            logger.debug(
                "reconcile_fleet_member: updated member id=%s (%s/%s) lifecycle=%s",
                member.id,
                member_type,
                member_id,
                lifecycle_state,
            )

    return member


def _load_target(
    db: Session,
    member_type: str,
    member_id: int,
) -> tuple[object | None, dict[str, str], str, int | None, str | None, dict]:
    """Load the target row and derive (target, labels, name, project_id, health_status, extra).

    extra is a type-specific dict for any additional data needed by lifecycle mappers.
    For hosts: {"latest_deployment": <BareMetalDeployment|None>}
    For others: {}
    """
    from models.fleet import MEMBER_TYPE_CLUSTER, MEMBER_TYPE_DPU, MEMBER_TYPE_HOST

    if member_type == MEMBER_TYPE_CLUSTER:
        from models.kubernetes import KubernetesCluster
        target = db.query(KubernetesCluster).filter(KubernetesCluster.id == member_id).first()
        if target is None:
            return None, {}, "", None, None, {}
        labels = _discovered_labels_for_cluster(target)
        return target, labels, target.name, target.project_id, target.status, {}

    if member_type == MEMBER_TYPE_HOST:
        from models.bare_metal import BareMetalDeployment, BareMetalHost
        target = db.query(BareMetalHost).filter(BareMetalHost.id == member_id).first()
        if target is None:
            return None, {}, "", None, None, {}
        labels = _discovered_labels_for_host(target)
        health = target.last_discovery_status or None
        # Fetch the most-recent deployment for this host (lifecycle mapping needs it).
        latest_dep = (
            db.query(BareMetalDeployment)
            .filter(BareMetalDeployment.host_id == member_id)
            .order_by(BareMetalDeployment.id.desc())
            .first()
        )
        extra = {"latest_deployment": latest_dep}
        return target, labels, target.name, target.project_id, health, extra

    if member_type == MEMBER_TYPE_DPU:
        from models.dpu import Dpu
        target = db.query(Dpu).filter(Dpu.id == member_id).first()
        if target is None:
            return None, {}, "", None, None, {}
        labels = _discovered_labels_for_dpu(target)
        # Use flash_status as health_status proxy; fall back to last_discovery_status
        health = target.flash_status or target.last_discovery_status or None
        # DPU names are optional — fall back to bmc_ip or serial
        name = target.name or target.bmc_ip or target.serial_number or f"dpu-{member_id}"
        return target, labels, name, target.project_id, health, {}

    # Unreachable — guard against future code paths
    raise ValueError(f"Unhandled member_type: {member_type}")
