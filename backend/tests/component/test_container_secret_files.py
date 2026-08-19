"""#442: container artifacts materialize declared project secrets into the workspace.

Covers the manifest contract (validation), the write path (content, mode,
containment, missing-secret failure), and the requirement surfacing that makes
the project's Secrets tab list them.
"""

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from core.encryption import encrypt_value
from models import ProjectSecret
from services.execution.container_run_secrets import (
    MissingRequiredSecretError,
    materialize_secret_files,
)
from services.module_lock import ModuleLock
from services.module_metadata import InvalidMetadataSchemaError, ModuleMetadataValidator
from services.secrets_service import SecretsService
from tests.factories import ModuleLibraryFactory, ProjectModuleFactory, TaskFactory


def _artifact(secret_files, kind="container_image"):
    manifest = {
        "schema_version": 1,
        "name": "runner",
        "version": "1.0.0",
        "kind": kind,
        "lifecycle": {"supports_apply": True},
        "steps": {"apply": [{"name": "up", "args": ["tool", "up"]}]},
    }
    if kind == "container_image":
        manifest["container_image"] = {
            "registry_host": "ghcr.io",
            "repository": "org/runner",
            "digest": "sha256:" + "a" * 64,
        }
    else:
        manifest["helm_chart"] = {
            "registry_host": "ghcr.io",
            "repository": "org/chart",
            "digest": "sha256:" + "b" * 64,
        }
        manifest.pop("steps")
    if secret_files is not None:
        manifest["secret_files"] = secret_files
    return manifest


def _validate(manifest):
    return ModuleMetadataValidator().validate_artifact_manifest(
        manifest, registry_host_allowlist=["ghcr.io"]
    )


# ── manifest contract ─────────────────────────────────────────────────────


def test_secret_files_absent_is_valid():
    _validate(_artifact(None))


def test_secret_files_valid_shape_accepted():
    _validate(_artifact([{"secret_name": "far_tarball", "path": "poc/keys/f5-far.tgz"}]))


@pytest.mark.parametrize(
    "entry, expected",
    [
        ({"path": "keys/x"}, r"secret_files\[\].secret_name is required"),
        ({"secret_name": "s"}, r"secret_files\[\].path is required"),
        ({"secret_name": "s", "path": "/etc/passwd"}, "must be workspace-relative"),
        ({"secret_name": "s", "path": "../../etc/passwd"}, "escapes the workspace"),
        ({"secret_name": "s", "path": "~/x"}, "must be workspace-relative"),
    ],
)
def test_secret_files_invalid_entries_rejected(entry, expected):
    with pytest.raises(InvalidMetadataSchemaError, match=expected):
        _validate(_artifact([entry]))


def test_secret_files_duplicate_path_rejected():
    with pytest.raises(InvalidMetadataSchemaError, match="declared more than once"):
        _validate(_artifact([
            {"secret_name": "a", "path": "keys/x"},
            {"secret_name": "b", "path": "./keys/x"},
        ]))


def test_secret_files_rejected_for_declarative_kind():
    with pytest.raises(InvalidMetadataSchemaError, match="no run workspace"):
        _validate(_artifact([{"secret_name": "s", "path": "keys/x"}], kind="helm_chart"))


# ── write path ────────────────────────────────────────────────────────────


def _file_secret(db, project_id, name, content: bytes):
    secret = ProjectSecret(
        project_id=project_id,
        name=name,
        secret_type="file",
        filename=name,
        file_content_encrypted=encrypt_value(base64.b64encode(content).decode()),
        file_size=len(content),
    )
    db.add(secret)
    db.commit()
    return secret


def _value_secret(db, project_id, name, value: str):
    secret = ProjectSecret(
        project_id=project_id,
        name=name,
        secret_type="value",
        value_encrypted=encrypt_value(value),
    )
    db.add(secret)
    db.commit()
    return secret


def test_materialize_writes_file_secret_0600(db, make_project, tmp_path):
    project = make_project()
    db.commit()
    _file_secret(db, project.id, "far_tarball", b"\x1f\x8b binary far payload")

    written = materialize_secret_files(
        db, project.id,
        _artifact([{"secret_name": "far_tarball", "path": "poc/keys/f5-far.tgz"}]),
        str(tmp_path),
    )

    dest = tmp_path / "poc" / "keys" / "f5-far.tgz"
    assert written == ["poc/keys/f5-far.tgz"]
    assert dest.read_bytes() == b"\x1f\x8b binary far payload"  # decrypted, byte-exact
    assert oct(os.stat(dest).st_mode & 0o777) == "0o600"


