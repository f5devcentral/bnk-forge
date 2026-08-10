"""Service for the admin-managed catalog of bf.conf templates.

Used at DPU flash time (Phase 4) to render per-DPU bf.conf files. The
admin tab under System → System Administration is the authoritative
management surface (CRUD).
"""

import logging
import re

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, ValidationError
from models.dpu import BfConfTemplate, ProjectDpuSettings
from schemas.dpu import BfConfTemplateCreate, BfConfTemplateUpdate

logger = logging.getLogger(__name__)


# Match a literal `bond0` token outside of YAML/shell strings — covers both
# the netplan stanza (`bonds:\n    bond0:`) and OVS bridge port lists
# (`OVS_BRIDGE1_PORTS="bond0 …"`).
_BOND0_RE = re.compile(r"\bbond0\b")
# Both seeds reference the physical PFs (`p0:` / `p1:` netplan stanzas,
# OVS port lists).
_PF_RE = re.compile(r"\b(p0|p1)\b")
# Shell-style line comment: everything from an unquoted `#` to EOL. We strip
# these before classifying because the non-LAG seed mentions "bond0" in a
# narrative comment ("# the LAG-specific bond0 stanza is intentionally
# omitted") which would otherwise mis-classify the template as LAG.
_SHELL_COMMENT_RE = re.compile(r"#.*$", re.MULTILINE)


def classify_uplink_mode(content: str | None) -> str:
    """Classify a bf.conf template by which host-side uplink ports it exposes.

    Returns:
      * ``"lag"``   — the template builds bond0 (LAG path); only bond0 is
        a valid VLAN attachment point.
      * ``"p0p1"``  — the template leaves p0/p1 directly attachable
        (non-LAG path); bond0 is not defined.
      * ``"unknown"`` — neither marker is present (custom template the
        operator added; UI should fall back to all choices).
    """
    if not content:
        return "unknown"
    # Strip shell comments so a narrative mention of "bond0" inside a
    # comment can't flip a non-LAG template's classification.
    code = _SHELL_COMMENT_RE.sub("", content)
    if _BOND0_RE.search(code):
        return "lag"
    if _PF_RE.search(code):
        return "p0p1"
    return "unknown"


class BfConfTemplateService:
    """CRUD for bf_conf_templates."""

    def __init__(self, db: Session):
        self.db = db

    def list_templates(self) -> list[BfConfTemplate]:
        return (
            self.db.query(BfConfTemplate)
            .order_by(BfConfTemplate.name.asc())
            .all()
        )

    def get_template(self, template_id: int) -> BfConfTemplate:
        t = self.db.get(BfConfTemplate, template_id)
        if t is None:
            raise NotFoundError("bf.conf template", template_id)
        return t

    def create_template(self, data: BfConfTemplateCreate) -> BfConfTemplate:
        t = BfConfTemplate(
            name=data.name,
            description=data.description,
            content=data.content,
            matching_bnk_version=data.matching_bnk_version,
        )
        self.db.add(t)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "name",
                f"bf.conf template '{data.name}' already exists",
            ) from exc
        logger.info("Created bf.conf template %s (%s)", t.id, t.name)
        return t

    def update_template(
        self, template_id: int, data: BfConfTemplateUpdate
    ) -> BfConfTemplate:
        t = self.get_template(template_id)
        provided = data.model_dump(exclude_unset=True)
        for field in ("name", "description", "content", "matching_bnk_version"):
            if field in provided:
                setattr(t, field, provided[field])
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "name",
                f"bf.conf template name '{data.name}' is already in use",
            ) from exc
        logger.info("Updated bf.conf template %s", template_id)
        return t

    def delete_template(self, template_id: int) -> None:
        t = self.get_template(template_id)
        # Refuse to delete if any project references this template — the
        # referencing project's bf_template_id would become NULL (ON DELETE
        # SET NULL), which could silently break a pending Flash action. Make
        # the operator unlink explicitly first.
        in_use = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.bf_template_id == template_id)
            .count()
        )
        if in_use:
            raise BadRequestError(
                f"bf.conf template is in use by {in_use} project(s); "
                "unassign it from those projects before deleting."
            )
        self.db.delete(t)
        self.db.flush()
        logger.info("Deleted bf.conf template %s", template_id)
