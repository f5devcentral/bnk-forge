"""#458 (D-034 PR-2.5): surface the report tree ctl tools write into a module workspace.

Covers the manifest contract (validation), run listing (newest-first, templated
dir, empty when undeclared), file-content reads (md/json), and the containment
guard (traversal, symlink leaf, size cap).
"""

import os

import pytest

from core.errors import AppError, BadRequestError
from services.module_metadata import InvalidMetadataSchemaError, ModuleMetadataValidator
from services.module_reports_service import MAX_REPORT_FILE_BYTES, ModuleReportsService
from services.workspace_manager import WorkspaceManager
from tests.factories import ModuleLibraryFactory, ProjectFactory, ProjectModuleFactory

# ── manifest contract ────────────────────────────────────────────────────────


def _artifact(reports=None):
    manifest = {
        "schema_version": 1,
        "name": "runner",
        "version": "1.0.0",
        "kind": "container_image",
        "lifecycle": {"supports_apply": True},
        "steps": {"apply": [{"name": "up", "args": ["tool", "up"]}]},
        "container_image": {
            "registry_host": "ghcr.io",
            "repository": "org/runner",
            "digest": "sha256:" + "a" * 64,
        },
    }
    if reports is not None:
        manifest["reports"] = reports
    return manifest


def _validate(manifest):
    return ModuleMetadataValidator().validate_artifact_manifest(
        manifest, registry_host_allowlist=["ghcr.io"]
    )


def test_reports_absent_is_valid():
    _validate(_artifact(None))


def test_reports_valid_dir_accepted():
    _validate(_artifact({"dir": "poc/reports"}))


def test_reports_templated_dir_accepted():
    _validate(_artifact({"dir": "{{inputs.poc_name}}/reports"}))


@pytest.mark.parametrize(
    "reports, expected",
    [
        ({}, "reports.dir is required"),
        ({"dir": ""}, "reports.dir is required"),
        ({"dir": 123}, "reports.dir is required"),
        ({"dir": "/etc/passwd"}, "must be workspace-relative"),
        ({"dir": "../escape"}, "escapes the workspace"),
        ({"dir": "a/../../b"}, "escapes the workspace"),
        ("notdict", "'reports' must be an object"),
    ],
)
def test_reports_invalid_rejected(reports, expected):
    with pytest.raises(InvalidMetadataSchemaError, match=expected):
        _validate(_artifact(reports))


def test_reports_rejected_on_declarative_kind():
    manifest = {
        "schema_version": 1,
        "name": "chart",
        "version": "1.0.0",
        "kind": "helm_chart",
        "helm_chart": {
            "registry_host": "ghcr.io",
            "repository": "org/chart",
            "digest": "sha256:" + "b" * 64,
        },
        "reports": {"dir": "poc/reports"},
    }
    with pytest.raises(InvalidMetadataSchemaError, match="has no run workspace"):
        _validate(manifest)


# ── service: workspace fixtures ──────────────────────────────────────────────


def _module_with_reports(db, monkeypatch, tmp_path, *, dir_value="poc/reports", variables=None):
    monkeypatch.setattr(WorkspaceManager, "BASE_PATH", str(tmp_path))
    project = ProjectFactory(db)
    lib = ModuleLibraryFactory(db, pack_manifest=_artifact({"dir": dir_value}))
    module = ProjectModuleFactory(
        db, project=project, library_module=lib, status="applied",
        variables=variables or {},
    )
    # Workspace root is BASE_PATH/<project>/<module> (component scope).
    ws_root = os.path.join(str(tmp_path), str(project.id), str(module.id))
    return module, ws_root


