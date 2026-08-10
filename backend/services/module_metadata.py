"""
Module Metadata Parser Service

Parses and validates module.json files from bnk-forge-modules repository
Based on MODULE_METADATA_SCHEMA.md specification
"""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from services.engine_registry import engine_registry

logger = logging.getLogger(__name__)


class ModuleMetadataError(Exception):
    """Base exception for module metadata errors"""
    pass


class MissingMetadataError(ModuleMetadataError):
    """Raised when module.json file is not found"""
    pass


class InvalidMetadataSchemaError(ModuleMetadataError):
    """Raised when module.json doesn't conform to schema"""
    pass


@dataclass
class ModuleInfo:
    """Module information from metadata"""
    name: str
    path: str
    version: str
    layer: str
    category: str
    description: str
    cloud_specific: bool
    supported_platforms: list[str]


CANONICAL_PLATFORM_PROFILES = [
    "generic_onprem",
    "eks",
    "aks",
    "gke",
    "ocp",
]

LEGACY_PLATFORM_TO_CANONICAL = {
    "aws": ["eks"],
    "azure": ["aks"],
    "gcp": ["gke"],
    "on-prem": ["generic_onprem"],
    "any": CANONICAL_PLATFORM_PROFILES,
}

VALID_SUPPORTED_PLATFORM_VALUES = [
    *LEGACY_PLATFORM_TO_CANONICAL.keys(),
    *CANONICAL_PLATFORM_PROFILES,
]

PACK_MANIFEST_FILENAME = "bnkforge.pack.json"
VALID_PACK_ENGINES: list[str] = engine_registry.valid_pack_engines()
VALID_TEMPLATE_ENGINES = ["simple", "jinja2"]
VALID_PACK_CATEGORIES = ["infra", "k8s", "bnk", "app", "other"]
VALID_RUNNER_PROFILES_BY_ENGINE: dict[str, list[str]] = engine_registry.runner_profiles_by_engine()
REQUIRED_PACK_LIFECYCLE_FIELDS = [
    "supports_init",
    "supports_plan",
    "supports_apply",
    "supports_destroy",
    "supports_refresh",
    "supports_drift",
]

PACK_ENGINE_TO_EXECUTION_ENGINE: dict[str, str] = engine_registry.pack_to_execution()

# ---------------------------------------------------------------------------
# Artifact manifest (bnkforge.artifact.json) — SEAMS spec
# ---------------------------------------------------------------------------
ARTIFACT_MANIFEST_FILENAME = "bnkforge.artifact.json"


def validate_workspace_relative_path(value: str, *, field: str) -> str:
    """Return ``value`` if it is a safe workspace-relative path, else raise.

    Shared by manifest validation and the write path (#442): a manifest is
    external input, so containment is checked again where the file is actually
    created rather than trusted from validation alone.
    """
    candidate = (value or "").strip()
    if not candidate:
        raise InvalidMetadataSchemaError(f"{field} must be a non-empty path")
    if os.path.isabs(candidate) or candidate.startswith("~"):
        raise InvalidMetadataSchemaError(f"{field} '{value}' must be workspace-relative, not absolute")
    if "\x00" in candidate:
        raise InvalidMetadataSchemaError(f"{field} contains a null byte")
    normalized = os.path.normpath(candidate)
    if normalized == ".." or normalized.startswith(".." + os.sep) or os.path.isabs(normalized):
        raise InvalidMetadataSchemaError(
            f"{field} '{value}' escapes the workspace (path traversal)"
        )
    return normalized

# Artifact kind -> (required typed block key, default execution engine).
ARTIFACT_KINDS: dict[str, dict[str, str]] = {
    "container_image": {"block": "container_image", "engine": "container"},
    "helm_chart": {"block": "helm_chart", "engine": "kubernetes"},
    "manifest": {"block": "manifest", "engine": "kubernetes"},
}

# Kinds whose lifecycle is procedural (driven by an explicit step-set).
PROCEDURAL_ARTIFACT_KINDS = {"container_image"}

# A digest pin looks like sha256:<64 hex>. Anything else (a floating tag, or a
# tag-only reference) is rejected — supply-chain immutability requires digests.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# Keys forbidden inside a step: a step may only invoke the artifact's OWN image
# via an argv vector. No shell, no command-string, no image override.
ARTIFACT_STEP_DENYLIST_KEYS = {
    "shell",
    "sh",
    "bash",
    "command",  # shell command-string form
    "script",
    "image",  # cannot point a step at a different image
    "entrypoint",  # cannot override the image entrypoint
}

# argv tokens that indicate a shell is being invoked rather than the own image.
ARTIFACT_STEP_SHELL_TOKENS = {"sh", "bash", "/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash"}

# Action names (D-034): lowercase slug, bounded length.
ARTIFACT_ACTION_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")

# Action names must not shadow a lifecycle phase — the dispatcher routes
# lifecycle ops and actions separately, and a collision would be ambiguous.
ARTIFACT_ACTION_RESERVED_NAMES = {"init", "plan", "apply", "destroy"}

# red-rated tests are untestable in the tool's shape and never offered, so a
# manifest may only declare green (default) or amber (runnable with warning).
ARTIFACT_ACTION_RATINGS = {"green", "amber"}

# Raw-secret field names disallowed anywhere a secret could be inlined.
ARTIFACT_DISALLOWED_SECRET_KEYS = {
    "value",
    "secret",
    "secret_value",
    "token",
    "password",
    "passphrase",
    "private_key",
    "key_material",
    "api_key",
}


