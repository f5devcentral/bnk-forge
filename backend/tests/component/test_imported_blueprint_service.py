"""Component tests for imported blueprint project creation flow."""

import pytest

from core.errors import BadRequestError
from services.blueprint_catalog_service import BlueprintCatalogService
from services.imported_blueprint_service import ImportedBlueprintService


def _source_data(**overrides):
    from types import SimpleNamespace

    defaults = {
        "name": "external-blueprints",
        "source_type": "git",
        "url": "https://github.com/example/blueprints.git",
        "branch": "main",
        "git_ref": None,
        "is_active": True,
        "description": "Cataloged blueprints",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _release_manifest() -> dict:
    return {
        "schema_version": 1,
        "blueprint": {
            "id": "ibm-roks-bnk-2-3-ehf.single-nic",
            "version": "2.3.0-ehf-2-3.2598.3-0.0.17",
            "name": "IBM ROKS BNK EHF",
            "description": "Imported release",
        },
        "estimated_time": "20-30 minutes",
        "estimated_cost": "IBM Cloud usage-based",
        "difficulty": "intermediate",
        "maturity": "reference",
        "outcomes": ["Deploys BNK", "Configures cluster integration"],
        "prerequisites": [
            {"type": "project_secret", "name": "ibmcloud_api_key", "description": "IBM Cloud API key"}
        ],
        "tags": ["ibm", "roks", "bnk"],
        "inputs": {
            "required": [
                {
                    "name": "ibmcloud_api_key",
                    "type": "string",
                    "description": "IBM API key",
                    "label": "IBM Cloud API key",
                    "example": "xxxxxxxx",
                    "sensitive": True,
                    "source": "credential_template",
                    "source_field": "ibmcloud_api_key",
                },
                {
                    "name": "ibmcloud_cluster_region",
                    "type": "string",
                    "description": "IBM region",
                    "source": "project",
                    "source_field": "region",
                }
            ],
            "optional": [
                {
                    "name": "ibmcloud_resource_group",
                    "type": "string",
                    "description": "IBM resource group",
                    "default": "default",
                    "source": "credential_template",
                    "source_field": "ibmcloud_resource_group",
                },
                {
                    "name": "deployment_id",
                    "type": "string",
                    "description": "Short deployment suffix",
                    "default": "abc123",
                },
                {
                    "name": "openshift_cluster_name",
                    "type": "string",
                    "description": "Cluster name",
                    "default": "tf-openshift-${deployment_id}",
                },
            ],
        },
        "modules": [
            {
                "id": "cluster",
                "module": "modules/cluster",
                "version": "2.3.0-ehf-2-3.2598.3-0.0.17",
                "name": "Cluster",
                "description": "Connects the imported project to ROKS.",
                "depends_on": [],
                "inputs": {
                    "ibmcloud_api_key": "${ibmcloud_api_key}",
                    "ibmcloud_cluster_region": "${ibmcloud_cluster_region}",
                    "ibmcloud_resource_group": "${ibmcloud_resource_group}",
                    "openshift_cluster_name": "${openshift_cluster_name}",
                },
            },
            {
                "id": "license",
                "module": "modules/license",
                "version": "2.3.0-ehf-2-3.2598.3-0.0.17",
                "depends_on": ["cluster"],
                "inputs": {
                    "jwt_token": "${jwt_token}",
                },
            },
        ],
    }


def test_create_project_from_release_creates_modules_and_dependencies(db, make_module_library):
    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    request = type("Req", (), {
        "name": "imported-ibm-project",
        "description": None,
        "project_type": "cloud-ibm",
        "cloud_provider": "ibm",
        "environment": "production",
        "region": "us-south",
        "credential_template_id": None,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "ibmcloud_api_key": "secret",
            "ibmcloud_cluster_region": "us-south",
            "jwt_token": "jwt-value",
        },
    })()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert result["module_count"] == 2

    from models import ProjectModule

    modules = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"])
        .order_by(ProjectModule.deployment_order)
        .all()
    )
    assert len(modules) == 2
    modules_by_path = {module.path_in_project.split("/")[-2] + "/" + module.path_in_project.split("/")[-1]: module for module in modules}
    cluster_module = modules_by_path["modules/cluster"]
    license_module = modules_by_path["modules/license"]
    assert cluster_module.variable_overrides["ibmcloud_api_key"] == "secret"
    assert license_module.deployment_order > cluster_module.deployment_order


def test_get_required_inputs_returns_blueprint_level_inputs(db):
    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    result = ImportedBlueprintService(db).get_required_inputs(release["id"])
    assert result["template_slug"] == f"release-{release['id']}"
    # ibmcloud_api_key (credential_template) and ibmcloud_cluster_region (project) are both
    # context-resolved → hidden=True, required=False, total_required=0.
    assert result["total_required"] == 0
    assert result["all_inputs"][0]["name"] == "ibmcloud_api_key"
    assert result["all_inputs"][0]["example"] == "xxxxxxxx"
    assert result["all_inputs"][0]["sensitive"] is True
    assert result["all_inputs"][0]["hidden"] is True
    inputs_by_name = {item["name"]: item for item in result["all_inputs"]}
    assert inputs_by_name["ibmcloud_cluster_region"]["hidden"] is True
    assert inputs_by_name["ibmcloud_cluster_region"]["resolved_from"] == "project"
    assert inputs_by_name["deployment_id"]["default"] == "abc123"
    assert inputs_by_name["openshift_cluster_name"]["default"] == "tf-openshift-${deployment_id}"


def test_get_template_like_includes_rich_blueprint_metadata(db):
    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    result = ImportedBlueprintService(db).get_template_like(release["id"])

    assert result["estimated_time"] == "20-30 minutes"
    assert result["outcomes"] == ["Deploys BNK", "Configures cluster integration"]
    assert result["prerequisites"][0]["name"] == "ibmcloud_api_key"
    assert result["modules"][0]["name"] == "Cluster"


