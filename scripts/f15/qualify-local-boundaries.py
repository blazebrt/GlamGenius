import asyncio
import hashlib
import json
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from app.domains.media.storage.local import LocalFilesystemStorage
from app.domains.off import importer, store
from app.domains.off.export import DATA_FILE, LICENSE_FILE, LICENSE_NOTICE, export
from app.domains.off.models import OffBase, OffProduct
from app.operations import recovery
from app.shared.database import sql as store_b


async def main():
    progress = json.loads(Path('/reports/F15-Real-Drill-Progress.json').read_text())
    assert progress['credential_disposed'] is True and progress['ordinary_database_parity'] is True
    assert progress['source_before']['storage_objects'] == 0 and progress['source_before']['public_sequences'] == []
    from app.domains.off.local_recovery import restored_store_b_authority
    identity = await restored_store_b_authority()
    assert identity['transaction_read_only'] and identity['production_session'] is False
    store_b_id = identity['system_identifier']
    def forbidden_store_b_session():
        raise AssertionError('Production Store B session acquired by local Store A qualification')
    store_b.get_sessionmaker = forbidden_store_b_session
    with tempfile.TemporaryDirectory(prefix='f15-corrective-synthetic-') as temporary:
        base = Path(temporary)
        storage = LocalFilesystemStorage(base / 'objects')
        key = 'synthetic/f15-corrective-object.bin'
        payload = bytes(range(256)) * 41 + b'F15 corrective synthetic bytes\x00\xff'
        source_object = {'key_sha256': hashlib.sha256(key.encode()).hexdigest(), 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
        await storage.put(key, payload, 'application/octet-stream')
        proof = await recovery.recover_object(storage, key, base / 'object.backup')
        recovery.assert_storage_proofs({'storage_objects': 1, 'storage_byte_manifest': [source_object]}, [proof])
        assert await storage.get(key) == payload
        await storage.delete(key)
        (base / 'object.backup').unlink()
        await store.create_off_schema()
        async with store.get_off_engine().begin() as connection:
            store_a_id = (await connection.execute(text('SELECT system_identifier::text FROM pg_control_system()'))).scalar_one()
            assert store_a_id != store_b_id
            await connection.execute(text('TRUNCATE off_data.off_products'))
        async with store.get_off_sessionmaker()() as session, session.begin():
            session.add_all([
                OffProduct(barcode='8900000000001', product_name='Synthetic α', brands='Fixture', nutriments={'sugars_100g': 2.5}, fetched_at=datetime.now(UTC)),
                OffProduct(barcode='8900000000002', product_name='Synthetic β', categories_hierarchy=['en:foods'], countries_tags=['en:india'], fetched_at=datetime.now(UTC)),
            ])
        original = await export(base / 'before')
        async with store.get_off_engine().begin() as connection:
            await connection.run_sync(OffBase.metadata.drop_all)
        async with store.get_off_engine().connect() as connection:
            assert (await connection.execute(text("SELECT to_regclass('off_data.off_products')"))).scalar() is None
        await store.create_off_schema()
        imported = await importer.import_export(base / 'before')
        restored = await export(base / 'after')
        assert original['sha256'] == restored['sha256'] == imported['sha256']
        assert original['record_count'] == restored['record_count'] == 2
        assert (base / 'before' / DATA_FILE).read_bytes() == (base / 'after' / DATA_FILE).read_bytes()
        assert (base / 'after' / LICENSE_FILE).read_text() == LICENSE_NOTICE
        async with store.get_off_sessionmaker()() as session:
            products = (await session.execute(select(OffProduct))).scalars().all()
            assert len(products) == 2 and all(p.fetched_at is None for p in products)
        await store.dispose_off_engine()
        synthetic_only = progress.get('synthetic_only', False)
        result = {'status': 'PASS', 'synthetic_only': synthetic_only, 'completed_at_utc': datetime.now(UTC).isoformat(),
                  'production_storage_objects': None if synthetic_only else 0,
                  'production_storage_object_bytes': 'Not measured: synthetic test only' if synthetic_only else 'N/A: actual production source contained zero objects',
                  'synthetic_storage': {'independent_source': source_object, 'recovery_proof': asdict(proof), 'exact_source_binding': True, 'actual_delete_restore_byte_parity': True},
                  'local_store_a': {'store_b_restored_cluster_authority': identity, 'store_a_system_identifier': store_a_id,
                                    'physically_distinct_clusters': True, 'record_count': 2, 'sha256': original['sha256'],
                                    'export_destroy_recreate_import_parity': True, 'fetched_at': 'NULL/unknown', 'license': original['license'],
                                    'attribution': original['attribution'], 'production_store_b_session_acquisition_forbidden': True, 'local_store_b_lookup_read_only': imported['local_store_b_authority']['transaction_read_only'], 'hosted_store_a_changed': False},
                  'synthetic_workspace_cleanup': 'TemporaryDirectory exits before report is written'}
    result['synthetic_workspace_absent'] = not base.exists()
    assert result['synthetic_workspace_absent']
    Path('/reports/Local-Boundary-Qualification.json').write_text(json.dumps(result, indent=2) + '\n')
    print('Corrective local Storage bytes and physically separate Store A recovery passed.', flush=True)


asyncio.run(main())
