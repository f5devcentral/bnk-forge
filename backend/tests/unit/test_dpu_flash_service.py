"""Unit tests for DpuFlashService.

Integration-style: uses an in-memory SQLite DB + a fake BfbCacheService
and a monkeypatched paramiko ssh-connect + in-memory SFTP write to
exercise the full state machine without touching the network.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models  # noqa: F401 — registers all models on Base
from core.encryption import encrypt_value
from database import Base
from models.dpu import BfConfTemplate, BluefieldSoftwareImage, Dpu, ProjectDpuSettings
from models.project import Project
from models.ssh_credential import SSHCredential
from services.bfb_cache_service import BfbCacheService, _stage_file_into_cache
from services.dpu_flash_service import (
    STAGE_WAITING_REBOOT,
    DpuFlashService,
    mark_flash_enqueued,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def project(db: Session) -> Project:
    p = Project(name="p", description="d")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture
def dpu(db: Session, project: Project) -> Dpu:
    d = Dpu(
        project_id=project.id,
        bmc_ip="192.0.2.50",
        bmc_username_encrypted=encrypt_value("root"),
        bmc_password_encrypted=encrypt_value("hb9rwm4hb9fx"),
        serial_number="MT-TEST-1",
        external_vlan_id=10,
        external_vlan_ipv4="10.10.20.5/24",
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


@pytest.fixture
def bfb_image(db: Session) -> BluefieldSoftwareImage:
    img = BluefieldSoftwareImage(
        image_filename="test.bfb",
        base_url="https://example.com/",
        notes="",
    )
    db.add(img)
    db.commit()
    db.refresh(img)
    return img


@pytest.fixture
def bf_template(db: Session) -> BfConfTemplate:
    tpl = BfConfTemplate(
        name="minimal",
        description="simple template",
        content=(
            'UPDATE_DPU_OS="yes"\n'
            "ubuntu_PASSWORD='{{ DPU_UBUNTU_PASSWORD_HASH }}'\n"
            "MTU={{ dpu_mtu }}\n"
            "HOSTNAME={{ bfb_hostname }}\n"
            "bfb_modify_os() {\n"
            "  cat << EOF > /mnt/etc/netplan/50-mgmt.yaml\n"
            "network:\n"
            "  renderer: networkd\n"
            "  ethernets:\n"
            "    oob_net0:\n"
            "      dhcp4: true\n"
            "      dhcp6: true\n"
            "  version: 2\n"
            "EOF\n"
            "}\n"
        ),
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@pytest.fixture
def settings_row(
    db: Session, project: Project, bfb_image: BluefieldSoftwareImage,
    bf_template: BfConfTemplate,
) -> ProjectDpuSettings:
    s = ProjectDpuSettings(
        project_id=project.id,
        bluefield_image_id=bfb_image.id,
        bf_template_id=bf_template.id,
        high_speed_mtu=9000,
        dns_servers=["1.1.1.1"],
        default_oob_username="root",
        default_oob_password_encrypted=encrypt_value("0penBmc"),
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value("ubuntu-pw"),
        external_vlan_id=10, external_vlan_uplink="bond0",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


@pytest.fixture
def cache(tmp_path: Path) -> BfbCacheService:
    c = BfbCacheService(cache_dir=tmp_path / "bfb")
    _stage_file_into_cache(c, "test.bfb", b"FAKEBFB" * 1024)
    return c


# ── Fake SFTP / SSH plumbing ───────────────────────────────────────────────

class _FakeSftpFile:
    def __init__(self):
        self.written = bytearray()
        self.pipelined = False

    def set_pipelined(self, v: bool):
        self.pipelined = v

    def write(self, data: bytes):
        self.written.extend(data)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeSftp:
    def __init__(self):
        self.opened: dict[str, _FakeSftpFile] = {}

    def open(self, path: str, mode: str):  # noqa: ARG002
        f = _FakeSftpFile()
        self.opened[path] = f
        return f

    def close(self):
        pass


class _FakeExecResult:
    def __init__(self, rc: int = 0, stderr: bytes = b""):
        self._rc = rc
        self._stderr = stderr

    class _Chan:
        def __init__(self, rc):
            self._rc = rc

        def recv_exit_status(self):
            return self._rc

    @property
    def channel(self):
        return self._Chan(self._rc)


class _FakeStderr:
    def __init__(self, data: bytes = b""):
        self._data = data

    def read(self):
        return self._data


class _FakeSshClient:
    def __init__(self):
        self.sftp = _FakeSftp()
        self.exec_commands: list[str] = []

    def open_sftp(self):
        return self.sftp

    def exec_command(self, command, timeout=None):  # noqa: ARG002
        self.exec_commands.append(command)
        return None, _FakeExecResult(rc=0), _FakeStderr(b"")

    def close(self):
        pass


@pytest.fixture
def fake_ssh(monkeypatch):
    """Replace the ssh_connect_bmc_with_fallback helper with a no-op client,
    and zero-out the post-SW_RESET sleep so tests don't wait 8s."""
    client = _FakeSshClient()
    monkeypatch.setattr(
        "services.dpu_flash_service.ssh_connect_bmc_with_fallback",
        lambda *a, **kw: client,
    )
    monkeypatch.setattr("services.dpu_flash_service._RSHIM_RESET_SETTLE_S", 0)
    # The flash path calls `ensure_rshim_active_on_bmc` before touching
    # the rshim device; the fake SSH client doesn't implement the stdout
    # interface the helper expects, so stub it out — the helper has its
    # own dedicated tests.
    monkeypatch.setattr(
        "services.rshim_service.ensure_rshim_active_on_bmc",
        lambda client, *, device="misc": None,
    )
    monkeypatch.setattr(
        "services.rshim_service.ensure_rshim_active_on_host",
        lambda client, *, device="misc": None,
    )
    return client


