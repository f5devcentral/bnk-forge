"""
Tests for module-source clone correctness in services.execution.opentofu_runtime.

Covers three bugs:
  - #321: git_ref from ModuleSource is honoured when no inline ?ref; inline still wins.
  - #322: relative `source = "../sibling"` references co-fetch the sibling dir.
  - #323: forge_kubeconfig local is injected into co-fetched child module dirs that
          reference it.
"""

import os
import shutil
from unittest.mock import MagicMock, patch

from services.execution.opentofu_runtime import (
    OpenTofuRuntime,
    _resolve_git_ref,
    _scan_relative_module_sources,
)

# ---------------------------------------------------------------------------
# #321 — ref resolution precedence
# ---------------------------------------------------------------------------

class TestResolveGitRef:
    def test_inline_ref_wins_over_module_source(self):
        ms = MagicMock()
        ms.git_ref = "release/2.3"
        ms.branch = "develop"
        assert _resolve_git_ref("v1.2.3", ms) == "v1.2.3"

    def test_module_source_git_ref_used_when_no_inline(self):
        ms = MagicMock()
        ms.git_ref = "release/2.3"
        ms.branch = "develop"
        assert _resolve_git_ref(None, ms) == "release/2.3"

    def test_falls_back_to_branch_when_no_git_ref(self):
        ms = MagicMock()
        ms.git_ref = None
        ms.branch = "develop"
        assert _resolve_git_ref(None, ms) == "develop"

    def test_defaults_to_main_when_nothing_configured(self):
        ms = MagicMock()
        ms.git_ref = None
        ms.branch = None
        assert _resolve_git_ref(None, ms) == "main"

    def test_defaults_to_main_when_no_module_source(self):
        assert _resolve_git_ref(None, None) == "main"

    def test_empty_inline_ref_treated_as_absent(self):
        ms = MagicMock()
        ms.git_ref = "release/2.3"
        ms.branch = None
        assert _resolve_git_ref("", ms) == "release/2.3"


# ---------------------------------------------------------------------------
# #322 — relative source scanning
# ---------------------------------------------------------------------------

class TestScanRelativeModuleSources:
    def test_finds_parent_and_dot_relative_sources(self, tmp_path):
        mod = tmp_path / "mod"
        mod.mkdir()
        (mod / "main.tf").write_text(
            'module "sib" { source = "../sibling" }\n'
            'module "child" { source = "./child" }\n'
            'module "remote" { source = "git::https://example.com/x.git" }\n'
            'module "registry" { source = "terraform-aws-modules/vpc/aws" }\n',
            encoding="utf-8",
        )
        refs = _scan_relative_module_sources(str(mod))
        assert refs == {"../sibling", "./child"}

    def test_returns_empty_for_missing_dir(self, tmp_path):
        assert _scan_relative_module_sources(str(tmp_path / "nope")) == set()

    def test_handles_single_quotes(self, tmp_path):
        mod = tmp_path / "mod"
        mod.mkdir()
        (mod / "x.tf").write_text("module \"s\" { source = '../shared' }\n", encoding="utf-8")
        assert _scan_relative_module_sources(str(mod)) == {"../shared"}


# ---------------------------------------------------------------------------
# #322 / #323 — co-fetch siblings + forge_kubeconfig injection
# ---------------------------------------------------------------------------

def _runtime():
    rt = OpenTofuRuntime.__new__(OpenTofuRuntime)
    rt.db = MagicMock()
    return rt


