"""Synthetic authority database on an explicitly local disposable test cluster."""
import os
from contextlib import contextmanager

from app import config
from app.domains.off.local_recovery import EXPECTED_HEAD, EXPECTED_TABLES, LOCAL_STORE_B_URL_ENV
from app.operations.recovery import run_checked
from sqlalchemy.engine import make_url


@contextmanager
def restored_store_b_fixture(monkeypatch):
    original = make_url(config.POSTGRES_URL)
    assert original.host in {'localhost', '127.0.0.1', '::1'} and 'test' in original.database
    target = original.set(database='f15_restored_store_b_test')
    admin = target.set(database='postgres', drivername='postgresql', password=None)
    url = target.set(drivername='postgresql', password=None)
    env = dict(os.environ, PGPASSWORD=original.password or '')

    def sql(query, *, database=url):
        return run_checked(['psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '--dbname',
                            database.render_as_string(hide_password=False), '-c', query], environment=env)

    sql('CREATE DATABASE f15_restored_store_b_test', database=admin)
    try:
        sql(';'.join(f'CREATE TABLE public."{t}"(id int)' for t in EXPECTED_TABLES if t != 'alembic_version') +
            f";CREATE TABLE public.alembic_version(version_num text);INSERT INTO public.alembic_version VALUES('{EXPECTED_HEAD}')")
        sql("CREATE ROLE f15_fixture_identity_reader LOGIN PASSWORD 'synthetic' NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;"
            "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO f15_fixture_identity_reader;"
            "GRANT USAGE ON SCHEMA public TO f15_fixture_identity_reader;"
            "GRANT SELECT ON public.alembic_version TO f15_fixture_identity_reader;"
            "REVOKE CREATE,TEMP ON DATABASE f15_restored_store_b_test FROM PUBLIC")
        reader = target.set(username='f15_fixture_identity_reader', password='synthetic')
        monkeypatch.setenv(LOCAL_STORE_B_URL_ENV, reader.render_as_string(hide_password=False))
        yield reader, sql
    finally:
        sql('DROP DATABASE f15_restored_store_b_test WITH (FORCE)', database=admin)
        sql('DROP OWNED BY f15_fixture_identity_reader; DROP ROLE f15_fixture_identity_reader', database=admin)