# ── Tests ──────────────────────────────────────────────────────────────────

class TestFlashHappyPath:
    def test_end_to_end(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)

        assert result["status"] == "waiting_reboot"
        assert dpu.flash_status == "waiting_reboot"
        assert dpu.flash_stage_detail == STAGE_WAITING_REBOOT
        assert dpu.flash_error is None
        assert dpu.flash_completed_at is not None

        # The DPU must be put into rshim-boot mode BEFORE streaming;
        # otherwise a running DPU OS only consumes firmware updates from
        # the BFB and ignores our bf.cfg.
        assert any("SW_RESET 1" in c for c in fake_ssh.exec_commands), (
            f"SW_RESET sequence not issued; commands: {fake_ssh.exec_commands}"
        )
        assert any("BOOT_MODE 1" in c for c in fake_ssh.exec_commands)

        # Verify what was written to /dev/rshim0/boot: BFB bytes + bf.cfg.
        written = bytes(fake_ssh.sftp.opened["/dev/rshim0/boot"].written)
        # Bytes start with the cached BFB content.
        assert written.startswith(b"FAKEBFB")
        # And they end with a rendered bf.cfg — check a few substitutions.
        tail = written.decode("utf-8", errors="ignore")
        assert "HOSTNAME=MT-TEST-1" in tail
        assert "MTU=9000" in tail
        assert "ubuntu_PASSWORD='$6$" in tail