class TestCofetchRelativeSiblings:
    def test_sibling_copied_in_tree_and_source_rewritten(self, tmp_path):
        # clone/<modules>/foo references ../bar (a peer dir in the clone). The
        # sibling must be co-fetched IN-TREE (under work_dir/.forge_siblings) and the
        # `source = "../bar"` reference rewritten to point at the in-tree copy, so the
        # sibling is execution-isolated and auto-cleaned with work_dir.
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        bar = modules / "bar"
        foo.mkdir(parents=True)
        bar.mkdir(parents=True)
        (foo / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        (bar / "main.tf").write_text('output "x" { value = 1 }\n', encoding="utf-8")

        # work_dir mirrors clone/modules/foo (the subpath was already copied here).
        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")

        project = MagicMock()
        project.cloud_provider = "on-prem"
        project.project_variables = None

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=project,
        )

        # Sibling lands IN-TREE under work_dir/.forge_siblings/bar — never out-of-tree.
        sibling = work_dir / ".forge_siblings" / "bar"
        assert sibling.is_dir()
        assert (sibling / "main.tf").read_text() == 'output "x" { value = 1 }\n'
        # No out-of-tree peer copy is created.
        assert not (tmp_path / "work" / "bar").exists()
        # The referencing source was rewritten to the in-tree location so `tofu init`
        # resolves it from within work_dir.
        rewritten = (work_dir / "main.tf").read_text()
        assert '"./.forge_siblings/bar"' in rewritten
        assert '"../bar"' not in rewritten

    def test_path_traversal_outside_clone_is_skipped(self, tmp_path):
        clone = tmp_path / "clone"
        foo = clone / "foo"
        foo.mkdir(parents=True)
        # ../../escape would resolve outside the clone root
        (foo / "main.tf").write_text('module "e" { source = "../../escape" }\n', encoding="utf-8")
        (tmp_path / "escape").mkdir()

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="foo",
            work_dir=str(work_dir),
            project=None,
        )
        # nothing copied outside
        assert not (tmp_path / "work" / "escape").exists()
        assert not (tmp_path / "escape" / "copied").exists()

    def test_forge_kubeconfig_injected_into_sibling_that_references_it(self, tmp_path):
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        helper = modules / "helper"
        foo.mkdir(parents=True)
        helper.mkdir(parents=True)
        (foo / "main.tf").write_text('module "h" { source = "../helper" }\n', encoding="utf-8")
        # sibling consumes forge_kubeconfig via local reference
        (helper / "main.tf").write_text(
            'resource "null_resource" "x" {\n'
            '  provisioner "local-exec" {\n'
            '    command = "echo ${try(local.forge_kubeconfig, var.forge_kubeconfig_content)}"\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)

        project = MagicMock()
        project.cloud_provider = ""  # generic/on-prem → file() local
        project.project_variables = None

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=project,
        )

        sibling = work_dir / ".forge_siblings" / "helper"
        locals_tf = sibling / "bnk_forge_locals.tf"
        assert locals_tf.exists(), "forge_kubeconfig locals not injected into sibling"
        assert "forge_kubeconfig" in locals_tf.read_text()

    def test_sibling_without_reference_gets_no_injection(self, tmp_path):
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        plain = modules / "plain"
        foo.mkdir(parents=True)
        plain.mkdir(parents=True)
        (foo / "main.tf").write_text('module "p" { source = "../plain" }\n', encoding="utf-8")
        (plain / "main.tf").write_text('output "y" { value = 2 }\n', encoding="utf-8")

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)

        project = MagicMock()
        project.cloud_provider = ""
        project.project_variables = None

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=project,
        )

        sibling = work_dir / ".forge_siblings" / "plain"
        assert sibling.is_dir()
        # no forge_kubeconfig reference → no locals file
        assert not (sibling / "bnk_forge_locals.tf").exists()

    def _build_module(self, root, name, bar_content):
        """Materialise clone/<name>/foo referencing ../bar, with a DISTINCT bar."""
        clone = root / name
        foo = clone / "foo"
        bar = clone / "bar"
        foo.mkdir(parents=True)
        bar.mkdir(parents=True)
        (foo / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        (bar / "main.tf").write_text(bar_content, encoding="utf-8")
        work_dir = root / f"work-{name}" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        return clone, work_dir

    def test_two_executions_referencing_same_sibling_do_not_clobber(self, tmp_path):
        """Regression: two modules/executions that each reference `../bar` with
        DIFFERENT sibling content must NOT clobber each other.

        The pre-fix code copied the sibling to work_dir's PARENT (a shared/out-of-tree
        path) with an `if os.path.exists: return` guard, so the second execution
        silently reused the FIRST execution's `bar`. With in-tree isolation each
        work_dir gets its OWN `.forge_siblings/bar`, so each build sees its own content.
        """
        project = MagicMock()
        project.cloud_provider = "on-prem"
        project.project_variables = None

        clone_a, work_a = self._build_module(tmp_path, "a", 'output "x" { value = "AAA" }\n')
        clone_b, work_b = self._build_module(tmp_path, "b", 'output "x" { value = "BBB" }\n')

        rt = _runtime()
        rt._cofetch_relative_siblings(
            clone_root=str(clone_a), subpath="foo", work_dir=str(work_a), project=project
        )
        rt._cofetch_relative_siblings(
            clone_root=str(clone_b), subpath="foo", work_dir=str(work_b), project=project
        )

        bar_a = (work_a / ".forge_siblings" / "bar" / "main.tf").read_text()
        bar_b = (work_b / ".forge_siblings" / "bar" / "main.tf").read_text()
        # Each execution sees its OWN sibling content — no cross-execution clobber.
        assert "AAA" in bar_a and "BBB" not in bar_a
        assert "BBB" in bar_b and "AAA" not in bar_b
        # And nothing was written out-of-tree (no shared peer copy).
        assert not (tmp_path / "bar").exists()

    def test_cofetched_siblings_removed_when_work_dir_cleaned(self, tmp_path):
        """Co-fetched siblings live under work_dir, so failure/teardown cleanup that
        removes work_dir also removes them — no out-of-tree leak."""
        clone = tmp_path / "clone"
        foo = clone / "foo"
        bar = clone / "bar"
        foo.mkdir(parents=True)
        bar.mkdir(parents=True)
        (foo / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        (bar / "main.tf").write_text('output "x" { value = 1 }\n', encoding="utf-8")
        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")

        project = MagicMock()
        project.cloud_provider = "on-prem"
        project.project_variables = None

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone), subpath="foo", work_dir=str(work_dir), project=project
        )
        assert (work_dir / ".forge_siblings" / "bar").is_dir()

        # Simulate failure/teardown cleanup of the workspace.
        shutil.rmtree(str(work_dir))
        # Everything (including the co-fetched sibling) is gone; nothing leaked out.
        assert not work_dir.exists()
        assert not (tmp_path / "work" / "bar").exists()
        assert not (tmp_path / "bar").exists()

    def test_chained_sibling_references_resolved_in_tree(self, tmp_path):
        """A co-fetched sibling that itself references `../baz` is also co-fetched and
        rewritten consistently relative to its new in-tree location."""
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        bar = modules / "bar"
        baz = modules / "baz"
        for d in (foo, bar, baz):
            d.mkdir(parents=True)
        (foo / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        (bar / "main.tf").write_text('module "baz" { source = "../baz" }\n', encoding="utf-8")
        (baz / "main.tf").write_text('output "z" { value = 1 }\n', encoding="utf-8")

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")

        project = MagicMock()
        project.cloud_provider = "on-prem"
        project.project_variables = None

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone), subpath="modules/foo", work_dir=str(work_dir), project=project
        )

        siblings = work_dir / ".forge_siblings"
        assert (siblings / "bar" / "main.tf").exists()
        assert (siblings / "baz" / "main.tf").exists()
        # foo's reference rewritten to the in-tree bar.
        assert '"./.forge_siblings/bar"' in (work_dir / "main.tf").read_text()
        # bar (now at .forge_siblings/bar) references baz at .forge_siblings/baz → "../baz".
        assert '"../baz"' in (siblings / "bar" / "main.tf").read_text()