def test_create_project_from_release_applies_blueprint_defaults_and_interpolation(db, make_module_library):
    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    request = type("Req", (), {
        "name": "imported-ibm-project",
        "description": None,
        "project_type": "cloud-ibm",
        "cloud_provider": "ibm",
        "environment": "production",
        "region": "us-south",
        "credential_template_id": None,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "ibmcloud_api_key": "secret",
            "ibmcloud_cluster_region": "us-south",
            "jwt_token": "jwt-value",
        },
    })()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)

    from models import ProjectModule

    cluster_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"], ProjectModule.path_in_project.like("%modules/cluster"))
        .first()
    )
    assert cluster_module is not None
    assert cluster_module.variable_overrides["openshift_cluster_name"] == "tf-openshift-abc123"


def test_create_project_from_release_applies_explicit_ibm_input_source_mappings(db, make_module_library):
    from core.encryption import encrypt_value
    from models import CloudCredentialTemplate, ProjectModule

    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    template = CloudCredentialTemplate(
        name="IBM Cloud",
        provider="ibm",
        region="us-south",
        ibmcloud_resource_group="platform-rg",
        ibmcloud_api_key_encrypted=encrypt_value("template-secret"),
    )
    db.add(template)
    db.flush()

    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    request = type("Req", (), {
        "name": "imported-ibm-project",
        "description": None,
        "project_type": "cloud-ibm",
        "cloud_provider": "ibm",
        "environment": "production",
        "region": "us-south",
        "credential_template_id": template.id,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "jwt_token": "jwt-value",
        },
    })()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)

    cluster_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"], ProjectModule.path_in_project.like("%modules/cluster"))
        .first()
    )

    assert cluster_module is not None
    assert cluster_module.variable_overrides["ibmcloud_api_key"] == "template-secret"
    assert cluster_module.variable_overrides["ibmcloud_cluster_region"] == "us-south"
    assert cluster_module.variable_overrides["ibmcloud_resource_group"] == "platform-rg"


def _make_release(db, manifest: dict):
    """Helper to create a BlueprintSource + BlueprintRelease from a manifest dict."""
    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": manifest,
            "source_path": "blueprints/test/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )
    return release


def test_create_project_from_release_user_variables_override_module_inputs(db, make_module_library):
    """User-supplied variables must win over blueprint-hardcoded module inputs.

    Before this fix, kubernetes_version would stay "1.28" (hardcoded literal)
    and create_project_from_release would raise BadRequestError for the
    required-but-unmapped user_ip variable.
    """
    make_module_library(
        name="eks",
        path="infra/aws/eks",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            {"name": "kubernetes_version", "type": "string", "default": "1.28", "required": False},
            {"name": "user_ip", "type": "string", "default": None, "required": True},
        ],
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "aws-eks-test",
            "version": "1.0.0",
            "name": "AWS EKS Test",
            "description": "Test blueprint",
        },
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "eks",
                "module": "infra/aws/eks",
                "version": "1.0.0",
                "name": "EKS",
                "depends_on": [],
                # Blueprint hardcodes kubernetes_version; user_ip not mapped at all.
                "inputs": {
                    "kubernetes_version": "1.28",
                },
            },
        ],
    }

    release = _make_release(db, manifest)

    request = type("Req", (), {
        "name": "override-test-project",
        "description": None,
        "project_type": "cloud-aws",
        "cloud_provider": "aws",
        "environment": "production",
        "region": "us-east-1",
        "credential_template_id": None,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "kubernetes_version": "1.29",
            "user_ip": "1.2.3.4/32",
        },
    })()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True

    from models import ProjectModule

    eks_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"])
        .first()
    )
    assert eks_module is not None
    assert eks_module.variable_overrides["kubernetes_version"] == "1.29"
    assert eks_module.variable_overrides["user_ip"] == "1.2.3.4/32"


def test_get_required_inputs_includes_module_level_required_vars(db, make_module_library):
    """get_required_inputs must surface module-declared vars even when
    the blueprint manifest's top-level inputs block is empty.

    Module-aggregated vars are always optional power-user overrides (required=False)
    regardless of how the module schema declares them — the deploy uses module/blueprint
    defaults for anything the user doesn't supply.
    """
    make_module_library(
        name="security",
        path="infra/aws/security",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            {"name": "user_ip", "type": "string", "default": None, "required": True,
             "description": "Allowed CIDR"},
            {"name": "instance_type", "type": "string", "default": "t3.medium", "required": False,
             "description": "EC2 instance type"},
        ],
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "aws-security-test",
            "version": "1.0.0",
            "name": "AWS Security Test",
            "description": "Test blueprint",
        },
        # Blueprint exposes no top-level inputs.
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "security",
                "module": "infra/aws/security",
                "version": "1.0.0",
                "name": "Security",
                "depends_on": [],
                "inputs": {},
            },
        ],
    }

    release = _make_release(db, manifest)
    result = ImportedBlueprintService(db).get_required_inputs(release["id"])

    module_inputs = result["inputs_by_module"].get("infra/aws/security", [])
    assert len(module_inputs) >= 1

    names_in_module = {item["name"] for item in module_inputs}
    assert "user_ip" in names_in_module
    assert "instance_type" in names_in_module

    # Module-aggregated vars are always required=False (optional power-user overrides).
    user_ip_entry = next(item for item in module_inputs if item["name"] == "user_ip")
    assert user_ip_entry["required"] is False

    all_names = {item["name"] for item in result["all_inputs"]}
    assert "user_ip" in all_names

    # With no blueprint-level required inputs, total_required must be 0.
    assert result["total_required"] == 0


def test_create_project_from_release_inherits_ibm_values_from_credential_template(db, make_module_library):
    from core.encryption import encrypt_value
    from models import CloudCredentialTemplate, ProjectModule

    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    template = CloudCredentialTemplate(
        name="IBM Team",
        provider="ibm",
        region="eu-de",
        ibmcloud_resource_group="platform-rg",
        ibmcloud_api_key_encrypted=encrypt_value("template-secret"),
    )
    db.add(template)
    db.commit()

    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": _release_manifest(),
            "source_path": "blueprints/ibm/forge-blueprint.json",
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )

    request = type("Req", (), {
        "name": "imported-ibm-project",
        "description": None,
        "project_type": "cloud-ibm",
        "cloud_provider": "ibm",
        "environment": "production",
        "region": "eu-de",
        "credential_template_id": template.id,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "jwt_token": "jwt-value",
        },
    })()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    cluster_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"], ProjectModule.path_in_project.like("%modules/cluster"))
        .first()
    )

    assert cluster_module is not None
    assert cluster_module.variable_overrides["ibmcloud_cluster_region"] == "eu-de"
    assert cluster_module.variable_overrides["ibmcloud_api_key"] == "template-secret"
    assert cluster_module.variable_overrides["ibmcloud_resource_group"] == "platform-rg"
    assert cluster_module.variable_overrides["openshift_cluster_name"] == "tf-openshift-abc123"


