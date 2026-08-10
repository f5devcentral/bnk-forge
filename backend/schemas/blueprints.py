"""Blueprint manifest schemas for imported governed artifacts."""

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

BLUEPRINT_MANIFEST_FILENAME = "forge-blueprint.json"
SUPPORTED_BLUEPRINT_SCHEMA_VERSION = 1
# Explicit pinned release: at least major.minor.patch, with optional further
# numeric segments (catalog packaging revisions, e.g. '1.20.0.1' — module
# versions in pack manifests are free-form, so the pin contract must accept
# any concrete version the catalog can hold) and optional pre-release/build
# suffixes. 'latest', ranges, and wildcards remain rejected.
PINNED_MODULE_VERSION_PATTERN = re.compile(
    r"^v?\d+\.\d+\.\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class BlueprintIdentity(BaseModel):
    """Stable identity and release metadata for a blueprint artifact."""

    id: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str | None = None


class BlueprintCompatibilityMetadata(BaseModel):
    """Compatibility metadata for platform/profile targeting."""

    supported_platform_profiles: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)


class BlueprintInputOption(BaseModel):
    """Select option for an input with enumerated choices."""

    label: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)
    description: str | None = None


class BlueprintInputDefinition(BaseModel):
    """Blueprint-level input declaration with optional default value."""

    name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    label: str | None = None
    example: str | None = None
    sensitive: bool = False
    validation: dict[str, Any] | None = None
    default: Any | None = None
    source: str | None = None
    source_field: str | None = None
    order: float | None = Field(
        default=None,
        description=(
            "Optional render position in the deploy form. Inputs are sorted "
            "ascending by this number; ties resolve by manifest array "
            "position. Inputs without an order render after all explicitly "
            "ordered inputs, in their original array sequence."
        ),
    )
    options: list[BlueprintInputOption] | None = Field(
        default=None,
        description=(
            "Optional enumerated choices for the input. If provided, the UI "
            "renders a select dropdown instead of a text input."
        ),
    )


class BlueprintPrerequisiteDefinition(BaseModel):
    """Prerequisite metadata used by the rich deploy UI."""

    type: str = Field(..., min_length=1)
    name: str | None = None
    description: str = Field(..., min_length=1)


class BlueprintInputSummaryItem(BaseModel):
    """Supplemental notes rendered above blueprint input forms."""

    label: str = Field(..., min_length=1)
    value: str = Field(..., min_length=1)


class BlueprintModuleDisplayMetadata(BaseModel):
    """Ordered module reference in a blueprint manifest."""

    id: str = Field(..., min_length=1)
    module: str = Field(..., min_length=1)
    version: str = Field(..., min_length=1)
    name: str | None = None
    description: str | None = None
    optional: bool = False
    depends_on: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


    @field_validator("version")
    @classmethod
    def _validate_pinned_module_version(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "latest":
            raise ValueError("implicit latest module version references are not allowed")

        if not PINNED_MODULE_VERSION_PATTERN.fullmatch(value.strip()):
            raise ValueError(
                "module version must be an explicit pinned release (e.g. '1.2.3' or 'v1.2.3'); "
                "floating/range refs are not allowed"
            )

        return value

    @field_validator("module")
    @classmethod
    def _require_non_blank_module_ref(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("module reference must be non-empty")
        return value


class BlueprintInputsMetadata(BaseModel):
    """Required and optional blueprint input declarations."""

    required: list[BlueprintInputDefinition] = Field(default_factory=list)
    optional: list[BlueprintInputDefinition] = Field(default_factory=list)


class BlueprintManifest(BaseModel):
    """Canonical imported blueprint manifest contract."""

    schema_version: int
    blueprint: BlueprintIdentity
    compatibility: BlueprintCompatibilityMetadata = Field(default_factory=BlueprintCompatibilityMetadata)
    category: str | None = None
    cloud_provider: str | None = None
    icon: str | None = None
    color: str | None = None
    estimated_time: str | None = None
    estimated_cost: str | None = None
    difficulty: str | None = None
    maturity: str | None = None
    bnk_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    prerequisites: list[BlueprintPrerequisiteDefinition] = Field(default_factory=list)
    input_summary: list[BlueprintInputSummaryItem] = Field(default_factory=list)
    platform_defaults: dict[str, Any] = Field(default_factory=dict)
    variable_templates: dict[str, Any] = Field(default_factory=dict)
    inputs: BlueprintInputsMetadata = Field(default_factory=BlueprintInputsMetadata)
    is_featured: bool = False
    modules: list[BlueprintModuleDisplayMetadata] = Field(..., min_length=1)

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: int) -> int:
        if value != SUPPORTED_BLUEPRINT_SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SUPPORTED_BLUEPRINT_SCHEMA_VERSION}")
        return value
