"""Read-only viewer for the report tree ctl tools write into a module workspace.

D-034 PR-2.5 (#458). Vendor CLIs (ocibnkctl, tmmlitectl) write their run/
scenario/bench deliverables into a persistent workspace under
``<poc>/reports/<UTC-stamp>/`` — the actual output of a run, otherwise trapped
on the workspace volume. A module's artifact manifest declares WHERE that tree
lives via a top-level ``reports`` block (``{"dir": "<workspace-relative path>"}``,
``{{inputs.*}}`` templated); this service lists the runs and reads one file,
staying strictly inside the module's own workspace.

Read-only: no writes, no side effects. Containment is enforced with a fully
resolved (realpath) path that must stay inside the reports dir, which must stay
inside the workspace root, and a refusal of any symlink leaf.
"""

from __future__ import annotations

import errno
import os

from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError
from models import ProjectModule
from services.execution.container_engine import _INPUT_TOKEN_RE
from services.workspace_manager import WorkspaceManager

# Refuse to serve a single report file larger than this — a report viewer is
# for human-readable deliverables, not for streaming multi-megabyte blobs.
MAX_REPORT_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB

_KIND_BY_EXT = {
    ".md": "md",
    ".json": "json",
    ".log": "log",
}


def _kind_for(path: str) -> str:
    return _KIND_BY_EXT.get(os.path.splitext(path)[1].lower(), "other")


def _render_input_tokens(value: str, variables: dict) -> str:
    """Expand ``{{inputs.foo}}`` tokens — same convention as step argv.

    The containment guard runs on the rendered result, so a hostile input
    value cannot smuggle in traversal.
    """

    def _sub(match) -> str:  # noqa: ANN001 — re.Match, local
        node: object = variables
        for part in match.group(1).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return ""
        return "" if node is None else str(node)

    return _INPUT_TOKEN_RE.sub(_sub, value)


