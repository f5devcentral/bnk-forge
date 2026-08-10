"""
Bridges the Python module registry into the ModuleLibrary catalog.

PR #52 made ModuleLibrary the source of truth for the catalog-aware UI
(blueprint deploy gates, etc). Bare-metal SSH modules are still defined
as Python classes (see backend/modules/bare_metal/) until the catalog-
ization follow-up lands. This seeder mirrors them into ModuleLibrary on
startup so the catalog check finds them.

Idempotent: re-runs upsert by path.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from models import ModuleLibrary
from modules import get_module_registry
from modules.base import InputSpec, OutputSpec

logger = logging.getLogger(__name__)

PYTHON_REGISTRY_GIT_SOURCE = "builtin://python-registry"


def seed_python_modules(db: Session) -> tuple[int, int]:
    """Upsert ModuleLibrary rows for every entry in the Python module registry.

    Returns (created_count, updated_count).
    """
    registry = get_module_registry()
    created = 0
    updated = 0

    for path, module in registry.items():
        existing = db.query(ModuleLibrary).filter(ModuleLibrary.path == path).first()
        attrs = _attrs_from_python_module(module)
        if existing is None:
            db.add(ModuleLibrary(**attrs))
            created += 1
        elif existing.content_sha256 is not None:
            # D-033: a content-hashed catalog version row owns this path now
            # (e.g. adopted + grandfathered by a catalog sync). Its structural
            # fields are immutable — re-asserting seed values would abort the
            # whole seed transaction.
            continue
        else:
            for key, value in attrs.items():
                setattr(existing, key, value)
            updated += 1

    if created or updated:
        db.commit()

    return created, updated


def _input_spec_to_dict(key: str, spec: InputSpec) -> dict[str, Any]:
    """Serialize an InputSpec to the dict shape expected by the rest of the codebase.

    Uses the dict key as the name when spec.name is empty (InputSpec default).
    Includes from_module / from_output so the variable assembler can wire
    cross-module dependencies.
    """
    return {
        "name": spec.name if spec.name else key,
        "type": spec.type,
        "source": spec.source,
        "description": spec.description,
        "default": spec.default,
        "sensitive": spec.sensitive,
        "from_module": spec.from_module,
        "from_output": spec.from_output,
        "encoding": spec.encoding,
    }


def _output_spec_to_dict(key: str, spec: OutputSpec) -> dict[str, Any]:
    """Serialize an OutputSpec to the dict shape stored in outputs_metadata."""
    return {
        "name": key,
        "static_value": spec.static_value,
    }


def _build_inputs_metadata(module: Any) -> dict[str, Any]:
    """Build the inputs_metadata dict from a Python module's inputs class attr."""
    raw_inputs: dict[str, InputSpec] = getattr(module, "inputs", {})
    required: list[dict[str, Any]] = []
    optional: list[dict[str, Any]] = []

    for key, spec in raw_inputs.items():
        entry = _input_spec_to_dict(key, spec)
        if spec.required:
            required.append(entry)
        else:
            optional.append(entry)

    return {
        "required": required,
        "optional": optional,
        "providers": {},
        "compatibility": {},
    }


def _build_outputs_metadata(module: Any) -> list[dict[str, Any]]:
    """Build the outputs_metadata list from a Python module's outputs class attr."""
    raw_outputs: dict[str, OutputSpec] = getattr(module, "outputs", {})
    return [_output_spec_to_dict(key, spec) for key, spec in raw_outputs.items()]


def _attrs_from_python_module(module: Any) -> dict[str, Any]:
    """Map a Python module instance to ModuleLibrary column values."""
    return {
        "name": module.name,
        "path": module.path,
        "category": module.category or "bare-metal",
        "description": module.description or "",
        "git_source": PYTHON_REGISTRY_GIT_SOURCE,
        "version": getattr(module, "version", "1.0.0"),
        "module_source_kind": "builtin",
        "execution_engine": "ssh",
        "engine_type": "ssh",
        "deploy_model": "ssh",
        "is_active": True,
        "is_official": True,
        "is_tested": True,
        "dependencies": list(getattr(module, "dependencies", [])),
        "inputs_metadata": _build_inputs_metadata(module),
        "outputs_metadata": _build_outputs_metadata(module),
    }