def test_create_project_from_release_is_atomic_on_variable_validation_failure(db, make_module_library):
    """A post-creation failure must not orphan a project.

    ProjectService.create_project() commits in its own transaction, so without
    compensating cleanup a later add_module() variable-validation error leaves
    an empty project behind. This is a regression test for that leak.
    """
    import pytest

    from core.errors import BadRequestError
    from models import Project

    # Module declares a string input; the blueprint maps a non-string value to it,
    # so add_module() type-validation fails AFTER create_project() has already
    # committed the project row (mirrors the real "prefix must be a string" leak).
    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [{"name": "must_have", "type": "string", "source": "user"}], "optional": []},
        variables_schema=[],
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {"id": "atomic", "version": "1.0.0", "name": "Atomic", "description": "d"},
        "inputs": {"required": [], "optional": []},
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {"must_have": 12345}},
        ],
    }
    release = _make_release(db, manifest, source_path="blueprints/atomic/forge-blueprint.json")

    request = _minimal_request(name="atomic-leak-test-project")

    with pytest.raises(BadRequestError):
        ImportedBlueprintService(db).create_project_from_release(release["id"], request)

    leaked = db.query(Project).filter(Project.name == "atomic-leak-test-project").all()
    assert leaked == [], f"create_project_from_release leaked {len(leaked)} project(s) on validation failure"


def test_create_project_from_release_preserves_manifest_dependencies(db, make_module_library):
    """Manifest depends_on edges must survive deployment-order calculation.

    Library modules carry no dependency metadata, so the default library-derived
    recompute in calculate_deployment_order would clobber the manifest edges set by
    set_dependencies — collapsing every module into one parallel layer. The import
    must keep the manifest-declared dependencies.
    """
    from models import ProjectModule

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    make_module_library(name="license", path="modules/license", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    release = _make_release(db, _release_manifest())

    request = _minimal_request(
        name="deps-preserved-project",
        variables={"ibmcloud_api_key": "secret", "ibmcloud_cluster_region": "us-south", "jwt_token": "jwt-value"},
    )

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)

    modules = {
        m.path_in_project.rsplit("/", 1)[-1]: m
        for m in db.query(ProjectModule).filter(ProjectModule.project_id == result["project_id"]).all()
    }
    cluster, license_mod = modules["cluster"], modules["license"]
    # license depends_on [cluster] in the manifest — that edge must be persisted.
    assert license_mod.dependencies == [cluster.id], (
        f"manifest dependency lost: license.dependencies={license_mod.dependencies}, expected [{cluster.id}]"
    )
    assert cluster.dependencies in ([], None)


# ---------------------------------------------------------------------------
# M2 parity tests: optional flag, aggregated errors, enriched error message
# ---------------------------------------------------------------------------


def _make_release(db, manifest, *, source_path="blueprints/ibm/forge-blueprint.json"):
    """Helper: create a source + release with the given manifest."""
    source = BlueprintCatalogService(db).create_source(_source_data())
    release = BlueprintCatalogService(db).create_release(
        type("ReleaseData", (), {
            "blueprint_source_id": source["id"],
            "manifest": manifest,
            "source_path": source_path,
            "source_ref": "refs/heads/main",
            "release_state": "imported",
            "state_reason": "manual import",
            "is_active": True,
        })()
    )
    return release



def _minimal_request(**overrides):
    defaults = {
        "name": "test-project",
        "description": None,
        "project_type": "cloud-ibm",
        "cloud_provider": "ibm",
        "environment": "production",
        "region": "us-south",
        "credential_template_id": None,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {},
    }
    defaults.update(overrides)
    return type("Req", (), defaults)()


def test_missing_modules_aggregates_all_required_missing(db):
    """_missing_modules returns ALL absent required modules, not just the first."""
    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "a", "module": "modules/absent-a", "version": "1.0.0", "depends_on": []},
            {"id": "b", "module": "modules/absent-b", "version": "1.0.0", "depends_on": []},
        ],
    }
    release = _make_release(db, manifest)
    svc = ImportedBlueprintService(db)
    missing = svc._missing_modules(manifest["modules"])
    paths = [m["path"] for m in missing]
    assert "modules/absent-a" in paths
    assert "modules/absent-b" in paths
    assert len(missing) == 2


