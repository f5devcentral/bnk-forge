"""Component test — BenchmarkAgent SSH-host registration columns (Slice 1).

Verifies that:
  - A Forge-managed project-scoped agent persists with all new columns.
  - The legacy global/built-in agent (project_id=NULL, managed=False) is unchanged.
  - Both coexist in the same table without constraint violations.
"""
import pytest

from models.benchmark import BenchmarkAgent
from tests.factories import ProjectFactory, UserFactory


@pytest.mark.component
class TestBenchmarkAgentHostColumns:
    """Verify the new SSH-host registration columns on BenchmarkAgent."""

    def test_managed_project_scoped_agent_persists(self, db):
        """A Forge-managed remote host agent with all Slice-1 fields round-trips through ORM."""
        user = UserFactory(db)
        project = ProjectFactory(db, user_id=user.id)

        agent = BenchmarkAgent(
            name="loadgen-remote-01",
            managed=True,
            project_id=project.id,
            host_ip="10.0.1.55",
            ssh_port=22,
            jumphost_chain=[{"ssh_credential_id": 99}],
            provision_status="unprovisioned",
        )
        db.add(agent)
        db.flush()

        fetched = db.query(BenchmarkAgent).filter_by(name="loadgen-remote-01").one()
        assert fetched.managed is True
        assert fetched.project_id == project.id
        assert fetched.host_ip == "10.0.1.55"
        assert fetched.ssh_port == 22
        assert fetched.jumphost_chain == [{"ssh_credential_id": 99}]
        assert fetched.provision_status == "unprovisioned"
        assert fetched.provision_message is None
        assert fetched.readiness is None
        assert fetched.ssh_credential_id is None

    def test_legacy_builtin_agent_unaffected(self, db):
        """A global built-in agent (no project, managed=False) still persists normally."""
        agent = BenchmarkAgent(
            name="forge-local-builtin",
            managed=False,
        )
        db.add(agent)
        db.flush()

        fetched = db.query(BenchmarkAgent).filter_by(name="forge-local-builtin").one()
        assert fetched.managed is False
        assert fetched.project_id is None
        assert fetched.host_ip is None
        assert fetched.ssh_credential_id is None
        # ORM Python-level default applies; 'unprovisioned' is fine for a non-managed row
        assert fetched.provision_status == "unprovisioned"

    def test_both_agents_coexist(self, db):
        """Managed and built-in agents can coexist in the table."""
        user = UserFactory(db)
        project = ProjectFactory(db, user_id=user.id)

        managed = BenchmarkAgent(name="managed-coexist-01", managed=True, project_id=project.id, host_ip="192.168.1.10")
        builtin = BenchmarkAgent(name="builtin-coexist-01", managed=False)
        db.add_all([managed, builtin])
        db.flush()

        managed_rows = db.query(BenchmarkAgent).filter_by(managed=True, project_id=project.id).all()
        builtin_rows = db.query(BenchmarkAgent).filter_by(managed=False, project_id=None).all()

        assert any(a.name == "managed-coexist-01" for a in managed_rows)
        assert any(a.name == "builtin-coexist-01" for a in builtin_rows)
