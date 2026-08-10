"""Shared SSH session builder for bare-metal and benchmark agent operations.

Extracted from BareMetalDiscoveryService._build_ssh to allow reuse by the
benchmark agent scan service without coupling to the bare-metal domain model.
"""

import logging
import os
import tempfile

from sqlalchemy.orm import Session

from services.bare_metal.ssh_session import SSHSession
from services.ssh.paramiko_utils import decrypt_ssh_credential

logger = logging.getLogger(__name__)


def build_ssh_session_from_credential(
     db: Session,
     ssh_credential_id: int | None,
     host_ip: str,
     ssh_port: int | None,
     jumphost_chain: list[dict] | None,
 ) -> SSHSession:
     """Build an SSHSession from a stored SSHCredential + optional jumphost chain.

     Decrypts the host credential, walks ``jumphost_chain`` resolving each
     entry's ``ssh_credential_id``, and returns a fully-configured SSHSession.

     Args:
         db: SQLAlchemy session.
         ssh_credential_id: FK to ``ssh_credentials.id`` for the target host.
             If None, falls back to root/no-auth (useful for testing without a
             stored credential, though real scans will always have one).
         host_ip: IP address to connect to.
         ssh_port: SSH port on the target host (defaults to 22 when None).
         jumphost_chain: Optional list of ``{"ssh_credential_id": N}`` dicts
             describing the hop chain, innermost host first.

     Returns:
         Configured ``SSHSession`` ready to connect.
     """
     from models.ssh_credential import SSHCredential

     username = "root"
     password: str | None = None
     private_key_path: str | None = None
     # Track temp files for cleanup
     temp_key_files: list[str] = []

     try:
         if ssh_credential_id:
             cred = db.query(SSHCredential).filter_by(id=ssh_credential_id).first()
             if cred:
                 cred_data = decrypt_ssh_credential(cred)
                 username = cred_data["username"]
                 password = cred_data["password"]
                 if cred_data["private_key_content"]:
                     # Use mkstemp for secure temp file (mode 0600 at creation)
                     fd, key_path = tempfile.mkstemp(prefix="forge-ssh-", suffix=".key")
                     try:
                         os.write(fd, cred_data["private_key_content"].encode("utf-8"))
                     finally:
                         os.close(fd)
                     temp_key_files.append(key_path)
                     private_key_path = key_path

         # Resolve jumphost chain — include key/password material for each hop
         resolved_chain: list[dict] | None = None
         if jumphost_chain:
             resolved_chain = []
             for jh_entry in jumphost_chain:
                 jh_cred_id = jh_entry.get("ssh_credential_id") if isinstance(jh_entry, dict) else None
                 if jh_cred_id:
                     jh_cred = db.query(SSHCredential).filter_by(id=jh_cred_id).first()
                     if jh_cred:
                         jh_data = decrypt_ssh_credential(jh_cred)
                         jh_info: dict = {
                             "host": jh_data["host"],
                             "port": jh_data["port"],
                             "username": jh_data["username"],
                         }
                         if jh_data["private_key_content"]:
                             # Use mkstemp for secure temp file (mode 0600 at creation)
                             fd, jh_key_path = tempfile.mkstemp(prefix="forge-jh-", suffix=".key")
                             try:
                                 os.write(fd, jh_data["private_key_content"].encode("utf-8"))
                             finally:
                                 os.close(fd)
                             temp_key_files.append(jh_key_path)
                             jh_info["private_key_path"] = jh_key_path
                         elif jh_data["password"]:
                             jh_info["password"] = jh_data["password"]
                         resolved_chain.append(jh_info)

         session = SSHSession(
             host=host_ip,
             username=username,
             port=ssh_port or 22,
             private_key_path=private_key_path,
             password=password,
             jumphost_chain=resolved_chain if resolved_chain else None,
         )
         # Store temp files on the session so they can be cleaned up later
         session._temp_key_files = temp_key_files
         return session

     except Exception:
         # Clean up temp files on error
         for fp in temp_key_files:
             try:
                 os.remove(fp)
             except OSError:
                 pass
         raise
