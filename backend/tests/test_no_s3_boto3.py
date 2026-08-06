"""S3 / boto3 / MinIO absence regression.

The S3-compatible adapter and the ``boto3`` dependency were removed.
These assertions fail if either sneak back in.
"""
from __future__ import annotations

import importlib
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def test_boto3_is_not_importable_and_not_in_requirements():
    """boto3 must not be a declared or installed dependency."""
    # A. Not in requirements.txt
    req_path = pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
    req = req_path.read_text()
    assert "boto3" not in req.lower(), "boto3 must not appear in requirements.txt"

    # B. Not importable. If a stale wheel is on disk, the test wants to fail
    # so the pod is cleaned up before deploy.
    with pytest.raises(ImportError):
        importlib.import_module("boto3")


def test_s3_module_is_gone():
    """The old ``storage/s3.py`` adapter must not be present."""
    s3_path = (
        REPO_ROOT / "backend" / "app" / "domains" / "media" / "storage" / "s3.py"
    )
    assert not s3_path.exists(), "backend/app/domains/media/storage/s3.py must be removed"


def test_factory_refuses_s3_backend(monkeypatch):
    """``MEDIA_STORAGE_BACKEND=s3`` must be rejected as a misconfiguration."""
    from app.domains.media.storage import factory
    from app.domains.media.storage.base import StorageMisconfigured

    factory.set_storage(None)  # clear the singleton
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "s3")
    with pytest.raises(StorageMisconfigured):
        factory.get_storage()


def test_source_tree_has_no_active_s3_or_minio_references():
    """Every mention of ``s3`` or ``minio`` in active source must be in an
    absence-assertion test, or an intentional comment referencing the
    removal.
    """
    backend = REPO_ROOT / "backend" / "app"
    # Terms that intentionally appear in the removal comments and error
    # messages of the storage factory. Every hit is expected to be in one of
    # these strings.
    allowed_context_substrings = (
        "S3/MinIO support was removed",
        "S3/MinIO adapter",
    )
    forbidden_hits: list[str] = []
    for path in backend.rglob("*.py"):
        text = path.read_text()
        lowered = text.lower()
        for needle in ("boto3", "s3compatiblestorage", "minio"):
            if needle not in lowered:
                continue
            # Allow it if every occurrence sits inside one of the allowed
            # comments; otherwise this counts as an active reference.
            surviving_lines = []
            for line in text.splitlines():
                if needle in line.lower() and not any(
                    ctx in line for ctx in allowed_context_substrings
                ):
                    surviving_lines.append(line.strip())
            if surviving_lines:
                forbidden_hits.append(f"{path}: {surviving_lines}")
    assert forbidden_hits == [], forbidden_hits


def test_production_refuses_local_backend(monkeypatch):
    """``MEDIA_STORAGE_BACKEND=local`` is refused when APP_ENV=production."""
    from app.domains.media.storage import factory
    from app.domains.media.storage.base import StorageMisconfigured

    factory.set_storage(None)
    monkeypatch.setattr(factory, "MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setattr(factory, "APP_ENV", "production")
    monkeypatch.setattr(factory, "MEDIA_ALLOW_LOCAL_IN_PRODUCTION", False)
    with pytest.raises(StorageMisconfigured):
        factory.get_storage()
    factory.set_storage(None)