def test_create_project_from_release_skips_absent_optional_module(db, make_module_library):
    """An absent optional module does not block deploy; only required modules are created."""
    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
            {"id": "optional-addon", "module": "modules/optional-addon", "version": "1.0.0", "optional": True, "depends_on": [], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _minimal_request()

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert result["module_count"] == 1  # only cluster was created; optional-addon absent but skipped


def test_missing_modules_excludes_absent_optional(db, make_module_library):
    """_missing_modules returns an empty list when only optional modules are absent."""
    make_module_library(name="present", path="modules/present", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    manifest_modules = [
        {"id": "req", "module": "modules/present", "version": "1.0.0", "depends_on": []},
        {"id": "opt", "module": "modules/absent-optional", "version": "1.0.0", "optional": True, "depends_on": []},
    ]
    svc = ImportedBlueprintService(db)
    missing = svc._missing_modules(manifest_modules)
    assert missing == [], f"Expected no missing required modules, got: {missing}"


def test_serialize_modules_for_preview_reflects_optional_flag(db, make_module_library):
    """_serialize_modules_for_preview sets required=False for optional modules."""
    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    manifest_modules = [
        {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "name": "Cluster", "depends_on": [], "inputs": {}},
        {"id": "opt", "module": "modules/absent-opt", "version": "1.0.0", "name": "OptAddon", "optional": True, "depends_on": [], "inputs": {}},
    ]
    svc = ImportedBlueprintService(db)
    preview = svc._serialize_modules_for_preview(manifest_modules)
    by_path = {m["path"]: m for m in preview}

    assert by_path["modules/cluster"]["required"] is True
    assert by_path["modules/absent-opt"]["required"] is False
    assert by_path["modules/absent-opt"]["module_catalog_status"] == "missing"


def test_create_project_from_release_optional_module_added_disabled_and_unvalidated(db, make_module_library):
    """Optional modules in the manifest must be added disabled=True without validating their inputs.

    Before this fix, create_project_from_release would raise BadRequestError because the
    optional module's required user input 'registry' was missing.
    """
    make_module_library(
        name="core",
        path="infra/test/core",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[{"name": "region", "type": "string", "required": False}],
    )
    make_module_library(
        name="extra",
        path="infra/test/extra",
        inputs_metadata={
            "required": [
                {"name": "registry", "type": "string", "source": "user"},
            ],
            "optional": [],
        },
        variables_schema=None,
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "test-optional-disabled",
            "version": "1.0.0",
            "name": "Optional Disabled Test",
            "description": "Test blueprint for optional disabled module",
        },
        "inputs": {"required": [], "optional": []},
        "modules": [
            {
                "id": "core",
                "module": "infra/test/core",
                "version": "1.0.0",
                "name": "Core",
                "depends_on": [],
                "inputs": {},
            },
            {
                "id": "extra",
                "module": "infra/test/extra",
                "version": "1.0.0",
                "name": "Extra (optional)",
                "optional": True,
                "depends_on": [],
                "inputs": {},
                # 'registry' is required by the module schema but NOT provided here
                # — before the fix this would raise BadRequestError
            },
        ],
    }
    release = _make_release(db, manifest)
    request = _minimal_request()

    # Must NOT raise BadRequestError for the missing 'registry' input
    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert result["module_count"] == 2

    from models import ProjectModule

    modules = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"])
        .all()
    )
    by_path = {m.path_in_project.split("/")[-1]: m for m in modules}
    assert by_path["core"].enabled is True
    assert by_path["extra"].enabled is False


def test_create_project_from_release_creates_stack_instance_with_all_modules(db, make_module_library):
    """create_project_from_release must create a StackInstance linked to every module.

    Asserts:
    - A StackInstance row exists with blueprint_release_id == release.id and template_id is None.
    - Every created ProjectModule.stack_instance_id == stack.id.
    """
    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    release = _make_release(db, _release_manifest())
    request = _minimal_request(
        name="stack-instance-test",
        variables={"ibmcloud_api_key": "secret", "ibmcloud_cluster_region": "us-south", "jwt_token": "jwt-value"},
    )

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert "stack_instance_id" in result

    from models import ProjectModule, StackInstance

    stack = db.query(StackInstance).filter(StackInstance.id == result["stack_instance_id"]).first()
    assert stack is not None, "StackInstance must be created"
    assert stack.blueprint_release_id == release["id"]
    assert stack.template_id is None
    assert stack.project_id == result["project_id"]
    assert stack.status == "pending"
    assert stack.total_steps == result["module_count"]

    modules = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"])
        .all()
    )
    assert len(modules) == result["module_count"]
    for module in modules:
        assert module.stack_instance_id == stack.id, (
            f"Module {module.id} (path={module.path_in_project}) has "
            f"stack_instance_id={module.stack_instance_id!r}, expected {stack.id}"
        )


def _project_name_manifest() -> dict:
    """Blueprint manifest with eks_cluster_name resolved from project name (v0.5.4 pattern)."""
    base = _release_manifest()
    base["blueprint"]["id"] = "aws-eks-bnk23-traffic"
    base["blueprint"]["version"] = "0.5.4"
    base["blueprint"]["name"] = "AWS EKS + BNK 2.3 Traffic-Ready"
    base["blueprint"]["description"] = "Deploys AWS EKS and BNK 2.3 with opinionated defaults."
    base["inputs"] = {
        "required": [],
        "optional": [
            {
                "name": "eks_cluster_name",
                "type": "string",
                "description": "EKS cluster name (derived from project name).",
                "source": "project",
                "source_field": "name",
            },
        ],
    }
    return base


def test_apply_explicit_input_source_mappings_project_name_fills_eks_cluster_name(db):
    """source=project, source_field=name must fill a variable from request.name.

    This backs the aws-eks-bnk23-traffic v0.5.4 blueprint where eks_cluster_name
    is declared with source=project/source_field=name so it is derived from the
    project name and requires no user input.
    """
    manifest = _project_name_manifest()
    svc = ImportedBlueprintService(db)
    request = type("Req", (), {
        "name": "my-bnk-cluster",
        "region": "us-east-1",
        "credential_template_id": None,
    })()

    variables: dict = {}
    variables = svc._apply_blueprint_input_defaults(manifest, variables)
    variables = svc._apply_explicit_input_source_mappings(manifest, request, variables)

    assert variables.get("eks_cluster_name") == "my-bnk-cluster", (
        f"eks_cluster_name should be filled from request.name, got: {variables.get('eks_cluster_name')!r}"
    )


def test_apply_explicit_input_source_mappings_project_name_does_not_override_explicit_value(db):
    """An explicitly provided eks_cluster_name must not be overwritten by the project-name mapping."""
    manifest = _project_name_manifest()
    svc = ImportedBlueprintService(db)
    request = type("Req", (), {
        "name": "project-name",
        "region": "us-east-1",
        "credential_template_id": None,
    })()

    # User explicitly provided a different cluster name.
    variables: dict = {"eks_cluster_name": "custom-cluster-override"}
    variables = svc._apply_explicit_input_source_mappings(manifest, request, variables)

    # The explicitly provided value must be preserved.
    assert variables.get("eks_cluster_name") == "custom-cluster-override", (
        f"Explicit eks_cluster_name must not be overwritten, got: {variables.get('eks_cluster_name')!r}"
    )


