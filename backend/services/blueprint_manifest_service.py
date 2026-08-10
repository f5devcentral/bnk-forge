"""Validation service for canonical imported blueprint manifests."""

from collections import defaultdict, deque
from dataclasses import dataclass

from pydantic import ValidationError as PydanticValidationError

from schemas.blueprints import BlueprintManifest


@dataclass
class BlueprintManifestValidationIssue:
    """Machine-readable blueprint manifest validation issue."""

    code: str
    path: str
    message: str


class BlueprintManifestValidationError(Exception):
    """Raised when a blueprint manifest fails validation."""

    def __init__(self, issues: list[BlueprintManifestValidationIssue]):
        self.issues = issues
        super().__init__("Blueprint manifest validation failed")


def validate_blueprint_manifest(manifest_data: dict) -> BlueprintManifest:
    """Parse and validate the canonical blueprint manifest contract."""
    issues: list[BlueprintManifestValidationIssue] = []

    try:
        manifest = BlueprintManifest.model_validate(manifest_data)
    except PydanticValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", []))
            issues.append(
                BlueprintManifestValidationIssue(
                    code=_map_schema_error_code(loc, err.get("type", "")),
                    path=loc,
                    message=err.get("msg", "Invalid manifest field"),
                )
            )
        raise BlueprintManifestValidationError(issues) from exc

    _extend_with_semantic_issues(manifest, issues)

    if issues:
        raise BlueprintManifestValidationError(issues)

    return manifest


def _extend_with_semantic_issues(
    manifest: BlueprintManifest,
    issues: list[BlueprintManifestValidationIssue],
) -> None:
    modules = manifest.modules
    module_ids = [module.id for module in modules]
    id_to_index = {module.id: index for index, module in enumerate(modules)}

    _collect_duplicate_module_id_issues(module_ids, issues)

    for index, module in enumerate(modules):
        seen_dependencies: set[str] = set()
        for dep_index, dependency_id in enumerate(module.depends_on):
            if dependency_id in seen_dependencies:
                issues.append(
                    BlueprintManifestValidationIssue(
                        code="BLUEPRINT_MODULE_DEPENDENCY_DUPLICATE",
                        path=f"modules.{index}.depends_on.{dep_index}",
                        message=(
                            f"duplicate depends_on reference '{dependency_id}' "
                            "within the same module is not allowed"
                        ),
                    )
                )
                continue

            seen_dependencies.add(dependency_id)

            if dependency_id == module.id:
                issues.append(
                    BlueprintManifestValidationIssue(
                        code="BLUEPRINT_MODULE_SELF_DEPENDENCY",
                        path=f"modules.{index}.depends_on.{dep_index}",
                        message=f"module '{module.id}' cannot depend on itself",
                    )
                )
                continue

            if dependency_id not in id_to_index:
                issues.append(
                    BlueprintManifestValidationIssue(
                        code="BLUEPRINT_MODULE_REFERENCE_NOT_FOUND",
                        path=f"modules.{index}.depends_on.{dep_index}",
                        message=f"depends_on reference '{dependency_id}' does not match any module id",
                    )
                )

    _collect_cycle_issues(modules, issues)


def _collect_duplicate_module_id_issues(
    module_ids: list[str],
    issues: list[BlueprintManifestValidationIssue],
) -> None:
    index_by_id: dict[str, list[int]] = defaultdict(list)
    for index, module_id in enumerate(module_ids):
        index_by_id[module_id].append(index)

    for module_id, indexes in index_by_id.items():
        if len(indexes) < 2:
            continue
        for index in indexes:
            issues.append(
                BlueprintManifestValidationIssue(
                    code="BLUEPRINT_MODULE_ID_DUPLICATE",
                    path=f"modules.{index}.id",
                    message=f"duplicate module id '{module_id}' is not allowed",
                )
            )


def _collect_cycle_issues(
    modules: list,
    issues: list[BlueprintManifestValidationIssue],
) -> None:
    module_ids = [module.id for module in modules]
    id_to_index = {module.id: index for index, module in enumerate(modules)}
    in_degree = {module_id: 0 for module_id in module_ids}
    dependents: dict[str, list[str]] = {module_id: [] for module_id in module_ids}

    for module in modules:
        for dependency in module.depends_on:
            if dependency not in id_to_index:
                continue
            dependents[dependency].append(module.id)
            in_degree[module.id] += 1

    queue = deque([module_id for module_id, degree in in_degree.items() if degree == 0])
    processed = 0

    while queue:
        module_id = queue.popleft()
        processed += 1
        for dependent_id in dependents[module_id]:
            in_degree[dependent_id] -= 1
            if in_degree[dependent_id] == 0:
                queue.append(dependent_id)

    if processed == len(module_ids):
        return

    cycle_module_ids = sorted(module_id for module_id, degree in in_degree.items() if degree > 0)
    for module_id in cycle_module_ids:
        issues.append(
            BlueprintManifestValidationIssue(
                code="BLUEPRINT_DEPENDENCY_CYCLE",
                path=f"modules.{id_to_index[module_id]}.depends_on",
                message=(
                    "cyclic dependency detected among modules: "
                    f"{', '.join(cycle_module_ids)}"
                ),
            )
        )


def _map_schema_error_code(path: str, pydantic_error_type: str) -> str:
    if pydantic_error_type == "missing":
        return "BLUEPRINT_MANIFEST_FIELD_REQUIRED"

    if path == "schema_version":
        return "BLUEPRINT_SCHEMA_VERSION_UNSUPPORTED"

    if path.endswith(".version") and path.startswith("modules."):
        return "BLUEPRINT_MODULE_VERSION_INVALID"

    if path.endswith(".module") and path.startswith("modules."):
        return "BLUEPRINT_MODULE_REFERENCE_INVALID"

    return "BLUEPRINT_MANIFEST_SCHEMA_INVALID"
