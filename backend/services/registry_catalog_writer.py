"""Write fetched registry/git module sources to the on-disk module catalog.

Imports always go to:

    <MODULE_CATALOG_PATH>/registry/<source_short>/<namespace>/<name>/

The synthesized `bnkforge.pack.json` is written alongside the extracted source.
On re-import, the existing directory is wiped and recreated; `metadata_overrides`
preservation happens at a higher layer (RegistryService) before this writer is
called, since overrides live in the DB, not on disk.
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import zipfile
from io import BytesIO
from pathlib import Path

from services.module_catalog_service import MODULE_CATALOG_PATH

logger = logging.getLogger(__name__)


_SOURCE_TYPE_DIR_MAP = {
    "terraform_registry": "terraform",
    "opentofu_registry": "opentofu",
    "tfc_private": "tfc",
    "public_git": "git",
}


class CatalogWriteError(RuntimeError):
    pass


def _resolve_dir(source_type: str, namespace: str, name: str) -> Path:
    short = _SOURCE_TYPE_DIR_MAP.get(source_type)
    if not short:
        raise CatalogWriteError(f"Unknown registry source_type {source_type!r}")
    safe_ns = namespace.replace("/", "_").replace("..", "_")
    safe_name = name.replace("/", "_").replace("..", "_")
    return Path(MODULE_CATALOG_PATH) / "registry" / short / safe_ns / safe_name


def _extract_archive(tarball_bytes: bytes, target_dir: Path) -> None:
    """Try tar.gz first; fall back to zip. Strip a leading single-directory wrapper."""
    target_dir.mkdir(parents=True, exist_ok=True)

    extracted = False
    try:
        with tarfile.open(fileobj=BytesIO(tarball_bytes), mode="r:*") as tar:
            tar.extractall(target_dir, filter="data")
            extracted = True
    except (tarfile.TarError, EOFError):
        pass

    if not extracted:
        try:
            with zipfile.ZipFile(BytesIO(tarball_bytes)) as zf:
                zf.extractall(target_dir)
                extracted = True
        except zipfile.BadZipFile as exc:
            raise CatalogWriteError(f"Archive is neither tar nor zip: {exc}") from exc

    # If the archive contains exactly one top-level directory, flatten it.
    children = [c for c in target_dir.iterdir() if c.name != "bnkforge.pack.json"]
    if len(children) == 1 and children[0].is_dir():
        wrapper = children[0]
        for item in wrapper.iterdir():
            shutil.move(str(item), str(target_dir / item.name))
        wrapper.rmdir()


def write_module(
    *,
    tarball_bytes: bytes,
    source_type: str,
    namespace: str,
    name: str,
    pack_manifest: dict,
    extracted_root: str = "",
) -> Path:
    """Extract the tarball + write the synthesized pack.json alongside.

    Returns the absolute path of the module directory on disk.
    """
    target_dir = _resolve_dir(source_type, namespace, name)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    _extract_archive(tarball_bytes, target_dir)

    if extracted_root:
        # The fetcher already trimmed to the subpath when producing the tarball,
        # so this is informational only.
        logger.debug("registry module %s/%s extracted from subpath %s", namespace, name, extracted_root)

    manifest_path = target_dir / "bnkforge.pack.json"
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(pack_manifest, fh, indent=2, sort_keys=False)

    return target_dir


def remove_module(*, source_type: str, namespace: str, name: str) -> None:
    target_dir = _resolve_dir(source_type, namespace, name)
    if target_dir.exists():
        shutil.rmtree(target_dir, ignore_errors=True)
