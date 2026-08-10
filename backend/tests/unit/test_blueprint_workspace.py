"""Unit tests for blueprint workspace/cache helpers in WorkspaceManager."""

from types import SimpleNamespace

from services.workspace_manager import WorkspaceManager


def _make_module(*, version: str = "1.0.0", stack_instance_id: int | None = 21, source_kind: str = "git_catalog"):
    lib = SimpleNamespace(
        id=77,
        version=version,
        git_source="git::https://github.com/example/modules.git//bnk/vlans?ref=main",
        module_source_kind=source_kind,
        execution_engine="opentofu",
    )
    return SimpleNamespace(
        id=42,
        project_id=9,
        stack_instance_id=stack_instance_id,
        workspace_path=None,
        library_module=lib,
    )


def test_compute_cache_key_deterministic(tmp_path):
    wm = WorkspaceManager(db=SimpleNamespace())
    wm.BASE_PATH = str(tmp_path / "workspaces")
    module = _make_module()

    workspace = wm.get_workspace_path(module)
    module.workspace_path = workspace
    tmp_path.joinpath("workspaces", str(module.project_id), str(module.id)).mkdir(parents=True)
    with open(f"{workspace}/main.tf", "w") as f:
        f.write('terraform { required_version = ">= 1.6" }')

    key1 = wm.compute_cache_key(module)
    key2 = wm.compute_cache_key(module)
    assert key1 == key2


def test_compute_cache_key_varies_by_version(tmp_path):
    wm = WorkspaceManager(db=SimpleNamespace())
    wm.BASE_PATH = str(tmp_path / "workspaces")

    m1 = _make_module(version="1.0.0")
    m2 = _make_module(version="1.1.0")

    for mod in (m1, m2):
        workspace = wm.get_workspace_path(mod)
        mod.workspace_path = workspace
        tmp_path.joinpath("workspaces", str(mod.project_id), str(mod.id)).mkdir(parents=True, exist_ok=True)
        with open(f"{workspace}/main.tf", "w") as f:
            f.write('terraform { required_version = ">= 1.6" }')

    assert wm.compute_cache_key(m1) != wm.compute_cache_key(m2)


def test_is_blueprint_eligible_true():
    wm = WorkspaceManager(db=SimpleNamespace())
    assert wm.is_blueprint_eligible(_make_module()) is True


def test_is_blueprint_eligible_false_no_stack():
    wm = WorkspaceManager(db=SimpleNamespace())
    assert wm.is_blueprint_eligible(_make_module(stack_instance_id=None)) is False


def test_is_blueprint_eligible_false_not_git_catalog():
    wm = WorkspaceManager(db=SimpleNamespace())
    assert wm.is_blueprint_eligible(_make_module(source_kind="local")) is False


def test_blueprint_workspace_path_format():
    wm = WorkspaceManager(db=SimpleNamespace())
    assert wm.get_blueprint_workspace_path(9, 21) == "/app/workspaces/9/blueprint-21"
