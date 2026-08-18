"""Services for previewing and instantiating imported blueprint releases."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models import (
    BlueprintRelease,
    CloudCredentialTemplate,
    KubernetesCluster,
    ModuleLibrary,
    ProjectModule,
    StackInstance,
)
from models.enums import StackInstanceStatus
from schemas.projects import ProjectCreate
from services.blueprint_catalog_common import _resolve_category
from services.module_resolution import warn_on_cross_source_ambiguity
from services.execution.k8s_catalog_payload import _render_template_obj
from services.module_version_query import available_module_versions
from services.project_module_service import ProjectModuleService
from services.project_service import ProjectService
from services.stack_service import StackService
from utils.security import is_sensitive_input

logger = logging.getLogger(__name__)

# Mirrors the frontend constant in ImportedBlueprintDeployDialog/StackDetailDialog.
# A blank input would also be treated as inherit, but the dialogs send this token
# so the user can see in the UI that the value will be filled from the template.
INHERIT_FROM_TEMPLATE_SENTINEL = "__inherited_from_template__"


def _is_inherit_request(value: Any) -> bool:
    return value is None or value == "" or value == INHERIT_FROM_TEMPLATE_SENTINEL


class ImportedBlueprintService:
    """Bridge imported BlueprintRelease records into project/module creation flows."""

    def __init__(self, db: Session):
        self.db = db

    def get_release(self, release_id: int) -> BlueprintRelease:
        release = self.db.query(BlueprintRelease).filter(BlueprintRelease.id == release_id).first()
        if release is None:
            raise NotFoundError("blueprint_release", release_id)
        return release

    def _resolve_library_module(
        self, module_ref: str, pinned_version: str | None = None
    ) -> ModuleLibrary | None:
        """Resolve a blueprint module reference against the module library.

        D-033: when the blueprint pins a version, resolution is EXACT — never a
        different version. Transitional fallback: a path whose rows are ALL
        legacy (content_sha256 IS NULL) predates versioned identity, so a pin
        resolves it as pre-D-033 (latest active row). The moment any hashed
        version exists for the path, pins are strict — a miss returns None and
        surfaces BLUEPRINT_MODULE_VERSION_MISSING rather than substituting
        other content. Unpinned refs (legacy manifests predating the
        pinned-version contract) resolve to the is_latest row, newest id as
        tiebreak.
        """
        query = self.db.query(ModuleLibrary).filter(
            ModuleLibrary.path == module_ref, ModuleLibrary.is_active
        )
        if pinned_version:
            # D-033 identity is (module_source_id, path, version), but a blueprint
            # pin carries no source -- so if two sources catalog the same
            # path+version (a fork registered alongside the original), highest id
            # silently wins and the deploy binds the fork's manifest. Nothing the
            # blueprint author can see or control. Cannot be auto-resolved without
            # source-aware pins, so make it loud (#90 F3).
            candidates = (
                query.filter(ModuleLibrary.version == pinned_version)
                .order_by(ModuleLibrary.is_latest.desc(), ModuleLibrary.id.desc())
                .all()
            )
            if candidates:
                warn_on_cross_source_ambiguity(candidates, module_ref)
                return candidates[0]
            # Transitional fallback applies ONLY when the path has no hashed
            # (version-identified) rows at all — a purely pre-D-033 path. Once
            # ANY hashed version exists, a missed pin is a hard miss; silently
            # substituting a legacy row's (different) content is exactly the
            # drift D-033 forbids.
            has_hashed = (
                query.filter(ModuleLibrary.content_sha256.isnot(None)).first() is not None
            )
            if has_hashed:
                return None
            return (
                query.filter(ModuleLibrary.content_sha256.is_(None))
                .order_by(ModuleLibrary.is_latest.desc(), ModuleLibrary.id.desc())
                .first()
            )
        return query.order_by(ModuleLibrary.is_latest.desc(), ModuleLibrary.id.desc()).first()

    def _available_versions(self, module_ref: str) -> list[str]:
        """Active catalog versions for a module path, latest first."""
        return available_module_versions(self.db, module_ref)

    @staticmethod
    def _pinned_version(module_def: dict[str, Any]) -> str | None:
        version = module_def.get("version")
        if isinstance(version, str) and version.strip():
            return version.strip()
        return None

    def get_template_like(self, release_id: int) -> dict[str, Any]:
        release = self.get_release(release_id)
        manifest = release.manifest or {}
        modules = manifest.get("modules") or []

        return {
            "id": 1000000 + release.id,
            "name": release.blueprint_name,
            "slug": f"release-{release.id}",
            "description": release.blueprint_description,
            "category": _resolve_category(manifest),
            "cloud_provider": manifest.get("cloud_provider") or self._infer_cloud_provider(modules),
            "icon": manifest.get("icon") or "Layers",
            "color": manifest.get("color") or "blue",
            "estimated_time": manifest.get("estimated_time") or "Varies",
            "estimated_cost": manifest.get("estimated_cost"),
            "difficulty": manifest.get("difficulty") or "intermediate",
            "modules": self._serialize_modules_for_preview(modules),
            "variable_templates": manifest.get("variable_templates") or {},
            "prerequisites": manifest.get("prerequisites") or [],
            "tags": manifest.get("tags") or [],
            "maturity": manifest.get("maturity") or "reference",
            "outcomes": manifest.get("outcomes") or [],
            "platform_defaults": manifest.get("platform_defaults") or {},
            "is_active": release.is_active,
            "is_featured": False,
            "version": release.blueprint_version,
            "created_by": "blueprint-catalog",
            "is_public": True,
            "forked_from": None,
            "source_kind": "blueprint_release",
            "blueprint_release_id": release.id,
            "blueprint_source_id": release.blueprint_source_id,
            "release_state": release.release_state,
            "validation_state": release.validation_state,
            "source_path": release.source_path,
            "created_at": release.created_at,
            "updated_at": release.updated_at,
        }

    def preview_release(self, release_id: int) -> dict[str, Any]:
        template = self.get_template_like(release_id)
        return {
            "modules": template["modules"],
            "prerequisites": template.get("prerequisites") or [],
            "estimated_cost": template.get("estimated_cost"),
            "estimated_time": template.get("estimated_time"),
            "total_modules": len(template["modules"]),
        }

    def get_required_inputs(self, release_id: int) -> dict[str, Any]:
        release = self.get_release(release_id)
        manifest = release.manifest or {}
        inputs = manifest.get("inputs") or {}
        required_inputs = inputs.get("required") or []
        optional_inputs = inputs.get("optional") or []

        # Sources that forge resolves from project context at create/deploy time —
        # these must be hidden from the user-facing create form.
        CONTEXT_RESOLVED_SOURCES = {"credential_template", "project", "project_secret", "module"}

        # Collect input names that the blueprint itself declares as context-resolved.
        # Module-declared vars with the same name are also hidden (blueprint wins).
        context_resolved_names: set[str] = set()
        for item in required_inputs + optional_inputs:
            if isinstance(item, dict) and item.get("source") in CONTEXT_RESOLVED_SOURCES:
                name = item.get("name")
                if name:
                    context_resolved_names.add(name)

        def _coerce_order(value: Any) -> float | None:
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            return None

        def _normalize_input(item: dict[str, Any]) -> dict[str, Any]:
            source = item.get("source")
            is_hidden = source in CONTEXT_RESOLVED_SOURCES
            return {
                "name": item.get("name"),
                "type": item.get("type", "string"),
                "description": item.get("description", ""),
                "example": item.get("example", ""),
                "default": item.get("default"),
                "module_path": "blueprint",
                "module_name": release.blueprint_name,
                "required": (item in required_inputs) and not is_hidden,
                "sensitive": is_sensitive_input(item),
                "validation": item.get("validation"),
                "source": source,
                "source_field": item.get("source_field"),
                "order": _coerce_order(item.get("order")),
                "hidden": is_hidden,
                "resolved_from": source if is_hidden else None,
                "options": item.get("options"),
            }

        defaults = self._collect_blueprint_input_defaults(manifest)
        all_inputs = [_normalize_input(item) for item in required_inputs + optional_inputs]
        for item in all_inputs:
            if item["name"] in defaults and item["default"] is None:
                item["default"] = defaults[item["name"]]

        # Sort by author-specified order when present. Inputs without an
        # ``order`` field fall to the end in their original (required +
        # optional) array sequence, so existing manifests render unchanged.
        def _sort_key(indexed: tuple[int, dict[str, Any]]) -> tuple[int, float, int]:
            idx, item = indexed
            explicit = item["order"]
            if explicit is None:
                return (1, 0.0, idx)
            return (0, explicit, idx)

        all_inputs = [item for _, item in sorted(enumerate(all_inputs), key=_sort_key)]

        inputs_by_module: dict[str, list[dict[str, Any]]] = {}
        if all_inputs:
            inputs_by_module["blueprint"] = all_inputs

        # Track unique non-hidden required input names for dedup across blueprint + module buckets.
        seen_required_names: set[str] = set()
        for item in all_inputs:
            if item["required"] and not item["hidden"]:
                name = item["name"]
                if name:
                    seen_required_names.add(name)

        total_optional = len(optional_inputs)

        # Visible blueprint-level inputs win over identically-named module vars:
        # when the blueprint already exposes an input (e.g. a dropdown for
        # "pattern"), skip the module's own copy so the form shows it once, with
        # the blueprint's richer definition (options, ordering). Hidden,
        # context-resolved inputs (creds/region) are excluded here so their
        # module-aggregated copies are still surfaced (and hidden) as before.
        blueprint_visible_input_names = {
            item["name"] for item in all_inputs if item.get("name") and not item.get("hidden")
        }

        # Aggregate module-level variable declarations so the UI/harness can
        # discover and configure them, even when the blueprint didn't expose
        # them via its top-level inputs block.
        for module_def in manifest.get("modules") or []:
            module_ref = str(module_def.get("module") or "").strip()
            if not module_ref:
                continue
            library_module = self._resolve_library_module(module_ref, self._pinned_version(module_def))
            if library_module is None:
                continue
            module_inputs_literal = module_def.get("inputs") if isinstance(module_def.get("inputs"), dict) else {}
            module_vars: list[dict[str, Any]] = []
            for var_def in (library_module.variables_schema or []):
                if not isinstance(var_def, dict):
                    continue
                vname = var_def.get("name")
                if not vname:
                    continue
                # Skip vars the blueprint already exposes as a visible top-level input.
                if vname in blueprint_visible_input_names:
                    continue
                # Use any literal value the blueprint wired as the effective default.
                literal_default = module_inputs_literal.get(vname)
                is_required = bool(var_def.get("required"))
                var_source = var_def.get("source")
                # A module var is hidden if its own source is context-resolved OR
                # the blueprint already declared this name as context-resolved.
                is_hidden = (var_source in CONTEXT_RESOLVED_SOURCES) or (vname in context_resolved_names)
                entry: dict[str, Any] = {
                    "name": vname,
                    "type": var_def.get("type", "string"),
                    "description": var_def.get("description", ""),
                    "example": var_def.get("example", ""),
                    "default": literal_default if literal_default is not None else var_def.get("default"),
                    "module_path": module_ref,
                    "module_name": module_def.get("name") or module_ref,
                    # Module vars are optional power-user overrides; the deploy uses module/blueprint
                    # defaults for anything the user doesn't explicitly supply. Never block-required.
                    "required": False,
                    "sensitive": is_sensitive_input(var_def),
                    "validation": var_def.get("validation"),
                    "source": var_source,
                    "source_field": var_def.get("source_field"),
                    "order": _coerce_order(var_def.get("order")),
                    "hidden": is_hidden,
                    "resolved_from": var_source if is_hidden else None,
                }
                module_vars.append(entry)
                all_inputs.append(entry)
                if not is_hidden:
                    if is_required:
                        total_optional += 0  # optional counter not incremented for module vars
                    else:
                        total_optional += 1
            if module_vars:
                inputs_by_module[module_ref] = module_vars

        # Count unique required non-hidden names (dedup across blueprint + module buckets).
        for item in all_inputs:
            if item.get("required") and not item.get("hidden"):
                name = item.get("name")
                if name:
                    seen_required_names.add(name)

        total_required = len(seen_required_names)

        return {
            "template_slug": f"release-{release.id}",
            "template_name": release.blueprint_name,
            "inputs_by_module": inputs_by_module,
            "all_inputs": all_inputs,
            "total_required": total_required,
            "total_optional": total_optional,
            "missing_modules": self._missing_modules(manifest.get("modules") or []),
            "summary": [
                {
                    "label": "Blueprint source",
                    "value": release.source_path or release.source_name or "Imported release",
                },
                *(
                    manifest.get("input_summary") or []
                ),
            ],
        }

    def create_project_from_release(self, release_id: int, request: Any, *, user_id: int | None = None) -> dict[str, Any]:
        release = self._validate_release(release_id)
        manifest = release.manifest or {}
        modules = manifest.get("modules") or []

        project_service = ProjectService(self.db)
        create_payload = ProjectCreate(
            name=request.name,
            description=request.description or f"{release.blueprint_name} deployment",
            project_type=request.project_type,
            cloud_provider=request.cloud_provider or self._infer_cloud_provider(modules),
            environment=request.environment or "production",
            region=request.region,
            backend_type=request.backend_type or "local",
            credential_template_id=request.credential_template_id,
            color=request.color or "#2563eb",
            icon=request.icon or "",
        )
        created_project = project_service.create_project(create_payload, user_id=user_id)
        project_id = created_project["project_id"]

        # create_project() commits the project row in its own transaction, so any
        # failure below (most commonly per-module variable validation in add_module)
        # would orphan an empty project. Roll back the in-flight work and delete the
        # already-committed project so the operation is atomic for the caller.
        try:
            variables = request.variables or {}
            variables = self._apply_blueprint_input_defaults(manifest, variables)
            variables = self._apply_credential_template_inheritance(request, variables)
            variables = self._apply_explicit_input_source_mappings(manifest, request, variables)
            project = project_service._get_project(project_id)
            StackService(self.db)._persist_stack_variable_defaults(project, variables)
            self.db.flush()

            created_module_ids = self._create_release_modules(release, manifest, modules, project_id, variables)

            # Create a StackInstance that tracks this blueprint-backed project so that
            # deploy-all can auto-chain all layers via _trigger_next_stack_module.
            stack = StackInstance(
                project_id=project_id,
                template_id=None,
                blueprint_release_id=release.id,
                name=request.name,
                status=StackInstanceStatus.PENDING,
                variables=variables or {},
                current_step=0,
                total_steps=len(created_module_ids),
            )
            self.db.add(stack)
            self.db.flush()

            # Wire every created module to this stack instance.
            self.db.query(ProjectModule).filter(ProjectModule.id.in_(created_module_ids)).update(
                {ProjectModule.stack_instance_id: stack.id}, synchronize_session=False
            )

            self.db.commit()
        except Exception:
            self.db.rollback()
            try:
                ProjectService(self.db).delete_project(project_id, force=True)
            except Exception:
                logger.exception("Failed to clean up orphaned project %s after blueprint deploy failure", project_id)
            raise

        return {
            "success": True,
            "project_id": project_id,
            "project_name": created_project["name"],
            "blueprint_release_id": release.id,
            "module_count": len(created_module_ids),
            "created_module_ids": created_module_ids,
            "stack_instance_id": stack.id,
            "message": f"Created project '{created_project['name']}' from imported blueprint release.",
        }

    def add_release_to_project(self, release_id: int, project_id: int, request: Any, *, user_id: int | None = None) -> dict[str, Any]:
        """Add imported blueprint release modules to an existing project."""
        release = self._validate_release(release_id)
        manifest = release.manifest or {}
        modules = manifest.get("modules") or []

        # If the manifest declares a kubernetes_cluster prerequisite, require
        # the target project to have at least one registered cluster.
        prerequisites = manifest.get("prerequisites") or []
        needs_cluster = any(p.get("type") == "kubernetes_cluster" for p in prerequisites)
        if needs_cluster:
            cluster_count = (
                self.db.query(KubernetesCluster)
                .filter(KubernetesCluster.project_id == project_id)
                .count()
            )
            if cluster_count == 0:
                raise BadRequestError(
                    "This blueprint requires a registered Kubernetes cluster in the target project. "
                    "Register a cluster first, then add this blueprint.",
                    code="BLUEPRINT_REQUIRES_REGISTERED_CLUSTER",
                )

        project_service = ProjectService(self.db)
        project = project_service._get_project(project_id)

        variables = request.variables or {}
        variables = self._apply_blueprint_input_defaults(manifest, variables)
        variables = self._apply_credential_template_inheritance(project, variables)
        variables = self._apply_explicit_input_source_mappings(manifest, project, variables)
        self._reject_surviving_inherit_sentinels(variables)
        StackService(self.db)._persist_stack_variable_defaults(project, variables)
        self.db.flush()

        # CRITICAL-3: Guard against installing the same blueprint release twice.
        prefix = f"imported-blueprint/{release.id}/"
        already_installed = (
            self.db.query(ProjectModule)
            .filter(
                ProjectModule.project_id == project_id,
                ProjectModule.path_in_project.like(f"{prefix}%"),
            )
            .first()
        )
        if already_installed:
            raise BadRequestError(
                f"Blueprint release '{release.blueprint_name}' (id={release.id}) is already "
                f"installed in this project. Each blueprint release can only be added once.",
                code="BLUEPRINT_ALREADY_INSTALLED",
            )

        created_module_ids = self._create_release_modules(release, manifest, modules, project_id, variables)

        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name,
            "blueprint_release_id": release.id,
            "module_count": len(created_module_ids),
            "created_module_ids": created_module_ids,
            "message": f"Added blueprint '{release.blueprint_name}' modules to project '{project.name}'.",
        }

    def _validate_release(self, release_id: int) -> BlueprintRelease:
        """Validate a blueprint release is ready for deployment and return it."""
        release = self.get_release(release_id)
        if release.validation_state != "valid":
            raise BadRequestError("Blueprint release must be valid before project creation", code="BLUEPRINT_RELEASE_INVALID")
        if release.release_state not in {"imported", "approved"}:
            raise BadRequestError("Blueprint release must be imported or approved before deployment", code="BLUEPRINT_RELEASE_NOT_DEPLOYABLE")

        manifest = release.manifest or {}
        modules = manifest.get("modules") or []
        missing = self._missing_modules(modules)
        if missing:
            source_label = release.source_path or release.blueprint_name
            paths = ", ".join(m["path"] for m in missing)
            # D-033: when a pinned version is absent but the path exists in the
            # catalog, the actionable fix is different (import/sync the right
            # VERSION, or bump the blueprint) — signal it with a distinct code.
            version_misses = [m for m in missing if m.get("reason") == "version_missing"]
            code = "BLUEPRINT_MODULE_VERSION_MISSING" if version_misses else "BLUEPRINT_MODULES_MISSING"
            raise BadRequestError(
                f"Required modules missing from catalog (blueprint: {source_label}): {paths}. "
                f"Re-sync the blueprint catalog or check the catalog source — "
                f"deploy blocked so no partial stack is applied.",
                code=code,
                details={"missing_modules": missing},
            )
        return release

    def _create_release_modules(
        self,
        release: BlueprintRelease,
        manifest: dict[str, Any],
        modules: list[dict[str, Any]],
        project_id: int,
        variables: dict[str, Any],
    ) -> list[int]:
        """Create ProjectModule rows for every module in a blueprint release.

        Returns the list of created module IDs.
        """
        from sqlalchemy import func

        # Collect input names that the blueprint declares as context-resolved.
        # These are filled by Forge at deploy time (credentials, project region, secrets)
        # and must not be validated as missing/empty at create time.
        _CONTEXT_RESOLVED_SOURCES = {"credential_template", "project", "project_secret", "module"}
        _manifest_inputs = manifest.get("inputs") or {}
        blueprint_context_resolved_names: set[str] = {
            item["name"]
            for item in (
                (_manifest_inputs.get("required") or [])
                + (_manifest_inputs.get("optional") or [])
            )
            if isinstance(item, dict)
            and item.get("source") in _CONTEXT_RESOLVED_SOURCES
            and item.get("name")
        }

        project_module_service = ProjectModuleService(self.db)
        module_id_by_blueprint_id: dict[str, int] = {}
        created_module_ids: list[int] = []

        # Offset deployment_order past any modules already in the project so that
        # new blueprint modules don't collide with pre-existing ones.
        existing_max = (
            self.db.query(func.max(ProjectModule.deployment_order))
            .filter(ProjectModule.project_id == project_id)
            .scalar()
        )
        order_offset = (existing_max + 1) if existing_max is not None else 0

        for idx, module_def in enumerate(modules):
            module_ref = str(module_def.get("module") or "").strip()
            library_module = self._resolve_library_module(module_ref, self._pinned_version(module_def))
            if library_module is None:
                # Invariant: _missing_modules above must have caught this; if we reach here
                # it means an optional module was absent and intentionally skipped.
                # Skip it rather than failing — the aggregated gate already blocked required gaps.
                continue

            module_inputs = module_def.get("inputs") if isinstance(module_def.get("inputs"), dict) else {}
            resolved_inputs = self._resolve_module_inputs(module_inputs, variables)
            for var_def in (library_module.variables_schema or []):
                if not isinstance(var_def, dict):
                    continue
                vname = var_def.get("name")
                if vname and vname in variables:
                    resolved_inputs[vname] = variables[vname]
            path_in_project = f"imported-blueprint/{release.id}/{module_ref}"

            is_optional = bool(module_def.get("optional", False))
            result = project_module_service.add_module(
                project_id=project_id,
                module_library_id=library_module.id,
                path_in_project=path_in_project,
                variable_overrides=resolved_inputs,
                deployment_order=order_offset + idx,
                enabled=not is_optional,
                context_resolved_var_names=blueprint_context_resolved_names,
            )
            module_id = int(result["module_id"])
            created_module_ids.append(module_id)
            module_id_by_blueprint_id[str(module_def.get("id"))] = module_id

        for module_def in modules:
            bp_id = str(module_def.get("id"))
            if bp_id not in module_id_by_blueprint_id:
                # Optional module was absent and skipped — no row to wire dependencies for.
                continue
            module_id = module_id_by_blueprint_id[bp_id]
            dependency_ids = [module_id_by_blueprint_id[dep] for dep in module_def.get("depends_on", []) if dep in module_id_by_blueprint_id]
            if dependency_ids:
                project_module_service.set_dependencies(module_id, dependency_ids)

        # Blueprint dependency edges come from the manifest (set above via
        # set_dependencies); order from those, not the library metadata, which
        # would otherwise overwrite them.
        project_module_service.calculate_deployment_order(project_id, use_existing_dependencies=True)
        return created_module_ids

    def _serialize_modules_for_preview(self, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        preview_modules: list[dict[str, Any]] = []
        for module_def in modules:
            module_ref = str(module_def.get("module") or "")
            pinned = self._pinned_version(module_def)
            library_module = self._resolve_library_module(module_ref, pinned)
            status = "available" if library_module else "missing"
            preview_modules.append(
                {
                    "path": module_ref,
                    "name": module_def.get("name") or module_def.get("id") or module_ref.split("/")[-1],
                    "required": not module_def.get("optional", False),
                    "description": module_def.get("description") or f"Imported blueprint module '{module_ref}'",
                    "variables": module_def.get("inputs") or {},
                    "version": pinned,
                    "module_catalog_status": status,
                    "module_catalog_message": None
                    if library_module
                    else self._missing_module_message(module_ref, pinned),
                }
            )
        return preview_modules

    def _missing_module_message(
        self, module_ref: str, pinned_version: str | None, available: list[str] | None = None
    ) -> str:
        if pinned_version:
            if available is None:
                available = self._available_versions(module_ref)
            if available:
                return (
                    f"Module '{module_ref}' pinned version {pinned_version} is not in the "
                    f"active module catalog. Available versions: {', '.join(available)}."
                )
            return (
                f"Module '{module_ref}' (pinned version {pinned_version}) is not present "
                f"in the active module catalog."
            )
        return f"Module '{module_ref}' is not present in the active module catalog."

    def _missing_modules(self, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return absent REQUIRED modules only.

        Absent optional modules are surfaced via a warning and excluded from the
        returned list — they do not block deploy.
        """
        missing: list[dict[str, Any]] = []
        for module_def in modules:
            module_ref = str(module_def.get("module") or "")
            is_optional = bool(module_def.get("optional"))
            pinned = self._pinned_version(module_def)
            exists = self._resolve_library_module(module_ref, pinned)
            if exists is None:
                if is_optional:
                    logger.warning(
                        f"Optional module '{module_ref}' not found in library — "
                        f"skipping (will be absent from the deployed stack)"
                    )
                    continue
                # D-033: distinguish "the pinned version is absent" (other
                # versions of the path exist) from "the path is absent".
                available = self._available_versions(module_ref) if pinned else []
                missing.append(
                    {
                        "path": module_ref,
                        "name": str(module_def.get("id") or module_ref),
                        "message": self._missing_module_message(module_ref, pinned, available),
                        "reason": "version_missing" if (pinned and available) else "path_missing",
                        "pinned_version": pinned,
                        "available_versions": available,
                    }
                )
        return missing

    def _resolve_module_inputs(self, module_inputs: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        return _render_template_obj(module_inputs, variables)

    def _resolve_value(self, value: Any, variables: dict[str, Any]) -> Any:
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            variable_name = value[2:-1].strip()
            if variable_name.startswith("inputs."):
                variable_name = variable_name.split(".", 1)[1]
            return variables.get(variable_name, value)
        if isinstance(value, list):
            return [self._resolve_value(item, variables) for item in value]
        if isinstance(value, dict):
            return {k: self._resolve_value(v, variables) for k, v in value.items()}
        return value

    def _infer_cloud_provider(self, modules: list[dict[str, Any]]) -> str | None:
        module_refs = " ".join(str(module.get("module") or "") for module in modules).lower()
        if "ibm" in module_refs or "roks" in module_refs:
            return "ibm"
        if "aws" in module_refs:
            return "aws"
        if "azure" in module_refs:
            return "azure"
        if "gcp" in module_refs:
            return "gcp"
        return None

    def _collect_blueprint_input_defaults(self, manifest: dict[str, Any]) -> dict[str, Any]:
        inputs = manifest.get("inputs") or {}
        defaults: dict[str, Any] = {}
        for input_def in [*(inputs.get("required") or []), *(inputs.get("optional") or [])]:
            if not isinstance(input_def, dict):
                continue
            name = input_def.get("name")
            if name and "default" in input_def:
                defaults[str(name)] = input_def["default"]
        return defaults

    def _apply_blueprint_input_defaults(self, manifest: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(variables)
        for name, value in self._collect_blueprint_input_defaults(manifest).items():
            resolved.setdefault(name, value)
        return _render_template_obj(resolved, resolved)

    def _reject_surviving_inherit_sentinels(self, variables: dict[str, Any]) -> None:
        """Fail loudly instead of persisting an unresolved inherit-from-template sentinel.

        For the existing-project path, `_apply_credential_template_inheritance` only fills
        `INHERIT_FROM_TEMPLATE_SENTINEL` values when the target project has a matching
        credential template. If the project has no (or a non-matching) template, the literal
        sentinel would otherwise be baked into ProjectModule.variable_overrides and the
        project's persisted variable defaults (PR #401 review residual).
        """
        sentinel_keys = sorted(key for key, value in variables.items() if value == INHERIT_FROM_TEMPLATE_SENTINEL)
        if sentinel_keys:
            raise BadRequestError(
                f"Blueprint requires values inherited from a credential template "
                f"({', '.join(sentinel_keys)}), but the target project has no matching "
                f"credential template configured. Set a matching credential template on "
                f"the project, or supply these values explicitly, then try again.",
                code="BLUEPRINT_CREDENTIAL_TEMPLATE_REQUIRED",
            )

    def _apply_credential_template_inheritance(self, request: Any, variables: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(variables)

        def fill(field: str, value: str | None) -> None:
            if value and _is_inherit_request(resolved.get(field)):
                resolved[field] = value

        region = getattr(request, "region", None)
        fill("ibmcloud_cluster_region", region)

        credential_template_id = getattr(request, "credential_template_id", None)
        if not credential_template_id:
            return resolved

        template = (
            self.db.query(CloudCredentialTemplate)
            .filter(CloudCredentialTemplate.id == credential_template_id)
            .first()
        )
        if template is None or template.provider != "ibm":
            return resolved

        fill("ibmcloud_cluster_region", template.region)
        fill("ibmcloud_resource_group", template.ibmcloud_resource_group)
        if template.ibmcloud_api_key_encrypted:
            fill("ibmcloud_api_key", decrypt_value_or_none(template.ibmcloud_api_key_encrypted))
        return resolved

    def _apply_explicit_input_source_mappings(self, manifest: dict[str, Any], request: Any, variables: dict[str, Any]) -> dict[str, Any]:
        resolved = dict(variables)
        inputs = manifest.get("inputs") or {}
        all_inputs = [*(inputs.get("required") or []), *(inputs.get("optional") or [])]

        credential_template_id = getattr(request, "credential_template_id", None)
        template = None
        if credential_template_id:
            template = (
                self.db.query(CloudCredentialTemplate)
                .filter(CloudCredentialTemplate.id == credential_template_id)
                .first()
            )

        project_region = getattr(request, "region", None)

        for input_def in all_inputs:
            if not isinstance(input_def, dict):
                continue

            name = input_def.get("name")
            source = input_def.get("source")
            source_field = input_def.get("source_field")
            default_value = input_def.get("default")
            if not name or not source or not source_field:
                continue

            current_value = resolved.get(name)
            should_override = _is_inherit_request(current_value) or current_value == default_value
            if not should_override:
                continue

            if source == "project":
                if source_field == "region" and project_region:
                    resolved[name] = project_region
                elif source_field == "name":
                    project_name = getattr(request, "name", None)
                    if project_name:
                        resolved[name] = project_name
                continue

            if source == "credential_template" and template is not None and template.provider == "ibm":
                if source_field == "ibmcloud_resource_group" and template.ibmcloud_resource_group:
                    resolved[name] = template.ibmcloud_resource_group
                elif source_field == "ibmcloud_api_key" and template.ibmcloud_api_key_encrypted:
                    decrypted_api_key = decrypt_value_or_none(template.ibmcloud_api_key_encrypted)
                    if decrypted_api_key:
                        resolved[name] = decrypted_api_key
                elif source_field == "region" and template.region:
                    resolved[name] = template.region

        return resolved