class ModuleReportsService:
    """List and read the manifest-declared report tree for a container module."""

    def __init__(self, db: Session):
        self.db = db

    # ── internals ────────────────────────────────────────────────────────────

    def _get_module(self, module_id: int) -> ProjectModule:
        module = (
            self.db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
        )
        if module is None:
            raise NotFoundError("module", module_id)
        return module

    def _resolve_reports_dir(self, module: ProjectModule) -> str | None:
        """Return the absolute reports dir for a module, or None when undeclared.

        None means: no manifest, no ``reports`` block, or the declared dir does
        not resolve to a real directory inside the workspace. Callers treat
        None as an empty report set (listing) — never an error.
        """
        lib = module.library_module
        manifest = getattr(lib, "pack_manifest", None) if lib else None
        if not isinstance(manifest, dict):
            return None
        reports = manifest.get("reports")
        if not isinstance(reports, dict):
            return None
        raw_dir = reports.get("dir")
        if not isinstance(raw_dir, str) or not raw_dir.strip():
            return None

        variables = {**(module.variables or {}), **(module.variable_overrides or {})}
        rendered = _render_input_tokens(raw_dir, variables).strip()
        if not rendered or rendered.startswith("/") or rendered.startswith("~"):
            return None

        # Workspace root resolved exactly like the engine does (tasks/
        # container_tasks.py::_build_engine_and_ctx) — never reimplemented here.
        wm = WorkspaceManager(self.db)
        state_block = manifest.get("state") or {}
        state_scope = state_block.get("scope")
        ws_key = wm.artifact_workspace_key(module, state_scope)
        workspace_root = wm.artifact_workspace_path(module.project_id, ws_key)

        workspace_real = os.path.realpath(workspace_root)
        reports_real = os.path.realpath(os.path.join(workspace_real, rendered))
        if not (
            reports_real == workspace_real
            or reports_real.startswith(workspace_real + os.sep)
        ):
            # A rendered path that escapes the workspace: treat as no reports.
            return None
        return reports_real

    # ── list ─────────────────────────────────────────────────────────────────

    def list_runs(self, module_id: int) -> dict:
        """Newest-first list of report runs for a module.

        Each ``<stamp>/`` subdirectory of the reports dir is one run, listing
        its files recursively (relative path, size, kind). Files directly under
        the reports dir are grouped into a synthetic run with an empty stamp.
        No reports block / dir missing → empty list (not an error).
        """
        module = self._get_module(module_id)
        reports_dir = self._resolve_reports_dir(module)
        if reports_dir is None or not os.path.isdir(reports_dir):
            return {"module_id": module.id, "runs": []}

        runs: list[dict] = []
        top_level_files: list[dict] = []
        for entry in sorted(os.scandir(reports_dir), key=lambda e: e.name):
            if entry.is_dir(follow_symlinks=False):
                files = self._collect_files(entry.path, base=reports_dir)
                runs.append({"stamp": entry.name, "files": files})
            elif entry.is_file(follow_symlinks=False):
                top_level_files.append(self._file_info(entry.path, base=reports_dir))

        # Stamps are UTC timestamps (2026-07-18T06-13-59Z) — lexicographic sort
        # is chronological, so reverse gives newest-first.
        runs.sort(key=lambda r: r["stamp"], reverse=True)
        if top_level_files:
            runs.append({"stamp": "", "files": sorted(top_level_files, key=lambda f: f["path"])})

        return {"module_id": module.id, "runs": runs}

    def _collect_files(self, run_dir: str, *, base: str) -> list[dict]:
        collected: list[dict] = []
        for root, _dirs, names in os.walk(run_dir):
            for name in names:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue  # never advertise a symlink leaf
                collected.append(self._file_info(full, base=base))
        collected.sort(key=lambda f: f["path"])
        return collected

    @staticmethod
    def _file_info(full_path: str, *, base: str) -> dict:
        rel = os.path.relpath(full_path, base)
        try:
            size = os.path.getsize(full_path)
        except OSError:
            size = 0
        return {"path": rel, "kind": _kind_for(rel), "size": size}

    # ── content ──────────────────────────────────────────────────────────────

    def read_content(self, module_id: int, path: str) -> dict:
        """Read one report file's content, guarded against traversal.

        ``path`` is workspace-relative under the reports dir. The fully resolved
        (realpath) target must stay inside the reports dir, which must stay
        inside the workspace; a symlink leaf is refused outright. Files over
        MAX_REPORT_FILE_BYTES are refused.
        """
        module = self._get_module(module_id)
        reports_dir = self._resolve_reports_dir(module)
        if reports_dir is None:
            raise NotFoundError("report", path)

        candidate = (path or "").strip()
        if not candidate:
            raise BadRequestError("A report file 'path' is required")
        if os.path.isabs(candidate) or candidate.startswith("~") or "\x00" in candidate:
            raise BadRequestError("Report path must be relative to the reports directory")

        target = os.path.join(reports_dir, candidate)
        # Defence in depth: re-assert containment against the FULLY resolved
        # path (every component including the leaf). The workspace is writable
        # by the artifact's own container, so a prior run could plant a symlink
        # anywhere under it to redirect this read outside the workspace.
        target_real = os.path.realpath(target)
        if not (
            target_real == reports_dir or target_real.startswith(reports_dir + os.sep)
        ):
            raise BadRequestError("Report path resolves outside the reports directory")
        # A symlink leaf is never a legitimate report file — refuse even one
        # that points back inside the reports dir.
        if os.path.islink(target):
            raise BadRequestError("Report path is a symlink; refusing to read through it")
        if not os.path.isfile(target_real):
            raise NotFoundError("report", path)

        # Open with O_NOFOLLOW, then stat the file descriptor rather than the path.
        # This closes the TOCTOU window between the checks above and the read, and
        # lets us inspect the real inode we actually opened.
        try:
            fd = os.open(target_real, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise BadRequestError("Report path is a symlink; refusing to read through it")
            raise NotFoundError("report", path)
        with os.fdopen(fd, encoding="utf-8", errors="replace") as handle:
            st = os.fstat(handle.fileno())
            # A legitimate report file has exactly one link. nlink > 1 means the
            # artifact's own (already-privileged) container hardlinked another file
            # — a materialized secret_file (#451), kubeconfig, or terraform state —
            # into the report tree to surface it here. Refuse it (defence in depth).
            if st.st_nlink > 1:
                raise BadRequestError("Report file has multiple hard links; refusing to read")
            size = st.st_size
            if size > MAX_REPORT_FILE_BYTES:
                raise BadRequestError(
                    f"Report file is too large to view ({size} bytes; "
                    f"limit {MAX_REPORT_FILE_BYTES} bytes)"
                )
            content = handle.read()

        rel = os.path.relpath(target_real, reports_dir)
        return {"path": rel, "kind": _kind_for(rel), "size": size, "content": content}
