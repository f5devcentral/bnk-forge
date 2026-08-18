"""
Stack Service — DB and business logic for stack templates and instances.

Extracted from routes/stacks.py to separate HTTP handling from domain logic.

Covers:
- Stack template CRUD (list, get, preview, required-inputs, save-as, duplicate, export, import, publish)
- Stack instance CRUD (create, list, get, delete)
- Stack deployment orchestration (deploy, run-deploy, status)

Delegates heavy deployment logic to StackDeploymentService.
"""

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified

from core.errors import BadRequestError, ForbiddenError, NotFoundError
from models import (
    ModuleLibrary,
    ProjectModule,
    StackInstance,
    StackTemplate,
)
from models.enums import ModuleStatus, StackInstanceStatus, TaskStatus
from modules import get_module_registry
from services.base_service import BaseService
from services.module_resolution import _map_order, resolve_module_row
from services.module_capabilities import serialize_engine_metadata
from services.workspace_manager import WorkspaceManager
from utils.security import is_sensitive_input

logger = logging.getLogger(__name__)

_TEMPLATES_JSON_PATH = Path(__file__).parent.parent / "data" / "stack_templates.json"


def _template_requires_existing_cluster_guidance(slug: str) -> bool:
    """Return True if the slug maps to a template that requires a pre-existing cluster.

    Reads the requires_existing_cluster flag from stack_templates.json, falling
    back to the legacy hardcoded slug set for templates not yet in the JSON file.
    """
    try:
        templates: list[dict] = json.loads(_TEMPLATES_JSON_PATH.read_text())
        for t in templates:
            if t.get("slug") == slug:
                return bool(t.get("requires_existing_cluster", False))
    except Exception:
        logger.warning("Could not load stack_templates.json; using legacy slug check")
    return slug in {"bnk-on-k8s", "f5-bnk-2.2"}


