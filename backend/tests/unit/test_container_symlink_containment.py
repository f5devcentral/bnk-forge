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
