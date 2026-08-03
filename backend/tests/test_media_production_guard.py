"""Guard test for Fix 9 (WP2): production must refuse local media storage."""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture(autouse=True)
def _reset_storage():
    """Every test in this file starts with a fresh storage singleton."""
    from app.domains.media.storage import factory

    factory.set_storage(None)
    yield
    factory.set_storage(None)


def _reload_config():
    """Re-import app.config so a mutated environment variable is honoured."""
    import app.config as cfg

    importlib.reload(cfg)
    # get_storage() imports these names at module load time; reload the
    # factory too so it picks up the fresh values.
    from app.domains.media.storage import factory as fac

    importlib.reload(fac)
    return fac


def test_local_backend_is_refused_in_production(monkeypatch):
    from app.domains.media.storage.base import StorageError

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.delenv("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", raising=False)

    factory = _reload_config()

    with pytest.raises(StorageError) as excinfo:
        factory.get_storage()

    message = str(excinfo.value)
    assert "MEDIA_STORAGE_BACKEND=local is not permitted" in message
    assert "APP_ENV=production" in message
    # Escape hatch is documented right there so an operator does not have to
    # dig through code to find it.
    assert "MEDIA_ALLOW_LOCAL_IN_PRODUCTION=true" in message


def test_local_backend_is_allowed_with_explicit_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setenv("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", "true")

    factory = _reload_config()
    storage = factory.get_storage()
    assert storage.backend_name == "local"


def test_local_backend_is_allowed_in_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.delenv("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", raising=False)

    factory = _reload_config()
    storage = factory.get_storage()
    assert storage.backend_name == "local"


def test_local_backend_is_allowed_in_test(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.delenv("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", raising=False)

    factory = _reload_config()
    storage = factory.get_storage()
    assert storage.backend_name == "local"


def test_unknown_backend_is_refused(monkeypatch):
    from app.domains.media.storage.base import StorageError

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "azure")
    monkeypatch.delenv("MEDIA_ALLOW_LOCAL_IN_PRODUCTION", raising=False)

    factory = _reload_config()
    with pytest.raises(StorageError) as excinfo:
        factory.get_storage()
    assert "must be 'local' or 's3'" in str(excinfo.value)
    assert "'azure'" in str(excinfo.value)


def test_module_reload_restores_dev_default_for_subsequent_tests(monkeypatch):
    """Sanity: after this test file exits, the media suite still sees local
    storage. Explicitly restore the module to the pre-test state."""
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MEDIA_STORAGE_BACKEND", "local")
    _reload_config()
    # Actual assertion happens by not leaking state into other test files.
