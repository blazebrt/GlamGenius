"""Fail-closed orchestration of operator-owned backup/restore adapters.

Adapters own secure credentials and isolated destination setup. This module
never logs commands, SQL, stderr, object keys or row values. Tests use actual
local PostgreSQL commands; they are distinct from the recorded live drill.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARTIFACT_NAMES = ("roles.sql", "schema.sql", "data.sql")
ORDINARY_FIELDS = (
    "database", "schema_context", "alembic_heads", "public_tables", "public_table_counts",
    "auth_users", "storage_buckets", "storage_objects", "external_integrations",
    "off_schema_present", "later_label_report_resources_present", "extensions",
    "public_indexes", "public_constraints", "sorted_primary_id_sha256",
    "public_columns", "public_table_content_sha256", "public_sequences",
)


class RecoveryFailed(RuntimeError):
    """Only fixed failure labels cross this boundary."""


def run_checked(arguments: Sequence[str], *, environment: Mapping[str, str] | None = None,
                input_text: str | None = None, timeout: int = 600) -> str:
    """No shell, debug output or native error text, including on failed SQL."""
    try:
        result = subprocess.run(list(arguments), env=environment, input=input_text, text=True,
                                capture_output=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise RecoveryFailed("COMMAND_DID_NOT_COMPLETE") from None
    if result.returncode != 0:
        raise RecoveryFailed("COMMAND_FAILED")
    return result.stdout


def artifact(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RecoveryFailed("ARTIFACT_MISSING")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
            size += len(block)
    if size == 0:
        raise RecoveryFailed("ARTIFACT_EMPTY")
    return {"filename": path.name, "bytes": size, "sha256": digest.hexdigest()}


def assert_manifest_parity(source: Mapping[str, Any], restored: Mapping[str, Any]) -> None:
    for manifest in (source, restored):
        tables = manifest.get("public_tables")
        counts = manifest.get("public_table_counts")
        digests = manifest.get("public_table_content_sha256")
        columns = manifest.get("public_columns")
        sequences = manifest.get("public_sequences")
        if (not isinstance(tables, list) or not all(isinstance(t, str) for t in tables)
                or len(tables) != len(set(tables)) or not isinstance(counts, dict) or not isinstance(digests, dict)
                or set(tables) != counts.keys() or set(tables) != digests.keys()
                or any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts.values())
                or any(not isinstance(d, str) or re.fullmatch(r"[0-9a-f]{64}", d) is None for d in digests.values())
                or not isinstance(columns, list) or not isinstance(sequences, list)):
            raise RecoveryFailed("MANIFEST_MISMATCH")
        identities = set()
        column_tables = set()
        for column in columns:
            if (not isinstance(column, dict) or column.get("schema") != "public" or column.get("table") not in tables
                    or not isinstance(column.get("column"), str) or not isinstance(column.get("attnum"), int) or isinstance(column.get("attnum"), bool)
                    or column["attnum"] < 1 or (column["table"], column["attnum"]) in identities):
                raise RecoveryFailed("MANIFEST_MISMATCH")
            identities.add((column["table"], column["attnum"]))
            column_tables.add(column["table"])
        if column_tables != set(tables):
            raise RecoveryFailed("MANIFEST_MISMATCH")
        sequence_names = set()
        for sequence in sequences:
            if (not isinstance(sequence, dict) or sequence.get("schema") != "public"
                    or not isinstance(sequence.get("name"), str) or not sequence["name"]
                    or sequence["name"] in sequence_names
                    or not isinstance(sequence.get("last_value"), int) or isinstance(sequence.get("last_value"), bool)
                    or not isinstance(sequence.get("is_called"), bool)):
                raise RecoveryFailed("MANIFEST_MISMATCH")
            sequence_names.add(sequence["name"])
    for field in ORDINARY_FIELDS:
        if field not in source or field not in restored or source[field] != restored[field]:
            raise RecoveryFailed("MANIFEST_MISMATCH")


@dataclass(frozen=True)
class ByteRecovery:
    bytes: int
    sha256: str
    source_key_sha256: str
    source_bytes: int
    source_sha256: str


def assert_storage_proofs(source: Mapping[str, Any], proofs: Sequence[ByteRecovery]) -> None:
    """Compare recovered objects to an independently measured source manifest."""
    expected = source.get("storage_byte_manifest", [])
    count = source["storage_objects"]
    if not isinstance(expected, list) or len(expected) != count or len(proofs) != count:
        raise RecoveryFailed("PRODUCTION_OBJECT_BYTE_PROOF_REQUIRED")
    def valid_digest(value: Any) -> bool:
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None
    authority = {}
    for item in expected:
        if (not isinstance(item, dict) or set(item) != {"key_sha256", "bytes", "sha256"}
                or not valid_digest(item["key_sha256"]) or not valid_digest(item["sha256"])
                or not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] < 0
                or item["key_sha256"] in authority):
            raise RecoveryFailed("INVALID_BYTE_PROOF")
        authority[item["key_sha256"]] = item
    seen = set()
    for proof in proofs:
        if not isinstance(proof, ByteRecovery) or proof.source_key_sha256 in seen:
            raise RecoveryFailed("INVALID_BYTE_PROOF")
        seen.add(proof.source_key_sha256)
        item = authority.get(proof.source_key_sha256)
        if (item is None or not valid_digest(proof.sha256) or not valid_digest(proof.source_sha256)
                or not isinstance(proof.bytes, int) or isinstance(proof.bytes, bool)
                or not isinstance(proof.source_bytes, int) or isinstance(proof.source_bytes, bool)
                or proof.bytes != proof.source_bytes or proof.bytes != item["bytes"]
                or proof.sha256 != proof.source_sha256 or proof.sha256 != item["sha256"]):
            raise RecoveryFailed("INVALID_BYTE_PROOF")
    if seen != authority.keys():
        raise RecoveryFailed("INVALID_BYTE_PROOF")


async def recover_object(storage: Any, key: str, protected_backup: Path) -> ByteRecovery:
    """Actual local object → backup → delete → restore → exact byte parity."""
    original = await storage.get(key)
    if original is None:
        raise RecoveryFailed("EMPTY_OBJECT_PROOF")
    with protected_backup.open("xb") as handle:
        handle.write(original)
    expected = artifact(protected_backup)
    await storage.delete(key)
    if await storage.exists(key):
        raise RecoveryFailed("OBJECT_DESTRUCTION_FAILED")
    backup = protected_backup.read_bytes()
    if len(backup) != expected["bytes"] or hashlib.sha256(backup).hexdigest() != expected["sha256"]:
        raise RecoveryFailed("OBJECT_BACKUP_CHANGED")
    await storage.put(key, backup, "application/octet-stream")
    actual = await storage.get(key)
    if actual != original:
        raise RecoveryFailed("OBJECT_RESTORE_MISMATCH")
    return ByteRecovery(expected["bytes"], expected["sha256"], hashlib.sha256(key.encode("utf-8")).hexdigest(),
                        expected["bytes"], expected["sha256"])


def qualify_database(
    workspace: Path, *, dump: Callable[[str, Path], None], restore: Callable[[tuple[Path, ...]], None],
    read_source: Callable[[], Mapping[str, Any]], verify_restore: Callable[[], Mapping[str, Any]] | None,
    expected_head: str, storage_proofs: Sequence[ByteRecovery] = (),
) -> dict[str, Any]:
    """Execute all required phases; a prior status file cannot skip verification.

