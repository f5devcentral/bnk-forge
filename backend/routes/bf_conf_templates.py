"""bf.conf template catalog — admin CRUD.

List: viewer-role (needed by the Project DPU Settings dropdown).
Create / update / delete: operator-role (admin surface under System →
System Administration).
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from schemas.dpu import (
    BfConfTemplateCreate,
    BfConfTemplateListResponse,
    BfConfTemplateResponse,
    BfConfTemplateSummary,
    BfConfTemplateUpdate,
)
from services.bf_conf_template_service import (
    BfConfTemplateService,
    classify_uplink_mode,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bf-conf-templates", tags=["bf-conf-templates"])


def _summary(row) -> BfConfTemplateSummary:
    """BfConfTemplateSummary + computed `uplink_mode` from the row's content."""
    s = BfConfTemplateSummary.model_validate(row)
    s.uplink_mode = classify_uplink_mode(row.content)
    return s


def _full(row) -> BfConfTemplateResponse:
    """BfConfTemplateResponse + computed `uplink_mode`."""
    r = BfConfTemplateResponse.model_validate(row)
    r.uplink_mode = classify_uplink_mode(row.content)
    return r


@router.get(
    "",
    response_model=BfConfTemplateListResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list bf.conf templates")
def list_bf_conf_templates(db: Session = Depends(get_db)) -> BfConfTemplateListResponse:
    """Lightweight list for dropdowns — omits the full template content."""
    rows = BfConfTemplateService(db).list_templates()
    return BfConfTemplateListResponse(templates=[_summary(r) for r in rows])


@router.get(
    "/{template_id}",
    response_model=BfConfTemplateResponse,
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get bf.conf template")
def get_bf_conf_template(template_id: int, db: Session = Depends(get_db)) -> BfConfTemplateResponse:
    return _full(BfConfTemplateService(db).get_template(template_id))


@router.post(
    "",
    response_model=BfConfTemplateResponse,
    status_code=201,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("create bf.conf template")
def create_bf_conf_template(
    data: BfConfTemplateCreate,
    db: Session = Depends(get_db),
) -> BfConfTemplateResponse:
    t = BfConfTemplateService(db).create_template(data)
    db.commit()
    return _full(t)


@router.patch(
    "/{template_id}",
    response_model=BfConfTemplateResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("update bf.conf template")
def update_bf_conf_template(
    template_id: int,
    data: BfConfTemplateUpdate,
    db: Session = Depends(get_db),
) -> BfConfTemplateResponse:
    t = BfConfTemplateService(db).update_template(template_id, data)
    db.commit()
    return _full(t)


@router.delete(
    "/{template_id}",
    status_code=204,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("delete bf.conf template")
def delete_bf_conf_template(template_id: int, db: Session = Depends(get_db)) -> None:
    BfConfTemplateService(db).delete_template(template_id)
    db.commit()
    return None
