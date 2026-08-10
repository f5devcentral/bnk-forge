"""Component tests for WO-4 service-level audit fixes.

Covers:
- FEAT-0109 / ERR-0005 — manual credential refresh is actually forced.
- FEAT-0184 / ERR-0021 — reveal_bmc_password falls back to the project default.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from core.encryption import encrypt_value
from models.dpu import Dpu, ProjectDpuSettings
from services.credential_refresh_service import CredentialRefreshService
from services.dpu_service import DpuService


class TestForceRefresh:
    def _make_aws_project(self, db, make_project):
        project = make_project(name="wo4-refresh-proj")
        project.cloud_provider = "aws"
        # Credentials valid for another hour — well past the 15-min refresh gate.
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        project.cloud_credentials_encrypted = encrypt_value(
            json.dumps({"auth_method": "sso", "expiration": future})
        )
        db.commit()
        return project

    def test_unforced_refresh_skips_when_not_expiring(self, db, make_project):
        project = self._make_aws_project(db, make_project)
        svc = CredentialRefreshService()
        with patch.object(svc, "refresh_aws_credentials", return_value=True) as mock_refresh:
            result = svc.check_and_refresh_project(project, db, force=False)
        assert result is False
        mock_refresh.assert_not_called()

    def test_forced_refresh_runs_even_when_not_expiring(self, db, make_project):
        project = self._make_aws_project(db, make_project)
        svc = CredentialRefreshService()
        with patch.object(svc, "refresh_aws_credentials", return_value=True) as mock_refresh:
            result = svc.check_and_refresh_project(project, db, force=True)
        assert result is True
        mock_refresh.assert_called_once()


class TestRevealBmcPasswordFallback:
    def _make_dpu(self, db, make_project, **dpu_kwargs):
        project = make_project(name=f"wo4-dpu-proj-{id(dpu_kwargs)}")
        dpu = Dpu(project_id=project.id, name="dpu-1", access_mode="bmc", **dpu_kwargs)
        db.add(dpu)
        db.commit()
        return project, dpu

    def test_falls_back_to_project_default(self, db, make_project):
        project, dpu = self._make_dpu(db, make_project)  # no per-DPU password
        db.add(
            ProjectDpuSettings(
                project_id=project.id,
                default_oob_username="root",
                default_oob_password_encrypted=encrypt_value("project-default-pw"),
            )
        )
        db.commit()

        pw = DpuService(db).reveal_bmc_password(project.id, dpu.id)
        assert pw == "project-default-pw"

    def test_per_dpu_password_takes_precedence(self, db, make_project):
        project, dpu = self._make_dpu(
            db, make_project, bmc_password_encrypted=encrypt_value("per-dpu-pw")
        )
        db.add(
            ProjectDpuSettings(
                project_id=project.id,
                default_oob_password_encrypted=encrypt_value("project-default-pw"),
            )
        )
        db.commit()

        pw = DpuService(db).reveal_bmc_password(project.id, dpu.id)
        assert pw == "per-dpu-pw"

    def test_404_when_no_password_anywhere(self, db, make_project):
        from core.errors import NotFoundError

        project, dpu = self._make_dpu(db, make_project)
        with pytest.raises(NotFoundError):
            DpuService(db).reveal_bmc_password(project.id, dpu.id)