class StackService(BaseService):
    """Service layer for stack template and instance operations."""

    @staticmethod
    def _extract_stack_variable_defaults(variables: dict) -> dict:
        """Flatten stack-supplied variables into project-level defaults.

        Accepts both legacy flat stack-wide inputs and module-scoped inputs
        (`{"module/path": {"name": "value"}}`) so shared values such as
        `user_ip` remain available across deploy/retry flows.
        """
        defaults: dict = {}
        if not isinstance(variables, dict):
            return defaults

        for key, value in variables.items():
            if isinstance(value, dict):
                for nested_key, nested_value in value.items():
                    if not isinstance(nested_value, dict):
                        defaults[nested_key] = nested_value
                continue

            if "/" not in key:
                defaults[key] = value

        return defaults

    def _persist_stack_variable_defaults(self, project, variables: dict | None) -> None:
        """Persist stack-derived shared defaults onto project.project_variables.

        Uses explicit JSON dirty-flagging so updates are reliably written even when
        the JSON payload is semantically similar to previous in-memory state.
        """
        if not variables or not isinstance(variables, dict):
            return

        existing_project_vars = project.project_variables if isinstance(project.project_variables, dict) else {}
        project_vars = dict(existing_project_vars)
        existing_variable_defaults = project_vars.get("variable_defaults", {})
        variable_defaults = dict(existing_variable_defaults) if isinstance(existing_variable_defaults, dict) else {}

        for key, value in self._extract_stack_variable_defaults(variables).items():
            variable_defaults[key] = value

        project_vars["variable_defaults"] = variable_defaults
        project.project_variables = project_vars
        flag_modified(project, "project_variables")
        self.db.add(project)

    # ================================================================
    # Lookups
    # ================================================================

    def _get_stack(self, project_id: int, stack_id: int, for_update: bool = False) -> StackInstance:
        query = self.db.query(StackInstance).filter(
            StackInstance.id == stack_id,
            StackInstance.project_id == project_id,
        )
        if for_update:
            query = query.with_for_update(skip_locked=True)
        stack = query.first()
        if not stack:
            raise NotFoundError("stack_instance", f"{stack_id} in project {project_id}")
        return stack

    def _get_template_by_slug(self, slug: str) -> StackTemplate:
        template = self.db.query(StackTemplate).filter(
            StackTemplate.slug == slug,
            StackTemplate.is_active,
        ).first()
        if not template:
            raise NotFoundError("stack_template", slug)
        return template

    def _get_template_by_id(self, template_id: int) -> StackTemplate:
        template = self.db.query(StackTemplate).filter(
            StackTemplate.id == template_id,
        ).first()
        if not template:
            raise NotFoundError("stack_template", template_id)
        return template

    def _generate_unique_slug(self, name: str) -> str:
        """Generate a unique slug from a name."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not self.db.query(StackTemplate).filter(StackTemplate.slug == slug).first():
            return slug
        counter = 1
        while self.db.query(StackTemplate).filter(StackTemplate.slug == f"{slug}-{counter}").first():
            counter += 1
        return f"{slug}-{counter}"

    def _serialize_template_modules_with_capabilities(self, modules: list | None) -> list:
        """Attach additive engine/capability metadata to template module entries."""
        if not isinstance(modules, list):
            return []

        module_paths = {
            str(module.get("path"))
            for module in modules
            if isinstance(module, dict) and isinstance(module.get("path"), str)
        }

        modules_by_path: dict[str, ModuleLibrary] = {}
        if module_paths:
            module_rows = (
                self.db.query(ModuleLibrary)
                .filter(
                    ModuleLibrary.path.in_(module_paths),
                    ModuleLibrary.is_active,
                )
                # D-033: multiple version rows may share a path. Ordering lives in
                # services.module_resolution so this map cannot drift away from
                # what stack deploy resolves (#90 F8).
                .order_by(*_map_order())
                .all()
            )
            modules_by_path = {row.path: row for row in module_rows if isinstance(row.path, str)}

        # Defensive fallback for in-tree Python modules (bare-metal SSH).
        # The startup seeder mirrors these into ModuleLibrary, but on first boot
        # or before the seeder runs, the registry is the canonical source.
        python_registry = get_module_registry()

        serialized_modules: list = []
        for module in modules:
            if not isinstance(module, dict):
                serialized_modules.append(module)
                continue

            serialized = dict(module)
            module_path = module.get("path")
            library_module = modules_by_path.get(module_path) if isinstance(module_path, str) else None
            if library_module is not None:
                serialized.update(serialize_engine_metadata(library_module))
                serialized["module_catalog_status"] = "available"
            elif isinstance(module_path, str) and module_path in python_registry:
                serialized["module_catalog_status"] = "available"
            else:
                serialized["module_catalog_status"] = "missing"
                serialized["module_catalog_message"] = (
                    f"Module '{module_path}' is not present in the active module catalog. "
                    "Sync/import the required module source before deploying this blueprint."
                )

            serialized_modules.append(serialized)

        return serialized_modules

    # ================================================================
    # Stack Templates
    # ================================================================

    def list_templates(
        self,
        category: str | None = None,
        cloud_provider: str | None = None,
        tags: str | None = None,
        is_featured: bool | None = None,
        created_by: str | None = None,
        is_public: bool | None = None,
    ) -> list[StackTemplate]:
        """List active stack templates with optional filters."""
        query = self.db.query(StackTemplate).filter(StackTemplate.is_active)

        if category:
            query = query.filter(StackTemplate.category == category)
        if cloud_provider:
            query = query.filter(or_(
                StackTemplate.cloud_provider == cloud_provider,
                StackTemplate.cloud_provider == "any",
            ))
        if is_featured is not None:
            query = query.filter(StackTemplate.is_featured == is_featured)
        if created_by is not None:
            query = query.filter(StackTemplate.created_by == created_by)
        if is_public is not None:
            query = query.filter(StackTemplate.is_public == is_public)

        templates = query.order_by(StackTemplate.is_featured.desc(), StackTemplate.name).all()

        if tags:
            tag_list = [t.strip() for t in tags.split(",")]
            templates = [t for t in templates if t.tags and any(tag in t.tags for tag in tag_list)]

        return templates

    def get_template(self, slug: str) -> dict:
        """Get a stack template by slug."""
        template = self._get_template_by_slug(slug)
        return {
            "id": template.id,
            "name": template.name,
            "slug": template.slug,
            "description": template.description,
            "category": template.category,
            "cloud_provider": template.cloud_provider,
            "icon": template.icon,
            "color": template.color,
            "estimated_time": template.estimated_time,
            "estimated_cost": template.estimated_cost,
            "difficulty": template.difficulty,
            "modules": self._serialize_template_modules_with_capabilities(template.modules),
            "variable_templates": template.variable_templates,
            "prerequisites": template.prerequisites,
            "tags": template.tags,
            "maturity": template.maturity,
            "outcomes": template.outcomes,
            "platform_defaults": template.platform_defaults,
            "is_active": template.is_active,
            "is_featured": template.is_featured,
            "version": template.version,
            "created_by": template.created_by,
            "is_public": template.is_public,
            "forked_from": template.forked_from,
            "created_at": template.created_at,
            "updated_at": template.updated_at,
        }

    def preview_template(self, slug: str) -> dict:
        """Preview what modules will be created from a stack template."""
        template = self._get_template_by_slug(slug)
        modules = self._serialize_template_modules_with_capabilities(template.modules)
        return {
            "modules": modules,
            "prerequisites": template.prerequisites or [],
            "estimated_cost": template.estimated_cost,
            "estimated_time": template.estimated_time,
            "total_modules": len(modules),
        }

    def get_required_inputs(
        self,
        slug: str,
        *,
        project_id: int | None = None,
        bare_metal_host_id: int | None = None,
    ) -> dict:
        """Get all required user inputs for a stack template."""
        template = self._get_template_by_slug(slug)
        modules = template.modules or []
        required_inputs = {}
        all_inputs = []
        missing_modules: list[dict[str, str]] = []

        # Resolve project context for pre-populating defaults (BM-020)
        project_ctx = None
        context_defaults: dict[str, Any] = {}
        if project_id:
            from services.execution.blueprint_context import (
                resolve_dialog_field_default,
                resolve_project_context,
                transform_for_module,
            )
            project_ctx = resolve_project_context(
                self.db, project_id, bare_metal_host_id=bare_metal_host_id
            )
            # Canonical vars available to all modules
            context_defaults = project_ctx.as_low_precedence_vars()

        for module_def in modules:
            module_path = module_def.get("path")
            module_name = module_def.get("name", module_path)
            stack_variables = module_def.get("variables", {})

            library_module = resolve_module_row(self.db, module_path)

            if not library_module:
                logger.warning(f"Module '{module_path}' not found in library, skipping input analysis")
                missing_modules.append({
                    "path": str(module_path),
                    "name": str(module_name),
                    "message": (
                        f"Module '{module_path}' is missing from the active module catalog. "
                        "Required-input analysis for this module is unavailable until the source is synced."
                    ),
                })
                continue

            inputs_metadata = library_module.inputs_metadata or {}
            required_inputs_def = inputs_metadata.get("required", [])
            module_required = []

            for inp in required_inputs_def:
                inp_name = inp.get("name")
                inp_source = inp.get("source", "user")
                module_default = inp.get("default")

                if inp_source != "user":
                    continue
                if inp_name in {"jwt_token", "cne_pull_secret"}:
                    # Provided via project/system secrets flow; not a manual blueprint input.
                    continue
                # Only skip if the stack template provides a non-empty value.
                # Empty string placeholders (e.g., "bfb_url": "") mean
                # "this variable is wired but needs user input."
                stack_val = stack_variables.get(inp_name)
                if stack_val is not None and str(stack_val).strip():
                    continue
                if module_default is not None:
                    continue

                input_def = {
                    "name": inp_name,
                    "type": inp.get("type", "string"),
                    "description": inp.get("description", ""),
                    "example": inp.get("example", ""),
                    "default": None,
                    "module_path": module_path,
                    "module_name": module_name,
                    "required": True,
                    "sensitive": is_sensitive_input(inp),
                    "validation": inp.get("validation"),
                }

                # Attach catalog options for inputs that map to known catalogs

                # DOCA release catalog — single dropdown selecting by row ID
                if inp_name == "doca_release_id":
                    from models.dpu import BluefieldSoftwareImage

                    releases = (
                        self.db.query(BluefieldSoftwareImage)
                        .filter(BluefieldSoftwareImage.doca_version.isnot(None))
                        .order_by(BluefieldSoftwareImage.doca_version.desc())
                        .all()
                    )
                    if releases:
                        input_def["options"] = [
                            {
                                "label": f"DOCA {rel.doca_version} — {rel.host_os or '?'} {rel.host_os_version or ''} {rel.host_arch or '?'}"
                                + (" (default)" if rel.is_default else ""),
                                "value": str(rel.id),
                                "description": f"BFB: {rel.image_filename or 'N/A'} | Host pkg: {rel.doca_host_url or 'N/A'}",
                            }
                            for rel in releases
                        ]
                        default_rel = next((r for r in releases if r.is_default), None)
                        if default_rel and input_def.get("default") is None:
                            input_def["default"] = str(default_rel.id)

                # Pre-populate default from project context (BM-020)
                if project_ctx is not None:
                    # 1. Check canonical context vars (direct name match)
                    ctx_val = context_defaults.get(inp_name)
                    # 2. Check module-specific transform output (catalog variable names)
                    if ctx_val is None:
                        module_transform_vars = transform_for_module(
                            module_path or "", {**stack_variables, **context_defaults}, project_ctx
                        )
                        ctx_val = module_transform_vars.get(inp_name)
                    # 3. Dialog-field → context scalar mapping (handles name impedance mismatches,
                    #    e.g. external_selfip (dialog) vs external_self_ips (catalog list))
                    if ctx_val is None:
                        ctx_val = resolve_dialog_field_default(
                            inp_name, module_path or "", project_ctx
                        )
                    if ctx_val is not None:
                        input_def["default"] = ctx_val if isinstance(ctx_val, str) else str(ctx_val)

                module_required.append(input_def)
                all_inputs.append(input_def)

            # Surface optional inputs that have catalog backing (e.g., version dropdowns).
            # These have defaults but we still show them so users can pick from available
            # chart versions rather than blindly accepting the module default.
            optional_inputs_def = inputs_metadata.get("optional", [])
            for inp in optional_inputs_def:
                inp_name = inp.get("name")
                inp_source = inp.get("source", "user")
                if inp_source != "user":
                    continue

                # Check if this input has catalog options
                catalog_options = None
                catalog_default = None

                # DOCA release catalog — single dropdown selecting by row ID
                if inp_name == "doca_release_id":
                    from models.dpu import BluefieldSoftwareImage

                    releases = (
                        self.db.query(BluefieldSoftwareImage)
                        .filter(BluefieldSoftwareImage.doca_version.isnot(None))
                        .order_by(BluefieldSoftwareImage.doca_version.desc())
                        .all()
                    )
                    if releases:
                        catalog_options = [
                            {
                                "label": f"DOCA {rel.doca_version} — {rel.host_os or '?'} {rel.host_os_version or ''} {rel.host_arch or '?'}"
                                + (" (default)" if rel.is_default else ""),
                                "value": str(rel.id),
                                "description": f"BFB: {rel.image_filename or 'N/A'} | Host pkg: {rel.doca_host_url or 'N/A'}",
                            }
                            for rel in releases
                        ]
                        default_rel = next((r for r in releases if r.is_default), None)
                        if default_rel:
                            catalog_default = str(default_rel.id)

                if catalog_options is None:
                    continue  # No catalog backing — don't surface this optional input

                input_def = {
                    "name": inp_name,
                    "type": inp.get("type", "string"),
                    "description": inp.get("description", ""),
                    "example": inp.get("example", ""),
                    "default": catalog_default or inp.get("default"),
                    "module_path": module_path,
                    "module_name": module_name,
                    "required": False,
                    "sensitive": inp.get("sensitive", False),
                    "validation": inp.get("validation"),
                    "options": catalog_options,
                }

                module_required.append(input_def)
                all_inputs.append(input_def)

            if module_required:
                required_inputs[module_path] = module_required

        # Deduplicate inputs that appear across multiple modules.
        # Keep the first occurrence (which typically comes from the earliest module
        # in the chain). Later modules will receive the value through auto-wiring.
        seen_input_names: set[str] = set()
        deduped_all_inputs: list[dict] = []
        deduped_by_module: dict[str, list] = {}
        for inp in all_inputs:
            if inp["name"] not in seen_input_names:
                seen_input_names.add(inp["name"])
                deduped_all_inputs.append(inp)
                mp = inp["module_path"]
                if mp not in deduped_by_module:
                    deduped_by_module[mp] = []
                deduped_by_module[mp].append(inp)

        all_inputs = deduped_all_inputs
        required_inputs = deduped_by_module

        summary: list[dict[str, str]] = []
        if slug == "aws-k8s-foundation":
            input_names = {inp["name"] for inp in all_inputs}
            if "user_ip" in input_names:
                summary.append({
                    "label": "Why your IP is needed",
                    "value": "Used to restrict administrative/security-group access to the EKS environment during initial provisioning.",
                })
            if "ecr_registry" in input_names:
                summary.append({
                    "label": "About ECR registry",
                    "value": "This input is still exposed by one of the AWS foundation modules, but the blueprint itself does not clearly demonstrate an active ECR dependency in its template definition. Treat it as suspicious/legacy until the underlying module metadata is reviewed.",
                })
        elif _template_requires_existing_cluster_guidance(slug):
            summary.append({
                "label": "Required BNK project secrets",
                "value": "Configure jwt_token (license token) and cne_pull_secret (FAR pull secret) in Project Settings → Secrets. They are validated before deploy and not requested as module inputs.",
            })
            summary.append({
                "label": "FAR credential fallback",
                "value": "If project cne_pull_secret is not set, Forge can use the encrypted global FAR fallback from System → Defaults.",
            })

        return {
            "template_slug": slug,
            "template_name": template.name,
            "inputs_by_module": required_inputs,
            "all_inputs": all_inputs,
            "total_required": sum(1 for inp in all_inputs if inp["required"]),
            "total_optional": sum(1 for inp in all_inputs if not inp["required"]),
            "missing_modules": missing_modules,
            "summary": summary,
        }

    def save_project_as_template(self, project_id: int, template_data: dict) -> StackTemplate:
        """Save a project's current module configuration as a stack template."""
        project = self._get_project(project_id)

        modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.project_id == project_id)
            .order_by(ProjectModule.deployment_order)
            .all()
        )
        if not modules:
            raise BadRequestError("Project has no modules to save", code="NO_MODULES")

        slug = self._generate_unique_slug(template_data["name"])

        template_modules = []
        for module in modules:
            lm = module.library_module
            template_modules.append({
                "path": lm.source_path if lm.source_type == "git" else lm.path,
                "name": lm.name,
                "required": True,
                "description": lm.description,
                "variables": module.variables or {},
            })

        template = StackTemplate(
            name=template_data["name"],
            slug=slug,
            description=template_data.get("description") or f"Custom stack created from project '{project.name}'",
            category=template_data.get("category", "custom"),
            cloud_provider=project.cloud_provider or "any",
            icon=template_data.get("icon") or "Package",
            color=template_data.get("color") or "blue",
            estimated_time=None,
            estimated_cost=None,
            difficulty="intermediate",
            modules=template_modules,
            variable_templates={},
            prerequisites=[],
            tags=template_data.get("tags") or [],
            version=template_data.get("version", "1.0.0"),
            created_by="user",
            is_public=template_data.get("is_public", False),
            forked_from=None,
            is_active=True,
            is_featured=False,
        )

        self.db.add(template)
        self.db.flush()
        self.db.refresh(template)

        logger.info(f"Created custom template '{template.name}' from project {project_id}")
        return template

    def duplicate_template(self, template_id: int, duplicate_data: dict) -> StackTemplate:
        """Duplicate an existing stack template."""
        original = self._get_template_by_id(template_id)
        slug = self._generate_unique_slug(duplicate_data["name"])

        duplicate = StackTemplate(
            name=duplicate_data["name"],
            slug=slug,
            description=duplicate_data.get("description") or original.description,
            category=original.category,
            cloud_provider=original.cloud_provider,
            icon=original.icon,
            color=original.color,
            estimated_time=original.estimated_time,
            estimated_cost=original.estimated_cost,
            difficulty=original.difficulty,
            modules=original.modules,
            variable_templates=original.variable_templates,
            prerequisites=original.prerequisites,
            tags=original.tags,
            version=duplicate_data.get("version", "1.0.0"),
            created_by="user",
            is_public=False,
            forked_from=template_id,
            is_active=True,
            is_featured=False,
        )

        self.db.add(duplicate)
        self.db.flush()
        self.db.refresh(duplicate)

        logger.info(f"Duplicated template {template_id} as '{duplicate.name}'")
        return duplicate

    def export_template(self, template_id: int) -> dict:
        """Export a stack template as JSON."""
        template = self._get_template_by_id(template_id)
        logger.info(f"Exported template {template_id}")
        return {
            "name": template.name,
            "description": template.description,
            "category": template.category,
            "cloud_provider": template.cloud_provider,
            "icon": template.icon,
            "color": template.color,
            "estimated_time": template.estimated_time,
            "estimated_cost": template.estimated_cost,
            "difficulty": template.difficulty,
            "modules": template.modules,
            "variable_templates": template.variable_templates,
            "prerequisites": template.prerequisites,
            "tags": template.tags,
            "version": template.version,
            "export_version": "1.0",
            "export_date": datetime.now(UTC).isoformat(),
        }

    def import_template(self, template_json: dict) -> StackTemplate:
        """Import a stack template from JSON."""
        if "name" not in template_json or "modules" not in template_json:
            raise BadRequestError("Invalid template JSON: missing name or modules", code="INVALID_TEMPLATE_JSON")

        name = template_json.get("name", "Imported Template")
        slug = self._generate_unique_slug(name)

        template = StackTemplate(
            name=name,
            slug=slug,
            description=template_json.get("description"),
            category=template_json.get("category", "custom"),
            cloud_provider=template_json.get("cloud_provider", "any"),
            icon=template_json.get("icon", "Package"),
            color=template_json.get("color", "blue"),
            estimated_time=template_json.get("estimated_time"),
            estimated_cost=template_json.get("estimated_cost"),
            difficulty=template_json.get("difficulty", "intermediate"),
            modules=template_json.get("modules"),
            variable_templates=template_json.get("variable_templates", {}),
            prerequisites=template_json.get("prerequisites", []),
            tags=template_json.get("tags", []),
            version=template_json.get("version", "1.0.0"),
            created_by="user",
            is_public=False,
            forked_from=None,
            is_active=True,
            is_featured=False,
        )

        self.db.add(template)
        self.db.flush()
        self.db.refresh(template)

        logger.info(f"Imported template '{template.name}'")
        return template

    def publish_template(self, template_id: int, is_public: bool) -> StackTemplate:
        """Publish or unpublish a stack template."""
        template = self._get_template_by_id(template_id)

        if template.created_by == "system" and not is_public:
            raise ForbiddenError("Cannot unpublish system templates")

        template.is_public = is_public
        self.db.flush()
        self.db.refresh(template)

        action = "published" if is_public else "unpublished"
        logger.info(f"Template {template_id} {action}")
        return template

    # ================================================================
    # Stack Instances
    # ================================================================

    def create_instance(self, project_id: int, template_id: int, name: str, variables: dict | None = None) -> StackInstance:
        """Create a new stack instance in a project."""
        project = self._get_project(project_id)

        template = self.db.query(StackTemplate).filter(
            StackTemplate.id == template_id,
            StackTemplate.is_active,
        ).first()
        if not template:
            raise NotFoundError("stack_template", template_id)

        stack_instance = StackInstance(
            project_id=project_id,
            template_id=template_id,
            name=name,
            status=StackInstanceStatus.PENDING,
            variables=variables or {},
            deployed_modules=[],
            current_step=0,
            total_steps=len(template.modules or []),
        )

        self.db.add(stack_instance)
        self.db.flush()
        self.db.refresh(stack_instance)

        # Mirror stack-wide flat inputs into project variable defaults immediately
        # so all modules in the blueprint can reliably resolve shared inputs such as
        # user_ip across deploy/retry/resume flows.
        if variables:
            self._persist_stack_variable_defaults(project, variables)
            self.db.commit()
            self.db.refresh(project)

        logger.info(f"Created stack instance '{stack_instance.name}' (ID: {stack_instance.id}) in project {project_id}")
        return stack_instance

    def list_instances(self, project_id: int, status: str | None = None) -> list[StackInstance]:
        """List all stack instances in a project."""
        self._get_project(project_id)

        query = self.db.query(StackInstance).filter(StackInstance.project_id == project_id)
        if status:
            query = query.filter(StackInstance.status == status)

        return query.order_by(StackInstance.created_at.desc()).all()

    def get_instance(self, project_id: int, stack_id: int) -> StackInstance:
        """Get a stack instance."""
        stack = self._get_stack(project_id, stack_id)

        modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.stack_instance_id == stack.id)
            .order_by(ProjectModule.deployment_order)
            .all()
        )

        serialized_modules = []
        for module in modules:
            serialized = {
                "id": module.id,
                "name": module.library_module.name if module.library_module else module.path_in_project.split("/")[-1],
                "path": module.path_in_project,
                "status": module.status,
                "deployment_order": module.deployment_order,
                "error": module.deployment_error,
            }

            if module.library_module:
                serialized.update(serialize_engine_metadata(module.library_module))
            else:
                serialized["engine_type"] = "opentofu"

            serialized_modules.append(serialized)

        return {
            "id": stack.id,
            "project_id": stack.project_id,
            "template_id": stack.template_id,
            "name": stack.name,
            "status": stack.status,
            "variables": stack.variables,
            "deployed_modules": [module.id for module in modules],
            "current_step": stack.current_step,
            "total_steps": stack.total_steps,
            "started_at": stack.started_at,
            "completed_at": stack.completed_at,
            "error_message": stack.error_message,
            "created_at": stack.created_at,
            "updated_at": stack.updated_at,
            "modules": serialized_modules,
        }

    def _stamp_host_release(self, stack: StackInstance, deployable_release_id: int) -> None:
        """Stamp the chosen BNK release onto the bare-metal host's version_profile_id.

        Searches for bare_metal_host_id nested in stack.variables (module-scoped dict)
        and updates the host so resolve_project_context picks up the per-deploy override.
        No-op when the stack has no bare-metal host association.
        """
        from models.bare_metal import BareMetalHost

        variables = stack.variables or {}
        host_id: int | None = None
        for value in variables.values():
            if isinstance(value, dict):
                raw = value.get("bare_metal_host_id")
                if raw is not None:
                    try:
                        host_id = int(raw)
                    except (ValueError, TypeError):
                        continue
                    break

        if host_id is None:
            return

        host = self.db.query(BareMetalHost).filter(BareMetalHost.id == host_id).first()
        if host is None:
            return

        host.version_profile_id = deployable_release_id
        self.db.flush()
        logger.info(
            "Stamped BareMetalHost %s version_profile_id=%s for stack %s deploy",
            host_id, deployable_release_id, stack.id,
        )

    def deploy_stack(self, project_id: int, stack_id: int, *, deployable_release_id: int | None = None) -> dict:
        """Start stack deployment using StackDeploymentService."""
        from services.stack_deployment_service import StackDeploymentService
        from services.system_service import SystemService

        # Block deployments while a system upgrade is running
        if SystemService.is_upgrade_in_progress():
            raise BadRequestError(
                "System upgrade in progress. Deployments are locked until the upgrade completes.",
                code="UPGRADE_IN_PROGRESS",
            )

        stack = self._get_stack(project_id, stack_id)

        # ADR-478 P1b: stamp the chosen BNK release onto the host carrier before module creation.
        if deployable_release_id is not None:
            self._stamp_host_release(stack, deployable_release_id)

        try:
            deployment_service = StackDeploymentService(self.db)
        except Exception as e:
            if "System defaults not configured" in str(e):
                raise BadRequestError(
                    "System not configured. Please go to System → Defaults and configure all required settings, then sync the Module Library.",
                    code="SYSTEM_NOT_CONFIGURED",
                )
            raise

        success, message = deployment_service.deploy_stack(stack)
        if not success:
            raise BadRequestError(message, code="STACK_DEPLOY_ERROR")

        logger.info(f"Stack {stack_id} deployment initiated: {message}")

        return {
            "status": stack.status,
            "message": message,
            "current_step": stack.current_step,
            "total_steps": stack.total_steps,
            "deployed_modules": stack.deployed_modules,
        }

    def check_prerequisites(self, slug: str, project_id: int) -> dict:
        """
        S19-001: Check stack template prerequisites against a project.

        Verifies that all required project secrets exist.
        """
        from services.stack_deployment_service import StackDeploymentService

        template = self._get_template_by_slug(slug)
        self._get_project(project_id)  # 404 if missing

        deployment_service = StackDeploymentService(self.db)
        return deployment_service.check_prerequisites(project_id, template)

    def run_deploy(self, project_id: int, stack_id: int, *, deployable_release_id: int | None = None) -> dict:
        """Deploy all stack modules (init + apply) with dependency ordering."""
        from models import Task as TaskModel
        from services.execution.task_dispatch import dispatch_apply, dispatch_init
        from services.stack_deployment_service import StackDeploymentService

        stack = self._get_stack(project_id, stack_id)

        if not stack.deployed_modules:
            raise BadRequestError("Stack has no modules to deploy", code="EMPTY_STACK")

        # ADR-478 P1b: re-stamp host release on run-deploy (idempotent; supports retry with a different release).
        if deployable_release_id is not None:
            self._stamp_host_release(stack, deployable_release_id)

        # Re-sync stack-supplied shared defaults before every run to keep retry/resume
        # flows truthful when stack variables are module-scoped (e.g., user_ip).
        if stack.variables:
            self._persist_stack_variable_defaults(stack.project, stack.variables)

        if stack.status in (StackInstanceStatus.DEPLOYING, StackInstanceStatus.DESTROYING):
            # Slice bnk-existing-cluster-004: reconcile stale in-progress stack state
            # before enforcing OPERATION_IN_PROGRESS guard. Early module failures can
            # leave the stack row stale as "deploying" until progress reconciliation runs.
            StackDeploymentService(self.db).update_stack_progress(stack)
            self.db.refresh(stack)

        if stack.status in (StackInstanceStatus.DEPLOYING, StackInstanceStatus.DESTROYING):
            stack_module_ids = [m.id for m in stack.modules]
            has_active_tasks = False
            if stack_module_ids:
                has_active_tasks = self.db.query(TaskModel).filter(
                    TaskModel.module_id.in_(stack_module_ids),
                    TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]),
                ).first() is not None

            in_flight_statuses = {
                ModuleStatus.INITIALIZING,
                ModuleStatus.PLANNING,
                ModuleStatus.APPLYING,
                ModuleStatus.DESTROYING,
            }
            has_in_flight_modules = any(m.status in in_flight_statuses for m in stack.modules)

            if stack.status == StackInstanceStatus.DESTROYING or has_active_tasks or has_in_flight_modules:
                raise BadRequestError(f"Stack {stack.status} already in progress", code="OPERATION_IN_PROGRESS")

            logger.warning(
                "Detected stale stack status '%s' with no active tasks/in-flight modules for stack %s; resuming deployment",
                stack.status,
                stack.id,
            )
            stack.status = StackInstanceStatus.FAILED
            self.db.flush()

        # S19-001: Pre-flight secrets validation before deploying (SEC-001: includes stack variables)
        template = stack.template
        if template:
            deployment_service = StackDeploymentService(self.db)
            prereq_report = deployment_service.check_prerequisites(
                project_id, template,
                stack_variables=stack.variables,
            )
            if not prereq_report.get("all_satisfied", True):
                missing = prereq_report.get("missing_secrets") or []
                blocking_preflight = [
                    check
                    for check in (prereq_report.get("preflight_checks") or [])
                    if check.get("status") == "fail" and bool(check.get("blocking"))
                ]

                if missing:
                    names = ", ".join(m["name"] for m in missing)
                    missing_guidance = [m.get("guidance") for m in missing if m.get("guidance")]
                    guidance_suffix = f" {' '.join(dict.fromkeys(missing_guidance))}" if missing_guidance else ""
                    raise BadRequestError(
                        f"Missing required project secrets: {names}. "
                        f"Configure these secrets in Project Settings → Secrets before deploying."
                        f"{guidance_suffix}",
                        code="MISSING_SECRETS",
                        details={
                            "missing_secrets": missing,
                            "guidance": missing_guidance,
                            "preflight_checks": prereq_report.get("preflight_checks") or [],
                            "preflight_summary": prereq_report.get("preflight_summary") or {},
                            "selected_cluster": prereq_report.get("selected_cluster"),
                        },
                    )

                if blocking_preflight:
                    blocking_messages = "; ".join(
                        f"{check.get('title')}: {check.get('message')}"
                        for check in blocking_preflight
                    )
                    raise BadRequestError(
                        f"Cluster preflight blockers detected: {blocking_messages}",
                        code="STACK_PREFLIGHT_FAILED",
                        details={
                            "preflight_checks": prereq_report.get("preflight_checks") or [],
                            "preflight_summary": prereq_report.get("preflight_summary") or {},
                            "selected_cluster": prereq_report.get("selected_cluster"),
                        },
                    )

        modules = (
            self.db.query(ProjectModule)
            .filter(ProjectModule.id.in_(stack.deployed_modules))
            .order_by(ProjectModule.deployment_order)
            .all()
        )
        if not modules:
            raise BadRequestError("No modules found for stack", code="NO_MODULES")

        # S18-001: For applied modules, check if variables changed — if so, treat
        # them as needing re-plan/re-apply instead of skipping them
        from services.workspace_manager import WorkspaceManager
        workspace = WorkspaceManager(self.db)

        unchanged_applied = [m for m in modules if m.status == ModuleStatus.APPLIED and not workspace.vars_changed(m)]
        changed_applied = [m for m in modules if m.status == ModuleStatus.APPLIED and workspace.vars_changed(m)]
        # Disabled modules are filtered OUT here rather than left to raise at
        # dispatch. This loop commits a queued Task row before calling
        # dispatch_init, and has no try/except: a raise would abandon every
        # later module, leave the stack DEPLOYING, and leave an orphan queued
        # row that makes _has_active_task true forever — permanently skipping
        # that module on every re-run.
        #
        # _apply_topology_module_filter is the tree's main producer of disabled
        # modules (optional bare-metal modules that don't match host topology),
        # and those deploy through exactly this path.
        pending_modules = [
            m for m in ([m for m in modules if m.status != ModuleStatus.APPLIED] + changed_applied)
            if m.enabled
        ]
        skipped_disabled = [
            m.id for m in modules
            if not m.enabled and (m.status != ModuleStatus.APPLIED or m in changed_applied)
        ]
        if skipped_disabled:
            logger.info(
                "Stack deploy: skipping %d disabled module(s): %s",
                len(skipped_disabled), skipped_disabled,
            )

        if changed_applied:
            logger.info(
                f"S18-001: {len(changed_applied)} applied module(s) have changed variables and will be re-deployed: "
                f"{[m.id for m in changed_applied]}"
            )

        already_applied = unchanged_applied

        stack.status = StackInstanceStatus.DEPLOYING
        stack.current_step = len(already_applied)
        stack.total_steps = len(modules)

        if not pending_modules:
            stack.status = StackInstanceStatus.DEPLOYED
            stack.completed_at = datetime.now(UTC)
            return {
                "status": "deployed",
                "message": f"All {len(already_applied)} modules already deployed, no variable changes detected.",
                "queued_modules": 0,
                "skipped_modules": len(already_applied),
                "total_modules": len(modules),
            }

        queued_count = 0
        skipped_count = len(already_applied)

        def _has_active_task(module_id: int) -> bool:
            return self.db.query(TaskModel).filter(
                TaskModel.module_id == module_id,
                TaskModel.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED, TaskStatus.IN_PROGRESS]),
            ).first() is not None

        for module in pending_modules:
            if _has_active_task(module.id):
                logger.info(
                    "Skipping queue for module %s (%s) - active task already exists",
                    module.id,
                    module.path_in_project,
                )
                continue

            deps_satisfied = True
            for dep_id in module.dependencies or []:
                dep = self.db.query(ProjectModule).filter(ProjectModule.id == dep_id).first()
                if not dep or dep.status != ModuleStatus.APPLIED:
                    deps_satisfied = False
                    break

            if not deps_satisfied:
                logger.info(f"Deferred module {module.id} ({module.path_in_project}) - waiting for dependencies")
                continue

            if module.status == ModuleStatus.NOT_INITIALIZED:
                task = TaskModel(
                    task_type="init",
                    status=TaskStatus.QUEUED,
                    project_id=module.project_id,
                    module_id=module.id,
                    created_at=datetime.now(UTC),
                )
                self.db.add(task)
                self.db.flush()
                self.db.refresh(task)
                # BUG-007: Commit before Celery dispatch so worker can find this task row
                self.db.commit()

                celery_result = dispatch_init(task.id, module, auto_apply=True)
                task.celery_task_id = celery_result.id
                module.status = ModuleStatus.INITIALIZING
                self.db.commit()
                queued_count += 1
                logger.info(f"Queued init task {task.id} for module {module.id}")

            elif module.status in [ModuleStatus.INITIALIZED, ModuleStatus.PLANNED, ModuleStatus.INIT_FAILED, ModuleStatus.PLAN_FAILED, ModuleStatus.APPLY_FAILED]:
                task = TaskModel(
                    task_type="apply",
                    status=TaskStatus.QUEUED,
                    project_id=module.project_id,
                    module_id=module.id,
                    created_at=datetime.now(UTC),
                )
                self.db.add(task)
                self.db.flush()
                self.db.refresh(task)
                # BUG-007: Commit before Celery dispatch so worker can find this task row
                self.db.commit()

                has_saved_plan = workspace.has_saved_plan(module)
                plan_valid = False
                plan_serial = module.plan_serial or 0
                if has_saved_plan and plan_serial > 0:
                    plan_valid, _ = workspace.plan_is_valid(module)

                celery_result = dispatch_apply(
                    task.id,
                    module,
                    force_new_plan=(not has_saved_plan or plan_serial <= 0 or not plan_valid),
                )
                task.celery_task_id = celery_result.id
                module.status = ModuleStatus.APPLYING
                self.db.commit()
                queued_count += 1
                logger.info(f"Queued apply task {task.id} for module {module.id}")

            # S18-001: Applied modules with changed variables — re-apply
            elif module.status == ModuleStatus.APPLIED:
                task = TaskModel(
                    task_type="apply",
                    status=TaskStatus.QUEUED,
                    project_id=module.project_id,
                    module_id=module.id,
                    created_at=datetime.now(UTC),
                )
                self.db.add(task)
                self.db.flush()
                self.db.refresh(task)
                # BUG-007: Commit before Celery dispatch so worker can find this task row
                self.db.commit()

                has_saved_plan = workspace.has_saved_plan(module)
                plan_valid = False
                plan_serial = module.plan_serial or 0
                if has_saved_plan and plan_serial > 0:
                    plan_valid, _ = workspace.plan_is_valid(module)

                celery_result = dispatch_apply(
                    task.id,
                    module,
                    force_new_plan=(not has_saved_plan or plan_serial <= 0 or not plan_valid),
                )
                task.celery_task_id = celery_result.id
                module.status = ModuleStatus.APPLYING
                self.db.commit()
                queued_count += 1
                logger.info(f"Queued re-apply task {task.id} for module {module.id} (vars changed, S18-001)")

        if queued_count == 0 and pending_modules:
            stack.status = StackInstanceStatus.FAILED
            stack.error_message = f"Cannot deploy: {len(pending_modules)} modules have unsatisfied dependencies"
            return {
                "status": "failed",
                "message": f"Cannot deploy: {len(pending_modules)} modules have unsatisfied dependencies. Check module dependency configuration.",
                "queued_modules": 0,
                "skipped_modules": skipped_count,
                "total_modules": len(modules),
            }

        return {
            "status": "deploying",
            "message": f"Deploying {queued_count} modules ({skipped_count} already applied).",
            "queued_modules": queued_count,
            "skipped_modules": skipped_count,
            "total_modules": len(modules),
        }

    def get_stack_status(self, project_id: int, stack_id: int) -> dict:
        """Get deployment progress and status."""
        from services.stack_deployment_service import StackDeploymentService

        stack = self._get_stack(project_id, stack_id)
        deployment_service = StackDeploymentService(self.db)

        # Reconcile stale in-progress rows before serializing status. This keeps
        # API/UI status truthful after async destroy/deploy paths where module
        # states may already be terminal.
        if stack.status in (StackInstanceStatus.DEPLOYING, StackInstanceStatus.DESTROYING):
            deployment_service.update_stack_progress(stack)
            self.db.refresh(stack)

        return deployment_service.get_deployment_status(stack)

    def delete_stack(self, project_id: int, stack_id: int, force: bool = False) -> dict:
        """Delete a stack instance (destroy modules first unless force=True)."""
        from services.stack_deployment_service import StackDeploymentService

        stack = self._get_stack(project_id, stack_id, for_update=True)

        existing_modules = []
        if stack.deployed_modules:
            existing_modules = (
                self.db.query(ProjectModule)
                .filter(ProjectModule.id.in_(stack.deployed_modules))
                .all()
            )

        if force:
            # Delete all modules belonging to this stack (they have no remote
            # infra state worth preserving — SSH modules are stateless, and
            # force-delete is an explicit user decision to clean up).
            all_stack_modules = (
                self.db.query(ProjectModule)
                .filter(ProjectModule.stack_instance_id == stack_id)
                .all()
            )
            logger.info(f"Force deleting stack {stack_id} with {len(all_stack_modules)} modules")
            for m in all_stack_modules:
                self.db.delete(m)
            try:
                WorkspaceManager(self.db).cleanup_blueprint_workspace(project_id, stack_id)
            except Exception as e:
                logger.warning("Failed cleaning blueprint workspace for force-deleted stack %s: %s", stack_id, e)
            self.db.delete(stack)
            return {
                "status": "deleted",
                "message": f"Stack and {len(all_stack_modules)} module(s) deleted.",
            }

        if not existing_modules:
            logger.info(f"Stack {stack_id} has no remaining modules, deleting directly")
            try:
                WorkspaceManager(self.db).cleanup_blueprint_workspace(project_id, stack_id)
            except Exception as e:
                logger.warning("Failed cleaning blueprint workspace for stack %s: %s", stack_id, e)
            self.db.delete(stack)
            return {"status": "deleted", "message": "Stack deleted (modules already removed)"}

        deployment_service = StackDeploymentService(self.db)

        # Reconcile stale stack status before enforcing guards.
        if stack.status in (StackInstanceStatus.DEPLOYING, StackInstanceStatus.DESTROYING):
            deployment_service.update_stack_progress(stack)
            self.db.refresh(stack)

        # Check if any modules actually need infrastructure destruction.
        # SSH modules and not_initialized modules have no remote state to destroy —
        # they can be deleted directly from the DB.
        needs_infra_destroy = False
        for m in existing_modules:
            engine_type = m.library_module.engine_type if m.library_module else None
            if engine_type in ("ssh", "ansible"):
                continue  # No remote state to destroy
            if m.status in (ModuleStatus.NOT_INITIALIZED, ModuleStatus.DESTROYED):
                continue  # Nothing to destroy
            needs_infra_destroy = True
            break

        if not needs_infra_destroy:
            # All modules are SSH/ansible or not_initialized — delete directly
            for m in existing_modules:
                self.db.delete(m)
            self.db.delete(stack)
            self.db.commit()
            logger.info(f"Stack {stack_id} deleted directly ({len(existing_modules)} modules cleaned up)")
            return {"status": "deleted", "message": f"Stack deleted ({len(existing_modules)} modules removed)"}

        # Has infrastructure modules that need destroy — use the destroy flow
        success, message = deployment_service.destroy_stack(stack)
        if not success:
            raise BadRequestError(message, code="STACK_DESTROY_ERROR")

        logger.info(f"Stack {stack_id} destruction initiated: {message}")
        return {"status": "destroying", "message": message}
