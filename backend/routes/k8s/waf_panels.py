"""WAF Panel Builder API — CRUD for per-cluster dashboard panels + data queries.

Each panel stores:
  - a query_template key (maps to a safe parameterised CH SQL fragment)
  - chart_type, time_range, width, title, panel_order

GET  /api/k8s/clusters/{id}/waf/panels              list panels ordered by panel_order
POST /api/k8s/clusters/{id}/waf/panels              create panel
PUT  /api/k8s/clusters/{id}/waf/panels/{panel_id}   update panel
DELETE /api/k8s/clusters/{id}/waf/panels/{panel_id} delete panel
GET  /api/k8s/clusters/{id}/waf/panels/{panel_id}/data  execute query → chart data
GET  /api/k8s/clusters/{id}/waf/panels/templates    list available query templates
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from core.errors import handle_route_errors, NotFoundError, BadRequestError
from database import get_db
from models.waf_panels import PANEL_QUERY_TEMPLATES, VALID_CHART_TYPES, VALID_TIME_RANGES, VALID_WIDTHS, WafPanel
from routes.auth import require_operator, require_viewer
from services.clickhouse import get_clickhouse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_RANGE_HOURS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720}
_BUCKET_HOURS = {"1h": 1, "24h": 1, "7d": 1, "30d": 6}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PanelCreate(BaseModel):
    title:          str
    chart_type:     str = "bar"
    query_template: str
    time_range:     str = "7d"
    width:          str = "full"
    panel_order:    int = 0
    tab_id:         int | None = None
    extra_config:   dict | None = None

    @field_validator("chart_type")
    @classmethod
    def valid_chart(cls, v: str) -> str:
        if v not in VALID_CHART_TYPES:
            raise ValueError(f"chart_type must be one of {VALID_CHART_TYPES}")
        return v

    @field_validator("query_template")
    @classmethod
    def valid_template(cls, v: str) -> str:
        if v not in PANEL_QUERY_TEMPLATES:
            raise ValueError(f"query_template must be one of {list(PANEL_QUERY_TEMPLATES)}")
        return v

    @field_validator("time_range")
    @classmethod
    def valid_range(cls, v: str) -> str:
        if v not in VALID_TIME_RANGES:
            raise ValueError(f"time_range must be one of {VALID_TIME_RANGES}")
        return v

    @field_validator("width")
    @classmethod
    def valid_width(cls, v: str) -> str:
        if v not in VALID_WIDTHS:
            raise ValueError(f"width must be one of {VALID_WIDTHS}")
        return v


class PanelUpdate(BaseModel):
    title:          str | None = None
    chart_type:     str | None = None
    query_template: str | None = None
    time_range:     str | None = None
    width:          str | None = None
    panel_order:    int | None = None
    tab_id:         int | None = None
    extra_config:   dict | None = None


def _panel_to_dict(p: WafPanel) -> dict:
    return {
        "id":             p.id,
        "cluster_id":     p.cluster_id,
        "tab_id":         p.tab_id,
        "title":          p.title,
        "chart_type":     p.chart_type,
        "query_template": p.query_template,
        "time_range":     p.time_range,
        "width":          p.width,
        "panel_order":    p.panel_order,
        "extra_config":   p.extra_config,
        "created_at":     p.created_at.isoformat() if p.created_at else None,
    }


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/k8s/clusters/{cluster_id}/waf/panels/templates", dependencies=[Depends(require_viewer)])
@handle_route_errors("list panel templates")
def list_templates(cluster_id: int):
    """Return all available query templates with descriptions."""
    templates = [
        {"key": k, "description": k.replace("_", " ").title()}
        for k in PANEL_QUERY_TEMPLATES
    ]
    return {"templates": templates, "chart_types": sorted(VALID_CHART_TYPES), "time_ranges": sorted(VALID_TIME_RANGES)}


@router.get("/k8s/clusters/{cluster_id}/waf/panels", dependencies=[Depends(require_viewer)])
@handle_route_errors("list waf panels")
def list_panels(
    cluster_id: int,
    tab_id: Annotated[int | None, Query()] = None,
    db: Session = Depends(get_db),
):
    q = db.query(WafPanel).filter(WafPanel.cluster_id == cluster_id)
    if tab_id is not None:
        q = q.filter(WafPanel.tab_id == (tab_id if tab_id != 0 else None))
    panels = q.order_by(WafPanel.panel_order, WafPanel.id).all()
    return {"panels": [_panel_to_dict(p) for p in panels]}


@router.post("/k8s/clusters/{cluster_id}/waf/panels", dependencies=[Depends(require_operator)])
@handle_route_errors("create waf panel")
def create_panel(cluster_id: int, body: PanelCreate, db: Session = Depends(get_db)):
    data = body.model_dump()
    if data.get("tab_id") == 0:
        data["tab_id"] = None
    panel = WafPanel(cluster_id=cluster_id, **data)
    db.add(panel)
    db.commit()
    db.refresh(panel)
    return _panel_to_dict(panel)


@router.put("/k8s/clusters/{cluster_id}/waf/panels/{panel_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("update waf panel")
def update_panel(cluster_id: int, panel_id: int, body: PanelUpdate, db: Session = Depends(get_db)):
    panel = db.query(WafPanel).filter(WafPanel.id == panel_id, WafPanel.cluster_id == cluster_id).first()
    if not panel:
        raise NotFoundError(f"Panel {panel_id} not found")
    data = body.model_dump(exclude_none=True)
    if data.get("tab_id") == 0:
        data["tab_id"] = None
    for field, value in data.items():
        setattr(panel, field, value)
    db.commit()
    db.refresh(panel)
    return _panel_to_dict(panel)


@router.delete("/k8s/clusters/{cluster_id}/waf/panels/{panel_id}", dependencies=[Depends(require_operator)])
@handle_route_errors("delete waf panel")
def delete_panel(cluster_id: int, panel_id: int, db: Session = Depends(get_db)):
    panel = db.query(WafPanel).filter(WafPanel.id == panel_id, WafPanel.cluster_id == cluster_id).first()
    if not panel:
        raise NotFoundError(f"Panel {panel_id} not found")
    db.delete(panel)
    db.commit()
    return {"deleted": panel_id}


@router.get("/k8s/clusters/{cluster_id}/waf/panels/{panel_id}/data", dependencies=[Depends(require_viewer)])
@handle_route_errors("execute panel query")
def panel_data(
    cluster_id: int,
    panel_id: int,
    time_range: Annotated[str, Query()] = "7d",
    db: Session = Depends(get_db),
):
    """Execute the panel's query template against ClickHouse and return chart data."""
    panel = db.query(WafPanel).filter(WafPanel.id == panel_id, WafPanel.cluster_id == cluster_id).first()
    if not panel:
        raise NotFoundError(f"Panel {panel_id} not found")

    ch = get_clickhouse()
    if not ch.available:
        return {"available": False, "reason": "ClickHouse not configured"}

    tr = time_range if time_range in VALID_TIME_RANGES else panel.time_range
    h = _RANGE_HOURS.get(tr, 168)
    bucket = _BUCKET_HOURS.get(tr, 1)

    # Safe: query_template is validated against PANEL_QUERY_TEMPLATES at creation
    sql_template = PANEL_QUERY_TEMPLATES[panel.query_template]
    sql = sql_template.format(cid=cluster_id, h=h, bucket=bucket)

    rows = ch.query(sql)
    return {
        "available": True,
        "panel_id": panel_id,
        "chart_type": panel.chart_type,
        "title": panel.title,
        "time_range": tr,
        "rows": rows,
    }
