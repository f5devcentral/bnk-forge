"""Project-scoped SSH / HTTPS tunneling to DPU BMCs.

Every BMC-touching code path (Redfish inventory, BMC credential helper,
DPU-OS probe, serial console, flash engine, factory reset) needs to
honour the project's optional jumphost. This module is the single source
of truth for:

  * resolving a project's jumphost credential dict;
  * opening an SSH connection to a BMC directly or through the jumphost;
  * issuing HTTP/1.1 requests to a BMC's Redfish service via a
    paramiko direct-tcpip channel wrapped with TLS (MemoryBIO — paramiko
    Channels don't satisfy ssl.wrap_socket()'s socket protocol).

Callers pass the resolved `jumphost_cred` dict through to their probe /
exec helpers; when the dict is None, each helper falls back to direct
access. That keeps the diff minimal at each call site.
"""

from __future__ import annotations

import base64
import logging
import ssl
from typing import Any

import paramiko
from sqlalchemy.orm import Session

from core.encryption import decrypt_value
from services.ssh.paramiko_utils import load_private_key_from_content

logger = logging.getLogger(__name__)


# ── Project jumphost resolver ─────────────────────────────────────────────

def resolve_project_jumphost_cred(db: Session, project_id: int) -> dict[str, Any] | None:
    """Return the project's jumphost credential dict, or None if the
    project has no `ssh_credential_id` configured."""
    from models.project import Project
    from models.ssh_credential import SSHCredential

    project = db.get(Project, project_id)
    if project is None or not project.ssh_credential_id:
        return None
    cred = db.get(SSHCredential, project.ssh_credential_id)
    if cred is None:
        return None
    return {
        "host": cred.host,
        "port": cred.port or 22,
        "username": cred.username,
        "auth_type": cred.auth_type or "key",
        "password": decrypt_value(cred.password_encrypted) if cred.password_encrypted else None,
        "private_key": decrypt_value(cred.private_key_encrypted) if cred.private_key_encrypted else None,
        "key_passphrase": decrypt_value(cred.key_passphrase_encrypted) if cred.key_passphrase_encrypted else None,
    }


# ── SSH ───────────────────────────────────────────────────────────────────