class TestPrerequisites:
    def test_missing_bfb_image_fails_cleanly(
        self, db, dpu, settings_row, bf_template, cache, fake_ssh,
    ):
        settings_row.bluefield_image_id = None
        db.commit()
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert dpu.flash_status == "failed"
        assert "BFB image" in (dpu.flash_error or "")

    def test_missing_bf_template_fails_cleanly(
        self, db, dpu, settings_row, bfb_image, cache, fake_ssh,
    ):
        settings_row.bf_template_id = None
        db.commit()
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "bf.conf template" in (dpu.flash_error or "")

    def test_missing_os_creds_fails_cleanly(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        settings_row.default_os_password_encrypted = None
        db.commit()
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "DPU OS" in (dpu.flash_error or "")


class TestIbGuardrail:
    """Phase 5: refuse to flash an IB-mode DPU regardless of caller path.

    The bf.conf templates render Ethernet-only netplan; flashing them to
    an IB-mode DPU produces unusable network config. The UI gates the
    action, but this is the backend safety net for MCP / curl / racy
    state when the badge hasn't refreshed yet.
    """

    def _set_link_mode_ib(self, db, dpu):
        dpu.last_discovery_payload = {
            "rshim_device_present": True,
            "mlxconfig_settings": {
                "LINK_TYPE_P1": "IB(1)",
                "LINK_TYPE_P2": "IB(1)",
            },
        }
        db.commit()

    def test_flash_refuses_ib_dpu(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        self._set_link_mode_ib(db, dpu)
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "InfiniBand" in (dpu.flash_error or "")
        assert "Convert to Ethernet" in (dpu.flash_error or "")
        # Must abort before the SFTP step — nothing reached the rshim.
        assert fake_ssh.sftp.opened == {}

    def test_flash_refuses_mixed_link_mode(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        # Mixed (P1 ETH, P2 IB) is just as broken as full IB for the
        # seeded templates — guard refuses it too.
        dpu.last_discovery_payload = {
            "rshim_device_present": True,
            "mlxconfig_settings": {
                "LINK_TYPE_P1": "ETH(2)",
                "LINK_TYPE_P2": "IB(1)",
            },
        }
        db.commit()
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "mixed" in (dpu.flash_error or "")
        assert fake_ssh.sftp.opened == {}

    def test_render_only_refuses_ib_dpu(
        self, db, dpu, settings_row, bf_template, cache,
    ):
        # Preview bf.conf must also refuse — the rendered output would
        # claim Ethernet uplinks the operator can't actually use.
        from services.dpu_flash_service import DpuFlashError
        self._set_link_mode_ib(db, dpu)
        svc = DpuFlashService(db, bfb_cache=cache)
        with pytest.raises(DpuFlashError, match="InfiniBand"):
            svc.render_only(project_id=dpu.project_id, dpu_id=dpu.id)


class TestLintGate:
    def test_broken_template_aborts_before_sftp(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        # Replace content with a broken heredoc (unterminated quote).
        bf_template.content = "ubuntu_PASSWORD='unterminated\n"
        db.commit()
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "bf.conf lint failed" in (dpu.flash_error or "")
        # Nothing was SFTP'd because we aborted before the upload step.
        assert fake_ssh.sftp.opened == {}


class TestAuthorizedKeysInjection:
    def test_public_key_rendered_into_bf_cfg(
        self, db, project, dpu, settings_row, bfb_image, bf_template, cache, fake_ssh,
    ):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
        cred = SSHCredential(
            name="my-key", host="dpu-os", username="ubuntu",
            auth_type="key", private_key_encrypted=encrypt_value(priv_pem),
        )
        db.add(cred)
        db.commit()
        settings_row.default_os_ssh_credential_id = cred.id
        # Template exposes authorized_keys for this test.
        bf_template.content += "AUTHORIZED_KEYS={{ hostvars[bfb_hostname].authorized_keys }}\n"
        db.commit()

        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        assert result["status"] == "waiting_reboot"
        written = fake_ssh.sftp.opened["/dev/rshim0/boot"].written.decode("utf-8", errors="ignore")
        assert "ssh-ed25519" in written


class TestMarkEnqueued:
    def test_sets_uploading_state(self, db, dpu, settings_row):
        mark_flash_enqueued(db, project_id=dpu.project_id, dpu_id=dpu.id, celery_task_id="abc")
        db.commit()
        db.refresh(dpu)
        assert dpu.flash_status == "uploading"
        assert dpu.flash_celery_task_id == "abc"
        assert dpu.flash_error is None
        assert dpu.flash_started_at is not None

    def test_refuses_when_already_flashing(self, db, dpu, settings_row):
        from core.errors import BadRequestError
        mark_flash_enqueued(db, project_id=dpu.project_id, dpu_id=dpu.id)
        db.commit()
        with pytest.raises(BadRequestError):
            mark_flash_enqueued(db, project_id=dpu.project_id, dpu_id=dpu.id)


class TestBmcSshFailure:
    def test_auth_failure_records_on_row(
        self, db, dpu, settings_row, bfb_image, bf_template, cache, monkeypatch,
    ):
        import paramiko

        def boom(*a, **kw):
            raise paramiko.ssh_exception.AuthenticationException("bad pw")

        monkeypatch.setattr(
            "services.dpu_flash_service.ssh_connect_bmc_with_fallback", boom,
        )
        svc = DpuFlashService(db, bfb_cache=cache)
        result = svc.flash(project_id=dpu.project_id, dpu_id=dpu.id)
        db.refresh(dpu)
        assert result["status"] == "failed"
        assert "auth" in (dpu.flash_error or "").lower()