# ---------------------------------------------------------------------------
# #321 — end-to-end: ref passed to git clone via _clone_module_source
# ---------------------------------------------------------------------------

class TestCloneModuleSourceRef:
    def _fake_clone(self, ref_holder):
        """Return a subprocess.run side-effect that materialises a fake clone
        and records the --branch arg passed to `git clone`."""
        def _side_effect(cmd, **kwargs):
            # cmd: ["git", "clone", "--depth=1", "--branch", <ref>, "--", source, dest]
            ref_holder["ref"] = cmd[cmd.index("--branch") + 1]
            dest = cmd[-1]
            # materialise a minimal repo with the module subpath
            mod = os.path.join(dest, "modules", "foo")
            os.makedirs(mod, exist_ok=True)
            with open(os.path.join(mod, "main.tf"), "w") as f:
                f.write('output "ok" { value = 1 }\n')
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result
        return _side_effect

    def _run_clone(self, git_source, module_source):
        import tempfile
        ref_holder = {}
        work_dir = tempfile.mkdtemp(prefix="test-work-")
        auth_ctx = MagicMock()
        auth_ctx.secret = None
        try:
            with patch("subprocess.run", side_effect=self._fake_clone(ref_holder)), \
                 patch(
                     "services.git_auth_service.GitAuthService.resolve_for_module_source",
                     return_value=auth_ctx,
                 ), \
                 patch(
                     "services.git_auth_service.GitAuthService.resolve_for_module_library_token_setting",
                     return_value=auth_ctx,
                 ), \
                 patch(
                     "services.git_auth_service.GitAuthService.build_git_environment",
                     return_value=({}, lambda: None),
                 ), \
                 patch(
                     "services.git_auth_service.GitAuthService.strip_url_credentials",
                     side_effect=lambda u: u,
                 ):
                _runtime()._clone_module_source(git_source, work_dir, module_source)
            return ref_holder.get("ref")
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def test_uses_module_source_git_ref_when_no_inline_ref(self):
        ms = MagicMock()
        ms.git_ref = "release/2.3"
        ms.branch = None
        ref = self._run_clone(
            "git::https://github.com/org/repo.git//modules/foo", ms
        )
        assert ref == "release/2.3"

    def test_inline_ref_overrides_module_source(self):
        ms = MagicMock()
        ms.git_ref = "release/2.3"
        ms.branch = None
        ref = self._run_clone(
            "git::https://github.com/org/repo.git//modules/foo?ref=v9.9.9", ms
        )
        assert ref == "v9.9.9"

    def test_defaults_to_main_without_ref_or_module_source(self):
        ref = self._run_clone(
            "git::https://github.com/org/repo.git//modules/foo", None
        )
        assert ref == "main"


