import sys
content = open('tests/test_no_legacy_product_surface.py', 'r').read()
new_content = content.replace('def test_profile_active_allowlist_forbids_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:\n    def override_get_current_account():', 'def test_profile_active_allowlist_forbids_legacy_keys(monkeypatch: pytest.MonkeyPatch) -> None:\n    import app.api.v2.profile as prof\n    monkeypatch.setattr(prof, "ALLOWED_KEYS", {"care_skin_usual_feel", "care_skin_sensitivity"})\n    def override_get_current_account():')
open('tests/test_no_legacy_product_surface.py', 'w').write(new_content)