class ModuleMetadataParser:
    """
    Parses module.json files and extracts metadata

    Usage:
        parser = ModuleMetadataParser("/path/to/bnk-forge-modules")
        metadata = parser.parse("infra/aws/vpc")
        inputs = parser.get_required_inputs(metadata)
    """

    def __init__(self, modules_repo_path: str):
        """
        Initialize parser with path to modules repository

        Args:
            modules_repo_path: Path to cloned bnk-forge-modules repository
        """
        self.repo_path = Path(modules_repo_path)
        self._cache: dict[str, dict] = {}

    def parse(self, module_path: str) -> dict:
        """
        Parse module.json for given module path

        Args:
            module_path: Relative path to module (e.g., "infra/aws/vpc")

        Returns:
            Parsed module metadata as dictionary

        Raises:
            MissingMetadataError: If module.json doesn't exist
            ModuleMetadataError: If JSON is invalid
        """
        # Check cache first
        if module_path in self._cache:
            return self._cache[module_path]

        # Construct path to module.json
        metadata_file = self.repo_path / module_path / "module.json"

        if not metadata_file.exists():
            raise MissingMetadataError(
                f"module.json not found for {module_path} at {metadata_file}"
            )

        try:
            with open(metadata_file) as f:
                metadata = json.load(f)
        except json.JSONDecodeError as e:
            raise ModuleMetadataError(
                f"Invalid JSON in {metadata_file}: {e}"
            )

        # Cache and return
        self._cache[module_path] = metadata
        return metadata

    def parse_pack_manifest(self, module_path: str) -> dict:
        """
        Parse bnkforge.pack.json for given pack path.

        Args:
            module_path: Relative path to pack root.

        Returns:
            Parsed pack manifest dictionary.

        Raises:
            MissingMetadataError: If bnkforge.pack.json doesn't exist.
            ModuleMetadataError: If JSON is invalid.
        """
        cache_key = f"pack::{module_path}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        manifest_file = self.repo_path / module_path / PACK_MANIFEST_FILENAME
        if not manifest_file.exists():
            raise MissingMetadataError(
                f"{PACK_MANIFEST_FILENAME} not found for {module_path} at {manifest_file}"
            )

        try:
            with open(manifest_file) as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            raise ModuleMetadataError(f"Invalid JSON in {manifest_file}: {e}")

        self._cache[cache_key] = manifest
        return manifest

    def parse_artifact_manifest(self, module_path: str) -> dict:
        """Parse bnkforge.artifact.json for the given artifact path.

        Raises:
            MissingMetadataError: If bnkforge.artifact.json doesn't exist.
            ModuleMetadataError: If JSON is invalid.
        """
        cache_key = f"artifact::{module_path}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        manifest_file = self.repo_path / module_path / ARTIFACT_MANIFEST_FILENAME
        if not manifest_file.exists():
            raise MissingMetadataError(
                f"{ARTIFACT_MANIFEST_FILENAME} not found for {module_path} at {manifest_file}"
            )

        try:
            with open(manifest_file) as f:
                manifest = json.load(f)
        except json.JSONDecodeError as e:
            raise ModuleMetadataError(f"Invalid JSON in {manifest_file}: {e}")

        self._cache[cache_key] = manifest
        return manifest

    def get_module_info(self, metadata: dict) -> ModuleInfo:
        """Extract module info section"""
        module_data = metadata.get("module", {})
        return ModuleInfo(
            name=module_data.get("name", ""),
            path=module_data.get("path", ""),
            version=module_data.get("version", ""),
            layer=module_data.get("layer", ""),
            category=module_data.get("category", ""),
            description=module_data.get("description", ""),
            cloud_specific=module_data.get("cloud_specific", False),
            supported_platforms=module_data.get("supported_platforms", [])
        )

    def get_required_dependencies(self, metadata: dict) -> list[dict]:
        """Extract required dependencies"""
        deps = metadata.get("dependencies", {})
        return deps.get("required", [])

    def get_optional_dependencies(self, metadata: dict) -> list[dict]:
        """Extract optional dependencies"""
        deps = metadata.get("dependencies", {})
        return deps.get("optional", [])

    def get_required_inputs(self, metadata: dict) -> list[dict]:
        """Extract required input variables"""
        inputs = metadata.get("inputs", {})
        return inputs.get("required", [])

    def get_optional_inputs(self, metadata: dict) -> list[dict]:
        """Extract optional input variables"""
        inputs = metadata.get("inputs", {})
        return inputs.get("optional", [])

    def get_user_inputs(self, metadata: dict) -> list[dict]:
        """Get inputs that require user input (source=user)"""
        all_inputs = (
            self.get_required_inputs(metadata) +
            self.get_optional_inputs(metadata)
        )
        return [inp for inp in all_inputs if inp.get("source") == "user"]

    def get_module_inputs(self, metadata: dict) -> list[dict]:
        """Get inputs from other modules (source=module)"""
        all_inputs = (
            self.get_required_inputs(metadata) +
            self.get_optional_inputs(metadata)
        )
        return [inp for inp in all_inputs if inp.get("source") == "module"]

    def get_auto_inputs(self, metadata: dict) -> list[dict]:
        """Get auto-calculated inputs (source=auto)"""
        all_inputs = (
            self.get_required_inputs(metadata) +
            self.get_optional_inputs(metadata)
        )
        return [inp for inp in all_inputs if inp.get("source") == "auto"]

    def get_outputs(self, metadata: dict) -> list[dict]:
        """Extract module outputs"""
        outputs = metadata.get("outputs", {})
        return outputs.get("key_outputs", [])

    def get_required_providers(self, metadata: dict) -> list[str]:
        """Extract required provider list"""
        providers = metadata.get("providers", {})
        return providers.get("required", [])

    def get_optional_providers(self, metadata: dict) -> list[str]:
        """Extract optional provider list"""
        providers = metadata.get("providers", {})
        return providers.get("optional", [])

    def get_backend_recommendations(self, metadata: dict) -> dict[str, str]:
        """Extract backend recommendations per platform"""
        backend = metadata.get("backend", {})
        return backend.get("recommendations", {})

    def get_deployment_info(self, metadata: dict) -> dict:
        """Extract deployment metadata"""
        return metadata.get("deployment", {})

    def get_sensitive_inputs(self, metadata: dict) -> list[str]:
        """Get list of sensitive input variable names"""
        deployment = self.get_deployment_info(metadata)
        return deployment.get("sensitive_inputs", [])

    def get_raw_supported_platforms(self, metadata: dict) -> list[str]:
        """Extract raw/transitional supported_platforms values from metadata."""
        module_data = metadata.get("module", {})
        compatibility_data = metadata.get("compatibility", {})
        supported = module_data.get("supported_platforms", compatibility_data.get("supported_platforms", []))
        if not isinstance(supported, list):
            return []
        return [str(item) for item in supported]

    def get_required_capabilities(self, metadata: dict) -> list[str]:
        """Extract additive required_capabilities values where present."""
        module_data = metadata.get("module", {})
        compatibility_data = metadata.get("compatibility", {})
        capabilities = compatibility_data.get("required_capabilities", module_data.get("required_capabilities", []))
        if not isinstance(capabilities, list):
            return []
        return [str(item) for item in capabilities]

    def invalidate_cache(self, module_path: str | None = None):
        """
        Invalidate metadata cache

        Args:
            module_path: Specific module to invalidate, or None for all
        """
        if module_path:
            self._cache.pop(module_path, None)
        else:
            self._cache.clear()


