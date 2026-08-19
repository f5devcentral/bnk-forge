"""Service for managing the admin catalog of DOCA releases (paired BFB + host DOCA)."""

import logging

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.errors import NotFoundError, ValidationError
from models.dpu import BluefieldSoftwareImage
from schemas.dpu import (
    BluefieldSoftwareImageCreate,
    BluefieldSoftwareImageUpdate,
)

logger = logging.getLogger(__name__)

_HEAD_TIMEOUT = 10  # seconds


def _head_check(url: str) -> str | None:
    """HEAD url and return a warning string if non-200 or unreachable, else None."""
    try:
        resp = requests.head(url, timeout=_HEAD_TIMEOUT, allow_redirects=True)
        if resp.status_code != 200:
            return f"BFB URL returned HTTP {resp.status_code} (expected 200): {url}"
    except requests.RequestException as exc:
        return f"BFB URL check failed (network error): {url} — {exc!s}"
    return None


def _bfb_url_warnings(
    base_url: str | None,
    image_filename: str | None,
    doca_host_url: str | None,
) -> list[str]:
    """Check reachability of BFB/DOCA artifact URLs via HEAD.

    Returns a list of human-readable warning strings (never raises).
    An empty list means all present URLs responded with HTTP 200.
    """
    warnings: list[str] = []
    if base_url and image_filename:
        composed = base_url.rstrip("/") + "/" + image_filename
        w = _head_check(composed)
        if w:
            warnings.append(w)
    if doca_host_url:
        w = _head_check(doca_host_url)
        if w:
            warnings.append(w)
    return warnings


class BluefieldImageService:
    """CRUD for the admin-managed DOCA release catalog."""

    def __init__(self, db: Session):
        self.db = db

    def list_images(self) -> list[BluefieldSoftwareImage]:
        return (
            self.db.query(BluefieldSoftwareImage)
            .order_by(BluefieldSoftwareImage.doca_version.desc())
            .all()
        )

    def get_image(self, image_id: int) -> BluefieldSoftwareImage:
        img = self.db.get(BluefieldSoftwareImage, image_id)
        if img is None:
            raise NotFoundError("Bluefield image", image_id)
        return img

    def get_default(self) -> BluefieldSoftwareImage | None:
        """Return the single row marked is_default, or None."""
        return (
            self.db.query(BluefieldSoftwareImage)
            .filter(BluefieldSoftwareImage.is_default.is_(True))
            .first()
        )

    def create_image(self, data: BluefieldSoftwareImageCreate) -> BluefieldSoftwareImage:
        if data.is_default:
            self._clear_default()
        img = BluefieldSoftwareImage(
            image_filename=data.image_filename,
            base_url=data.base_url,
            doca_version=data.doca_version,
            doca_package=data.doca_package,
            host_os=data.host_os,
            host_os_version=data.host_os_version,
            host_arch=data.host_arch,
            doca_host_url=data.doca_host_url,
            is_default=data.is_default,
            notes=data.notes,
        )
        self.db.add(img)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "doca_version",
                f"DOCA release '{data.doca_version}' for {data.host_os} {data.host_os_version}/{data.host_arch} already exists",
            ) from exc
        warnings = _bfb_url_warnings(img.base_url, img.image_filename, img.doca_host_url)
        for w in warnings:
            logger.warning("BluefieldSoftwareImage id=%s URL warning: %s", img.id, w)
        img.url_warnings = warnings  # type: ignore[attr-defined]  # transient, serialised by response schema
        logger.info("Created Bluefield image %s (DOCA %s %s %s/%s)", img.id, img.doca_version, img.host_os, img.host_os_version, img.host_arch)
        return img

    def update_image(
        self, image_id: int, data: BluefieldSoftwareImageUpdate
    ) -> BluefieldSoftwareImage:
        img = self.get_image(image_id)
        if data.is_default is True:
            self._clear_default()
        if data.image_filename is not None:
            img.image_filename = data.image_filename
        if data.base_url is not None:
            img.base_url = data.base_url
        if data.doca_version is not None:
            img.doca_version = data.doca_version
        if data.doca_package is not None:
            img.doca_package = data.doca_package
        if data.host_os is not None:
            img.host_os = data.host_os
        if data.host_os_version is not None:
            img.host_os_version = data.host_os_version
        if data.host_arch is not None:
            img.host_arch = data.host_arch
        if data.doca_host_url is not None:
            img.doca_host_url = data.doca_host_url
        if data.is_default is not None:
            img.is_default = data.is_default
        if data.notes is not None:
            img.notes = data.notes
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "doca_version",
                f"DOCA release '{data.doca_version or img.doca_version}' for "
                f"{data.host_os or img.host_os} {data.host_os_version or img.host_os_version}/{data.host_arch or img.host_arch} already exists",
            ) from exc
        warnings = _bfb_url_warnings(img.base_url, img.image_filename, img.doca_host_url)
        for w in warnings:
            logger.warning("BluefieldSoftwareImage id=%s URL warning: %s", image_id, w)
        img.url_warnings = warnings  # type: ignore[attr-defined]  # transient, serialised by response schema
        logger.info("Updated Bluefield image %s", image_id)
        return img

    def delete_image(self, image_id: int) -> None:
        img = self.get_image(image_id)
        self.db.delete(img)
        self.db.flush()
        logger.info("Deleted Bluefield image %s", image_id)

    def _clear_default(self) -> None:
        """Unset is_default on all rows so at most one default exists."""
        self.db.query(BluefieldSoftwareImage).filter(
            BluefieldSoftwareImage.is_default.is_(True)
        ).update({"is_default": False})
