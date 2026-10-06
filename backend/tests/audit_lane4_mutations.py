"""Explicit opt-in runtime mutants for the F04 regression proofs.

Run pytest with -p tests.audit_lane4_mutations --lane4-mutant NAME. Never loaded
by the normal suite. Each mutant alters the real boundary in-process, with no
source/history rewrite. A kill must be a regression assertion, not a setup or
syntax/import failure. No production module reads this option.
"""
from __future__ import annotations

import inspect

import pytest


def pytest_addoption(parser):
    parser.addoption("--lane4-mutant", default=None)


def _replace(monkeypatch, module, name, old, new):
    source = inspect.getsource(getattr(module, name))
    source = source[source.index("async def " if "async def " in source else "def "):]
    assert old in source, (name, old)
    namespace = dict(vars(module))
    exec(compile(source.replace(old, new, 1), f"<lane4-mutant-{name}>", "exec"), namespace)
    monkeypatch.setattr(module, name, namespace[name])
    return namespace[name]


@pytest.fixture(autouse=True)
def _inject_lane4_mutant(request, monkeypatch):
    from app.api.v2 import product as route
    from app.domains.product import report_policy as policy
    from app.domains.product import service
    from app.shared.validation import media
    mutant = request.config.getoption("--lane4-mutant")
    async def nothing(*args, **kwargs):
        return None
    if mutant == "quota_removed":
        monkeypatch.setattr(policy, "admit_report", nothing)
    elif mutant == "quota_after_storage":
        call = "    await report_policy.admit_report(session, device_id=device_id, account_id=account_id, photo_bytes=size)\n"
        source = inspect.getsource(service.file_label_error_report)
        assert call in source
        source = source.replace(call, "", 1).replace("    row = LabelErrorReport(", call + "    row = LabelErrorReport(", 1)
        namespace = dict(vars(service))
        exec(compile(source, "<lane4-mutant-quota-after-storage>", "exec"), namespace)
        monkeypatch.setattr(service, "file_label_error_report", namespace["file_label_error_report"])
    elif mutant == "legacy_unknown_zero":
        _replace(monkeypatch, policy, "admit_report", "func.coalesce(LabelErrorReport.photo_byte_size, MAX_REPORT_PHOTO_BYTES)", "func.coalesce(LabelErrorReport.photo_byte_size, 0)")
    elif mutant == "replay_charged":
        _replace(monkeypatch, service, "file_label_error_report", "    if existing is not None:\n        return existing, False, None",
                 "    if existing is not None:\n        await report_policy.admit_report(session, device_id=device_id, account_id=account_id, photo_bytes=len(photo or b''))\n        return existing, False, None")
    elif mutant == "serialization_removed":
        monkeypatch.setattr(policy, "lock_report_quotas", nothing)
    elif mutant == "upload_ack_compensation_removed":
        _replace(monkeypatch, service, "file_label_error_report", "            await discard_unfiled_report_photo(key)", "            pass")
    elif mutant == "trust_declared_mime":
        _replace(monkeypatch, service, "file_label_error_report", "content_type, size = validate_upload(photo, photo_content_type)", "content_type, size = photo_content_type, len(photo)")
    elif mutant == "accept_magic_only":
        monkeypatch.setattr(media, "image_dimensions", lambda *args: (1, 1))
    elif mutant == "unbounded_photo_read":
        async def read(photo):
            data = await photo.read()
            if len(data) > route.MAX_REPORT_PHOTO_BYTES:
                from app.shared.errors.exceptions import MediaTooLargeError
                raise MediaTooLargeError("oversize")
            return data
        monkeypatch.setattr(route, "_read_report_photo", read)
    elif mutant == "commit_exception_guess_delete":
        original_endpoint = route.report_label_error
        replacement = _replace(monkeypatch, route, "report_label_error", "        if outcome is service.ReportCommitOutcome.COMMITTED:",
                               "        if written_key is not None:\n            await service.discard_unfiled_report_photo(written_key)\n        if outcome is service.ReportCommitOutcome.COMMITTED:")
        # FastAPI captures the endpoint in its compiled handler. Mutate that
        # exact function object, not only a copied router/dependant attribute.
        monkeypatch.setattr(original_endpoint, "__code__", replacement.__code__)
    elif mutant == "rejection_still_stores":
        original = policy.admit_report
        async def admit(session, **kwargs):
            try:
                await original(session, **kwargs)
            except policy.ReportQuotaExceeded:
                from app.domains.media.storage.factory import get_storage
                await get_storage().put("label-reports/rejected-mutation.png", b"rejected", "image/png")
                raise
        monkeypatch.setattr(policy, "admit_report", admit)
    elif mutant is not None:
        raise ValueError(f"Unknown Lane 4 mutant: {mutant}")