class ModuleMetadataValidator:
    """
    Validates module metadata against schema

    Usage:
        validator = ModuleMetadataValidator()
        validator.validate(metadata)  # Raises InvalidMetadataSchemaError if invalid
    """

    VALID_LAYERS = [
        "infrastructure",
        "kubernetes",
        "bnk-foundation",
        "bnk-platform",
        "bnk-gateway",
        "bnk-policy"
    ]

    VALID_SOURCES = ["user", "module", "auto"]

    VALID_PLATFORMS = VALID_SUPPORTED_PLATFORM_VALUES

    def validate(self, metadata: dict):
        """
        Validate metadata against schema

        Args:
            metadata: Module metadata dictionary

        Raises:
            InvalidMetadataSchemaError: If validation fails
        """
        self._validate_module_section(metadata)
        self._validate_dependencies_section(metadata)
        self._validate_inputs_section(metadata)
        self._validate_outputs_section(metadata)
        self._validate_providers_section(metadata)

    def validate_pack_manifest(self, manifest: dict):
        """Validate bnkforge.pack.json manifest against bounded schema rules."""
        self._validate_pack_required_top_level(manifest)
        self._validate_pack_schema_version(manifest)
        self._validate_pack_module_section(manifest)
        self._validate_pack_deployment_pack_section(manifest)
        self._validate_pack_dependencies_section(manifest)
        self._validate_pack_inputs_section(manifest)
        self._validate_pack_outputs_section(manifest)
        self._validate_manifest_secret_safety(manifest)

    def validate_artifact_manifest(
        self,
        manifest: dict,
        *,
        registry_host_allowlist: list[str] | None = None,
        known_artifact_refs: set[str] | None = None,
    ) -> dict:
        """Validate a bnkforge.artifact.json manifest per the SEAMS spec.

        Args:
            manifest: parsed artifact manifest dict.
            registry_host_allowlist: admin-configured set of permitted registry
                hosts. When None, the host allowlist check is skipped (callers
                that enforce supply-chain policy must pass it explicitly).
            known_artifact_refs: set of artifact identifiers (name@version or
                name) that references[] entries may resolve to. When None, any
                non-empty reference string is accepted shape-wise but no graph
                is resolved.

        Returns:
            The resolved references graph: ``{"nodes": [...], "edges": [...]}``.

        Raises:
            InvalidMetadataSchemaError: if any rule is violated.
        """
        kind = self._validate_artifact_top_level(manifest)
        self._validate_artifact_typed_block(manifest, kind)
        self._validate_artifact_registry_host(manifest, kind, registry_host_allowlist)
        self._validate_artifact_lifecycle(manifest, kind)
        self._validate_artifact_steps(manifest, kind)
        self._validate_artifact_secret_files(manifest, kind)
        self._validate_artifact_actions(manifest, kind)
        self._validate_artifact_reports(manifest, kind)
        self._validate_artifact_secret_safety(manifest)
        self._normalize_artifact_execution_engine(manifest, kind)
        return self._resolve_artifact_references(manifest, known_artifact_refs)

    def _validate_artifact_top_level(self, manifest: dict) -> str:
        if not isinstance(manifest, dict):
            raise InvalidMetadataSchemaError(f"{ARTIFACT_MANIFEST_FILENAME} must be an object")

        schema_version = manifest.get("schema_version")
        if schema_version != 1:
            raise InvalidMetadataSchemaError(
                f"Invalid schema_version '{schema_version}'. Must be 1"
            )

        for field in ("name", "version"):
            if not self._is_non_empty_string(manifest.get(field)):
                raise InvalidMetadataSchemaError(f"Missing or invalid required field '{field}'")

        kind = manifest.get("kind")
        if kind not in ARTIFACT_KINDS:
            raise InvalidMetadataSchemaError(
                f"Invalid kind '{kind}'. Must be one of: {', '.join(ARTIFACT_KINDS)}"
            )
        return kind

    def _validate_artifact_typed_block(self, manifest: dict, kind: str) -> None:
        block_key = ARTIFACT_KINDS[kind]["block"]
        block = manifest.get(block_key)
        if not isinstance(block, dict):
            raise InvalidMetadataSchemaError(
                f"kind '{kind}' requires a '{block_key}' object block"
            )

        # Every kind that ships an image/chart/manifest must pin an immutable
        # digest — no floating tags.
        digest = block.get("digest")
        if not self._is_non_empty_string(digest):
            raise InvalidMetadataSchemaError(
                f"{block_key}.digest is required (immutable sha256 digest)"
            )
        if not _DIGEST_RE.match(digest.strip()):
            raise InvalidMetadataSchemaError(
                f"{block_key}.digest must be a pinned 'sha256:<64 hex>' digest, not a floating tag"
            )

    def _validate_artifact_registry_host(
        self, manifest: dict, kind: str, registry_host_allowlist: list[str] | None
    ) -> None:
        block_key = ARTIFACT_KINDS[kind]["block"]
        block = manifest.get(block_key, {})
        registry_host = block.get("registry_host")
        if not self._is_non_empty_string(registry_host):
            raise InvalidMetadataSchemaError(f"{block_key}.registry_host is required")

        if registry_host_allowlist is None:
            return

        normalized_allow = {h.strip().lower() for h in registry_host_allowlist if h and h.strip()}
        if registry_host.strip().lower() not in normalized_allow:
            raise InvalidMetadataSchemaError(
                f"{block_key}.registry_host '{registry_host}' is not in the configured registry host allowlist"
            )

    def _validate_artifact_lifecycle(self, manifest: dict, kind: str) -> None:
        lifecycle = manifest.get("lifecycle")
        if lifecycle is None:
            # Lifecycle truth defaults: apply supported, destroy not.
            return
        if not isinstance(lifecycle, dict):
            raise InvalidMetadataSchemaError("'lifecycle' must be an object")

        for field, value in lifecycle.items():
            if not isinstance(value, bool):
                raise InvalidMetadataSchemaError(f"lifecycle.{field} must be a boolean")

    def _validate_artifact_secret_files(self, manifest: dict, kind: str) -> None:
        """Validate the optional ``secret_files`` block (#442).

        Shape: ``[{"secret_name": "<ProjectSecret name>", "path": "<workspace-relative>"}]``.
        (``secret_name``, not ``secret``: the latter is on the raw-secret key
        denylist — that key is how someone would inline a secret VALUE.)
        The engine materializes each named project secret into the run workspace
        before the steps run, so a vendor CLI that expects entitlement material
        as files can be driven from the UI.

        Only procedural kinds have a workspace to write into. Paths are
        workspace-relative and must stay inside it — the write path re-checks
        containment too (defence in depth: a manifest is external input, and
        `state.outputs_file` traversal is the cautionary precedent).
        """
        secret_files = manifest.get("secret_files")
        if secret_files is None:
            return
        if kind not in PROCEDURAL_ARTIFACT_KINDS:
            raise InvalidMetadataSchemaError(
                f"kind '{kind}' has no run workspace and must not declare 'secret_files'"
            )
        if not isinstance(secret_files, list):
            raise InvalidMetadataSchemaError("'secret_files' must be a list")

        seen_paths: set[str] = set()
        for entry in secret_files:
            if not isinstance(entry, dict):
                raise InvalidMetadataSchemaError("each secret_files entry must be an object")
            secret_name = entry.get("secret_name")
            if not self._is_non_empty_string(secret_name):
                raise InvalidMetadataSchemaError(
                    "secret_files[].secret_name is required (a project secret name)"
                )
            path_value = entry.get("path")
            if not self._is_non_empty_string(path_value):
                raise InvalidMetadataSchemaError(
                    f"secret_files[].path is required for secret '{secret_name}'"
                )
            validate_workspace_relative_path(path_value, field=f"secret_files[{secret_name}].path")
            normalized = os.path.normpath(path_value.strip())
            if normalized in seen_paths:
                raise InvalidMetadataSchemaError(
                    f"secret_files[].path '{path_value}' is declared more than once"
                )
            seen_paths.add(normalized)

    def _validate_artifact_steps(self, manifest: dict, kind: str) -> None:
        steps = manifest.get("steps")
        is_procedural = kind in PROCEDURAL_ARTIFACT_KINDS
        lifecycle = manifest.get("lifecycle") if isinstance(manifest.get("lifecycle"), dict) else {}

        if not is_procedural:
            if steps is not None:
                raise InvalidMetadataSchemaError(
                    f"kind '{kind}' is declarative and must not declare 'steps'"
                )
            return

        # Procedural kinds: a step-set is the unit of execution.
        if not isinstance(steps, dict):
            raise InvalidMetadataSchemaError(f"kind '{kind}' requires a 'steps' object")

        # apply step-set is always required for a procedural artifact.
        apply_steps = steps.get("apply")
        self._validate_artifact_step_list(apply_steps, manifest, phase="apply")

        # If destroy is supported, a destroy step-set is required.
        supports_destroy = bool(lifecycle.get("supports_destroy", False))
        if supports_destroy:
            destroy_steps = steps.get("destroy")
            self._validate_artifact_step_list(destroy_steps, manifest, phase="destroy")
        elif steps.get("destroy") is not None:
            self._validate_artifact_step_list(steps.get("destroy"), manifest, phase="destroy")

        # Any other declared phases must still satisfy step rules.
        for phase, step_list in steps.items():
            if phase in ("apply", "destroy"):
                continue
            self._validate_artifact_step_list(step_list, manifest, phase=phase)

    def _validate_artifact_step_list(self, step_list: object, manifest: dict, *, phase: str) -> None:
        if not isinstance(step_list, list) or not step_list:
            raise InvalidMetadataSchemaError(
                f"steps.{phase} must be a non-empty list for a procedural artifact"
            )
        for idx, step in enumerate(step_list):
            self._validate_artifact_step(step, manifest, path=f"steps.{phase}[{idx}]")

    def _validate_artifact_step(self, step: object, manifest: dict, *, path: str) -> None:
        if not isinstance(step, dict):
            raise InvalidMetadataSchemaError(f"{path} must be an object")

        # No shell / command-string / image-override keys.
        for key in step:
            if key in ARTIFACT_STEP_DENYLIST_KEYS:
                raise InvalidMetadataSchemaError(
                    f"{path} must not contain '{key}' — steps run the artifact's own image via argv only "
                    f"(no shell, no command-string, no image override)"
                )

        # A step must invoke the artifact's OWN image via an argv vector.
        argv = step.get("args")
        if not isinstance(argv, list) or not argv:
            raise InvalidMetadataSchemaError(
                f"{path}.args must be a non-empty argv list invoking the artifact's own image"
            )
        for token in argv:
            if not isinstance(token, str):
                raise InvalidMetadataSchemaError(f"{path}.args entries must be strings")
        # First token must not be a shell binary.
        first = argv[0].strip().lower()
        if first in ARTIFACT_STEP_SHELL_TOKENS:
            raise InvalidMetadataSchemaError(
                f"{path}.args must not invoke a shell ('{argv[0]}') — argv runs in the artifact's own image directly"
            )

        # A step may not point at a different image (defense-in-depth: the key is
        # denylisted above, but reject any 'ref'/'from' image redirection too).
        for redirect_key in ("ref", "from", "uses"):
            if self._is_non_empty_string(step.get(redirect_key)):
                raise InvalidMetadataSchemaError(
                    f"{path} must not reference another image via '{redirect_key}'"
                )

    def _validate_artifact_actions(self, manifest: dict, kind: str) -> None:
        """Validate the optional top-level ``actions`` block (D-034).

        Each named action declares a step-set that obeys the exact same rules
        as lifecycle steps (argv-only invoking the artifact's own image,
        denylist, no shell, no image redirection, ``when`` gates and
        ``{{inputs.*}}`` templating allowed).
        """
        actions = manifest.get("actions")
        if actions is None:
            return
        if not isinstance(actions, dict):
            raise InvalidMetadataSchemaError(
                "'actions' must be an object mapping action names to definitions"
            )
        if kind not in PROCEDURAL_ARTIFACT_KINDS:
            raise InvalidMetadataSchemaError(
                f"kind '{kind}' is declarative and must not declare 'actions'"
            )

        for name, definition in actions.items():
            if not isinstance(name, str) or not ARTIFACT_ACTION_NAME_RE.match(name):
                raise InvalidMetadataSchemaError(
                    f"Invalid action name '{name}'. Must match [a-z0-9-]{{1,64}}"
                )
            if name in ARTIFACT_ACTION_RESERVED_NAMES:
                raise InvalidMetadataSchemaError(
                    f"Action name '{name}' collides with a lifecycle phase"
                )
            if not isinstance(definition, dict):
                raise InvalidMetadataSchemaError(f"actions.{name} must be an object")

            if not self._is_non_empty_string(definition.get("title")):
                raise InvalidMetadataSchemaError(
                    f"actions.{name}.title is required and must be a non-empty string"
                )

            description = definition.get("description")
            if description is not None and not isinstance(description, str):
                raise InvalidMetadataSchemaError(f"actions.{name}.description must be a string")

            rating = definition.get("rating")
            if rating is not None and rating not in ARTIFACT_ACTION_RATINGS:
                raise InvalidMetadataSchemaError(
                    f"Invalid actions.{name}.rating '{rating}'. "
                    f"Must be one of: {', '.join(sorted(ARTIFACT_ACTION_RATINGS))}"
                )

            steps = definition.get("steps")
            if not isinstance(steps, list) or not steps:
                raise InvalidMetadataSchemaError(
                    f"actions.{name}.steps must be a non-empty list"
                )
            for idx, step in enumerate(steps):
                self._validate_artifact_step(step, manifest, path=f"actions.{name}.steps[{idx}]")

            self._validate_action_inputs(definition.get("inputs"), action_name=name)

    def _validate_action_inputs(self, inputs: object, *, action_name: str) -> None:
        """Minimal shape check for an action's optional input declarations."""
        if inputs is None:
            return
        if not isinstance(inputs, list):
            raise InvalidMetadataSchemaError(f"actions.{action_name}.inputs must be a list")
        for idx, inp in enumerate(inputs):
            if not isinstance(inp, dict):
                raise InvalidMetadataSchemaError(
                    f"actions.{action_name}.inputs[{idx}] must be an object"
                )
            for field in ("name", "type"):
                if not self._is_non_empty_string(inp.get(field)):
                    raise InvalidMetadataSchemaError(
                        f"actions.{action_name}.inputs[{idx}] missing or invalid '{field}'"
                    )

    def _validate_artifact_reports(self, manifest: dict, kind: str) -> None:
        """Validate the optional top-level ``reports`` block (D-034 PR-2.5).

        Shape: ``{"dir": "<workspace-relative path>"}`` — where the tool writes
        its report tree (``<poc>/reports/<stamp>/...``). ``{{inputs.*}}`` is
        templated like a step arg; the path must stay inside the run workspace
        (rejected if absolute or containing a ``..`` segment). Read-only viewer
        surface only, so only procedural kinds — which own a run workspace — may
        declare it.
        """
        reports = manifest.get("reports")
        if reports is None:
            return
        if kind not in PROCEDURAL_ARTIFACT_KINDS:
            raise InvalidMetadataSchemaError(
                f"kind '{kind}' has no run workspace and must not declare 'reports'"
            )
        if not isinstance(reports, dict):
            raise InvalidMetadataSchemaError("'reports' must be an object")
        dir_value = reports.get("dir")
        if not self._is_non_empty_string(dir_value):
            raise InvalidMetadataSchemaError(
                "reports.dir is required (a non-empty workspace-relative path)"
            )
        candidate = dir_value.strip()
        if candidate.startswith("/") or candidate.startswith("~") or "\x00" in candidate:
            raise InvalidMetadataSchemaError(
                f"reports.dir '{dir_value}' must be workspace-relative, not absolute"
            )
        if any(part == ".." for part in candidate.replace("\\", "/").split("/")):
            raise InvalidMetadataSchemaError(
                f"reports.dir '{dir_value}' escapes the workspace (path traversal)"
            )

    def _validate_artifact_secret_safety(self, manifest: dict) -> None:
        self._reject_embedded_secrets(manifest, path="$")

    def _reject_embedded_secrets(self, node: object, *, path: str, _depth: int = 0) -> None:
        if _depth > 12:
            raise InvalidMetadataSchemaError(f"{path} nesting is too deep")
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    isinstance(key, str)
                    and key.strip().lower() in ARTIFACT_DISALLOWED_SECRET_KEYS
                    and self._is_non_empty_string(value)
                ):
                    raise InvalidMetadataSchemaError(
                        f"{path}.{key} must not contain an embedded secret value"
                    )
                self._reject_embedded_secrets(value, path=f"{path}.{key}", _depth=_depth + 1)
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                self._reject_embedded_secrets(item, path=f"{path}[{idx}]", _depth=_depth + 1)

    def _normalize_artifact_execution_engine(self, manifest: dict, kind: str) -> None:
        execution = manifest.get("execution")
        default_engine = ARTIFACT_KINDS[kind]["engine"]
        if execution is None:
            manifest["execution"] = {"engine": default_engine}
            return
        if not isinstance(execution, dict):
            raise InvalidMetadataSchemaError("'execution' must be an object")
        engine = execution.get("engine")
        if engine is None:
            execution["engine"] = default_engine
        elif not self._is_non_empty_string(engine):
            raise InvalidMetadataSchemaError("execution.engine must be a non-empty string")

    def _resolve_artifact_references(
        self, manifest: dict, known_artifact_refs: set[str] | None
    ) -> dict:
        self_id = f"{manifest.get('name')}@{manifest.get('version')}"
        references = manifest.get("references", [])
        if references is None:
            references = []
        if not isinstance(references, list):
            raise InvalidMetadataSchemaError("'references' must be a list")

        edges: list[dict] = []
        nodes = [self_id]
        for idx, ref in enumerate(references):
            if isinstance(ref, str):
                target = ref.strip()
            elif isinstance(ref, dict):
                target = str(ref.get("ref") or ref.get("name") or "").strip()
            else:
                raise InvalidMetadataSchemaError(f"references[{idx}] must be a string or object")

            if not target:
                raise InvalidMetadataSchemaError(f"references[{idx}] is empty")

            # A node cannot depend on itself — a self-loop makes the graph
            # cyclic and every consumer would have to defend against it.
            if target in (self_id, str(manifest.get("name") or "")):
                raise InvalidMetadataSchemaError(
                    f"references[{idx}] '{target}' is a self-reference (cycle)"
                )

            if known_artifact_refs is not None and target not in known_artifact_refs:
                raise InvalidMetadataSchemaError(
                    f"references[{idx}] '{target}' does not resolve to a known artifact"
                )

            if target not in nodes:
                nodes.append(target)
            edges.append({"from": self_id, "to": target})

        return {"root": self_id, "nodes": nodes, "edges": edges}

    def _validate_module_section(self, metadata: dict):
        """Validate module section"""
        if "module" not in metadata:
            raise InvalidMetadataSchemaError("Missing 'module' section")

        module = metadata["module"]
        required_fields = ["name", "path", "version", "layer", "category", "description"]

        for field in required_fields:
            if field not in module:
                raise InvalidMetadataSchemaError(
                    f"Missing required field 'module.{field}'"
                )

        # Validate layer value
        if module.get("layer") not in self.VALID_LAYERS:
            raise InvalidMetadataSchemaError(
                f"Invalid layer '{module.get('layer')}'. "
                f"Must be one of: {', '.join(self.VALID_LAYERS)}"
            )

        # Validate platforms
        if "supported_platforms" in module:
            for platform in module["supported_platforms"]:
                if platform not in self.VALID_PLATFORMS:
                    raise InvalidMetadataSchemaError(
                        f"Invalid platform '{platform}'. "
                        f"Must be one of: {', '.join(self.VALID_PLATFORMS)}"
                    )

    def _validate_dependencies_section(self, metadata: dict):
        """Validate dependencies section"""
        if "dependencies" not in metadata:
            return  # Dependencies are optional

        deps = metadata["dependencies"]

        # Validate required dependencies
        if "required" in deps:
            for dep in deps["required"]:
                if "module" not in dep:
                    raise InvalidMetadataSchemaError(
                        "Required dependency missing 'module' field"
                    )
                if "reason" not in dep:
                    raise InvalidMetadataSchemaError(
                        "Required dependency missing 'reason' field"
                    )

        # Validate optional dependencies
        if "optional" in deps:
            for dep in deps["optional"]:
                if "module" not in dep:
                    raise InvalidMetadataSchemaError(
                        "Optional dependency missing 'module' field"
                    )

    def _validate_inputs_section(self, metadata: dict):
        """Validate inputs section"""
        if "inputs" not in metadata:
            return  # Inputs are optional

        inputs = metadata["inputs"]

        # Validate required inputs
        if "required" in inputs:
            for inp in inputs["required"]:
                self._validate_input(inp, "required")

        # Validate optional inputs
        if "optional" in inputs:
            for inp in inputs["optional"]:
                self._validate_input(inp, "optional")

    def _validate_input(self, inp: dict, input_type: str):
        """Validate single input definition"""
        required_fields = ["name", "type", "description", "source"]

        for field in required_fields:
            if field not in inp:
                raise InvalidMetadataSchemaError(
                    f"{input_type.capitalize()} input missing '{field}' field"
                )

        # Validate source value
        if inp.get("source") not in self.VALID_SOURCES:
            raise InvalidMetadataSchemaError(
                f"Invalid input source '{inp.get('source')}'. "
                f"Must be one of: {', '.join(self.VALID_SOURCES)}"
            )

        # If source is 'module', validate from_module and from_output
        if inp.get("source") == "module":
            if "from_module" not in inp:
                raise InvalidMetadataSchemaError(
                    "Input with source='module' must have 'from_module' field"
                )
            if "from_output" not in inp:
                raise InvalidMetadataSchemaError(
                    "Input with source='module' must have 'from_output' field"
                )

    def _validate_outputs_section(self, metadata: dict):
        """Validate outputs section"""
        if "outputs" not in metadata:
            return  # Outputs are optional

        outputs = metadata["outputs"]

        if "key_outputs" in outputs:
            for out in outputs["key_outputs"]:
                if "name" not in out:
                    raise InvalidMetadataSchemaError(
                        "Output missing 'name' field"
                    )
                if "type" not in out:
                    raise InvalidMetadataSchemaError(
                        "Output missing 'type' field"
                    )

    def _validate_providers_section(self, metadata: dict):
        """Validate providers section"""
        # Providers section is optional
        pass

    def _validate_pack_required_top_level(self, manifest: dict):
        required_fields = ["schema_version", "module", "deployment_pack", "inputs", "outputs"]
        for field in required_fields:
            if field not in manifest:
                raise InvalidMetadataSchemaError(f"Missing required field '{field}' in {PACK_MANIFEST_FILENAME}")

    def _validate_pack_schema_version(self, manifest: dict):
        schema_version = manifest.get("schema_version")
        if schema_version != 1:
            raise InvalidMetadataSchemaError(
                f"Invalid schema_version '{schema_version}'. Must be 1"
            )

    def _validate_pack_module_section(self, manifest: dict):
        module = manifest.get("module")
        if not isinstance(module, dict):
            raise InvalidMetadataSchemaError("'module' must be an object")

        required_fields = ["name", "path", "version", "category", "description"]
        for field in required_fields:
            value = module.get(field)
            if not isinstance(value, str) or not value.strip():
                raise InvalidMetadataSchemaError(f"Missing or invalid required field 'module.{field}'")

        category = module.get("category")
        if category not in VALID_PACK_CATEGORIES:
            raise InvalidMetadataSchemaError(
                f"Invalid module.category '{category}'. Must be one of: {', '.join(VALID_PACK_CATEGORIES)}"
            )

    def _validate_pack_deployment_pack_section(self, manifest: dict):
        deployment_pack = manifest.get("deployment_pack")
        if not isinstance(deployment_pack, dict):
            raise InvalidMetadataSchemaError("'deployment_pack' must be an object")

        engine = deployment_pack.get("engine")
        if engine not in VALID_PACK_ENGINES:
            raise InvalidMetadataSchemaError(
                f"Invalid deployment_pack.engine '{engine}'. Must be one of: {', '.join(VALID_PACK_ENGINES)}"
            )

        template_engine = deployment_pack.get("template_engine")
        if template_engine is not None:
            if engine != "kubernetes":
                raise InvalidMetadataSchemaError(
                    "deployment_pack.template_engine is only valid when deployment_pack.engine is 'kubernetes'"
                )
            if template_engine not in VALID_TEMPLATE_ENGINES:
                raise InvalidMetadataSchemaError(
                    "Invalid deployment_pack.template_engine "
                    f"'{template_engine}'. Must be one of: {', '.join(VALID_TEMPLATE_ENGINES)}"
                )

        runner_profile = deployment_pack.get("runner_profile")
        allowed_runner_profiles = VALID_RUNNER_PROFILES_BY_ENGINE.get(engine, [])
        if runner_profile not in allowed_runner_profiles:
            raise InvalidMetadataSchemaError(
                f"Invalid deployment_pack.runner_profile '{runner_profile}' for engine '{engine}'. "
                f"Allowed: {', '.join(allowed_runner_profiles)}"
            )

        working_directory = deployment_pack.get("working_directory")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise InvalidMetadataSchemaError("Missing or invalid required field 'deployment_pack.working_directory'")

        lifecycle = deployment_pack.get("lifecycle")
        if not isinstance(lifecycle, dict):
            raise InvalidMetadataSchemaError("'deployment_pack.lifecycle' must be an object")

        for field in REQUIRED_PACK_LIFECYCLE_FIELDS:
            if field not in lifecycle:
                raise InvalidMetadataSchemaError(f"Missing required field 'deployment_pack.lifecycle.{field}'")
            if not isinstance(lifecycle[field], bool):
                raise InvalidMetadataSchemaError(
                    f"Field 'deployment_pack.lifecycle.{field}' must be a boolean"
                )

        if lifecycle.get("supports_apply") is not True:
            raise InvalidMetadataSchemaError("deployment_pack.lifecycle.supports_apply must be true")

        entrypoints = deployment_pack.get("entrypoints")
        if not isinstance(entrypoints, dict):
            raise InvalidMetadataSchemaError("'deployment_pack.entrypoints' must be an object")

        self._validate_engine_entrypoints(engine=engine, entrypoints=entrypoints)

    def _validate_engine_entrypoints(self, engine: str, entrypoints: dict):
        if engine == "opentofu":
            self._validate_required_string(entrypoints, "deployment_pack.entrypoints.module_root")
            return

        if engine == "kubernetes":
            has_manifest_path = self._is_non_empty_string(entrypoints.get("manifest_path"))
            has_chart_path = self._is_non_empty_string(entrypoints.get("chart_path"))
            has_chart_ref = self._is_non_empty_string(entrypoints.get("chart_ref"))
            if not has_manifest_path and not has_chart_path and not has_chart_ref:
                raise InvalidMetadataSchemaError(
                    "deployment_pack.entrypoints requires at least one of 'manifest_path', 'chart_path', or 'chart_ref' for kubernetes"
                )
            return

        if engine == "ansible":
            self._validate_required_string(entrypoints, "deployment_pack.entrypoints.playbook")
            return

        if engine == "script":
            self._validate_required_string(entrypoints, "deployment_pack.entrypoints.apply_script")
            self._validate_required_string(entrypoints, "deployment_pack.entrypoints.outputs_file")

    def _validate_pack_dependencies_section(self, manifest: dict):
        dependencies = manifest.get("dependencies", {"required": [], "optional": []})
        if not isinstance(dependencies, dict):
            raise InvalidMetadataSchemaError("'dependencies' must be an object")

        required_dependencies = dependencies.get("required", [])
        optional_dependencies = dependencies.get("optional", [])

        if not isinstance(required_dependencies, list):
            raise InvalidMetadataSchemaError("'dependencies.required' must be a list")
        if not isinstance(optional_dependencies, list):
            raise InvalidMetadataSchemaError("'dependencies.optional' must be a list")

        for dep in required_dependencies:
            if not isinstance(dep, dict):
                raise InvalidMetadataSchemaError("Each required dependency must be an object")
            if not self._is_non_empty_string(dep.get("module")):
                raise InvalidMetadataSchemaError("Required dependency missing 'module' field")
            if not self._is_non_empty_string(dep.get("reason")):
                raise InvalidMetadataSchemaError("Required dependency missing 'reason' field")

        for dep in optional_dependencies:
            if not isinstance(dep, dict):
                raise InvalidMetadataSchemaError("Each optional dependency must be an object")
            if not self._is_non_empty_string(dep.get("module")):
                raise InvalidMetadataSchemaError("Optional dependency missing 'module' field")

    def _validate_pack_inputs_section(self, manifest: dict):
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict):
            raise InvalidMetadataSchemaError("'inputs' must be an object")

        required_inputs = inputs.get("required", [])
        optional_inputs = inputs.get("optional", [])

        if not isinstance(required_inputs, list):
            raise InvalidMetadataSchemaError("'inputs.required' must be a list")
        if not isinstance(optional_inputs, list):
            raise InvalidMetadataSchemaError("'inputs.optional' must be a list")

        for inp in required_inputs:
            if not isinstance(inp, dict):
                raise InvalidMetadataSchemaError("Each required input must be an object")
            self._validate_input(inp, "required")

        for inp in optional_inputs:
            if not isinstance(inp, dict):
                raise InvalidMetadataSchemaError("Each optional input must be an object")
            self._validate_input(inp, "optional")

    def _validate_pack_outputs_section(self, manifest: dict):
        outputs = manifest.get("outputs")
        if not isinstance(outputs, dict):
            raise InvalidMetadataSchemaError("'outputs' must be an object")

        key_outputs = outputs.get("key_outputs", [])
        if not isinstance(key_outputs, list):
            raise InvalidMetadataSchemaError("'outputs.key_outputs' must be a list")

        for out in key_outputs:
            if not isinstance(out, dict):
                raise InvalidMetadataSchemaError("Each output in outputs.key_outputs must be an object")
            if not self._is_non_empty_string(out.get("name")):
                raise InvalidMetadataSchemaError("Output missing 'name' field")
            if not self._is_non_empty_string(out.get("type")):
                raise InvalidMetadataSchemaError("Output missing 'type' field")
            if not self._is_non_empty_string(out.get("description")):
                raise InvalidMetadataSchemaError("Output missing 'description' field")

            if "value" in out:
                self._validate_pack_output_value(out["value"], path=f"outputs.key_outputs[{out.get('name', '?')}].value")

    def _validate_pack_output_value(self, value: object, *, path: str, _depth: int = 0):
        """Validate optional outputs.key_outputs[].value payload.

        Contract is intentionally bounded to JSON-compatible values with limited
        nesting depth so manifests can declare explicit static/templated outputs
        without becoming an unbounded arbitrary payload channel.
        """
        if _depth > 6:
            raise InvalidMetadataSchemaError(
                f"{path} nesting is too deep (max depth 6)"
            )

        if value is None or isinstance(value, (str, int, float, bool)):
            return

        if isinstance(value, list):
            for idx, item in enumerate(value):
                self._validate_pack_output_value(item, path=f"{path}[{idx}]", _depth=_depth + 1)
            return

        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise InvalidMetadataSchemaError(f"{path} object keys must be strings")
                self._validate_pack_output_value(item, path=f"{path}.{key}", _depth=_depth + 1)
            return

        raise InvalidMetadataSchemaError(
            f"{path} must be JSON-compatible (string/number/boolean/null/object/array)"
        )

    def _validate_manifest_secret_safety(self, manifest: dict):
        # Sensitive inputs should not declare inline defaults.
        inputs = manifest.get("inputs", {})
        input_groups = []
        if isinstance(inputs, dict):
            input_groups.extend(inputs.get("required", []))
            input_groups.extend(inputs.get("optional", []))

        for inp in input_groups:
            if not isinstance(inp, dict):
                continue
            if inp.get("sensitive") is True and self._is_non_empty_string(inp.get("default")):
                input_name = inp.get("name", "<unknown>")
                raise InvalidMetadataSchemaError(
                    f"Sensitive input '{input_name}' must not include an inline default value"
                )

        credentials = manifest.get("credentials")
        if credentials is None:
            return

        if not isinstance(credentials, dict):
            raise InvalidMetadataSchemaError("'credentials' must be an object")

        for group_name in ["required", "optional"]:
            entries = credentials.get(group_name, [])
            if not isinstance(entries, list):
                raise InvalidMetadataSchemaError(f"'credentials.{group_name}' must be a list")

            for entry in entries:
                if not isinstance(entry, dict):
                    raise InvalidMetadataSchemaError(
                        f"Each credentials entry in '{group_name}' must be an object"
                    )
                for required_field in ["name", "type", "description"]:
                    if not self._is_non_empty_string(entry.get(required_field)):
                        raise InvalidMetadataSchemaError(
                            f"Credential entry in '{group_name}' missing '{required_field}'"
                        )

                disallowed_secret_keys = [
                    "value",
                    "secret",
                    "secret_value",
                    "token",
                    "password",
                    "private_key",
                    "key_material",
                ]
                for key in disallowed_secret_keys:
                    if key in entry and self._is_non_empty_string(entry.get(key)):
                        raise InvalidMetadataSchemaError(
                            f"credentials.{group_name} entry must not contain raw secret field '{key}'"
                        )

    def _validate_required_string(self, data: dict, field_path: str):
        field_name = field_path.split(".")[-1]
        if not self._is_non_empty_string(data.get(field_name)):
            raise InvalidMetadataSchemaError(f"Missing or invalid required field '{field_path}'")

    def _is_non_empty_string(self, value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())