# ---------------------------------------------------------------------------
# Traversal guard regression tests (bonnyr-f5 review comment)
#
# The original guard allowed target == clone_root (e.g. source = "../.." from
# clone/modules/foo resolves to clone/) and target is an ancestor of the
# current subpath (e.g. source = ".." resolves to clone/modules/).  Both
# would cause shutil.copytree to copy large subtrees into .forge_siblings.
# ---------------------------------------------------------------------------

class TestCofetchTraversalGuard:
    """Regression tests for the clone-root and ancestor-of-subpath guard."""

    def test_clone_root_source_is_skipped(self, tmp_path):
        """source = '../..' from clone/modules/foo resolves to clone root — must be skipped.

        Before the fix, commonpath([clone, clone]) == clone passed the guard and
        the entire repository was copied into .forge_siblings.
        """
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        foo.mkdir(parents=True)
        # source = "../.." resolves to clone/ — the repo root.
        (foo / "main.tf").write_text(
            'module "root" { source = "../.." }\n', encoding="utf-8"
        )

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text(
            'module "root" { source = "../.." }\n', encoding="utf-8"
        )

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=None,
        )

        # Nothing should be copied into .forge_siblings — the source resolves to
        # the clone root itself and must be skipped.
        siblings_root = work_dir / ".forge_siblings"
        assert not siblings_root.exists(), (
            "clone root was copied into .forge_siblings — traversal guard failed"
        )
        # The referencing source must NOT be rewritten (no valid in-tree copy exists).
        content = (work_dir / "main.tf").read_text()
        assert '"../.."' in content, "source was rewritten even though target was skipped"

    def test_ancestor_of_subpath_source_is_skipped(self, tmp_path):
        """source = '..' from clone/modules/foo resolves to clone/modules/ — must be skipped.

        clone/modules/ is an ancestor of the current subpath (clone/modules/foo),
        copying it would pull in every sibling module directory.
        """
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        bar = modules / "bar"   # another module at the same level
        foo.mkdir(parents=True)
        bar.mkdir(parents=True)
        (foo / "main.tf").write_text(
            'module "parent" { source = ".." }\n', encoding="utf-8"
        )
        (bar / "main.tf").write_text('output "y" { value = 2 }\n', encoding="utf-8")

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text(
            'module "parent" { source = ".." }\n', encoding="utf-8"
        )

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=None,
        )

        # Nothing should be copied — ".." resolves to clone/modules/ which is an
        # ancestor of the module subpath (clone/modules/foo).
        siblings_root = work_dir / ".forge_siblings"
        assert not siblings_root.exists(), (
            "ancestor dir was copied into .forge_siblings — ancestor guard failed"
        )

    def test_legitimate_sibling_still_works_after_guard(self, tmp_path):
        """A genuine peer sibling (../bar) is still co-fetched correctly after the guard additions."""
        clone = tmp_path / "clone"
        modules = clone / "modules"
        foo = modules / "foo"
        bar = modules / "bar"
        foo.mkdir(parents=True)
        bar.mkdir(parents=True)
        (foo / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")
        (bar / "main.tf").write_text('output "z" { value = 99 }\n', encoding="utf-8")

        work_dir = tmp_path / "work" / "foo"
        work_dir.mkdir(parents=True)
        (work_dir / "main.tf").write_text('module "bar" { source = "../bar" }\n', encoding="utf-8")

        _runtime()._cofetch_relative_siblings(
            clone_root=str(clone),
            subpath="modules/foo",
            work_dir=str(work_dir),
            project=None,
        )

        sibling = work_dir / ".forge_siblings" / "bar"
        assert sibling.is_dir(), "legitimate sibling was not co-fetched"
        assert "99" in (sibling / "main.tf").read_text()
        assert '"./.forge_siblings/bar"' in (work_dir / "main.tf").read_text()
