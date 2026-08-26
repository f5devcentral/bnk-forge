"""WAF Dashboard custom tabs — CRUD for user-defined tabs that group custom panels.

GET    /api/k8s/clusters/{id}/waf/dashboard-tabs             list tabs (auto-creates a default "Custom" tab on first use)
POST   /api/k8s/clusters/{id}/waf/dashboard-tabs             create tab
PATCH  /api/k8s/clusters/{id}/waf/dashboard-tabs/{tab_id}    rename tab
DELETE /api/k8s/clusters/{id}/waf/dashboard-tabs/{tab_id}    delete tab + its panels
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors, NotFoundError, BadRequestError
from database import get_db
from models.waf_panels import WafDashboardTab, WafPanel
from routes.auth import require_operator, require_viewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

DEFAULT_TAB_NAME = "Custom"


class TabCreate(BaseModel):
    name: str


class TabUpdate(BaseModel):
    name: str


def _tab_to_dict(t: WafDashboardTab) -> dict:
    return {
        "id":         t.id,
        "cluster_id": t.cluster_id,
        "name":       t.name,
        "tab_order":  t.tab_order,
    }


def _ensure_default_tab(cluster_id: int, db: Session) -> None:
    """Self-healing migration: if a cluster has legacy panels (tab_id NULL) but
    no tabs yet, create the default 'Custom' tab and adopt those panels into it.
    Clusters with zero tabs and zero orphan panels are left alone — the user may
    have deliberately deleted all their custom tabs."""
    existing = db.query(WafDashboardTab).filter(WafDashboardTab.cluster_id == cluster_id).count()
    if existing > 0:
        return
    orphans = db.query(WafPanel).filter(WafPanel.cluster_id == cluster_id, WafPanel.tab_id.is_(None)).count()
    if orphans == 0:
        return
    tab = WafDashboardTab(cluster_id=cluster_id, name=DEFAULT_TAB_NAME, tab_order=0)
    db.add(tab)
    db.commit()
    db.refresh(tab)
    db.query(WafPanel).filter(WafPanel.cluster_id == cluster_id, WafPanel.tab_id.is_(None)).update(
        {"tab_id": tab.id}, synchronize_session=False,
    )
    db.commit()


@router.get("/k8s/clusters/{cluster_id}/waf/dashboard-tabs", dependencies=[Depends(require_viewer)])
@handle_route_errors("list waf dashboard tabs")
def list_tabs(cluster_id: int, db: Session = Depends(get_db)):
    _ensure_default_tab(cluster_id, db)
    tabs = (
        db.query(WafDashboardTab)
        .filter(WafDashboardTab.cluster_id == cluster_id)
        .order_by(WafDashboardTab.tab_order, WafDashboardTab.id)
        .all()
    )
    return {"tabs": [_tab_to_dict(t) for t in tabs]}


@router.post("/k8s/clusters/{cluster_id}/waf/dashboard-tabs", dependencies=[Depends(require_operator)])
@handle_route_errors("create waf dashboard tab")
def create_tab(cluster_id: int, body: TabCreate, db: Session = Depends(get_db)):
    if not body.name.strip():
        raise BadRequestError("Tab name is required")
    _ensure_default_tab(cluster_id, db)
    max_order = (
        db.query(WafDashboardTab)
        .filter(WafDashboardTab.cluster_id == cluster_id)
        .count()
    )
    tab = WafDashboardTab(cluster_id=cluster_id, name=body.name.strip(), tab_order=max_order)
    db.add(tab)
    db.commit()
    db.refresh(tab)
    return _tab_to_dict(tab)


@router.patch("/k8s/clusters/{cluster_id}/waf/dashboard-tabs/{tab_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("rename waf dashboard tab")
def rename_tab(cluster_id: int, tab_id: int, body: TabUpdate, db: Session = Depends(get_db)):
    if not body.name.strip():
        raise BadRequestError("Tab name is required")
    tab = db.query(WafDashboardTab).filter(WafDashboardTab.id == tab_id, WafDashboardTab.cluster_id == cluster_id).first()
    if not tab:
        raise NotFoundError(f"Tab {tab_id} not found")
    tab.name = body.name.strip()
    db.commit()
    db.refresh(tab)
    return _tab_to_dict(tab)


@router.delete("/k8s/clusters/{cluster_id}/waf/dashboard-tabs/{tab_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete waf dashboard tab")
def delete_tab(cluster_id: int, tab_id: int, db: Session = Depends(get_db)):
    tab = db.query(WafDashboardTab).filter(WafDashboardTab.id == tab_id, WafDashboardTab.cluster_id == cluster_id).first()
    if not tab:
        raise NotFoundError(f"Tab {tab_id} not found")
    db.query(WafPanel).filter(WafPanel.cluster_id == cluster_id, WafPanel.tab_id == tab_id).delete(synchronize_session=False)
    db.delete(tab)
    db.commit()
    return {"deleted": tab_id}
