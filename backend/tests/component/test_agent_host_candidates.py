"""Component tests — GET /api/benchmarks/agent-host-candidates and import-aws-jumphost.

Tests aggregation of bare-metal, cluster bastion, ssh_credential, and AWS jumphost
candidates. AWS jumphost tested with a sample outputs dict using real field names
from infrastructure_access_service.
"""
import os
import tempfile

import pytest

from models.bare_metal import BareMetalHost
from models.kubernetes import KubernetesCluster
from models.ssh_credential import SSHCredential
from services.infrastructure_access_service import (
    INFRA_JUMPHOST_COMMAND_FIELD,
    INFRA_PRIVATE_KEY_AVAILABLE_FIELD,
    INFRA_PRIVATE_KEY_PATH_FIELD,
)
from tests.factories import (
    KubernetesClusterFactory,
    ProjectFactory,
    ProjectModuleFactory,
    _next_seq,
)


def _cred(db, host: str = "10.99.0.1") -> SSHCredential:
    n = _next_seq("cand_cred")
    c = SSHCredential(name=f"cand-tc-{n}", host=host, port=22, username="forge", auth_type="key")
    db.add(c)
    db.flush()
    return c


def _bare_metal_host(db, project_id: int, cred_id: int, host_ip: str = "10.1.2.3") -> BareMetalHost:
    n = _next_seq("cand_bm")
    h = BareMetalHost(
        project_id=project_id,
        name=f"bm-host-{n}",
        host_ip=host_ip,
        ssh_credential_id=cred_id,
        ssh_port=22,
    )
    db.add(h)
    db.flush()
    return h