def open_jumphost_ssh_client(jumphost_cred: dict, *, timeout: int = 15) -> paramiko.SSHClient:
    """Paramiko SSHClient connected to the jumphost.

    Caller owns the returned client and must `close()` it.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pkey = None
    auth_type = (jumphost_cred.get("auth_type") or "key").lower()
    if auth_type == "key" and jumphost_cred.get("private_key"):
        pkey = load_private_key_from_content(
            jumphost_cred["private_key"],
            passphrase=jumphost_cred.get("key_passphrase"),
        )
    client.connect(
        hostname=jumphost_cred["host"],
        port=int(jumphost_cred.get("port") or 22),
        username=jumphost_cred["username"],
        password=jumphost_cred.get("password") if auth_type == "password" else None,
        pkey=pkey,
        timeout=timeout,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def ssh_connect_to_bmc(
    host: str,
    username: str,
    password: str,
    *,
    jumphost_cred: dict | None = None,
    timeout: int = 20,
) -> paramiko.SSHClient:
    """Open a paramiko SSHClient to the BMC.

    When `jumphost_cred` is provided the BMC connection tunnels through
    the jumphost via paramiko's `direct-tcpip` channel — SFTP, exec_command
    and any other SSHClient API work transparently against the returned
    client, including against channels that cannot be reached directly
    from the Forge container's network namespace.

    When jumphost_cred is None, connects directly.
    """
    if jumphost_cred is None:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host, username=username, password=password, timeout=timeout,
            allow_agent=False, look_for_keys=False,
        )
        return client

    jump = open_jumphost_ssh_client(jumphost_cred, timeout=timeout)
    try:
        transport = jump.get_transport()
        if transport is None:
            raise RuntimeError("jumphost SSH transport unavailable")
        chan = transport.open_channel(
            "direct-tcpip", (host, 22), ("127.0.0.1", 0), timeout=timeout,
        )
    except Exception:
        jump.close()
        raise

    bmc = paramiko.SSHClient()
    bmc.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        bmc.connect(
            host, username=username, password=password, timeout=timeout,
            allow_agent=False, look_for_keys=False, sock=chan,
        )
    except Exception:
        try:
            chan.close()
        except Exception:
            pass
        jump.close()
        raise

    # Tie the jumphost client's lifetime to the BMC client. When the BMC
    # client is closed, close the jumphost too — otherwise we leak SSH
    # connections to the jumphost on every BMC operation.
    _original_close = bmc.close

    def _close_cascade() -> None:
        try:
            _original_close()
        finally:
            try:
                jump.close()
            except Exception:
                pass

    bmc.close = _close_cascade  # type: ignore[method-assign]
    return bmc


# ── HTTPS-over-channel primitives ────────────────────────────────────────

def https_request_via_channel(
    jumphost_client: paramiko.SSHClient,
    *,
    host: str,
    port: int,
    method: str,
    path: str,
    json_body: dict | None = None,
    auth: tuple[str, str] | None = None,
    timeout: int = 10,
    content_type: str = "application/json",
) -> tuple[int, bytes] | None:
    """Issue a single HTTP/1.1 request to (host, port) via `jumphost_client`.

    Uses paramiko direct-tcpip + MemoryBIO TLS pump. TLS certificate
    verification is disabled — BMCs universally ship self-signed certs.

    Returns (status_code, body_bytes) or None on any failure.
    """
    import json as _json

    try:
        transport = jumphost_client.get_transport()
        if transport is None:
            return None
        channel = transport.open_channel(
            "direct-tcpip", (host, port), ("127.0.0.1", 0), timeout=timeout,
        )
    except Exception:
        return None
    channel.settimeout(timeout)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    incoming = ssl.MemoryBIO()
    outgoing = ssl.MemoryBIO()
    try:
        sslobj = ctx.wrap_bio(incoming, outgoing, server_hostname=host)
    except Exception:
        try:
            channel.close()
        except Exception:
            pass
        return None

    def _drain() -> bool:
        data = outgoing.read()
        if data:
            try:
                channel.sendall(data)
            except Exception:
                return False
        return True

    def _pump() -> bool:
        try:
            chunk = channel.recv(65536)
        except Exception:
            return False
        if not chunk:
            incoming.write_eof()
            return True
        incoming.write(chunk)
        return True

    try:
        while True:
            try:
                sslobj.do_handshake()
                break
            except ssl.SSLWantReadError:
                if not _drain() or not _pump():
                    raise ssl.SSLError("channel closed during handshake")
            except ssl.SSLWantWriteError:
                if not _drain():
                    raise ssl.SSLError("channel closed during handshake")
    except Exception as exc:
        logger.debug("HTTPS tunnel TLS handshake to %s:%s failed: %s", host, port, exc)
        try:
            channel.close()
        except Exception:
            pass
        return None

    # Build request
    body_bytes = b""
    if json_body is not None:
        body_bytes = _json.dumps(json_body).encode()
    headers = [
        f"{method.upper()} {path} HTTP/1.1",
        f"Host: {host}",
        "Accept: application/json",
        "User-Agent: bnk-forge/1.0",
        "Connection: close",
    ]
    if body_bytes:
        headers.append(f"Content-Type: {content_type}")
        headers.append(f"Content-Length: {len(body_bytes)}")
    if auth is not None:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        headers.append(f"Authorization: Basic {token}")
    req = ("\r\n".join(headers) + "\r\n\r\n").encode() + body_bytes

    try:
        written = 0
        while written < len(req):
            try:
                n = sslobj.write(req[written:])
                written += n
                _drain()
            except ssl.SSLWantWriteError:
                _drain()
    except Exception as exc:
        logger.debug("HTTPS tunnel send to %s%s failed: %s", host, path, exc)
        try:
            channel.close()
        except Exception:
            pass
        return None

    raw = bytearray()
    try:
        while True:
            try:
                chunk = sslobj.read(65536)
                if not chunk:
                    break
                raw.extend(chunk)
            except ssl.SSLWantReadError:
                if not _pump():
                    break
            except ssl.SSLZeroReturnError:
                break
            except ssl.SSLError:
                break
    finally:
        try:
            channel.close()
        except Exception:
            pass

    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        return None
    header_blob = bytes(raw[:sep]).decode("iso-8859-1", errors="replace")
    body = bytes(raw[sep + 4:])
    status_line = header_blob.split("\r\n", 1)[0]
    try:
        status_code = int(status_line.split(" ", 2)[1])
    except (IndexError, ValueError):
        return None
    if "transfer-encoding: chunked" in header_blob.lower():
        body = _decode_chunked_body(body)
    return status_code, body


def _decode_chunked_body(body: bytes) -> bytes:
    """Minimal HTTP/1.1 chunked decoder; empty bytes on malformed input."""
    out = bytearray()
    i = 0
    n = len(body)
    while i < n:
        nl = body.find(b"\r\n", i)
        if nl < 0:
            break
        size_hex = body[i:nl].split(b";", 1)[0].strip()
        try:
            size = int(size_hex, 16)
        except ValueError:
            break
        i = nl + 2
        if size == 0:
            break
        if i + size > n:
            break
        out.extend(body[i : i + size])
        i += size
        if body[i : i + 2] == b"\r\n":
            i += 2
    return bytes(out)


# ── Tunnel-aware BMC Redfish helpers (GET / POST / PATCH) ────────────────

def bmc_http_request(
    *,
    bmc_ip: str,
    method: str,
    path: str,
    json_body: dict | None = None,
    auth: tuple[str, str] | None = None,
    jumphost_cred: dict | None = None,
    timeout: int = 10,
) -> tuple[int, bytes] | None:
    """One-shot BMC Redfish HTTP call that routes via jumphost when set.

    Returns (status_code, body_bytes) or None on transport failure.
    """
    if jumphost_cred is None:
        import requests
        from requests.auth import HTTPBasicAuth

        url = f"https://{bmc_ip}{path}"
        try:
            resp = requests.request(
                method.upper(), url,
                auth=HTTPBasicAuth(*auth) if auth else None,
                json=json_body,
                verify=False,
                timeout=timeout,
            )
        except requests.RequestException:
            return None
        return resp.status_code, resp.content

    jump = None
    try:
        jump = open_jumphost_ssh_client(jumphost_cred, timeout=timeout)
    except Exception as exc:
        logger.debug("jumphost SSH connect failed for %s: %s", jumphost_cred.get("host"), exc)
        return None

    try:
        return https_request_via_channel(
            jump,
            host=bmc_ip, port=443,
            method=method, path=path,
            json_body=json_body, auth=auth,
            timeout=timeout,
        )
    finally:
        try:
            jump.close()
        except Exception:
            pass