This intentionally rejects raw CHECK hash changes. A minor-version difference
needs the separately recorded original-expression reparse proof, never a
blanket exclusion from comparison. Vault and hosted Auth authority stay outside
ordinary database parity.
"""
    started = datetime.now(UTC)
    clock = time.monotonic()
    source = deepcopy(dict(read_source()))
    if source.get("alembic_heads") != [expected_head]:
        raise RecoveryFailed("SOURCE_AUTHORITY_MISMATCH")
    if verify_restore is None:
        raise RecoveryFailed("VERIFICATION_REQUIRED")
    object_count = source.get("storage_objects")
    if isinstance(object_count, bool) or not isinstance(object_count, int) or object_count < 0:
        raise RecoveryFailed("STORAGE_COUNT_REQUIRED")
    if object_count and len(storage_proofs) != object_count:
        raise RecoveryFailed("PRODUCTION_OBJECT_BYTE_PROOF_REQUIRED")
    assert_storage_proofs(source, storage_proofs)
    paths = tuple(workspace / name for name in ARTIFACT_NAMES)
    if any(path.exists() for path in paths):
        raise RecoveryFailed("STALE_ARTIFACTS_REFUSED")
    records = []
    backup_clock = time.monotonic()
    for name, path in zip(ARTIFACT_NAMES, paths, strict=True):
        dump(name, path)
        records.append(artifact(path))
    backup_seconds = time.monotonic() - backup_clock
    restore_clock = time.monotonic()
    restore(paths)
    restore_seconds = time.monotonic() - restore_clock
    verification_clock = time.monotonic()
    restored = verify_restore()
    assert_manifest_parity(source, restored)
    after = dict(read_source())
    assert_manifest_parity(source, after)
    assert_storage_proofs(after, storage_proofs)
    if source.get("vault_rows") != after.get("vault_rows"):
        raise RecoveryFailed("SOURCE_CHANGED_DURING_BACKUP")
    if [artifact(path) for path in paths] != records:
        raise RecoveryFailed("ARTIFACT_CHANGED")
    return {
        "status": "ORDINARY_DATABASE_PARITY_PASSED", "alembic_head": expected_head,
        "started_utc": started.isoformat(), "finished_utc": datetime.now(UTC).isoformat(),
        "artifacts": records, "backup_seconds": backup_seconds, "restore_seconds": restore_seconds,
        "verification_seconds": time.monotonic() - verification_clock,
        "total_seconds": time.monotonic() - clock,
        "storage_proofs": [asdict(proof) for proof in storage_proofs],
        "hosted_auth_authority_proven": False, "vault_credential_recovery_proven": False,
    }