def _write(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


@pytest.mark.component
class TestModuleReportsListing:
    def test_lists_runs_newest_first_with_files(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        reports = os.path.join(ws_root, "poc", "reports")
        _write(os.path.join(reports, "2026-07-18T06-00-00Z", "run-poc.md"), "# Report\nok")
        _write(os.path.join(reports, "2026-07-18T06-00-00Z", "logs", "00-init.log"), "log")
        _write(os.path.join(reports, "2026-07-18T07-00-00Z", "run-poc.md"), "# Later")

        result = ModuleReportsService(db).list_runs(module.id)
        stamps = [r["stamp"] for r in result["runs"]]
        assert stamps == ["2026-07-18T07-00-00Z", "2026-07-18T06-00-00Z"]
        older = next(r for r in result["runs"] if r["stamp"] == "2026-07-18T06-00-00Z")
        paths = {f["path"]: f["kind"] for f in older["files"]}
        assert paths["2026-07-18T06-00-00Z/run-poc.md"] == "md"
        assert paths["2026-07-18T06-00-00Z/logs/00-init.log"] == "log"

    def test_includes_top_level_files(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        reports = os.path.join(ws_root, "poc", "reports")
        _write(os.path.join(reports, "index.json"), "{}")
        _write(os.path.join(reports, "2026-07-18T06-00-00Z", "run.md"), "x")

        result = ModuleReportsService(db).list_runs(module.id)
        root_run = next(r for r in result["runs"] if r["stamp"] == "")
        assert root_run["files"][0]["path"] == "index.json"
        # The top-level pseudo-run sorts last (after real stamped runs).
        assert result["runs"][-1]["stamp"] == ""

    def test_templated_dir_resolves(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(
            db, monkeypatch, tmp_path,
            dir_value="{{inputs.poc_name}}/reports",
            variables={"poc_name": "mypoc"},
        )
        _write(os.path.join(ws_root, "mypoc", "reports", "2026-07-18T06-00-00Z", "run.md"), "hi")

        result = ModuleReportsService(db).list_runs(module.id)
        assert [r["stamp"] for r in result["runs"]] == ["2026-07-18T06-00-00Z"]

    def test_no_reports_block_returns_empty(self, db, monkeypatch, tmp_path):
        monkeypatch.setattr(WorkspaceManager, "BASE_PATH", str(tmp_path))
        lib = ModuleLibraryFactory(db, pack_manifest=_artifact(None))
        module = ProjectModuleFactory(db, library_module=lib, status="applied")
        assert ModuleReportsService(db).list_runs(module.id) == {
            "module_id": module.id,
            "runs": [],
        }

    def test_missing_dir_returns_empty(self, db, monkeypatch, tmp_path):
        module, _ws = _module_with_reports(db, monkeypatch, tmp_path)
        # No reports dir created on disk.
        assert ModuleReportsService(db).list_runs(module.id)["runs"] == []


@pytest.mark.component
class TestModuleReportsContent:
    def test_reads_markdown(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        _write(os.path.join(ws_root, "poc", "reports", "run.md"), "# Title\nbody")

        out = ModuleReportsService(db).read_content(module.id, "run.md")
        assert out["kind"] == "md"
        assert out["content"] == "# Title\nbody"
        assert out["path"] == "run.md"

    def test_reads_json_raw(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        _write(os.path.join(ws_root, "poc", "reports", "s", "scn.json"), '{"a":1}')

        out = ModuleReportsService(db).read_content(module.id, "s/scn.json")
        assert out["kind"] == "json"
        assert out["content"] == '{"a":1}'

    def test_traversal_path_refused(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        # Plant a secret OUTSIDE the reports dir (still under workspace).
        _write(os.path.join(ws_root, "secret.txt"), "TOP SECRET")
        _write(os.path.join(ws_root, "poc", "reports", "run.md"), "x")

        with pytest.raises(AppError, match="outside the reports directory"):
            ModuleReportsService(db).read_content(module.id, "../../secret.txt")

    def test_symlink_leaf_refused(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        reports = os.path.join(ws_root, "poc", "reports")
        _write(os.path.join(reports, "real.md"), "real content")
        link = os.path.join(reports, "link.md")
        os.symlink(os.path.join(reports, "real.md"), link)

        with pytest.raises(AppError, match="symlink"):
            ModuleReportsService(db).read_content(module.id, "link.md")

    def test_symlink_escaping_workspace_refused(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        reports = os.path.join(ws_root, "poc", "reports")
        os.makedirs(reports, exist_ok=True)
        outside = tmp_path / "outside.txt"
        outside.write_text("escape target")
        os.symlink(str(outside), os.path.join(reports, "escape.md"))

        with pytest.raises(AppError):
            ModuleReportsService(db).read_content(module.id, "escape.md")

    def test_hardlink_into_reports_refused(self, db, monkeypatch, tmp_path):
        """#468 review: a hardlink passes the symlink/realpath guards (islink False,
        realpath stays inside), so the module's own container could hardlink a
        materialized secret into the report tree. Refuse files with nlink > 1."""
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        reports = os.path.join(ws_root, "poc", "reports")
        secret = tmp_path / "materialized_secret.txt"
        secret.write_text("super-secret")
        hardlink = os.path.join(reports, "leak.md")
        os.makedirs(reports, exist_ok=True)
        os.link(str(secret), hardlink)

        with pytest.raises(AppError, match="hard link"):
            ModuleReportsService(db).read_content(module.id, "leak.md")

    def test_size_cap_refused(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        big = "x" * (MAX_REPORT_FILE_BYTES + 1)
        _write(os.path.join(ws_root, "poc", "reports", "big.log"), big)

        with pytest.raises(AppError, match="too large"):
            ModuleReportsService(db).read_content(module.id, "big.log")

    def test_missing_file_is_not_found(self, db, monkeypatch, tmp_path):
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        os.makedirs(os.path.join(ws_root, "poc", "reports"), exist_ok=True)
        with pytest.raises(AppError):
            ModuleReportsService(db).read_content(module.id, "nope.md")


@pytest.mark.component
class TestReportsReadbackHardening:
    """Deferred #468-review nits on the report readback surface (issue #470)."""

    def test_rendered_dir_containing_dotdot_is_refused(self, db, monkeypatch, tmp_path):
        """`dir` renders from an input, so a manifest-clean value can still climb.

        realpath containment already held; the manifest-time validator rejects
        `..` on the DECLARED value but nothing rejected it on the RENDERED one.
        """
        module, ws_root = _module_with_reports(
            db, monkeypatch, tmp_path,
            dir_value="{{inputs.subdir}}/reports",
            variables={"subdir": "poc/.."},
        )
        # Put a file where the climb would land, so a pass here means real exposure.
        _write(os.path.join(ws_root, "reports", "2026-07-18T06-00-00Z", "r.md"), "# leaked")

        result = ModuleReportsService(db).list_runs(module.id)

        assert result["runs"] == [], (
            "a rendered reports dir containing '..' was accepted (issue #470)"
        )

    def test_plain_rendered_dir_still_works(self, db, monkeypatch, tmp_path):
        """Contrast: templated dirs without a climb must keep working."""
        module, ws_root = _module_with_reports(
            db, monkeypatch, tmp_path,
            dir_value="{{inputs.subdir}}/reports",
            variables={"subdir": "poc"},
        )
        _write(os.path.join(ws_root, "poc", "reports", "2026-07-18T06-00-00Z", "r.md"), "# ok")

        result = ModuleReportsService(db).list_runs(module.id)
        assert [r["stamp"] for r in result["runs"]] == ["2026-07-18T06-00-00Z"]

    def test_non_utf8_report_is_rejected_not_mojibake(self, db, monkeypatch, tmp_path):
        """A binary/latin-1 file was served as errors='replace' garbage.

        That reads as a corrupt report rather than as "this is not text".
        """
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        target = os.path.join(ws_root, "poc", "reports", "2026-07-18T06-00-00Z", "r.md")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(b"# Report\n\xff\xfe\x00binary\n")

        with pytest.raises(BadRequestError, match="(?i)utf-8"):
            ModuleReportsService(db).read_content(
                module.id, "2026-07-18T06-00-00Z/r.md"
            )

    def test_utf8_report_still_reads(self, db, monkeypatch, tmp_path):
        """Contrast: real UTF-8, including non-ASCII, must still be served."""
        module, ws_root = _module_with_reports(db, monkeypatch, tmp_path)
        _write(
            os.path.join(ws_root, "poc", "reports", "2026-07-18T06-00-00Z", "r.md"),
            "# Rapport — café ✅\n",
        )

        result = ModuleReportsService(db).read_content(
            module.id, "2026-07-18T06-00-00Z/r.md"
        )
        assert "café ✅" in result["content"]