@pytest.mark.component
class TestAgentHostCandidatesList:
    """GET /api/benchmarks/agent-host-candidates."""

    def test_bare_metal_candidates_returned(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db)
        _bare_metal_host(db, project.id, cred.id, host_ip="10.50.0.10")
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == project.id
        sources = [c["source"] for c in body["candidates"]]
        assert "bare_metal" in sources
        bm = next(c for c in body["candidates"] if c["source"] == "bare_metal")
        assert bm["host_ip"] == "10.50.0.10"
        assert bm["ssh_credential_id"] == cred.id

    def test_cluster_bastion_candidates_returned(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db, host="192.168.1.100")
        cluster = KubernetesClusterFactory(
            db, project=project, ssh_credential_id=cred.id, ssh_host_override="192.168.1.100"
        )
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        sources = [c["source"] for c in resp.json()["candidates"]]
        assert "cluster_bastion" in sources
        bastion = next(c for c in resp.json()["candidates"] if c["source"] == "cluster_bastion")
        assert bastion["host_ip"] == "192.168.1.100"
        assert bastion["ssh_credential_id"] == cred.id
        assert bastion["source_ref"] == str(cluster.id)

    def test_unassociated_ssh_credential_not_leaked(self, client, admin_headers, sample_user, db):
        """A global SSH credential not tied to the project must NOT appear.

        Regression for mwiget review: the candidate listing previously returned
        the full GLOBAL ssh_credential inventory (user@host + status) to any
        project viewer, leaking other projects' hosts/credentials.
        """
        project = ProjectFactory(db, user_id=sample_user.id)
        _cred(db, host="10.77.0.1")  # not referenced by this project's cluster/project row
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        ssh_creds = [c for c in resp.json()["candidates"] if c["source"] == "ssh_credential"]
        assert ssh_creds == []

    def test_project_associated_ssh_credential_returned(self, client, admin_headers, sample_user, db):
        """A credential referenced by the project's cluster IS surfaced as a candidate."""
        project = ProjectFactory(db, user_id=sample_user.id)
        cred = _cred(db, host="10.88.0.5")
        KubernetesClusterFactory(db, project=project, ssh_credential_id=cred.id)
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        ssh_creds = [c for c in resp.json()["candidates"] if c["source"] == "ssh_credential"]
        cred_ids = {c["ssh_credential_id"] for c in ssh_creds}
        assert cred.id in cred_ids

    def test_other_projects_ssh_credentials_excluded(self, client, admin_headers, sample_user, db):
        """Credentials associated with a DIFFERENT project must not leak in."""
        project_a = ProjectFactory(db, user_id=sample_user.id)
        project_b = ProjectFactory(db, user_id=sample_user.id)
        cred_a = _cred(db, host="10.10.0.1")
        cred_b = _cred(db, host="10.20.0.2")
        KubernetesClusterFactory(db, project=project_a, ssh_credential_id=cred_a.id)
        KubernetesClusterFactory(db, project=project_b, ssh_credential_id=cred_b.id)
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project_a.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        ssh_creds = [c for c in resp.json()["candidates"] if c["source"] == "ssh_credential"]
        cred_ids = {c["ssh_credential_id"] for c in ssh_creds}
        assert cred_a.id in cred_ids
        assert cred_b.id not in cred_ids

    def test_aws_jumphost_candidate_from_module_outputs(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        # Module with the real infra-access output field names
        outputs = {
            INFRA_PRIVATE_KEY_AVAILABLE_FIELD: True,
            INFRA_JUMPHOST_COMMAND_FIELD: "ssh -i /app/state/1/1/infrastructure/infrastructure-access.pem ec2-user@54.1.2.3",
            INFRA_PRIVATE_KEY_PATH_FIELD: "/app/state/1/1/infrastructure/infrastructure-access.pem",
        }
        ProjectModuleFactory(db, project=project, outputs=outputs)
        db.commit()

        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        aws = [c for c in resp.json()["candidates"] if c["source"] == "aws_jumphost"]
        assert len(aws) == 1
        assert aws[0]["host_ip"] == "54.1.2.3"
        assert aws[0]["needs_credential_import"] is True
        assert aws[0]["ssh_credential_id"] is None

    def test_viewer_can_call_candidates(self, client, viewer_headers, sample_viewer_user, db):
        project = ProjectFactory(db)
        db.commit()
        resp = client.get(
            f"/api/benchmarks/agent-host-candidates?project_id={project.id}",
            headers=viewer_headers,
        )
        # project belongs to no user — admin rule would 403, viewer would also 403
        # Just confirm the endpoint is reachable (not 404/405)
        assert resp.status_code in (200, 403)

    def test_missing_project_id_returns_422(self, client, admin_headers, sample_user):
        resp = client.get("/api/benchmarks/agent-host-candidates", headers=admin_headers)
        assert resp.status_code == 422

    def test_nonexistent_project_returns_404(self, client, admin_headers, sample_user, db):
        db.commit()
        resp = client.get(
            "/api/benchmarks/agent-host-candidates?project_id=999999",
            headers=admin_headers,
        )
        assert resp.status_code == 404


@pytest.mark.component
class TestImportAwsJumphost:
    """POST /api/benchmarks/agent-host-candidates/import-aws-jumphost."""

    def test_import_creates_ssh_credential(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)

        # Write a temporary PEM so the service can read it
        pem_content = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA0000000000000000000000000000000000000000000000000\n"
            "-----END RSA PRIVATE KEY-----\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as tmp:
            tmp.write(pem_content)
            pem_path = tmp.name

        try:
            outputs = {
                INFRA_PRIVATE_KEY_AVAILABLE_FIELD: True,
                INFRA_JUMPHOST_COMMAND_FIELD: f"ssh -i {pem_path} ec2-user@203.0.113.10",
                INFRA_PRIVATE_KEY_PATH_FIELD: pem_path,
            }
            module = ProjectModuleFactory(db, project=project, outputs=outputs)
            db.commit()

            resp = client.post(
                "/api/benchmarks/agent-host-candidates/import-aws-jumphost",
                json={"project_id": project.id, "module_id": module.id},
                headers=admin_headers,
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert "ssh_credential_id" in body
            cred_id = body["ssh_credential_id"]
            assert cred_id is not None

            # Second call is idempotent — same id returned
            resp2 = client.post(
                "/api/benchmarks/agent-host-candidates/import-aws-jumphost",
                json={"project_id": project.id, "module_id": module.id},
                headers=admin_headers,
            )
            assert resp2.status_code == 201
            assert resp2.json()["ssh_credential_id"] == cred_id
        finally:
            os.unlink(pem_path)

    def test_import_returns_400_when_key_unavailable(self, client, admin_headers, sample_user, db):
        project = ProjectFactory(db, user_id=sample_user.id)
        outputs = {INFRA_PRIVATE_KEY_AVAILABLE_FIELD: False}
        module = ProjectModuleFactory(db, project=project, outputs=outputs)
        db.commit()

        resp = client.post(
            "/api/benchmarks/agent-host-candidates/import-aws-jumphost",
            json={"project_id": project.id, "module_id": module.id},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "INFRA_KEY_UNAVAILABLE" in resp.text

    def test_viewer_cannot_import(self, client, viewer_headers, sample_viewer_user, db):
        project = ProjectFactory(db)
        module = ProjectModuleFactory(db, project=project)
        db.commit()
        resp = client.post(
            "/api/benchmarks/agent-host-candidates/import-aws-jumphost",
            json={"project_id": project.id, "module_id": module.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403
