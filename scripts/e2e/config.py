"""YAML config schema for the e2e test harness.

Single Pydantic model parses + validates the input file. Anything the
script needs at runtime lives here; nothing is read from process env
except the bnk-forge admin password override (`BNK_FORGE_PASSWORD`).
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


class JumphostConfig(BaseModel):
    """Optional jumphost. If provided, the script registers it as an
    SSH credential on bnk-forge before creating the project, then
    pins the project to use it."""

    host: str
    user: str
    ssh_key_path: str  # path on the box running this script
    port: int = 22

    @field_validator("ssh_key_path")
    @classmethod
    def _expand_user(cls, v: str) -> str:
        return str(Path(v).expanduser())


class TimeoutsConfig(BaseModel):
    # `make install` (docker compose down + volume rm + image rebuild
    # + bring-up + health waits) takes 4-8 minutes on first run on
    # this hardware; cap at 12 to leave room for slow hosts.
    local_deploy: int = 720
    discovery: int = 120
    flash_per_dpu: int = 1500
    matrix: int = 120
    poll_interval: int = 3
    total: int = 3600
    # Cloud-blueprint timeouts (seconds).  EKS+BNK deploys typically take
    # 20–40 min; destroy is faster (~10 min).  BNK readiness + license
    # each add a few minutes of polling after deploy completes.
    cloud_deploy: int = 3600
    cloud_bnk_ready: int = 600
    cloud_license: int = 300
    cloud_destroy: int = 1800
    # Traffic probe step: all configured patterns must complete within this budget.
    cloud_traffic: int = 600


class TrafficConfig(BaseModel):
    """SSH+EICE traffic probe configuration for `step_cloud_traffic`.

    When `enabled` is False (default) or any required field is missing,
    `step_cloud_traffic` skips cleanly (green).

    Fields:
        enabled              Master switch; defaults False so existing configs
                             skip the step without any changes.
        patterns             Traffic patterns to run; defaults to the basic
                             routing check only.
        jumphost_instance_id EC2 instance id for the EICE jumphost.  Read from
                             JUMPHOST_INSTANCE_ID env var when not set in config.
        source_interface_ip  BNK_EXT ENI IP; curl --interface uses this so
                             traffic hits the real data path.  Read from
                             JUMPHOST_BNK_EXT_ENI_IP env when not set.
        region               AWS region for EICE; defaults to cloud.region.
        vip                  Base VIP for probe commands (e.g. "10.0.1.100").
                             Patterns that pin an octet derive their VIP from this.
        iterations           Curl iterations per pattern; patterns enforce their
                             own minimum (e.g. split requires ≥10).
        timeout_seconds      Per-curl --max-time value (seconds).
        instance_os_user     SSH user on the jumphost; defaults to "ec2-user".
    """

    enabled: bool = False
    patterns: list[str] = Field(default_factory=lambda: ["http-routing-e2e"])
    jumphost_instance_id: str | None = None
    source_interface_ip: str | None = None
    region: str | None = None
    vip: str | None = None
    iterations: int = 5
    timeout_seconds: int = 10
    instance_os_user: str = "ec2-user"

    @model_validator(mode="after")
    def _apply_env_jumphost(self) -> TrafficConfig:
        env_iid = os.environ.get("JUMPHOST_INSTANCE_ID")
        if env_iid and not self.jumphost_instance_id:
            self.jumphost_instance_id = env_iid
        env_sip = os.environ.get("JUMPHOST_BNK_EXT_ENI_IP")
        if env_sip and not self.source_interface_ip:
            self.source_interface_ip = env_sip
        return self


class CloudBlueprintConfig(BaseModel):
    """Optional cloud-blueprint section — drives the `cloud` phase.

    Without this section (or without cloud credentials in the environment)
    all cloud steps report `skipped` (green).

    Fields:
        release_id       Explicit blueprint release id (skip name/version search).
        release_name     Substring match against blueprint_name (ignored when
                         release_id is set).
        release_version  Exact blueprint_version match (ignored when release_id set).
        cloud_provider   'aws' | 'gcp' | 'azure'  (gcp/azure skip until S3).
        region           Cloud region for project creation.
        credential_template_id  forge credential-template id to bind the project to.
        variables        Blueprint-level variables (maps input name → value).
        cluster_id       forge cluster id for BNK readiness + license gates.
                         None ⇒ skip those two gates.
        license_jwt      Raw JWT string for license activation.  Read from
                         CLOUD_LICENSE_JWT env var when not set in config.
        teardown_always  When True (default), teardown runs in the finally block
                         even after failure.
        teardown_on_success  When True (default), the normal `cloud_destroy_all`
                             + `cloud_delete_project` plan steps run after a
                             successful deploy.
        traffic          Optional SSH+EICE traffic probe config.  Absent or
                         `enabled: false` → `step_cloud_traffic` skips cleanly.
    """

    release_id: int | None = None
    release_name: str | None = None
    release_version: str | None = None
    cloud_provider: str | None = "aws"
    region: str | None = None
    credential_template_id: int | None = None
    variables: dict[str, object] = Field(default_factory=dict)
    cluster_id: int | None = None
    license_jwt: str | None = None
    teardown_always: bool = True
    teardown_on_success: bool = True
    traffic: TrafficConfig | None = None

    @model_validator(mode="after")
    def _apply_env_license_jwt(self) -> CloudBlueprintConfig:
        env_jwt = os.environ.get("CLOUD_LICENSE_JWT")
        if env_jwt and not self.license_jwt:
            self.license_jwt = env_jwt
        return self


class E2EConfig(BaseModel):
    """Top-level config. Loaded from YAML once at startup."""

    # Hardware reach (DPU-phase fields — required for phase1/phase2 but
    # optional when running the `cloud` phase only; default to empty so
    # a cloud-only config file can omit them without validation errors).
    worker_node_ips: list[str] = Field(default_factory=list)
    bmc_ips: list[str] = Field(default_factory=list)
    worker_ssh_user: str = ""
    worker_ssh_key_path: str = ""

    # DPU credentials the project will configure as defaults.
    bmc_password: str = ""
    dpu_os_password: str = ""

    # bf.conf template choice. The matching name is resolved by
    # substring match against the project's bf_conf_templates rows
    # (`uplink_mode == 'lag'` or `'p0p1'`).
    bf_template: str = Field(default="lag", pattern="^(lag|no-lag)$")

    # BFB image filename. Resolved by exact match against the
    # bluefield_software_images catalog. Must already be uploaded.
    bfb_image: str = ""

    # Optional jumphost. None ⇒ skip step 1.
    jumphost: JumphostConfig | None = None

    # bnk-forge target.
    bnk_forge_url: str = "https://localhost"
    bnk_forge_admin_user: str = "admin"
    # Don't put real passwords in committed YAML — leave this default
    # and override via env (BNK_FORGE_PASSWORD) at run time.
    bnk_forge_admin_password: str = "changeme"

    # Whether the harness should `make install` a fresh local
    # bnk-forge before running the smoke flow. Default true because
    # the design assumes a clean room — set false when targeting a
    # remote instance or when a clean state has already been ensured
    # out-of-band.
    local_deploy: bool = True
    # Repo root used for `make install`. Default = parent of the
    # `scripts/e2e/` directory this file lives in. Override only when
    # running the script from outside the repo (e.g. installed
    # somewhere else and pointed at a clone).
    bnk_forge_repo_dir: str | None = None

    # Project naming. Final name is `${prefix}${timestamp}-${tag}`.
    project_name_prefix: str = "e2e-"

    timeouts: TimeoutsConfig = Field(default_factory=TimeoutsConfig)

    # Optional cloud-blueprint section. Absent ⇒ cloud phase skips cleanly.
    cloud: CloudBlueprintConfig | None = None

    @field_validator("worker_ssh_key_path")
    @classmethod
    def _expand_worker_key(cls, v: str) -> str:
        if not v:
            return v
        return str(Path(v).expanduser())

    @field_validator("worker_node_ips", "bmc_ips")
    @classmethod
    def _validate_ips(cls, v: list[str]) -> list[str]:
        if not v:
            return v
        out: list[str] = []
        for item in v:
            for ip in _expand_ip_spec(item):
                ipaddress.ip_address(ip)  # raises if invalid
                out.append(ip)
        return out

    @model_validator(mode="after")
    def _apply_env_password(self) -> E2EConfig:
        env_pw = os.environ.get("BNK_FORGE_PASSWORD")
        if env_pw:
            self.bnk_forge_admin_password = env_pw
        return self


_RANGE_RE = re.compile(r"^(\d+\.\d+\.\d+)\.(\d+)-(\d+)$")


def _expand_ip_spec(spec: str) -> list[str]:
    """Accept a single IP, a CIDR, or `prefix.A-B` shorthand.

    Examples:
      `192.168.68.66`        → ["192.168.68.66"]
      `192.168.68.66-71`     → six IPs from .66 through .71
      `192.168.68.0/29`      → all hosts in the /29
    """
    spec = spec.strip()
    m = _RANGE_RE.match(spec)
    if m:
        prefix, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
        if lo > hi or lo < 0 or hi > 255:
            raise ValueError(f"bad ip range: {spec}")
        return [f"{prefix}.{i}" for i in range(lo, hi + 1)]
    if "/" in spec:
        net = ipaddress.ip_network(spec, strict=False)
        return [str(h) for h in net.hosts()]
    return [spec]


def load_config(path: str | Path) -> E2EConfig:
    """Read YAML, validate, return the model."""
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    return E2EConfig.model_validate(raw)