def test_materialize_writes_value_secret_as_file(db, make_project, tmp_path):
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "eyJhbGciOi.payload.sig")

    materialize_secret_files(
        db, project.id,
        _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}]),
        str(tmp_path),
    )
    assert (tmp_path / "poc" / "keys" / ".jwt").read_text() == "eyJhbGciOi.payload.sig"


def test_materialize_missing_secret_names_it(db, make_project, tmp_path):
    project = make_project()
    db.commit()

    with pytest.raises(MissingRequiredSecretError, match="far_tarball"):
        materialize_secret_files(
            db, project.id,
            _artifact([{"secret_name": "far_tarball", "path": "poc/keys/f5-far.tgz"}]),
            str(tmp_path),
        )


def test_materialize_rejects_traversal_at_write_time(db, make_project, tmp_path):
    """Validation guards the manifest, but the write path re-checks: a stored
    manifest is external input and must not be trusted to have been validated
    by this version of the code."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "s", "x")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(InvalidMetadataSchemaError):
        materialize_secret_files(
            db, project.id,
            {"secret_files": [{"secret_name": "s", "path": "../escape"}]},
            str(workspace),
        )
    assert not (tmp_path / "escape").exists()


def test_materialize_refuses_leaf_symlink_pointing_outside_workspace(db, make_project, tmp_path):
    """A previous run's container (rw on the shared workspace volume) can plant
    the destination file as a symlink to a path outside the workspace; the
    write must be refused and the link target left untouched."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "tok")
    workspace = tmp_path / "ws"
    (workspace / "poc" / "keys").mkdir(parents=True)
    target = tmp_path / "other-project" / "terraform.tfstate"
    target.parent.mkdir()
    target.write_text("victim state")
    (workspace / "poc" / "keys" / ".jwt").symlink_to(target)

    with pytest.raises(ValueError, match="outside the workspace"):
        materialize_secret_files(
            db, project.id,
            _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}]),
            str(workspace),
        )
    assert target.read_text() == "victim state"


def test_materialize_refuses_leaf_symlink_even_inside_workspace(db, make_project, tmp_path):
    """A leaf symlink is never legitimate (only regular files are ever
    created), so even one that resolves inside the workspace is refused —
    the secret must not be written through it."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "tok")
    workspace = tmp_path / "ws"
    (workspace / "poc" / "keys").mkdir(parents=True)
    target = workspace / "elsewhere-in-ws"
    target.write_text("innocent")
    (workspace / "poc" / "keys" / ".jwt").symlink_to(target)

    with pytest.raises(ValueError, match="symlink"):
        materialize_secret_files(
            db, project.id,
            _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}]),
            str(workspace),
        )
    assert target.read_text() == "innocent"
    assert (workspace / "poc" / "keys" / ".jwt").is_symlink()  # not replaced


def test_materialize_refuses_parent_dir_symlink_escape(db, make_project, tmp_path):
    """Full-path realpath also catches a symlinked parent directory."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "tok")
    workspace = tmp_path / "ws"
    (workspace / "poc").mkdir(parents=True)
    outside = tmp_path / "outside-keys"
    outside.mkdir()
    (workspace / "poc" / "keys").symlink_to(outside)

    with pytest.raises(ValueError, match="outside the workspace"):
        materialize_secret_files(
            db, project.id,
            _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}]),
            str(workspace),
        )
    assert list(outside.iterdir()) == []


