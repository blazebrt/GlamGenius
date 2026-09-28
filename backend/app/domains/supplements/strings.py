"""The customer copy of the supplement surface, keyed.

Every sentence the Step 13 supplement code can put in front of a customer lives
here, under a stable key, and nowhere else (``LEGAL_RULES.md``: never hard-code
a user-facing string). Implementation modules resolve keys with ``text()``; a
static test fails if one of them grows its own customer prose.

What is *not* here, on purpose: operator-only log reason codes, status and
reason identifiers, schema names, internal exception text, the model-facing
transcription prompt, and the chemical names the form table shows (vocabulary,
not prose). The app renders its own keyed copy for the photo action
(``frontend/src/strings/supplements.ts``); the API returns states such as
``created`` and ``replayed`` rather than duplicating it.

Wording rules: state what is kept, never advise, never characterise a product,
never say how, when or with what to take anything.
"""
from __future__ import annotations

#: Bump whenever any sentence below changes, so a screenshot can be traced to
#: the exact wording in force.
SUPPLEMENT_COPY_VERSION = "supplement-copy.v1"

SUPPLEMENT_COPY: dict[str, str] = {
    # --- Professional boundary --------------------------------------------
    # Shown when nothing fired. It answers nothing either; it says what is kept.
    "supplement.boundary.none": (
        "We keep a record of the supplements you own: name, brand, the dates you added "
        "and what the label says."
    ),
    # The professional boundary in supplement terms.
    "supplement.boundary.professional": (
        "This is outside what GlamGenius can help with. We keep a record of the supplements "
        "you own and what their labels say. We are not able to advise on taking a supplement, "
        "or look at symptoms, conditions or medicines. Please talk to a doctor or pharmacist "
        "about this one."
    ),
    # What the supplement surface can do instead: records and labels, nothing about use.
    "supplement.boundary.can_help.recorded": "What supplements you recorded",
    "supplement.boundary.can_help.label": "What the package label says, as you recorded it",
    "supplement.boundary.can_help.shared_component": "Which of your products list the same component",
    "supplement.boundary.can_help.expiry": "Expiry dates you recorded",
    "supplement.boundary.can_help.unconfirmed": "Which label details still need your confirmation",
    # --- Reading a label from a photo -------------------------------------
    "supplement.photo.not_an_image": "That file is not a photo we can read.",
    "supplement.photo.request_reused": "This photo request was already used for something else.",
    "supplement.photo.no_label_details": (
        "We could not read any label details from that photo. You can try again, or add them yourself."
    ),
    # --- Label facts -------------------------------------------------------
    "supplement.label_fact.reserved_key": "This retry key is reserved.",
    "supplement.label_fact.name_required": (
        "A label fact needs its name. Send a new name, or leave the name out to keep the one recorded."
    ),
}

#: The boundary's alternatives, in the order shown.
CAN_HELP_WITH_KEYS: tuple[str, ...] = (
    "supplement.boundary.can_help.recorded",
    "supplement.boundary.can_help.label",
    "supplement.boundary.can_help.shared_component",
    "supplement.boundary.can_help.expiry",
    "supplement.boundary.can_help.unconfirmed",
)


def text(key: str) -> str:
    """The reviewed sentence for ``key``. A missing key is a bug, never a blank."""
    return SUPPLEMENT_COPY[key]


__all__ = ["CAN_HELP_WITH_KEYS", "SUPPLEMENT_COPY", "SUPPLEMENT_COPY_VERSION", "text"]
