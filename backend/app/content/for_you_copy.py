"""The reviewed FOR YOU wording, keyed.

Step 8F deliberately stops at machine-readable keys. It proves that a decision
is *governed* — a reviewed rule, a reviewed reason, a named openable source —
but it says nothing about whether the sentence a customer would read has been
reviewed. Those are two different approvals, and Step 8K is the first surface
where the second one matters.

So this catalogue is a **second presentation gate**, and it fails closed. A key
with no reviewed sentence here means the customer sees no verdict at all, even
when every governed layer upstream said yes. A release can be authored,
approved and activated without anybody having written the customer wording for
its reason; that must not be the moment a shopper is shown a decision nobody
proofread.

**This module resolves keys and does nothing else.** It never sees a signal, a
claim, an evidence strength, an ingredient, a policy or a profile. It cannot
map a signal to an action, because it is never given one. That is enforced by
static test, not just by intention.

Wording rules live in ``LEGAL_RULES.md``. Every sentence below states a fact or
an absence: none characterises a product, none advises the person, and none
claims anything about a body.
"""

from __future__ import annotations

#: Bump this whenever any sentence or label below changes.
#:
#: It travels in every successful response, so a screenshot of a decision can
#: always be traced to the exact wording that was in force. A silent copy edit
#: with a stable version would make that impossible.
FOR_YOU_COPY_VERSION = "for-you-copy.v1"

#: Display labels for the verdict keys Step 8F emits.
#:
#: Keyed by the *verdict key*, never by a signal. This table cannot express
#: "supporting means buy": it never receives a signal, and the action was
#: already decided by a reviewed Step 8E policy long before any of this is
#: consulted. These are three words on a screen, nothing more.
FOR_YOU_VERDICT_COPY: dict[str, str] = {
    "for_you.verdict.buy": "BUY",
    "for_you.verdict.wait": "WAIT",
    "for_you.verdict.skip": "SKIP",
}

#: The reviewed sentence for each reason key.
#:
#: Two kinds of entry, deliberately in one table because both are resolved the
#: same way: the *product* reasons a reviewed release names, and the
#: *structural* sentences explaining why there is no decision to show.
FOR_YOU_REASON_COPY: dict[str, str] = {
    # --- Reviewed product reasons -------------------------------------------
    # Step 8I's reviewed reason for petrolatum on dry skin. It reports what
    # dermatologist guidance lists, and claims nothing about this person's
    # skin, this product's effect, or what anybody should do.
    #
    # The knowledge pack is NOT imported here — capture and copy stay free of
    # it, and the active Step 8H release is the runtime authority. The Step 8K
    # test imports the pack and asserts this sentence matches its reviewed
    # intent exactly, which pins the wording without a runtime dependency.
    "for_you.skin_care.petrolatum.dry_skin.dermatologist_guidance": (
        "For dry skin, dermatologist guidance includes petrolatum among ingredients to "
        "look for in a cream or ointment."
    ),
    # --- Structural absences ------------------------------------------------
    # Each says what is missing and, where the person can act, what would fix
    # it. None of them characterises the product: not having a decision about
    # something is not a judgement of it.
    "for_you.not_enough.personal_context": (
        "Add the missing skin details in your profile to get your FOR YOU result."
    ),
    "for_you.not_enough.formula": (
        "We couldn't interpret this ingredient list well enough to make a FOR YOU decision."
    ),
    "for_you.not_enough.semantic_mapping": (
        "We don't have enough reviewed ingredient knowledge for this formula yet."
    ),
    "for_you.not_enough.decision_policy": (
        "We don't have a reviewed decision for this exact formula and context yet."
    ),
    "for_you.not_enough.explanation": (
        "We don't have a fully reviewed explanation and source for this decision yet."
    ),
    "for_you.not_enough.copy": (
        "We haven't finished reviewing the wording for this result yet."
    ),
    # --- States only this API can be in -------------------------------------
    "for_you.not_enough.confirmed_pack": (
        "Confirm the ingredient label on the product in your hand before we personalize "
        "this result."
    ),
    "for_you.not_enough.pack_category": (
        "This FOR YOU check currently works with confirmed skin-care labels."
    ),
}

#: The three reason keys this API owns, as opposed to the ones Step 8F emits.
REASON_KEY_CONFIRMED_PACK = "for_you.not_enough.confirmed_pack"
REASON_KEY_PACK_CATEGORY = "for_you.not_enough.pack_category"
REASON_KEY_NO_COPY = "for_you.not_enough.copy"


def verdict_text(verdict_key: str | None) -> str | None:
    """The reviewed display label for a verdict key, or nothing.

    ``None`` means "no reviewed label", which the caller must treat as a reason
    to withhold the decision — never as a reason to invent one or to fall back
    on the key itself.
    """
    if verdict_key is None:
        return None
    return FOR_YOU_VERDICT_COPY.get(verdict_key)


def reason_text(reason_key: str | None) -> str | None:
    """The reviewed sentence for a reason key, or nothing.

    Exact lookup. No normalisation, no prefix matching, no fallback to a
    generic sentence: a near-miss key is an unreviewed key, and answering it
    with somebody else's approved sentence would be the exact failure this
    catalogue exists to prevent.
    """
    if reason_key is None:
        return None
    return FOR_YOU_REASON_COPY.get(reason_key)


__all__ = [
    "FOR_YOU_COPY_VERSION",
    "FOR_YOU_REASON_COPY",
    "FOR_YOU_VERDICT_COPY",
    "REASON_KEY_CONFIRMED_PACK",
    "REASON_KEY_NO_COPY",
    "REASON_KEY_PACK_CATEGORY",
    "reason_text",
    "verdict_text",
]