def test_get_required_inputs_hides_project_name_sourced_input(db):
    """An input declared with source=project/source_field=name must be hidden (not shown to user)."""
    manifest = _project_name_manifest()
    release = _make_release(db, manifest, source_path="blueprints/aws-eks-bnk23-traffic/forge-blueprint.json")

    result = ImportedBlueprintService(db).get_required_inputs(release["id"])

    assert result["total_required"] == 0, (
        f"Expected 0 visible required, got {result['total_required']}"
    )
    by_name = {item["name"]: item for item in result["all_inputs"]}
    assert "eks_cluster_name" in by_name, "eks_cluster_name must appear in all_inputs"
    entry = by_name["eks_cluster_name"]
    assert entry["hidden"] is True, "eks_cluster_name with source=project must be hidden"
    assert entry["resolved_from"] == "project", (
        f"expected resolved_from='project', got {entry['resolved_from']!r}"
    )


def test_create_project_from_release_error_names_source_and_remediation(db):
    """Aggregated BLUEPRINT_MODULES_MISSING error names the blueprint source and gives remediation."""
    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "absent", "module": "modules/absent-required", "version": "1.0.0", "depends_on": []},
        ],
    }
    release = _make_release(db, manifest, source_path="blueprints/eks/forge-blueprint.json")
    request = _minimal_request()

    with pytest.raises(BadRequestError) as exc_info:
        ImportedBlueprintService(db).create_project_from_release(release["id"], request)

    err = exc_info.value
    assert err.code == "BLUEPRINT_MODULES_MISSING"
    assert err.details["missing_modules"][0]["path"] == "modules/absent-required"
    # Error message must name the source path and include remediation text
    assert "blueprints/eks/forge-blueprint.json" in str(err)
    assert "re-sync" in str(err).lower() or "Re-sync" in str(err)


def test_create_project_from_release_creates_stack_instance_with_all_modules(db, make_module_library):
    """create_project_from_release must create a StackInstance linked to every module.

    Asserts:
    - A StackInstance row exists with blueprint_release_id == release.id and template_id is None.
    - Every created ProjectModule.stack_instance_id == stack.id.
    """
    make_module_library(
        name="cluster",
        path="modules/cluster",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    make_module_library(
        name="license",
        path="modules/license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[],
    )
    db.commit()

    release = _make_release(db, _release_manifest())
    request = _minimal_request(
        name="stack-instance-test",
        variables={"ibmcloud_api_key": "secret", "ibmcloud_cluster_region": "us-south", "jwt_token": "jwt-value"},
    )

    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert "stack_instance_id" in result

    from models import ProjectModule, StackInstance

    stack = db.query(StackInstance).filter(StackInstance.id == result["stack_instance_id"]).first()
    assert stack is not None, "StackInstance must be created"
    assert stack.blueprint_release_id == release["id"]
    assert stack.template_id is None
    assert stack.project_id == result["project_id"]
    assert stack.status == "pending"
    assert stack.total_steps == result["module_count"]

    modules = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == result["project_id"])
        .all()
    )
    assert len(modules) == result["module_count"]
    for module in modules:
        assert module.stack_instance_id == stack.id, (
            f"Module {module.id} (path={module.path_in_project}) has "
            f"stack_instance_id={module.stack_instance_id!r}, expected {stack.id}"
        )


# ---------------------------------------------------------------------------
# Simple-deploy classification: context-resolved inputs must be hidden
# ---------------------------------------------------------------------------


