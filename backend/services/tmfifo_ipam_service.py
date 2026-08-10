"""Cluster-scoped tmfifo IPAM Allocator (ADR-424).

Hands out unique /30 subnets per (host, DPU-rshim) link from a cluster's
tmfifo pool CIDR (default: 192.168.100.0/22).

Subnet structure for a /30:
  - Network address: e.g. 192.168.100.0
  - Host side (.1): e.g. 192.168.100.1
  - DPU side (.2): e.g. 192.168.100.2
  - Broadcast (.3): e.g. 192.168.100.3

NOTE: the first DPU IP in the default pool (192.168.100.2) collides with
the legacy single-host SSH-task fallback in tasks/ssh_tasks.py:
  dpu_host = host.dpu_info[0].get("mgmt_ip", "192.168.100.2")
In a mixed cluster where some DPUs use the legacy hardcoded IP, the first
IPAM allocation would assign the same address.  Avoid mixing legacy and
multi-host IPAM in the same L2 segment, or pick a non-overlapping pool CIDR.

Flash-path gap (tracked in issue #515) — flash_dpu.py and wait-dpu-ready
still use a static 192.168.100.2 DPU-side address rather than the
persisted IPAM IP from this allocator.  flash_dpu.py consumes
rendered_bf_conf (the persisted IPAM address for the host-side) and is
the only code that writes the DPU-side netplan; its fallback hardcodes
.2 so hosts 2..n fail.  This is NOT resolved here; fix is deferred to
issue #515 which will extend the bare-metal deploy path to read
dpu.dpu_tmfifo_ip from the DB.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import NamedTuple

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.errors import ValidationError
from database import DATABASE_URL
from models.dpu import Dpu
from models.kubernetes import BnkClusterConfig

logger = logging.getLogger(__name__)

# Namespace key for the two-argument pg_advisory_xact_lock(namespace, cluster_id)
# form. All ADR-424 cluster-scoped locks share this namespace so that the
# tmfifo allocator and assign_members serialise against the same lock space
# (the one-arg int8 form is a DISTINCT lock space and would not interlock).
ADR424_ADVISORY_LOCK_NAMESPACE = 424


class TmfifoAllocation(NamedTuple):
    host_ip: str  # e.g., "192.168.100.1"
    dpu_ip: str   # e.g., "192.168.100.2"
    subnet_cidr: str  # e.g., "192.168.100.0/30"


class TmfifoPoolAllocator:
    """Allocates /30 tmfifo subnets within a cluster's pool CIDR."""

    def __init__(self, db: Session):
        self.db = db

    def allocate_next_subnet(self, cluster_id: int) -> TmfifoAllocation:
        """Find and return the next available /30 subnet in the cluster's pool.

        Serialises concurrent callers via a PostgreSQL advisory transaction lock
        keyed on cluster_id so that two simultaneous /bnk-members calls cannot
        read the same "free" /30 and assign it to two different DPUs.  The lock
        is automatically released when the surrounding transaction commits or
        rolls back (pg_advisory_xact_lock semantics).  On SQLite (test env) the
        lock call is skipped — SQLite is single-process and needs no advisory
        locking.
        """
        if not DATABASE_URL.startswith("sqlite"):
            self.db.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                {"ns": ADR424_ADVISORY_LOCK_NAMESPACE, "k": cluster_id},
            )

        bnk_config = (
            self.db.query(BnkClusterConfig)
            .filter(BnkClusterConfig.cluster_id == cluster_id)
            .first()
        )
        if bnk_config is None:
            logger.warning(
                "allocate_next_subnet: no BnkClusterConfig for cluster %d — "
                "falling back to default pool 192.168.100.0/22; "
                "call assign_members first to persist a config",
                cluster_id,
            )
        pool_cidr = bnk_config.tmfifo_pool_cidr if bnk_config else "192.168.100.0/22"

        try:
            pool_net = ipaddress.ip_network(pool_cidr)
        except ValueError as exc:
            raise ValidationError("tmfifo_pool_cidr", f"Invalid tmfifo pool CIDR '{pool_cidr}': {exc}") from exc

        # Find all currently allocated DPU IPs in this cluster
        existing_dpus = (
            self.db.query(Dpu)
            .filter(Dpu.kubernetes_cluster_id == cluster_id)
            .all()
        )
        used_subnets = {
            ipaddress.ip_network(f"{dpu.dpu_tmfifo_ip}/30", strict=False)
            for dpu in existing_dpus
            if dpu.dpu_tmfifo_ip
        }

        # Iterate over /30 subnets in the pool — use the generator directly so
        # a large pool (e.g. /8 = 4M subnets) does not OOM when materialised.
        # subnets() raises ValueError lazily (on first iteration) when the pool
        # is already /30 or smaller; wrap the loop so a bad out-of-band DB
        # value surfaces as ValidationError (4xx) rather than a bare 500.
        try:
            for sub in pool_net.subnets(new_prefix=30):
                if sub not in used_subnets:
                    host_ip = str(sub[1])
                    dpu_ip = str(sub[2])
                    return TmfifoAllocation(
                        host_ip=host_ip,
                        dpu_ip=dpu_ip,
                        subnet_cidr=str(sub),
                    )
        except ValueError as exc:
            raise ValidationError(
                "tmfifo_pool_cidr",
                f"tmfifo pool CIDR '{pool_cidr}' cannot be subdivided into /30s: {exc}",
            ) from exc

        raise ValidationError("tmfifo_pool_cidr", f"tmfifo pool CIDR '{pool_cidr}' exhausted for cluster {cluster_id}")

    def assign_dpu_tmfifo(self, dpu: Dpu, cluster_id: int) -> TmfifoAllocation:
        """Assign a unique /30 tmfifo subnet to a DPU if not already allocated.

        Raises ValidationError (wrapping an IntegrityError) when a concurrent
        allocation races past the advisory lock and hits the partial unique index
        on (kubernetes_cluster_id, dpu_tmfifo_ip).
        """
        if dpu.kubernetes_cluster_id == cluster_id and dpu.host_tmfifo_ip and dpu.dpu_tmfifo_ip:
            # Already allocated for this cluster — idempotent return
            return TmfifoAllocation(
                host_ip=dpu.host_tmfifo_ip,
                dpu_ip=dpu.dpu_tmfifo_ip,
                subnet_cidr=str(ipaddress.ip_network(f"{dpu.dpu_tmfifo_ip}/30", strict=False)),
            )

        alloc = self.allocate_next_subnet(cluster_id)
        dpu.kubernetes_cluster_id = cluster_id
        dpu.host_tmfifo_ip = alloc.host_ip
        dpu.dpu_tmfifo_ip = alloc.dpu_ip
        self.db.add(dpu)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "dpu_tmfifo_ip",
                f"Concurrent allocation conflict for cluster {cluster_id}: "
                f"IP {alloc.dpu_ip} was already allocated (race condition). "
                "Retry the request.",
            ) from exc
        return alloc

    def release_dpu_tmfifo(self, dpu: Dpu) -> None:
        """Release a DPU's tmfifo allocation."""
        dpu.kubernetes_cluster_id = None
        dpu.host_tmfifo_ip = None
        dpu.dpu_tmfifo_ip = None
        self.db.add(dpu)
        self.db.flush()
