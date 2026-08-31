from app.domains.beta_access.entitlements import beta_entitlement_matrix
from app.domains.family.service import family_offer


def test_invited_beta_access_has_no_commercial_access_gate():
    matrix = beta_entitlement_matrix()

    assert matrix["access_model"] == "invite_beta"
    assert matrix["commercial_access_active"] is False
    assert all(item["available"] for item in matrix["items"])


def test_family_circle_offer_needs_a_real_outcome_conflict():
    assert family_offer({"ok"}) is False
    assert family_offer({"ok", "avoid"}) is True
