"""
Stack Template Seeding Service

Auto-seeds and updates default stack templates on application startup.
Templates are loaded from backend/data/stack_templates.json.

On every startup, existing templates are updated (upserted) so that changes
to stack_templates.json (e.g. new variables, namespace fixes) are always
reflected in the database without manual intervention.

ENG-006: This service retains its own db.commit() calls because it is called
exclusively from main.py startup and manages its own transaction.
"""
import json
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from models import StackTemplate

logger = logging.getLogger(__name__)

# Fields that get upserted on existing templates every startup.
# Preserves: id, slug, created_at, created_by, forked_from, is_public
UPSERT_FIELDS = [
    "name", "description", "category", "cloud_provider", "icon", "color",
    "estimated_time", "estimated_cost", "difficulty", "modules",
    "variable_templates", "prerequisites", "tags", "version",
    "is_active", "is_featured", "maturity", "outcomes", "platform_defaults",
    "bnk_version",
]

# JSON-only fields that carry runtime semantics but have no DB column.
# Stripped before creating/updating ORM objects.
_JSON_ONLY_FIELDS = {"requires_existing_cluster"}


def load_templates() -> list[dict]:
    """Load stack templates from JSON file."""
    templates_path = Path(__file__).parent.parent / "data" / "stack_templates.json"
    with open(templates_path, encoding="utf-8") as f:
        return json.load(f)


def seed_stack_templates(db: Session) -> tuple[int, int]:
    """
    Seed and update default stack templates in the database.

    - New templates (by slug) are created.
    - Existing templates are updated with the latest field values from JSON,
      so code changes to stack_templates.json always take effect on restart.

    Returns:
        Tuple of (created_count, updated_count)
    """
    created_count = 0
    updated_count = 0

    templates = load_templates()

    for template_data in templates:
        existing = db.query(StackTemplate).filter(
            StackTemplate.slug == template_data["slug"]
        ).first()

        if existing:
            # Upsert: update mutable fields from JSON
            changed = False
            for field in UPSERT_FIELDS:
                if field in template_data:
                    new_val = template_data[field]
                    old_val = getattr(existing, field, None)
                    if new_val != old_val:
                        setattr(existing, field, new_val)
                        changed = True
            if changed:
                db.commit()
                updated_count += 1
                logger.info(f"Updated stack template '{existing.slug}' from stack_templates.json")
        else:
            # Create new template — strip JSON-only fields that have no DB column
            orm_data = {k: v for k, v in template_data.items() if k not in _JSON_ONLY_FIELDS}
            template = StackTemplate(**orm_data)
            db.add(template)
            db.commit()
            db.refresh(template)
            created_count += 1
            logger.info(f"Created stack template '{template.slug}'")

    return created_count, updated_count
