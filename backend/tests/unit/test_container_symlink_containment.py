"""Workspace containment must survive a planted symlink (review finding).

The workspace is writable by the artifact's own container — that is its purpose
— so lexical path checks alone are not containment. A step can plant a symlink
at the expected name and the subsequent open() follows it as the WORKER uid.
The previous tests had six escape fixtures, all lexical, and no symlink.
"""

import json
import os
from unittest.mock import MagicMock

import pytest

from services.execution.container_engine import ContainerEngine


def _engine(tmp_path, **kw):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ContainerEngine(MagicMock(), workspace_host_path=str(ws),
                           workspace_local_path=str(ws), **kw), ws


@pytest.mark.unit
class TestOutputsFileSymlink:
    def test_symlinked_outputs_file_is_not_read(self, tmp_path):
        """The exact reported bypass: `ln -sf <secret> /state/outputs.json`."""
        engine, ws = _engine(tmp_path)
        secret = tmp_path / "encryption.key"
        secret.write_text(json.dumps({"master_key": "TOP-SECRET"}))
        os.symlink(secret, ws / "outputs.json")

        assert engine._read_outputs_file() == {}, (
            "read through a symlink out of the workspace — this is how "
            "/app/keys/encryption.key reaches module.outputs and the state viewer"
        )

    def test_symlinked_directory_component_is_not_traversed(self, tmp_path):
        """realpath containment, not just the final component."""
        engine, ws = _engine(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "outputs.json").write_text(json.dumps({"k": "v"}))
        os.symlink(outside, ws / "sub")

        assert engine._read_outputs_file("sub/outputs.json") == {}

    def test_a_real_file_still_reads(self, tmp_path):
        """Contrast: the normal path must keep working."""
        engine, ws = _engine(tmp_path)
        (ws / "outputs.json").write_text(json.dumps({"cluster": "c1"}))
        assert engine._read_outputs_file() == {"cluster": "c1"}

    def test_nested_relative_outputs_still_read(self, tmp_path):
        """Shipped artifacts declare nested relative paths."""
        engine, ws = _engine(tmp_path)
        nested = ws / ".roksbnkctl" / "forge"
        nested.mkdir(parents=True)
        (nested / "cluster-outputs.json").write_text(json.dumps({"a": "b"}))
        assert engine._read_outputs_file(".roksbnkctl/forge/cluster-outputs.json") == {"a": "b"}


@pytest.mark.unit
class TestStepMarkerSymlink:
    """The WRITE direction: a planted symlink was an arbitrary-file truncate."""

    def test_symlinked_marker_does_not_truncate_the_target(self, tmp_path):
        engine, ws = _engine(tmp_path)
        victim = tmp_path / "important.key"
        victim.write_text("ORIGINAL CONTENT")
        marker_name = os.path.basename(engine._step_marker_path("init"))
        os.symlink(victim, ws / marker_name)

        engine._write_step_marker("init")   # must not raise, must not follow

        assert victim.read_text() == "ORIGINAL CONTENT", (
            "the marker write followed a symlink and truncated an arbitrary file "
            "to 'done' as the worker uid"
        )

    def test_normal_marker_write_and_read_still_work(self, tmp_path):
        engine, _ = _engine(tmp_path)
        assert engine._step_marker_exists("init") is False
        engine._write_step_marker("init")
        assert engine._step_marker_exists("init") is True


@pytest.mark.unit
class TestParentDirectorySwap:
    """A symlinked PARENT must not be traversed either (review finding).

    O_NOFOLLOW on the final component alone is not containment: the path is
    resolved, re-resolved by isfile(), then re-resolved by open(), so swapping a
    parent between those steps escapes. Not contrived — the shipped artifact
    declares a NESTED outputs_file, and `state: {scope: deployment}` shares one
    workspace across blueprint modules dispatched concurrently, so a sibling
    module's step container can swap the directory mid-read.
    """

    def test_parent_swapped_AFTER_validation_is_refused(self, tmp_path, monkeypatch):
        """The actual TOCTOU: the swap happens between validation and open.

        A statically-planted symlink is caught by realpath in either design, so
        that alone does not distinguish a fixed implementation from a broken
        one. This models the real race — the directory is a genuine directory
        when the path is validated, and becomes a symlink before the open — by
        performing the swap from inside the first realpath() call.

        Fixed: the component walk opens `sub` with O_NOFOLLOW and gets ELOOP.
        Broken: validation saw a real directory, the later open() follows.
        """
        engine, ws = _engine(tmp_path)
        real_sub = ws / "sub"
        real_sub.mkdir()
        (real_sub / "out.json").write_text(json.dumps({"benign": True}))

        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "out.json").write_text(json.dumps({"master_key": "PWNED"}))

        # Hook os.open, not realpath: the swap has to land AFTER every
        # name-resolution the implementation does for validation and BEFORE the
        # first open, which is precisely the window a TOCTOU exploits.
        real_open = os.open
        swapped = {"done": False}

        def swapping_open(path, *a, **kw):
            if not swapped["done"]:
                swapped["done"] = True
                try:
                    real_sub.rename(tmp_path / "sub_aside")
                    os.symlink(outside, ws / "sub")
                except OSError:
                    pass
            return real_open(path, *a, **kw)

        monkeypatch.setattr(os, "open", swapping_open)

        data = engine._read_outputs_file("sub/out.json")

        assert data.get("master_key") != "PWNED", (
            "followed a parent directory swapped between validation and open — "
            "O_NOFOLLOW on the final component alone is not containment"
        )
        assert data == {}

    def test_symlinked_parent_is_refused(self, tmp_path):
        engine, ws = _engine(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "out.json").write_text(json.dumps({"master_key": "PWNED"}))
        # `sub` is itself a symlink — the swapped-parent shape.
        os.symlink(outside, ws / "sub")

        assert engine._read_outputs_file("sub/out.json") == {}, (
            "read through a symlinked PARENT — realpath validated one path and "
            "open() resolved another"
        )

    def test_deeply_nested_symlinked_parent_is_refused(self, tmp_path):
        engine, ws = _engine(tmp_path)
        real = ws / ".roksbnkctl"
        real.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "cluster-outputs.json").write_text(json.dumps({"k": "v"}))
        os.symlink(outside, real / "forge")   # mid-path component

        assert engine._read_outputs_file(".roksbnkctl/forge/cluster-outputs.json") == {}

    def test_the_real_nested_path_still_reads(self, tmp_path):
        """Contrast: the shipped artifact's nested layout must keep working."""
        engine, ws = _engine(tmp_path)
        nested = ws / ".roksbnkctl" / "forge"
        nested.mkdir(parents=True)
        (nested / "cluster-outputs.json").write_text(json.dumps({"cluster": "c1"}))

        assert engine._read_outputs_file(".roksbnkctl/forge/cluster-outputs.json") == {
            "cluster": "c1"
        }