def load_all_module_metadata(repo_path: str) -> dict[str, dict]:
    """
    Load metadata for all modules in repository

    Args:
        repo_path: Path to bnk-forge-modules repository

    Returns:
        Dictionary mapping module paths to their metadata
    """
    parser = ModuleMetadataParser(repo_path)
    metadata_map = {}

    repo_root = Path(repo_path)

    # Find all module.json files
    for metadata_file in repo_root.rglob("module.json"):
        # Get relative path to module directory
        module_dir = metadata_file.parent
        module_path = str(module_dir.relative_to(repo_root))

        try:
            metadata = parser.parse(module_path)
            metadata_map[module_path] = metadata
        except ModuleMetadataError as e:
            logger.warning(f"Failed to load metadata for {module_path}: {e}")
            continue

    return metadata_map


def normalize_pack_manifest_for_catalog(pack_manifest: dict) -> dict:
    """
    Normalize validated bnkforge.pack.json data into ModuleLibrary-compatible metadata.

    This helper is additive and intentionally bounded for catalog ingestion workflows.
    """
    module_data = pack_manifest.get("module", {})
    deployment_pack = pack_manifest.get("deployment_pack", {})
    dependencies = pack_manifest.get("dependencies") or {"required": [], "optional": []}
    inputs = pack_manifest.get("inputs", {})
    outputs = pack_manifest.get("outputs", {})

    supported_platforms = module_data.get("supported_platforms", [])
    required_capabilities = module_data.get("required_capabilities", [])

    dependencies_required = dependencies.get("required", [])
    pack_engine = str(deployment_pack.get("engine") or "").strip().lower()
    execution_engine = PACK_ENGINE_TO_EXECUTION_ENGINE.get(pack_engine, pack_engine or None)
    deploy_model = infer_deploy_model_from_pack_manifest(pack_manifest)

    return {
        "name": module_data.get("name"),
        "path": module_data.get("path"),
        "version": module_data.get("version"),
        "category": module_data.get("category"),
        "provider": module_data.get("provider"),
        "description": module_data.get("description"),
        "tags": module_data.get("tags", []),
        "module_source_kind": "git_catalog",
        "execution_engine": execution_engine,
        "deploy_model": deploy_model,
        "engine_type": deployment_pack.get("engine"),
        "pack_manifest": pack_manifest,
        "inputs_metadata": {
            "required": inputs.get("required", []),
            "optional": inputs.get("optional", []),
            "providers": {},
            "compatibility": {
                "supported_platforms": supported_platforms,
                "required_capabilities": required_capabilities,
            },
        },
        "outputs_metadata": outputs.get("key_outputs", []),
        "dependencies_metadata": {
            "required": dependencies_required,
            "optional": dependencies.get("optional", []),
        },
        "dependencies": [dep.get("module") for dep in dependencies_required if dep.get("module")],
    }