def test_materialize_overwrites_stale_content_and_mode(db, make_project, tmp_path):
    """Re-running must pick up a rotated secret, and tighten a loose mode."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "new-value")
    dest = tmp_path / "poc" / "keys" / ".jwt"
    dest.parent.mkdir(parents=True)
    dest.write_text("old-value")
    dest.chmod(0o644)

    materialize_secret_files(
        db, project.id,
        _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}]),
        str(tmp_path),
    )
    assert dest.read_text() == "new-value"
    assert oct(os.stat(dest).st_mode & 0o777) == "0o600"


def test_materialize_sets_mode_on_the_open_descriptor_not_the_path(
    db, make_project, tmp_path, monkeypatch
):
    """#94 N1: the 0600 must be applied via fchmod on the fd we opened with
    O_NOFOLLOW, never via os.chmod(path) after close.

    chmod by path re-resolves the name and follows symlinks, so a co-tenant
    swapping the destination for a symlink between our close and the chmod
    would get an arbitrary reachable file chmod'd. fchmod acts on the exact
    inode already open -- no re-resolution, nothing to race. Pin the
    mechanism: os.chmod must not be called on the destination at all.
    """
    import os as _os

    project = make_project()
    db.commit()
    _file_secret(db, project.id, "far_tarball", b"payload")

    path_chmods: list[str] = []
    real_chmod = _os.chmod

    def _spy_chmod(path, mode, *a, **kw):
        path_chmods.append(str(path))
        return real_chmod(path, mode, *a, **kw)

    fchmods: list[int] = []
    real_fchmod = _os.fchmod

    def _spy_fchmod(fd, mode):
        fchmods.append(mode)
        return real_fchmod(fd, mode)

    monkeypatch.setattr("services.execution.container_run_secrets.os.chmod", _spy_chmod)
    monkeypatch.setattr("services.execution.container_run_secrets.os.fchmod", _spy_fchmod)

    materialize_secret_files(
        db, project.id,
        _artifact([{"secret_name": "far_tarball", "path": "poc/keys/f5-far.tgz"}]),
        str(tmp_path),
    )

    dest = tmp_path / "poc" / "keys" / "f5-far.tgz"
    assert oct(_os.stat(dest).st_mode & 0o777) == "0o600"
    assert fchmods == [0o600], "mode must be applied via fchmod on the open descriptor"
    assert not any(p.endswith("f5-far.tgz") for p in path_chmods), (
        "os.chmod(path) was called on the destination -- that follows symlinks and "
        "reopens the race fchmod exists to close (#94 N1)"
    )


def test_materialize_noop_without_secret_files(db, make_project, tmp_path):
    project = make_project()
    db.commit()
    assert materialize_secret_files(db, project.id, _artifact(None), str(tmp_path)) == []
    assert list(tmp_path.iterdir()) == []


# ── requirement surfacing (the "Secrets (0)" fix) ─────────────────────────


def test_required_secrets_include_artifact_secret_files(db):
    required = SecretsService(db).get_required_secrets_for_module(
        "tools/ocibnkctl",
        {"required": [], "optional": []},
        pack_manifest={"secret_files": [
            {"secret_name": "far_tarball", "path": "poc/keys/f5-far.tgz"},
            {"secret_name": "jwt_token", "path": "poc/keys/.jwt"},
        ]},
    )
    assert [s["name"] for s in required] == ["far_tarball", "jwt_token"]
    assert all(s["required"] and s["type"] == "file" for s in required)
    assert "poc/keys/f5-far.tgz" in required[0]["description"]


def test_required_secrets_do_not_duplicate_input_and_secret_file(db):
    required = SecretsService(db).get_required_secrets_for_module(
        "tools/x",
        {"required": [{"name": "jwt_token", "sensitive": True}], "optional": []},
        pack_manifest={"secret_files": [{"secret_name": "jwt_token", "path": "keys/.jwt"}]},
    )
    assert len(required) == 1


def test_required_secrets_without_pack_manifest_unchanged(db):
    required = SecretsService(db).get_required_secrets_for_module(
        "tools/x", {"required": [{"name": "api_key", "sensitive": True}], "optional": []}
    )
    assert [s["name"] for s in required] == ["api_key"]


# ── destroy re-materializes (#451 review F2) ──────────────────────────────


def test_destroy_rematerializes_secret_files(db, tmp_path):
    """The tool contract (ocibnkctl) needs entitlement files present for
    destroy as well as apply. run_container_destroy goes through the shared
    _build_engine_and_ctx, so materialization must happen on the destroy path
    too — pin that end to end."""
    from tasks import container_tasks

    lib = ModuleLibraryFactory(
        db, category="container", module_source_kind="artifact", execution_engine="container"
    )
    module = ProjectModuleFactory(db, library_module=lib, status="deployed")
    task = TaskFactory(db, project=module.project, module=module, task_type="destroy")
    _value_secret(db, module.project_id, "jwt_token", "current-token")
    db.commit()

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Simulate the residue of a prior run being stale (rotated secret).
    stale = workspace / "poc" / "keys" / ".jwt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale-token")

    manifest = _artifact([{"secret_name": "jwt_token", "path": "poc/keys/.jwt"}])

    wm = MagicMock()
    wm.artifact_workspace_key.return_value = "m-1"
    wm.ensure_artifact_workspace.return_value = str(workspace)
    wm.artifact_workspace_host_path.return_value = str(workspace)
    wm.artifact_workspace_volume.return_value = None
    wm.artifact_workspace_subpath.return_value = "1/m-1"

    result = MagicMock(success=True, error_message=None, error_suggestion=None)
    engine = MagicMock()
    engine.destroy.return_value = result

    # SQLite returns naive datetimes on reload; keep now() naive to match
    # (same approach as test_opentofu_tasks).
    import datetime as _dt

    mock_dt = MagicMock(wraps=_dt.datetime)
    mock_dt.now.return_value = _dt.datetime.utcnow()

    db_ctx = MagicMock()
    db_ctx.return_value.__enter__ = MagicMock(return_value=db)
    db_ctx.return_value.__exit__ = MagicMock(return_value=False)
    lock_ctx = MagicMock()
    lock_ctx.return_value.__enter__ = MagicMock(
        return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0)
    )
    lock_ctx.return_value.__exit__ = MagicMock(return_value=False)

    with (
        patch.object(container_tasks, "datetime", mock_dt),
        patch.object(container_tasks, "get_db_context", db_ctx),
        patch.object(container_tasks, "module_lock", lock_ctx),
        patch.object(container_tasks, "_notify_task_started"),
        patch.object(container_tasks, "_artifact_manifest", return_value=manifest),
        patch.object(container_tasks, "_registry_host", return_value="ghcr.io"),
        patch.object(container_tasks, "_resolve_runner", return_value=MagicMock()),
        patch.object(container_tasks, "ContainerEngine", return_value=engine),
        patch.object(container_tasks, "set_locked_module_fields"),
        patch.object(container_tasks, "update_project_counts"),
        patch.object(container_tasks, "create_deployment_record"),
        patch.object(container_tasks, "_update_stack_status_if_needed"),
        patch.object(container_tasks, "_trigger_next_destroy_module"),
        patch.object(container_tasks, "_maybe_unregister_container_cluster"),
        patch("services.workspace_manager.WorkspaceManager", return_value=wm),
        patch(
            "services.execution.container_run_secrets.resolve_pull_authfile_for_module",
            return_value=None,
        ),
        patch("services.credentials_service.get_cloud_credentials_only", return_value={}),
    ):
        outcome = container_tasks.run_container_destroy(task.id, module.id)

    assert outcome["success"] is True
    assert engine.destroy.called
    # Destroy re-materialized the secret: fresh value, tight mode.
    assert stale.read_text() == "current-token"
    assert oct(os.stat(stale).st_mode & 0o777) == "0o600"


# ── path templating (#442) ────────────────────────────────────────────────


def test_materialize_renders_input_tokens_in_path(db, make_project, tmp_path):
    """A tool whose workspace layout depends on a form input (ocibnkctl:
    <poc_name>/keys/…) must be able to name its destination."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "tok")

    written = materialize_secret_files(
        db, project.id,
        _artifact([{"secret_name": "jwt_token", "path": "{{inputs.poc_name}}/keys/.jwt"}]),
        str(tmp_path),
        {"poc_name": "demo"},
    )
    assert written == ["demo/keys/.jwt"]
    assert (tmp_path / "demo" / "keys" / ".jwt").read_text() == "tok"


def test_materialize_blocks_traversal_injected_via_input_value(db, make_project, tmp_path):
    """Rendering happens before the containment check, so a hostile input value
    cannot smuggle traversal into an otherwise-safe manifest path."""
    project = make_project()
    db.commit()
    _value_secret(db, project.id, "jwt_token", "tok")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(InvalidMetadataSchemaError, match="escapes the workspace"):
        materialize_secret_files(
            db, project.id,
            _artifact([{"secret_name": "jwt_token", "path": "{{inputs.poc_name}}/keys/.jwt"}]),
            str(workspace),
            {"poc_name": "../../../tmp/pwned"},
        )
    assert not (tmp_path.parent / "tmp" / "pwned").exists()