def test_get_required_inputs_hides_context_resolved_vars_from_module_aggregation(db, make_module_library):
    """Synthetic aws-eks-bnk23-traffic scenario: blueprint required=[eks_cluster_name];
    creds/region are credential_template/project-sourced; modules declare aws creds,
    jwt_token, and cne_pull_secret as source:user.  After merge-classification,
    total_required must equal 1 (only eks_cluster_name) and the module-declared
    vars that share names with blueprint context-resolved inputs must be hidden.
    """
    # Modules that declare credentials + secrets as source:user (mirroring real BNK modules).
    make_module_library(
        name="eks-infra",
        path="infra/aws/eks-infra",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            # These are declared source:user in the module schema but the blueprint maps them
            # as credential_template / project_secret — classification must win.
            {"name": "aws_access_key_id", "type": "string", "required": True,
             "description": "AWS access key", "source": "user"},
            {"name": "aws_secret_access_key", "type": "string", "required": True,
             "description": "AWS secret key", "source": "user", "sensitive": True},
            {"name": "aws_region", "type": "string", "required": True,
             "description": "AWS region", "source": "user"},
        ],
    )
    make_module_library(
        name="bnk-license",
        path="infra/aws/bnk-license",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            {"name": "jwt_token", "type": "string", "required": True,
             "description": "BNK JWT token", "source": "user", "sensitive": True},
            {"name": "cne_pull_secret", "type": "string", "required": True,
             "description": "CNE pull secret", "source": "user", "sensitive": True},
            # This one has no blueprint-level counterpart and no context source → stays visible.
            {"name": "eks_cluster_name", "type": "string", "required": False,
             "description": "Cluster name (from blueprint level)", "source": "user"},
        ],
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "aws-eks-bnk23-traffic",
            "version": "2.3.0",
            "name": "AWS EKS BNK23 Traffic",
            "description": "EKS BNK deployment blueprint",
        },
        "inputs": {
            "required": [
                # Sole genuine user input — no source annotation.
                {"name": "eks_cluster_name", "type": "string",
                 "description": "EKS cluster name", "example": "my-eks-cluster"},
            ],
            "optional": [
                # These are resolved from forge context — hidden from user.
                {"name": "aws_access_key_id", "type": "string", "description": "AWS access key",
                 "source": "credential_template", "source_field": "aws_access_key_id"},
                {"name": "aws_secret_access_key", "type": "string", "description": "AWS secret key",
                 "source": "credential_template", "source_field": "aws_secret_access_key"},
                {"name": "aws_region", "type": "string", "description": "AWS region",
                 "source": "project", "source_field": "region"},
                {"name": "jwt_token", "type": "string", "description": "BNK JWT token",
                 "source": "project_secret", "source_field": "jwt_token"},
                {"name": "cne_pull_secret", "type": "string", "description": "CNE pull secret",
                 "source": "project_secret", "source_field": "cne_pull_secret"},
            ],
        },
        "modules": [
            {
                "id": "eks-infra",
                "module": "infra/aws/eks-infra",
                "version": "2.3.0",
                "name": "EKS Infrastructure",
                "depends_on": [],
                "inputs": {
                    "aws_access_key_id": "${aws_access_key_id}",
                    "aws_secret_access_key": "${aws_secret_access_key}",
                    "aws_region": "${aws_region}",
                },
            },
            {
                "id": "bnk-license",
                "module": "infra/aws/bnk-license",
                "version": "2.3.0",
                "name": "BNK License",
                "depends_on": ["eks-infra"],
                "inputs": {
                    "jwt_token": "${jwt_token}",
                    "cne_pull_secret": "${cne_pull_secret}",
                    "eks_cluster_name": "${eks_cluster_name}",
                },
            },
        ],
    }

    release = _make_release(db, manifest, source_path="blueprints/aws/forge-blueprint.json")
    result = ImportedBlueprintService(db).get_required_inputs(release["id"])

    # Only eks_cluster_name is a genuine user input.
    assert result["total_required"] == 1, (
        f"Expected total_required=1, got {result['total_required']}. "
        f"Non-hidden required inputs: {[i['name'] for i in result['all_inputs'] if i.get('required')]}"
    )

    # Blueprint-level required input is visible and not hidden.
    bp_inputs = result["inputs_by_module"].get("blueprint", [])
    eks_cluster_in_bp = next((i for i in bp_inputs if i["name"] == "eks_cluster_name"), None)
    assert eks_cluster_in_bp is not None
    assert eks_cluster_in_bp["hidden"] is False
    assert eks_cluster_in_bp["required"] is True

    # Blueprint context-resolved optional inputs are hidden.
    by_name = {i["name"]: i for i in result["all_inputs"]}
    for hidden_name in ("aws_access_key_id", "aws_secret_access_key", "aws_region",
                        "jwt_token", "cne_pull_secret"):
        assert by_name[hidden_name]["hidden"] is True, (
            f"Expected {hidden_name} to be hidden"
        )
        assert by_name[hidden_name]["required"] is False, (
            f"Expected {hidden_name} required=False (hidden)"
        )

    # Module-aggregated vars that share names with blueprint context-resolved inputs are hidden.
    eks_infra_vars = result["inputs_by_module"].get("infra/aws/eks-infra", [])
    eks_infra_by_name = {v["name"]: v for v in eks_infra_vars}
    assert eks_infra_by_name["aws_access_key_id"]["hidden"] is True
    assert eks_infra_by_name["aws_secret_access_key"]["hidden"] is True
    assert eks_infra_by_name["aws_region"]["hidden"] is True

    bnk_license_vars = result["inputs_by_module"].get("infra/aws/bnk-license", [])
    bnk_license_by_name = {v["name"]: v for v in bnk_license_vars}
    assert bnk_license_by_name["jwt_token"]["hidden"] is True
    assert bnk_license_by_name["cne_pull_secret"]["hidden"] is True
    # eks_cluster_name is exposed as a visible blueprint-level required input, so the
    # module's duplicate copy is deduped out of the aggregation (blueprint input wins).
    # The form shows it exactly once — in the blueprint bucket, asserted above.
    assert "eks_cluster_name" not in bnk_license_by_name


# ---------------------------------------------------------------------------
# Part 1: create-from-release must not fail for credential_template-sourced vars
# ---------------------------------------------------------------------------


def test_get_required_inputs_module_vars_are_never_blocking_required(db, make_module_library):
    """Module-aggregated vars must always have required=False regardless of their module schema.

    total_required must count ONLY the blueprint's own top-level required non-hidden inputs.
    Module vars are optional power-user overrides presented behind an Advanced toggle in the UI.
    """
    make_module_library(
        name="eks",
        path="infra/aws/eks",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            # Module schema marks these required — the service must override to False.
            {"name": "cluster_size", "type": "string", "default": None, "required": True,
             "description": "Number of nodes"},
            {"name": "kubernetes_version", "type": "string", "default": "1.28", "required": True,
             "description": "K8s version"},
            # Optional in module schema — also stays False.
            {"name": "instance_type", "type": "string", "default": "t3.medium", "required": False,
             "description": "EC2 instance type"},
        ],
    )
    db.commit()

    # Blueprint declares one genuine required user input + one context-resolved hidden input.
    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "aws-eks-test",
            "version": "1.0.0",
            "name": "AWS EKS Test",
            "description": "Test blueprint",
        },
        "inputs": {
            "required": [
                {"name": "project_name", "type": "string", "description": "Project name"},
            ],
            "optional": [
                {"name": "aws_region", "type": "string", "description": "AWS region",
                 "source": "project", "source_field": "region"},
            ],
        },
        "modules": [
            {
                "id": "eks",
                "module": "infra/aws/eks",
                "version": "1.0.0",
                "name": "EKS",
                "depends_on": [],
                "inputs": {},
            },
        ],
    }

    release = _make_release(db, manifest)
    result = ImportedBlueprintService(db).get_required_inputs(release["id"])

    # All module-aggregated vars must be required=False regardless of module schema.
    module_vars = result["inputs_by_module"].get("infra/aws/eks", [])
    assert len(module_vars) == 3
    for var in module_vars:
        assert var["required"] is False, (
            f"Module var '{var['name']}' should have required=False, got required={var['required']}"
        )

    # total_required reflects ONLY the blueprint's own required non-hidden top-level inputs.
    # project_name has no source → required=True, not hidden → counted.
    # aws_region has source=project → hidden, not counted.
    assert result["total_required"] == 1, (
        f"Expected total_required=1 (only blueprint-level project_name), got {result['total_required']}"
    )

    # No module vars appear in all_inputs with required=True.
    module_path_vars = [i for i in result["all_inputs"] if i["module_path"] != "blueprint"]
    for var in module_path_vars:
        assert var["required"] is False, (
            f"Non-blueprint var '{var['name']}' (module_path={var['module_path']}) "
            f"must have required=False, got required={var['required']}"
        )


