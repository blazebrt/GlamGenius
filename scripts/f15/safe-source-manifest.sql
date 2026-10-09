BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT jsonb_build_object(
  'observed_at_utc', to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'server_version', current_setting('server_version'),
  'database', current_database(),
  'schema_context', current_schema(),
  'connection_ssl', (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()),
  'alembic_heads', (SELECT jsonb_agg(version_num ORDER BY version_num) FROM public.alembic_version),
  'public_tables', (SELECT jsonb_agg(table_name ORDER BY table_name) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'),
  'public_table_counts', (
    SELECT jsonb_object_agg(table_name,
      ((xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', table_schema, table_name), false, true, '')))[1]::text)::bigint
      ORDER BY table_name)
    FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  ),
  'auth_users', (SELECT count(*) FROM auth.users),
  'storage_buckets', (SELECT count(*) FROM storage.buckets),
  'storage_objects', (SELECT count(*) FROM storage.objects),
  'vault_rows', (SELECT count(*) FROM vault.secrets),
  'external_integrations', (SELECT count(*) FROM public.external_integrations),
  'off_schema_present', EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'off_data'),
  'later_label_report_resources_present', to_regclass('public.label_report_resources') IS NOT NULL,
  'extensions', (
    SELECT jsonb_agg(jsonb_build_object('name', e.extname, 'version', e.extversion, 'schema', n.nspname) ORDER BY e.extname)
    FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace
  ),
  'public_indexes', (
    SELECT jsonb_agg(jsonb_build_object('table', t.relname, 'name', x.relname, 'valid', i.indisvalid,
      'ready', i.indisready, 'definition_sha256', encode(extensions.digest(convert_to(pg_get_indexdef(i.indexrelid), 'UTF8'), 'sha256'), 'hex')) ORDER BY t.relname, x.relname)
    FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_class x ON x.oid = i.indexrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public'
  ),
  'public_constraints', (
    SELECT jsonb_agg(jsonb_build_object('table', t.relname, 'name', c.conname, 'type', c.contype,
      'validated', c.convalidated, 'definition_sha256', encode(extensions.digest(convert_to(pg_get_constraintdef(c.oid), 'UTF8'), 'sha256'), 'hex')) ORDER BY t.relname, c.conname)
    FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public'
  ),
  'sorted_primary_id_sha256', jsonb_build_object(
    'accounts', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.accounts),
    'external_integrations', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.external_integrations),
    'inventory_items', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.inventory_items),
    'invites', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.invites),
    'media_assets', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.media_assets),
    'product_records', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.product_records),
    'scan_devices', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.scan_devices),
    'scans', (SELECT encode(extensions.digest(convert_to(coalesce(string_agg(id::text, E'\n' ORDER BY id::text COLLATE "C"), ''), 'UTF8'), 'sha256'), 'hex') FROM public.scans)
  )
) AS privacy_safe_manifest;
COMMIT;
