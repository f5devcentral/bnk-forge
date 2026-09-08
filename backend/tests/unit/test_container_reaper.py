"""Unit tests for the orphaned-step-container reaper.

The reaper is the backstop for a container whose worker died and whose step is
never retried. The dangerous case — an orphan racing its own retry against one
workspace — is closed synchronously in DockerRunner, not here; what these cover
is that the sweep uses the same live/dead signal execution_janitor already
applies to tasks, and that it refuses to guess.
"""

from unittest.mock import MagicMock, patch

import pytest

from tasks.container_reaper import reap_orphaned_step_containers


def _docker(ids, labels_by_id, removed):
    """Stand in for `docker ps`, `docker inspect` and `docker rm`."""
    def _run(argv, **kwargs):
        if "ps" in argv:
            return MagicMock(returncode=0, stdout="\n".join(ids), stderr="")
        if "inspect" in argv:
            cid = argv[-1]
            import json
            return MagicMock(returncode=0, stdout=json.dumps(labels_by_id.get(cid)), stderr="")
        if "rm" in argv:
            removed.append(argv[-1])
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return _run


@pytest.mark.unit
class TestContainerReaper:
    def test_reaps_a_container_whose_task_is_no_longer_live(self):
        removed: list[str] = []
        labels = {"c-dead": {"bnkforge.step": "1", "bnkforge.task": "task-gone"}}
        with patch("subprocess.run", side_effect=_docker(["c-dead"], labels, removed)), \
             patch("services.execution_janitor.get_live_task_ids", return_value={"task-live"}):
            result = reap_orphaned_step_containers()

        assert removed == ["c-dead"]
        assert result["reaped"] == 1

    def test_leaves_a_container_whose_task_is_still_running(self):
        """A running step must never be swept out from under itself."""
        removed: list[str] = []
        labels = {"c-live": {"bnkforge.step": "1", "bnkforge.task": "task-live"}}
        with patch("subprocess.run", side_effect=_docker(["c-live"], labels, removed)), \
             patch("services.execution_janitor.get_live_task_ids", return_value={"task-live"}):
            result = reap_orphaned_step_containers()

        assert removed == []
        assert result["reaped"] == 0

    def test_leaves_an_unowned_container_alone(self):
        """No owning task label is not evidence of an orphan.

        Such a container predates the labelling. Removing on a guess would kill
        a running deployment, which is worse than the leak it would recover.
        """
        removed: list[str] = []
        labels = {"c-old": {"bnkforge.step": "1"}}
        # A non-empty live set: this task's own id is always in a healthy one, so
        # set() would mean "lookup failed" and short-circuit before reaching the
        # unowned branch this test is about.
        with patch("subprocess.run", side_effect=_docker(["c-old"], labels, removed)), \
             patch("services.execution_janitor.get_live_task_ids",
                   return_value={"celery-self"}):
            result = reap_orphaned_step_containers()

        assert removed == []
        assert result["unowned"] == 1

    def test_a_failing_ps_reports_rather_than_raises(self):
        """A beat tick must not blow up because the endpoint is unreachable."""
        with patch("subprocess.run", return_value=MagicMock(
                returncode=1, stdout="", stderr="cannot connect")), \
             patch("services.execution_janitor.get_live_task_ids", return_value=set()):
            result = reap_orphaned_step_containers()

        assert result["reaped"] == 0
        assert "cannot connect" in result["error"]

    def test_a_degraded_live_lookup_reaps_nothing(self):
        """Wider blast radius than the runner's sweep: an empty set would make
        every labelled container on the host look dead, running ones included.

        This function is itself a Celery task, so its own id is in the set
        whenever the lookup works — empty means the lookup failed.
        """
        removed: list[str] = []
        labels = {"runningnow1": {"bnkforge.step": "1", "bnkforge.task": "celery-alive"}}
        with patch("subprocess.run", side_effect=_docker(["runningnow1"], labels, removed)), \
             patch("services.execution_janitor.get_live_task_ids", return_value=set()):
            result = reap_orphaned_step_containers()

        assert removed == [], "a degraded lookup must not reap a running container"
        assert result["reaped"] == 0
        assert result.get("skipped") == "live-task set unavailable"