def test_create_project_from_release_skips_validation_for_credential_template_sourced_vars(
    db, make_module_library
):
    """Regression: aws_access_key_id / aws_secret_access_key declared as
    source:credential_template in the blueprint manifest must NOT trigger
    'Variable validation failed: Variable … must be a string' at create time.

    The modules declare these as required in variables_schema (source:user), but
    the blueprint overrides their source to credential_template — which means Forge
    resolves them at apply time via TF_VAR injection, not at create time.
    """
    make_module_library(
        name="eks-cluster-create",
        path="infra/aws/eks-cluster-create",
        inputs_metadata={"required": [], "optional": []},
        variables_schema=[
            # Module declares them required (source: user) — this is the shape
            # that triggers the existing bug without the fix.
            {"name": "aws_access_key_id", "type": "string", "required": True,
             "description": "AWS access key ID"},
            {"name": "aws_secret_access_key", "type": "string", "required": True,
             "description": "AWS secret access key", "sensitive": True},
            {"name": "aws_region", "type": "string", "required": True,
             "description": "AWS region"},
            {"name": "cluster_name", "type": "string", "required": True,
             "description": "EKS cluster name"},
        ],
    )
    db.commit()

    manifest = {
        "schema_version": 1,
        "blueprint": {
            "id": "aws-eks-bnk23-traffic",
            "version": "2.3.0",
            "name": "AWS EKS BNK 23 Traffic",
            "description": "EKS BNK deployment",
        },
        "inputs": {
            "required": [
                # Sole genuine user input — no source annotation.
                {"name": "cluster_name", "type": "string",
                 "description": "EKS cluster name", "example": "my-eks-cluster"},
            ],
            "optional": [
                # Resolved by Forge from the credential template at deploy time.
                {"name": "aws_access_key_id", "type": "string",
                 "description": "AWS access key (resolved from credential template)",
                 "source": "credential_template", "source_field": "aws_access_key_id"},
                {"name": "aws_secret_access_key", "type": "string",
                 "description": "AWS secret key (resolved from credential template)",
                 "source": "credential_template", "source_field": "aws_secret_access_key"},
                {"name": "aws_region", "type": "string",
                 "description": "AWS region (resolved from project)",
                 "source": "project", "source_field": "region"},
            ],
        },
        "modules": [
            {
                "id": "eks-cluster",
                "module": "infra/aws/eks-cluster-create",
                "version": "2.3.0",
                "name": "EKS Cluster",
                "depends_on": [],
                "inputs": {
                    "aws_access_key_id": "${aws_access_key_id}",
                    "aws_secret_access_key": "${aws_secret_access_key}",
                    "aws_region": "${aws_region}",
                    "cluster_name": "${cluster_name}",
                },
            },
        ],
    }

    release = _make_release(db, manifest, source_path="blueprints/aws/forge-blueprint.json")

    # User only provides the genuine user input — NOT the credential vars.
    request = type("Req", (), {
        "name": "eks-test-project",
        "description": None,
        "project_type": "cloud-aws",
        "cloud_provider": "aws",
        "environment": "production",
        "region": "us-east-1",
        "credential_template_id": None,
        "backend_type": "local",
        "color": "#2563eb",
        "icon": "",
        "variables": {
            "cluster_name": "my-eks-cluster",
        },
    })()

    # Must NOT raise BadRequestError ("Variable validation failed: Variable … must be a string")
    result = ImportedBlueprintService(db).create_project_from_release(release["id"], request)
    assert result["success"] is True
    assert result["module_count"] == 1

    from models import ProjectModule

    module = db.query(ProjectModule).filter(ProjectModule.project_id == result["project_id"]).first()
    assert module is not None
    # The cluster_name user input is wired through.
    assert module.variable_overrides.get("cluster_name") == "my-eks-cluster"
    # The credential-template vars resolve to empty/None at create time — that's expected
    # (Forge fills TF_VAR_aws_access_key_id at apply time from the credential template).
    # Validation must not have blocked creation.


# ---------------------------------------------------------------------------
# add_release_to_project tests
# ---------------------------------------------------------------------------


def _add_to_project_request(**overrides):
    defaults = {"variables": {}}
    defaults.update(overrides)
    return type("Req", (), defaults)()


def test_add_release_to_project_creates_modules_in_existing_project(db, make_project, make_module_library):
    """add_release_to_project adds blueprint modules to an existing project."""
    from models import ProjectModule
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    make_module_library(name="license", path="modules/license", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="existing-project")

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
            {"id": "license", "module": "modules/license", "version": "1.0.0", "depends_on": ["cluster"], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)

    assert result["success"] is True
    assert result["project_id"] == project.id
    assert result["project_name"] == "existing-project"
    assert result["module_count"] == 2

    modules = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == project.id)
        .order_by(ProjectModule.deployment_order)
        .all()
    )
    assert len(modules) == 2
    # path_in_project is "imported-blueprint/{release_id}/modules/cluster" etc.
    # The module path ends with the last two components (e.g. "modules/cluster").
    paths = {"/".join(m.path_in_project.split("/")[-2:]) for m in modules}
    assert paths == {"modules/cluster", "modules/license"}


def test_add_release_to_project_no_cluster_prerequisite_no_cluster_required(db, make_module_library):
    """add_release_to_project succeeds when no kubernetes_cluster prerequisite in manifest."""
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="no-cluster-project")

    # Single-module manifest; prerequisites has only project_secret — no kubernetes_cluster
    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    # Should not raise even though project has no K8s cluster, since no kubernetes_cluster prereq
    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)
    assert result["success"] is True


def test_add_release_to_project_requires_cluster_when_prerequisite_declared(db, make_module_library):
    """add_release_to_project raises BLUEPRINT_REQUIRES_REGISTERED_CLUSTER when manifest declares kubernetes_cluster prerequisite and project has none."""
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    make_module_library(name="license", path="modules/license", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="cluster-required-project")

    manifest = {
        **_release_manifest(),
        "prerequisites": [{"type": "kubernetes_cluster", "description": "A registered K8s cluster"}],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    with pytest.raises(BadRequestError) as exc_info:
        ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)

    err = exc_info.value
    assert err.code == "BLUEPRINT_REQUIRES_REGISTERED_CLUSTER"


