"""Service for BNK multi-host / multi-DPU cluster management (ADR-424).

Handles:
  - Creating / updating BnkClusterConfig side-table.
  - Assigning bare-metal hosts and DPUs to a cluster.
  - Triggering cluster-scoped tmfifo IPAM allocations for member DPUs.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from core.errors import BadRequestError, ConflictError, NotFoundError, ValidationError
from database import DATABASE_URL
from models.bare_metal import BareMetalHost
from models.dpu import Dpu
from models.kubernetes import BnkClusterConfig, KubernetesCluster
from services.tmfifo_ipam_service import ADR424_ADVISORY_LOCK_NAMESPACE, TmfifoPoolAllocator

logger = logging.getLogger(__name__)


class BnkClusterService:
    def __init__(self, db: Session):
        self.db = db
        self.ipam_allocator = TmfifoPoolAllocator(db)

    @staticmethod
    def _check_pool_cidr_valid(cidr: str) -> ipaddress.IPv4Network:
        """Parse cidr and reject if it cannot contain at least one /30 subnet.

        A pool prefix longer than /30 (e.g. /31, /32) cannot be subdivided
        into /30 allocations — calling subnets(new_prefix=30) on it raises a
        ValueError that would otherwise surface as an opaque 500.  Validate
        early and return 422 instead.
        """
        try:
            net = ipaddress.ip_network(cidr)
        except ValueError as exc:
            raise ValidationError("tmfifo_pool_cidr", f"Invalid CIDR '{cidr}': {exc}") from exc
        if not isinstance(net, ipaddress.IPv4Network):
            raise ValidationError(
                "tmfifo_pool_cidr",
                f"Pool CIDR '{cidr}' is not IPv4; an IPv4 /30 pool is required for tmfifo links.",
            )
        if net.prefixlen > 30:
            raise ValidationError(
                "tmfifo_pool_cidr",
                f"Pool CIDR '{cidr}' has prefix /{net.prefixlen} which is too long; "
                "the pool must be /30 or shorter to fit at least one /30 allocation.",
            )
        return net

    def cluster_membership(self, cluster_id: int) -> tuple[list[int], list[int]]:
        """Return (host_ids, dpu_ids) currently bound to this cluster.

        Used to seed the member dialog from real membership (ADR-424 #4)
        instead of re-applying the B-all default on every open.
        """
        host_ids = [
            h.id
            for h in self.db.query(BareMetalHost.id)
            .filter(BareMetalHost.kubernetes_cluster_id == cluster_id)
            .all()
        ]
        dpu_ids = [
            d.id
            for d in self.db.query(Dpu.id)
            .filter(Dpu.kubernetes_cluster_id == cluster_id)
            .all()
        ]
        return sorted(host_ids), sorted(dpu_ids)

    def bulk_cluster_membership(
        self, cluster_ids: list[int]
    ) -> dict[int, tuple[list[int], list[int]]]:
        """Return {cluster_id: (host_ids, dpu_ids)} for multiple clusters in 2 queries.

        Eliminates the 2N query pattern in list_all_clusters / list_project_clusters:
        instead of 2 queries per BNK cluster, run one grouped host query and one
        grouped DPU query, then bucket by cluster_id.  Single-sources the query
        logic used by cluster_membership (ADR-424 finding C).
        """
        if not cluster_ids:
            return {}
        host_rows = (
            self.db.query(BareMetalHost.id, BareMetalHost.kubernetes_cluster_id)
            .filter(BareMetalHost.kubernetes_cluster_id.in_(cluster_ids))
            .all()
        )
        dpu_rows = (
            self.db.query(Dpu.id, Dpu.kubernetes_cluster_id)
            .filter(Dpu.kubernetes_cluster_id.in_(cluster_ids))
            .all()
        )
        buckets: dict[int, list[list[int]]] = {cid: [[], []] for cid in cluster_ids}
        for h_id, c_id in host_rows:
            if c_id in buckets:
                buckets[c_id][0].append(h_id)
        for d_id, c_id in dpu_rows:
            if c_id in buckets:
                buckets[c_id][1].append(d_id)
        return {cid: (sorted(v[0]), sorted(v[1])) for cid, v in buckets.items()}

    def get_or_create_config(
        self,
        cluster_id: int,
        tmfifo_pool_cidr: str | None = None,
        join_transport: str | None = None,
        control_plane_host_id: int | None = None,
        *,
        _require_cp_member: bool = True,
    ) -> BnkClusterConfig:
        """Get or create BnkClusterConfig for a KubernetesCluster.

        All params are sentinels: None means "leave the stored value unchanged".
        On first create, None falls back to "192.168.100.0/22" / "rshim" so the
        row has sensible defaults even when called without explicit values.

        If tmfifo_pool_cidr is provided and differs from the current value, it is
        validated against existing DPU allocations — shrinking/changing the pool
        while DPUs already have IPs outside the new CIDR is rejected.

        _require_cp_member: when True (default, used by POST /bnk-config), the CP
        host must already be a cluster member.  Pass False from assign_members,
        which establishes membership in the same transaction after this call.
        """
        cluster = self.db.get(KubernetesCluster, cluster_id)
        if not cluster:
            raise NotFoundError("kubernetes_cluster", cluster_id)

        # Take the advisory lock unconditionally so that a pool-CIDR-only
        # mutation is also serialised against a concurrent assign_members
        # allocation.  Previously only taken when control_plane_host_id was
        # supplied, leaving a race window for concurrent CIDR mutations.
        # SQLite (test env) has no advisory locks — skip there.
        if not DATABASE_URL.startswith("sqlite"):
            self.db.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                {"ns": ADR424_ADVISORY_LOCK_NAMESPACE, "k": cluster_id},
            )

        # control_plane_host_id is a bare_metal_hosts FK. require_cluster_owner
        # authorizes the cluster, but nothing stops a request from pointing the
        # CP FK at another project's host — validate it belongs to the cluster's
        # project (mirrors assign_members' M2 guard) and 404 otherwise.
        # Also require cluster membership (ADR-424 minor) so cfg.control_plane_host_id
        # never points at a host whose is_control_plane is False.
        if control_plane_host_id is not None:
            filters = [
                BareMetalHost.id == control_plane_host_id,
                BareMetalHost.project_id == cluster.project_id,
            ]
            if _require_cp_member:
                filters.append(BareMetalHost.kubernetes_cluster_id == cluster_id)
            cp_host = self.db.query(BareMetalHost).filter(*filters).first()
            if not cp_host:
                if _require_cp_member:
                    # Check if the host exists but isn't a member for a better error.
                    exists = (
                        self.db.query(BareMetalHost)
                        .filter(
                            BareMetalHost.id == control_plane_host_id,
                            BareMetalHost.project_id == cluster.project_id,
                        )
                        .first()
                    )
                    if exists:
                        raise BadRequestError(
                            f"Host {control_plane_host_id} is not a member of cluster {cluster_id}. "
                            "Assign it via POST /bnk-members before setting it as the control plane."
                        )
                raise NotFoundError("bare_metal_host", control_plane_host_id)

        cfg = (
            self.db.query(BnkClusterConfig)
            .filter(BnkClusterConfig.cluster_id == cluster_id)
            .first()
        )
        if not cfg:
            if tmfifo_pool_cidr is not None:
                self._check_pool_cidr_valid(tmfifo_pool_cidr)
            cfg = BnkClusterConfig(
                cluster_id=cluster_id,
                tmfifo_pool_cidr=tmfifo_pool_cidr or "192.168.100.0/22",
                join_transport=join_transport or "rshim",
                control_plane_host_id=control_plane_host_id,
            )
            self.db.add(cfg)
        else:
            if tmfifo_pool_cidr is not None and tmfifo_pool_cidr != cfg.tmfifo_pool_cidr:
                self._validate_cidr_change(cluster_id, tmfifo_pool_cidr)
                cfg.tmfifo_pool_cidr = tmfifo_pool_cidr
            if join_transport is not None:
                cfg.join_transport = join_transport
            if control_plane_host_id is not None:
                cfg.control_plane_host_id = control_plane_host_id

        # Sync is_control_plane on BareMetalHost rows so that this field and
        # cfg.control_plane_host_id never diverge (ADR-424 cold audit B).
        # assign_members keeps them in lockstep for every member call; replicate
        # that logic here so that a bare POST /bnk-config (e.g. moving the CP
        # host without re-sending the full member list) stays consistent.
        if control_plane_host_id is not None:
            cluster_hosts = (
                self.db.query(BareMetalHost)
                .filter(BareMetalHost.kubernetes_cluster_id == cluster_id)
                .all()
            )
            for h in cluster_hosts:
                new_flag = h.id == control_plane_host_id
                if h.is_control_plane != new_flag:
                    h.is_control_plane = new_flag
                    self.db.add(h)

        self.db.flush()
        return cfg

    def _validate_cidr_change(self, cluster_id: int, new_cidr: str) -> None:
        """Reject a pool CIDR change if existing DPU IPs fall outside the new range."""
        new_net = self._check_pool_cidr_valid(new_cidr)

        existing_dpus = (
            self.db.query(Dpu)
            .filter(Dpu.kubernetes_cluster_id == cluster_id, Dpu.dpu_tmfifo_ip.isnot(None))
            .all()
        )
        for dpu in existing_dpus:
            if ipaddress.ip_address(dpu.dpu_tmfifo_ip) not in new_net:
                raise ValidationError(
                    "tmfifo_pool_cidr",
                    f"Cannot change pool CIDR to '{new_cidr}': "
                    f"DPU {dpu.id} has allocated IP {dpu.dpu_tmfifo_ip} outside the new range",
                )

    def assign_members(
        self,
        cluster_id: int,
        control_plane_host_id: int,
        host_ids: list[int],
        dpu_ids: list[int],
        tmfifo_pool_cidr: str | None = None,
    ) -> dict[str, Any]:
        """Assign hosts and DPUs to a BNK cluster and perform tmfifo IP allocations.

        Authorization: host and DPU IDs must belong to cluster.project_id — any
        ID that belongs to a different project is treated as not found (404) to
        prevent cross-project resource attachment.

        Cross-cluster guard: a host or DPU already bound to a DIFFERENT
        kubernetes_cluster_id (in the same project) is rejected with 409.
        The former reassign=True escape-hatch was removed (ADR-424 cold audit C)
        because it reconciled only the target cluster, leaving the source cluster
        inconsistent (dangling CP FK, source DPUs kept their cluster_id).

        Reconciliation (hosts): hosts previously in the cluster but absent from
        host_ids are unassigned; their DPUs' tmfifo allocations are released and
        those DPUs are removed from the cluster.  This ensures that changing the
        CP host never leaves two is_control_plane=True rows.

        Reconciliation (DPUs): DPUs previously in the cluster but absent from
        dpu_ids (while their host may still be present) are released and removed.
        The dialog always POSTs the full current dpu_ids list, so absence == removal.
        An empty dpu_ids list releases all directly-tracked DPUs for the cluster.
        """
        # Acquire an xact-scoped advisory lock before ANY read so that
        # host-only calls (dpu_ids=[]) are serialised just as DPU calls are.
        # Without this, two concurrent host-only calls can race the CP-host
        # reconciliation and leave two is_control_plane=True rows.
        # SQLite (test env) has no advisory locks — skip there.
        if not DATABASE_URL.startswith("sqlite"):
            self.db.execute(
                sa.text("SELECT pg_advisory_xact_lock(:ns, :k)"),
                {"ns": ADR424_ADVISORY_LOCK_NAMESPACE, "k": cluster_id},
            )

        cluster = self.db.get(KubernetesCluster, cluster_id)
        if not cluster:
            raise NotFoundError("kubernetes_cluster", cluster_id)

        if control_plane_host_id not in host_ids:
            host_ids = [control_plane_host_id, *[h for h in host_ids if h != control_plane_host_id]]

        # Ensure control plane host exists and belongs to cluster's project (M2).
        cp_host = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id == control_plane_host_id, BareMetalHost.project_id == cluster.project_id)
            .first()
        )
        if not cp_host:
            raise NotFoundError("bare_metal_host", control_plane_host_id)

        # --- Cross-cluster 409 guards (hoisted above reconciliation) ---
        # Load requested hosts early to check for cross-cluster conflicts BEFORE
        # any session mutations.  The invariant is self-contained: no mutation
        # has occurred yet, so a 409 abort needs no session-level rollback.
        new_host_id_set = set(host_ids)
        hosts = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.id.in_(host_ids), BareMetalHost.project_id == cluster.project_id)
            .with_for_update()
            .all()
        )
        found_host_ids = {h.id for h in hosts}
        missing_hosts = new_host_id_set - found_host_ids
        if missing_hosts:
            raise NotFoundError("bare_metal_host", sorted(missing_hosts))

        stolen_hosts = sorted(
            h.id for h in hosts
            if h.kubernetes_cluster_id is not None and h.kubernetes_cluster_id != cluster_id
        )
        if stolen_hosts:
            raise ConflictError(
                "bare_metal_host",
                f"Host(s) {stolen_hosts} already belong to a different cluster.",
            )

        new_dpu_id_set = set(dpu_ids or [])
        dpus: list[Dpu] = []
        if dpu_ids:
            dpus = (
                self.db.query(Dpu)
                .filter(Dpu.id.in_(dpu_ids), Dpu.project_id == cluster.project_id)
                .with_for_update()
                .all()
            )
            found_dpu_ids = {d.id for d in dpus}
            missing_dpus = new_dpu_id_set - found_dpu_ids
            if missing_dpus:
                raise NotFoundError("dpu", sorted(missing_dpus))

            stolen_dpus = sorted(
                d.id for d in dpus
                if d.kubernetes_cluster_id is not None and d.kubernetes_cluster_id != cluster_id
            )
            if stolen_dpus:
                raise ConflictError(
                    "dpu",
                    f"DPU(s) {stolen_dpus} already belong to a different cluster.",
                )

        # --- Mutations begin here ---

        # Get or update cluster config (sentinel semantics — only update if provided).
        # _require_cp_member=False: host membership is established below in the same
        # transaction, so the CP host cannot be a member yet at this call site.
        cfg = self.get_or_create_config(
            cluster_id=cluster_id,
            tmfifo_pool_cidr=tmfifo_pool_cidr,
            control_plane_host_id=control_plane_host_id,
            _require_cp_member=False,
        )

        # Reconcile: find hosts currently in this cluster that are NOT in the new list.
        # Unassign them (clear cluster membership + CP flag) and release their DPUs.
        current_cluster_hosts = (
            self.db.query(BareMetalHost)
            .filter(BareMetalHost.kubernetes_cluster_id == cluster_id)
            .all()
        )
        hosts_to_remove = [h for h in current_cluster_hosts if h.id not in new_host_id_set]
        if hosts_to_remove:
            removed_host_ips = {h.host_ip for h in hosts_to_remove}
            # Release DPUs that belong to the removed hosts.
            dpus_to_release = (
                self.db.query(Dpu)
                .filter(
                    Dpu.kubernetes_cluster_id == cluster_id,
                    Dpu.host_node_ip.in_(removed_host_ips),
                )
                .all()
            )
            for dpu in dpus_to_release:
                self.ipam_allocator.release_dpu_tmfifo(dpu)
            for h in hosts_to_remove:
                h.kubernetes_cluster_id = None
                h.is_control_plane = False
                self.db.add(h)

        # Reconcile DPUs: release any DPU currently in this cluster that is NOT in dpu_ids.
        # This covers the case where a DPU is unchecked in the dialog while its host stays —
        # the host-removal path above only releases DPUs whose owner host was dropped.
        stale_dpus = (
            self.db.query(Dpu)
            .filter(Dpu.kubernetes_cluster_id == cluster_id, Dpu.id.notin_(new_dpu_id_set or [-1]))
            .all()
        )
        for dpu in stale_dpus:
            self.ipam_allocator.release_dpu_tmfifo(dpu)

        for h in hosts:
            h.kubernetes_cluster_id = cluster_id
            h.is_control_plane = (h.id == control_plane_host_id)
            self.db.add(h)

        # Assign DPUs and allocate tmfifo IPs.
        member_host_ips = {h.host_ip for h in hosts}
        assigned_dpus = []
        for dpu in dpus:
            # Reject in-band DPUs whose owner host is not a cluster member (M3).
            if dpu.host_node_ip and dpu.host_node_ip not in member_host_ips:
                raise BadRequestError(
                    f"DPU {dpu.id} (host_node_ip={dpu.host_node_ip!r}) owner host "
                    f"is not a member of cluster {cluster_id}. "
                    "Add the DPU's host to host_ids first."
                )
            alloc = self.ipam_allocator.assign_dpu_tmfifo(dpu, cluster_id)
            assigned_dpus.append({
                "dpu_id": dpu.id,
                "dpu_name": dpu.name,
                "host_tmfifo_ip": alloc.host_ip,
                "dpu_tmfifo_ip": alloc.dpu_ip,
                "subnet_cidr": alloc.subnet_cidr,
            })

        # Flush only — the route handler commits.  The advisory lock acquired
        # at the top of this method is xact-scoped and is released at that commit.
        self.db.flush()

        return {
            "cluster_id": cluster_id,
            "control_plane_host_id": control_plane_host_id,
            "host_ids": host_ids,
            "assigned_dpus": assigned_dpus,
            "bnk_config": {
                "id": cfg.id,
                "cluster_id": cfg.cluster_id,
                "tmfifo_pool_cidr": cfg.tmfifo_pool_cidr,
                "join_transport": cfg.join_transport,
                "control_plane_host_id": cfg.control_plane_host_id,
                "host_ids": sorted(found_host_ids),
                "dpu_ids": sorted(d["dpu_id"] for d in assigned_dpus),
            },
        }
