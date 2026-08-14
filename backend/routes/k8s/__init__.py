"""
K8s route sub-package — split from the monolithic kubernetes.py (2,926 lines).

Each module owns a focused domain:
  clusters.py   — Cluster CRUD, test, refresh, namespaces, scan, adaptive modules
  resources.py  — Generic K8s resource CRUD, pod/node ops, metrics, rollouts
  f5bnk.py      — F5 BNK data (gateways, firewall, egress, topology, health)
  dpf.py        — NVIDIA DPF data (DPU devices, clusters, services, health)
  tunnels.py    — SSH tunnel management
  tmm_debug.py  — TMM debug sidecar diagnostics (tmctl, configview, bdt_cli)
  recovery.py   — Post-reboot recovery (CWC cert re-sync, platform restart)

All routers keep prefix="/api" to preserve existing API paths.
"""

from routes.k8s.clusters import router as clusters_router
from routes.k8s.crds import router as crds_router
from routes.k8s.dpf import router as dpf_router
from routes.k8s.f5bnk import router as f5bnk_router
from routes.k8s.llm_observability import router as llm_observability_router
from routes.k8s.recovery import router as recovery_router
from routes.k8s.resources import router as resources_router
from routes.k8s.tmm_debug import router as tmm_debug_router
from routes.k8s.topology import router as topology_router
from routes.k8s.tunnels import router as tunnels_router
from routes.k8s.waf_policies import router as waf_policies_router

__all__ = [
    "clusters_router",
    "crds_router",
    "resources_router",
    "f5bnk_router",
    "llm_observability_router",
    "dpf_router",
    "tmm_debug_router",
    "tunnels_router",
    "recovery_router",
    "topology_router",
    "waf_policies_router",
]