def test_add_release_to_project_succeeds_when_cluster_present(db, make_module_library):
    """add_release_to_project succeeds when manifest requires cluster and project has one registered."""
    from tests.factories import KubernetesClusterFactory, ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    make_module_library(name="license", path="modules/license", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="project-with-cluster")
    KubernetesClusterFactory(db, project=project)

    manifest = {
        **_release_manifest(),
        "prerequisites": [{"type": "kubernetes_cluster", "description": "A registered K8s cluster"}],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request(variables={"jwt_token": "jwt-val"})

    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)
    assert result["success"] is True
    assert result["module_count"] == 2


def test_add_release_to_project_blocks_missing_required_modules(db):
    """add_release_to_project raises BLUEPRINT_MODULES_MISSING when required modules are absent."""
    from tests.factories import ProjectFactory

    project = ProjectFactory(db, name="target-project")

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "absent", "module": "modules/absent-required", "version": "1.0.0", "depends_on": []},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    with pytest.raises(BadRequestError) as exc_info:
        ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)

    err = exc_info.value
    assert err.code == "BLUEPRINT_MODULES_MISSING"


# ---------------------------------------------------------------------------
# CRITICAL-2: full variable pipeline for add_release_to_project
# ---------------------------------------------------------------------------


def test_add_release_to_project_applies_full_variable_pipeline(db, make_module_library):
    """add_release_to_project must apply blueprint defaults, credential-template inheritance,
    AND explicit source-mapping (source=project, source_field=region) — parity with
    create_project_from_release."""
    from models import ProjectModule
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    make_module_library(name="license", path="modules/license", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="pipeline-test-project", region="us-south")

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {
                "ibmcloud_cluster_region": "${ibmcloud_cluster_region}",
            }},
        ],
    }
    release = _make_release(db, manifest)
    # Request has no region — it should be resolved from the loaded project via
    # _apply_explicit_input_source_mappings (source=project, source_field=region).
    request = _add_to_project_request(variables={})

    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)
    assert result["success"] is True

    cluster_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == project.id, ProjectModule.path_in_project.like("%modules/cluster"))
        .first()
    )
    assert cluster_module is not None
    # _apply_explicit_input_source_mappings resolved ibmcloud_cluster_region from request.region
    assert cluster_module.variable_overrides.get("ibmcloud_cluster_region") == "us-south"


def test_add_release_to_project_rejects_surviving_inherit_sentinel(db, make_module_library):
    """add_release_to_project must reject (not persist) a value that still equals the
    inherit-from-template sentinel after the credential-template inheritance pipeline —
    e.g. the target project has no matching credential template to resolve it from
    (PR #401 review residual: previously this literal sentinel was silently persisted)."""
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    # No credential_template_id -> _apply_credential_template_inheritance is a no-op,
    # so the sentinel sent by the dialog survives unresolved.
    project = ProjectFactory(db, name="no-credential-template-project")

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request(variables={"ibmcloud_api_key": "__inherited_from_template__"})

    with pytest.raises(BadRequestError) as exc_info:
        ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)

    err = exc_info.value
    assert err.code == "BLUEPRINT_CREDENTIAL_TEMPLATE_REQUIRED"
    assert "ibmcloud_api_key" in str(err)


# ---------------------------------------------------------------------------
# CRITICAL-3: duplicate deploy guard
# ---------------------------------------------------------------------------


def test_add_release_to_project_blocks_duplicate_install(db, make_module_library):
    """add_release_to_project raises BLUEPRINT_ALREADY_INSTALLED on second call for same release."""
    from tests.factories import ProjectFactory

    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="dup-guard-project")

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    # First call succeeds
    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)
    assert result["success"] is True

    # Second call must be blocked
    with pytest.raises(BadRequestError) as exc_info:
        ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)

    err = exc_info.value
    assert err.code == "BLUEPRINT_ALREADY_INSTALLED"


# ---------------------------------------------------------------------------
# HIGH-1: deployment_order offset when project already has modules
# ---------------------------------------------------------------------------


def test_add_release_to_project_offsets_deployment_order_past_existing_modules(db, make_module_library):
    """New blueprint modules must have deployment_order distinct from (and after) pre-existing modules.

    After add_release_to_project, calculate_deployment_order runs a topological sort over ALL
    project modules and reassigns orders 0..N globally. The invariant we test is:
    - All modules are present (no rows dropped).
    - The new blueprint module exists in the project.
    - The two modules have different deployment_order values (no collision at final state).
    """
    from models import ProjectModule
    from tests.factories import ModuleLibraryFactory, ProjectFactory, ProjectModuleFactory

    pre_lib = ModuleLibraryFactory(db, name="pre-existing-module", path="modules/pre-existing")
    make_module_library(name="cluster", path="modules/cluster", inputs_metadata={"required": [], "optional": []}, variables_schema=[])
    db.commit()

    project = ProjectFactory(db, name="existing-modules-project")
    # Pre-existing module already in the project
    pre_module = ProjectModuleFactory(db, project=project, library_module=pre_lib, deployment_order=5)
    db.flush()

    manifest = {
        **_release_manifest(),
        "modules": [
            {"id": "cluster", "module": "modules/cluster", "version": "1.0.0", "depends_on": [], "inputs": {}},
        ],
    }
    release = _make_release(db, manifest)
    request = _add_to_project_request()

    result = ImportedBlueprintService(db).add_release_to_project(release["id"], project.id, request)
    assert result["success"] is True
    assert result["module_count"] == 1

    db.refresh(pre_module)
    new_module = (
        db.query(ProjectModule)
        .filter(ProjectModule.project_id == project.id, ProjectModule.path_in_project.like("%modules/cluster"))
        .first()
    )
    assert new_module is not None
    # After topological re-sort, both modules must have distinct deployment_order values
    # (the offset at add_module time prevents in-flight collisions; topo-sort finalises ordering).
    assert new_module.deployment_order != pre_module.deployment_order
