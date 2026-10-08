"""Executed synthetic qualification, separate from the recorded live source drill."""
from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app import config
from app.domains.media.storage.local import LocalFilesystemStorage
from app.domains.off import importer, store
from app.domains.off.export import DATA_FILE, LICENSE_FILE, LICENSE_NOTICE, MANIFEST_FILE, _record, export
from app.domains.off.models import OffBase, OffProduct
from app.domains.off.wall import ProprietaryFieldError
from app.operations import recovery
from sqlalchemy import select, text
from sqlalchemy.engine import make_url


def manifest():
    result = {field: [] for field in recovery.ORDINARY_FIELDS}
    result.update(database="postgres", schema_context="public", alembic_heads=["d0e1f2g3h4"],
                  public_tables=["synthetic"], public_table_counts={"synthetic": 1},
                  auth_users=0, storage_buckets=1, storage_objects=0, external_integrations=0,
                  off_schema_present=False, later_label_report_resources_present=False,
                  sorted_primary_id_sha256={"synthetic": hashlib.sha256(b"1").hexdigest()}, vault_rows=2)
    return result


def qualify(tmp_path, **changes):
    calls = []

    def dump(name, path):
        calls.append(name)
        recovery.run_checked([sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).write_bytes(b'-- synthetic SQL\\n')", str(path)])

    def restore(paths):
        calls.append("restore")
        assert tuple(path.name for path in paths) == recovery.ARTIFACT_NAMES

    def verify():
        calls.append("verify")
        return manifest()

    options = dict(dump=dump, restore=restore, read_source=manifest, verify_restore=verify,
                   expected_head="d0e1f2g3h4")
    options.update(changes)
    result = recovery.qualify_database(tmp_path, **options)
    return result, calls


def test_phase_order_and_measured_nonempty_artifacts(tmp_path):
    result, calls = qualify(tmp_path)
    assert calls == ["roles.sql", "schema.sql", "data.sql", "restore", "verify"]
    assert result["status"] == "ORDINARY_DATABASE_PARITY_PASSED"
    assert all(record["bytes"] > 0 and len(record["sha256"]) == 64 for record in result["artifacts"])
    assert result["total_seconds"] > 0
    assert result["hosted_auth_authority_proven"] is False
    assert result["vault_credential_recovery_proven"] is False


def test_zero_dump_is_rejected_before_restore(tmp_path):
    with pytest.raises(recovery.RecoveryFailed, match="ARTIFACT_EMPTY"):
        qualify(tmp_path, dump=lambda name, path: path.write_bytes(b""))


def test_dump_command_failure_cannot_become_success(tmp_path):
    def fail_dump(name, path):
        path.write_bytes(b"partial")
        recovery.run_checked([sys.executable, "-c", "import sys;sys.exit(19)"])
    with pytest.raises(recovery.RecoveryFailed, match="COMMAND_FAILED"):
        qualify(tmp_path, dump=fail_dump)


def test_restore_command_failure_cannot_become_success(tmp_path):
    def fail_restore(paths):
        recovery.run_checked([sys.executable, "-c", "import sys;sys.exit(23)"])
    with pytest.raises(recovery.RecoveryFailed, match="COMMAND_FAILED"):
        qualify(tmp_path, restore=fail_restore)


def test_verification_cannot_be_skipped(tmp_path):
    with pytest.raises(recovery.RecoveryFailed, match="VERIFICATION_REQUIRED"):
        qualify(tmp_path, verify_restore=None)


@pytest.mark.parametrize("field", recovery.ORDINARY_FIELDS)
def test_any_manifest_mismatch_fails(tmp_path, field):
    wrong = manifest()
    wrong[field] = "different"
    with pytest.raises(recovery.RecoveryFailed, match="MANIFEST_MISMATCH"):
        qualify(tmp_path, verify_restore=lambda: wrong)


def test_changed_source_or_artifact_fails(tmp_path):
    before = manifest()
    after = deepcopy(before)
    after["public_table_counts"]["synthetic"] = 2
    sources = iter((before, after))
    with pytest.raises(recovery.RecoveryFailed, match="MANIFEST_MISMATCH"):
        qualify(tmp_path, read_source=lambda: next(sources))
    for path in tmp_path.iterdir():
        path.unlink()
    def verify_tamper():
        (tmp_path / "data.sql").write_bytes(b"tampered")
        return before
    with pytest.raises(recovery.RecoveryFailed, match="ARTIFACT_CHANGED"):
        qualify(tmp_path, verify_restore=verify_tamper)


def test_source_snapshot_survives_adapter_reusing_a_mutable_manifest(tmp_path):
    cached = manifest()
    calls = 0
    def read_cached():
        nonlocal calls
        calls += 1
        if calls == 2:
            cached["public_table_counts"]["synthetic"] = 2
        return cached
    with pytest.raises(recovery.RecoveryFailed, match="MANIFEST_MISMATCH"):
        qualify(tmp_path, read_source=read_cached)


def test_required_storage_proof_cannot_be_skipped_for_nonzero_source(tmp_path):
    source = manifest()
    source["storage_objects"] = 1
    with pytest.raises(recovery.RecoveryFailed, match="PRODUCTION_OBJECT_BYTE_PROOF_REQUIRED"):
        qualify(tmp_path, read_source=lambda: source, verify_restore=lambda: source)


def test_old_workspace_and_wrong_source_authority_are_refused(tmp_path):
    with pytest.raises(recovery.RecoveryFailed, match="SOURCE_AUTHORITY_MISMATCH"):
        qualify(tmp_path, expected_head="o3p4q5r6s7")
    (tmp_path / "data.sql").write_bytes(b"stale")
    with pytest.raises(recovery.RecoveryFailed, match="STALE_ARTIFACTS_REFUSED"):
        qualify(tmp_path)


def test_native_failure_does_not_disclose_stderr():
    with pytest.raises(recovery.RecoveryFailed) as caught:
        recovery.run_checked([sys.executable, "-c", "import sys;sys.stderr.write('private fixture value');sys.exit(1)"])
    assert str(caught.value) == "COMMAND_FAILED"


@pytest.mark.asyncio
async def test_local_storage_backup_destroy_restore_size_and_sha256(tmp_path, record_testsuite_property):
    storage = LocalFilesystemStorage(str(tmp_path / "local-storage"))
    payload = bytes(range(256)) * 41 + b"F15 synthetic bytes\x00\xff"
    await storage.put("synthetic/f15-object.bin", payload, "application/octet-stream")
    proof = await recovery.recover_object(storage, "synthetic/f15-object.bin", tmp_path / "object.backup")
    assert proof.bytes == len(payload)
    assert proof.sha256 == hashlib.sha256(payload).hexdigest()
    assert await storage.get("synthetic/f15-object.bin") == payload
    record_testsuite_property("local_storage_recovery", json.dumps({"bytes": proof.bytes, "sha256": proof.sha256,
                    "backup_destroy_restore_verified": True, "production_object_bytes": False}))


def write_fixture_export(directory):
    product = OffProduct(barcode="8900000000001", product_name="Synthetic OFF fixture")
    row = _record(product)
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
    from app.domains.off.attribution import ATTRIBUTION_TEXT, LICENSE_NAME, LICENSE_URL, SOURCE_URL
    fixture = dict(dataset=DATA_FILE, format="JSON Lines, one product per line, UTF-8", record_count=1,
                   sha256=hashlib.sha256(payload).hexdigest(), generated_at=datetime.now(UTC).isoformat(),
                   attribution=ATTRIBUTION_TEXT, license=LICENSE_NAME, license_url=LICENSE_URL,
                   source_url=SOURCE_URL, fields=sorted(importer.CANONICAL_FIELDS), contains_proprietary_data=False)
    (directory / DATA_FILE).write_bytes(payload)
    (directory / LICENSE_FILE).write_text(LICENSE_NOTICE, encoding="utf-8")
    (directory / MANIFEST_FILE).write_text(json.dumps(fixture), encoding="utf-8")
    return fixture, row


@pytest.mark.parametrize("field", ["asli_score", "account_id", "unknown_field", "fetched_at"])
@pytest.mark.asyncio
async def test_importer_rejects_proprietary_unknown_and_fetch_time_before_session(tmp_path, monkeypatch, field):
    fixture, row = write_fixture_export(tmp_path)
    row[field] = "injected"
    payload = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode()
    fixture["sha256"] = hashlib.sha256(payload).hexdigest()
    (tmp_path / DATA_FILE).write_bytes(payload)
    (tmp_path / MANIFEST_FILE).write_text(json.dumps(fixture), encoding="utf-8")
    def forbidden():
        raise AssertionError("Invalid export reached a database session")
    monkeypatch.setattr(importer, "get_off_sessionmaker", forbidden)
    with pytest.raises(ProprietaryFieldError):
        await importer.import_export(tmp_path)


@pytest.mark.parametrize("problem", ["checksum", "license", "attribution", "count", "size", "duplicate", "unsorted"])
def test_importer_rejects_invalid_canonical_export(tmp_path, monkeypatch, problem):
    fixture, row = write_fixture_export(tmp_path)
    if problem == "checksum":
        (tmp_path / DATA_FILE).write_bytes(b"changed")
    elif problem == "license":
        (tmp_path / LICENSE_FILE).write_text("wrong", encoding="utf-8")
    elif problem == "attribution":
        fixture["attribution"] = "wrong"
    elif problem == "count":
        fixture["record_count"] = 2
    elif problem == "size":
        monkeypatch.setattr(importer, "MAX_BYTES", 4)
    else:
        second = dict(row)
        if problem == "unsorted":
            second["barcode"] = "0000000000000"
        payload = (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" + json.dumps(second, ensure_ascii=False, sort_keys=True) + "\n").encode()
        fixture.update(record_count=2, sha256=hashlib.sha256(payload).hexdigest())
        (tmp_path / DATA_FILE).write_bytes(payload)
    (tmp_path / MANIFEST_FILE).write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(importer.InvalidOffExport):
        importer.read_export(tmp_path)


def test_importer_refuses_store_b_aliases_and_hosted_target(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "test")
    monkeypatch.setattr(config, "POSTGRES_URL", "postgresql+asyncpg://test@localhost:5432/store_b")
    for target in ("postgresql+asyncpg://other@127.0.0.1:5432/store_b", "postgresql+asyncpg://test@db.example.test/store_a", ""):
        monkeypatch.setattr(config, "OFF_DATABASE_URL", target)
        with pytest.raises(importer.InvalidOffExport):
            importer._assert_local_separate_target()


def ensure_test_database(name):
    url = make_url(config.POSTGRES_URL).set(drivername="postgresql")
    assert url.host in {"localhost", "127.0.0.1", "::1"} and "test" in url.database
    assert name in {"f15_store_a", "f15_recovery_target"}
    command = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "--dbname", url.render_as_string(hide_password=False)]
    exists = recovery.run_checked(command + ["-c", f"SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname='{name}')"]).strip()
    if exists == "f":
        recovery.run_checked(command + ["-c", f'CREATE DATABASE "{name}"'])


@pytest_asyncio.fixture
async def separate_store_a(monkeypatch):
    """A distinct, disposable local database, never the Store B fallback."""
    main_url = make_url(config.POSTGRES_URL)
    assert main_url.host in {"localhost", "127.0.0.1", "::1"}
    target = main_url.set(database="f15_store_a", drivername="postgresql+asyncpg")
    ensure_test_database("f15_store_a")
    await store.dispose_off_engine()
    monkeypatch.setattr(config, "OFF_DATABASE_URL", target.render_as_string(hide_password=False))
    await store.create_off_schema()
    async with store.get_off_engine().begin() as connection:
        await connection.execute(text("TRUNCATE off_data.off_products"))
    yield
    await store.dispose_off_engine()


@pytest.mark.asyncio
async def test_store_a_export_destroy_recreate_import_actual_parity_and_unknown_freshness(separate_store_a, tmp_path, monkeypatch, record_testsuite_property):
    from app.shared.database import sql as store_b
    def forbidden():
        raise AssertionError("Store B session acquired by Store A recovery")
    monkeypatch.setattr(store_b, "get_sessionmaker", forbidden)
    async with store.get_off_sessionmaker()() as session, session.begin():
        session.add_all([
            OffProduct(barcode="8900000000001", product_name="Synthetic α", brands="Fixture", nutriments={"sugars_100g": 2.5}, fetched_at=datetime.now(UTC)),
            OffProduct(barcode="8900000000002", product_name="Synthetic β", categories_hierarchy=["en:foods"], countries_tags=["en:india"], fetched_at=datetime.now(UTC)),
        ])
    original = await export(tmp_path / "before")
    engine = store.get_off_engine()
    async with engine.begin() as connection:
        await connection.run_sync(OffBase.metadata.drop_all)
    async with engine.connect() as connection:
        assert (await connection.execute(text("SELECT to_regclass('off_data.off_products')"))).scalar() is None
    await store.create_off_schema()
    imported = await importer.import_export(tmp_path / "before")
    restored = await export(tmp_path / "after")
    assert original["sha256"] == restored["sha256"] == imported["sha256"]
    assert original["record_count"] == restored["record_count"] == 2
    assert (tmp_path / "before" / DATA_FILE).read_bytes() == (tmp_path / "after" / DATA_FILE).read_bytes()
    assert (tmp_path / "after" / LICENSE_FILE).read_text(encoding="utf-8") == LICENSE_NOTICE
    async with store.get_off_sessionmaker()() as session:
        products = (await session.execute(select(OffProduct))).scalars().all()
        assert len(products) == 2
        assert all(product.fetched_at is None for product in products)
    with pytest.raises(importer.InvalidOffExport, match="empty"):
        await importer.import_export(tmp_path / "before")
    record_testsuite_property("local_store_a_recovery", json.dumps({"record_count": original["record_count"],
                    "sha256": original["sha256"], "export_destroy_recreate_import_parity": True,
                    "fetched_at": "NULL/unknown", "license": original["license"],
                    "attribution": original["attribution"], "hosted_store_a_changed": False}))


def test_actual_local_postgres_restore_is_one_transaction_and_stops_on_sql_error(tmp_path):
    ensure_test_database("f15_recovery_target")
    url = make_url(config.POSTGRES_URL)
    assert url.host in {"localhost", "127.0.0.1", "::1"}
    target_url = url.set(drivername="postgresql", database="f15_recovery_target").render_as_string(hide_password=False)
    command = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "--dbname", target_url]
    recovery.run_checked(command + ["-c", "DROP TABLE IF EXISTS public.f15_probe"])
    payloads = {"roles.sql": "-- synthetic role precondition\nSELECT 1;\n",
                "schema.sql": "CREATE TABLE public.f15_probe (id integer PRIMARY KEY);\n",
                "data.sql": "COPY public.f15_probe (id) FROM stdin;\n7\n\\.\n"}
    def dump(name, path):
        recovery.run_checked([sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2])", str(path), payloads[name]])
    def restore(paths):
        args = command + ["--single-transaction"]
        for path in paths:
            args.extend(["-f", str(path)])
        recovery.run_checked(args)
    def verify():
        result = manifest()
        count = int(recovery.run_checked(command + ["-c", "SELECT count(*) FROM public.f15_probe WHERE id=7"]).strip())
        result["public_table_counts"] = {"synthetic": count}
        return result
    result = recovery.qualify_database(tmp_path, dump=dump, restore=restore, read_source=manifest,
                                       verify_restore=verify, expected_head="d0e1f2g3h4")
    assert result["status"] == "ORDINARY_DATABASE_PARITY_PASSED"
    with pytest.raises(recovery.RecoveryFailed, match="COMMAND_FAILED"):
        recovery.run_checked(command + ["--single-transaction", "-c", "CREATE TABLE public.f15_rollback (id int); INSERT INTO absent_f15_table VALUES (1);"])
    assert recovery.run_checked(command + ["-c", "SELECT to_regclass('public.f15_rollback') IS NULL"]).strip() == "t"
    recovery.run_checked(command + ["-c", "DROP TABLE public.f15_probe"])