def infer_deploy_model_from_pack_manifest(pack_manifest: dict) -> str | None:
    """Infer canonical deploy model from validated pack manifest metadata."""
    deployment_pack = pack_manifest.get("deployment_pack") if isinstance(pack_manifest, dict) else None
    if not isinstance(deployment_pack, dict):
        return None

    engine = str(deployment_pack.get("engine") or "").strip().lower()
    entrypoints = deployment_pack.get("entrypoints")
    entrypoints = entrypoints if isinstance(entrypoints, dict) else {}

    if engine == "opentofu":
        return "tofu_module"
    if engine == "ansible":
        return "ansible_playbook"
    if engine == "script":
        return "script"
    if engine == "kubernetes":
        chart_path = entrypoints.get("chart_path")
        chart_ref = entrypoints.get("chart_ref")
        if (isinstance(chart_path, str) and chart_path.strip()) or (isinstance(chart_ref, str) and chart_ref.strip()):
            return "helm"
        return "manifests"

    return None


def derive_platform_compatibility(
    supported_platforms: list[str] | None,
    required_capabilities: list[str] | None = None,
) -> dict:
    """
    Normalize legacy/transitional platform compatibility metadata.

    Returns a bounded additive contract that preserves raw input while exposing
    canonical profile compatibility for module-library APIs.
    """
    raw_supported = [str(item) for item in (supported_platforms or [])]
    raw_required_capabilities = [str(item) for item in (required_capabilities or [])]

    supported_profiles: list[str] = []
    unknown_values: list[str] = []

    for raw in raw_supported:
        normalized_key = raw.strip().lower()
        if normalized_key in CANONICAL_PLATFORM_PROFILES:
            if normalized_key not in supported_profiles:
                supported_profiles.append(normalized_key)
            continue

        mapped_profiles = LEGACY_PLATFORM_TO_CANONICAL.get(normalized_key)
        if mapped_profiles is None:
            unknown_values.append(raw)
            continue
        for profile in mapped_profiles:
            if profile not in supported_profiles:
                supported_profiles.append(profile)

    compatibility_scope = "unspecified"
    if "any" in [value.strip().lower() for value in raw_supported]:
        compatibility_scope = "declared_all_profiles"
    elif supported_profiles:
        compatibility_scope = "declared_subset"

    return {
        "supported_platforms": raw_supported,
        "required_capabilities": raw_required_capabilities,
        "platform_compatibility": {
            "supported_profiles": supported_profiles,
            "required_capabilities": raw_required_capabilities,
            "compatibility_scope": compatibility_scope,
            "declared_any": compatibility_scope == "declared_all_profiles",
            "unmapped_supported_platforms": unknown_values,
        },
    }
