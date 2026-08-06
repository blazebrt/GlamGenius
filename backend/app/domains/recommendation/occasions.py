"""The controlled occasion vocabulary for Phase 4.

Occasions are a closed set, exactly like the inventory categories and the
profile attribute registry. A free-text occasion would mean the deterministic
filter had nothing to filter on, and the only thing left deciding what you wear
would be a language model — which is the failure mode this phase exists to
avoid.

Each occasion carries:

* ``formality`` — a 1-5 integer used by the deterministic ranker. It is a
  property of the *event*, never a judgement about the person.
* ``dress_codes`` — what a user may legitimately choose for it, most common
  first. The first entry is the default when the user does not say.
* ``questions`` — only the follow-up questions that actually change the answer
  for this occasion. Asking about "preparation requirements" for a gym session
  wastes the user's time, so it is not asked.
* ``slots`` — which look slots are required and which are optional.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Look slots -------------------------------------------------------------
# A look is assembled slot by slot. Required slots must be filled from owned
# inventory or the look is not offered; optional slots may be left empty.
SLOT_CLOTHING = "clothing"
SLOT_SHOES = "shoes"
SLOT_ACCESSORIES = "accessories"
SLOT_PERFUME = "perfume"
SLOT_HAIR = "hair"
SLOT_GROOMING = "grooming"

ALL_SLOTS: tuple[str, ...] = (
    SLOT_CLOTHING, SLOT_SHOES, SLOT_ACCESSORIES, SLOT_PERFUME, SLOT_HAIR, SLOT_GROOMING,
)

# Which inventory category can fill each slot.
SLOT_CATEGORY: dict[str, str] = {
    SLOT_CLOTHING: "wardrobe",
    SLOT_SHOES: "shoes",
    SLOT_ACCESSORIES: "accessories",
    SLOT_PERFUME: "perfumes",
    SLOT_HAIR: "hair",
    SLOT_GROOMING: "beauty",
}

# --- Dress codes ------------------------------------------------------------
# Mapped to the same 1-5 formality scale as occasions so the two can be compared
# directly instead of through a lookup table of special cases.
DRESS_CODES: dict[str, int] = {
    "loungewear": 1,
    "athleisure": 1,
    "casual": 2,
    "smart_casual": 3,
    "business_casual": 4,
    "festive_traditional": 4,
    "business_formal": 5,
    "black_tie": 5,
}

# --- Follow-up questions ----------------------------------------------------
QUESTION_LABELS: dict[str, str] = {
    "date": "Which day is it?",
    "time_of_day": "Roughly what time?",
    "location": "Where is it?",
    "setting": "Indoors or outdoors?",
    "dress_code": "Is there a dress code?",
    "weather": "What is the weather likely to be?",
    "comfort_preference": "How comfortable do you need to be?",
    "preferred_item_ids": "Anything from your inventory you already want to wear?",
    "optional_budget": "Budget for anything you might need to buy (optional)?",
    "preparation_time": "How much time do you have to get ready?",
}

SETTINGS = ("indoor", "outdoor", "mixed")
TIMES_OF_DAY = ("morning", "afternoon", "evening", "night")
COMFORT_LEVELS = ("comfort_first", "balanced", "polish_first")
WEATHER_CONDITIONS = ("hot", "warm", "mild", "cool", "cold", "humid", "rainy", "windy")


@dataclass(frozen=True)
class Occasion:
    key: str
    label: str
    formality: int
    dress_codes: tuple[str, ...]
    questions: tuple[str, ...]
    required_slots: tuple[str, ...] = (SLOT_CLOTHING, SLOT_SHOES)
    optional_slots: tuple[str, ...] = (SLOT_ACCESSORIES, SLOT_PERFUME, SLOT_HAIR, SLOT_GROOMING)
    default_setting: str = "indoor"
    notes: str = ""


def _occasion(key: str, label: str, formality: int, dress_codes: tuple[str, ...], questions: tuple[str, ...], **kwargs) -> Occasion:
    return Occasion(key=key, label=label, formality=formality, dress_codes=dress_codes, questions=questions, **kwargs)


# The common question set for anything you leave the house dressed for.
_STANDARD = ("date", "time_of_day", "location", "setting", "dress_code", "weather", "comfort_preference", "preferred_item_ids", "optional_budget", "preparation_time")
_LIGHT = ("date", "time_of_day", "weather", "comfort_preference", "preferred_item_ids")


OCCASIONS: dict[str, Occasion] = {
    "everyday": _occasion(
        "everyday", "Everyday", 2, ("casual", "smart_casual", "athleisure"),
        ("date", "time_of_day", "setting", "weather", "comfort_preference", "preferred_item_ids"),
        notes="Day-to-day wear with no particular event.",
    ),
    "office": _occasion(
        "office", "Office", 4, ("business_casual", "smart_casual", "business_formal"),
        ("date", "setting", "dress_code", "weather", "comfort_preference", "preferred_item_ids", "preparation_time"),
        notes="A normal working day at your workplace.",
    ),
    "wedding": _occasion(
        "wedding", "Wedding", 5, ("festive_traditional", "business_formal", "black_tie"),
        _STANDARD, default_setting="mixed",
        notes="Indian weddings often run across several functions; answer for the one you are dressing for.",
    ),
    "festival": _occasion(
        "festival", "Festival", 4, ("festive_traditional", "smart_casual"),
        _STANDARD, default_setting="mixed",
        notes="Festive occasions where traditional wear is usually welcome.",
    ),
    "interview": _occasion(
        "interview", "Interview", 5, ("business_formal", "business_casual"),
        ("date", "time_of_day", "location", "setting", "dress_code", "weather", "preferred_item_ids", "preparation_time"),
        notes="Dressing to be taken seriously, with nothing distracting.",
    ),
    "date": _occasion(
        "date", "Date", 3, ("smart_casual", "casual", "business_casual"),
        _STANDARD, notes="An evening or daytime date.",
    ),
    "party": _occasion(
        "party", "Party", 3, ("smart_casual", "festive_traditional", "casual"),
        _STANDARD, notes="A social party or night out.",
    ),
    "conference": _occasion(
        "conference", "Conference", 4, ("business_casual", "smart_casual", "business_formal"),
        ("date", "location", "setting", "dress_code", "weather", "comfort_preference", "preferred_item_ids", "preparation_time"),
        notes="Long days on your feet, often in strong air conditioning.",
    ),
    "business_meeting": _occasion(
        "business_meeting", "Business meeting", 4, ("business_formal", "business_casual"),
        ("date", "time_of_day", "location", "setting", "dress_code", "weather", "preferred_item_ids", "preparation_time"),
        notes="A meeting where you represent yourself or your organisation.",
    ),
    "vacation": _occasion(
        "vacation", "Vacation", 2, ("casual", "smart_casual", "athleisure"),
        ("date", "location", "setting", "weather", "comfort_preference", "preferred_item_ids", "optional_budget"),
        default_setting="outdoor", notes="Relaxed wear for time away.",
    ),
    "travel": _occasion(
        "travel", "Travel day", 2, ("casual", "athleisure", "smart_casual"),
        ("date", "time_of_day", "weather", "comfort_preference", "preferred_item_ids"),
        default_setting="mixed", notes="Comfort on a travel day, with layers you can take off.",
    ),
    "photoshoot": _occasion(
        "photoshoot", "Photoshoot", 3, ("smart_casual", "festive_traditional", "business_casual"),
        _STANDARD, default_setting="mixed",
        notes="Clothes that read well on camera and hold their shape.",
    ),
    "birthday": _occasion(
        "birthday", "Birthday", 3, ("smart_casual", "casual", "festive_traditional"),
        _STANDARD, notes="Your own or someone else's birthday.",
    ),
    "college": _occasion(
        "college", "College", 2, ("casual", "smart_casual", "athleisure"),
        _LIGHT, notes="A regular day on campus.",
    ),
    "gym": _occasion(
        "gym", "Gym", 1, ("athleisure",), _LIGHT,
        required_slots=(SLOT_CLOTHING, SLOT_SHOES), optional_slots=(SLOT_ACCESSORIES, SLOT_GROOMING),
        notes="Training kit that moves with you.",
    ),
    "home": _occasion(
        "home", "At home", 1, ("loungewear", "casual", "athleisure"),
        ("date", "time_of_day", "weather", "comfort_preference", "preferred_item_ids"),
        required_slots=(SLOT_CLOTHING,), optional_slots=(SLOT_SHOES, SLOT_ACCESSORIES, SLOT_HAIR, SLOT_GROOMING),
        notes="Comfortable wear for a day at home.",
    ),
}

OCCASION_KEYS: tuple[str, ...] = tuple(OCCASIONS)


def get_occasion(key: str) -> Occasion:
    occasion = OCCASIONS.get(key)
    if occasion is None:
        raise ValueError(
            f"'{key}' is not an occasion we support. Choose one of: {', '.join(OCCASION_KEYS)}."
        )
    return occasion


def dress_code_formality(dress_code: str | None, occasion: Occasion) -> int:
    """The formality target for this request.

    An explicit dress code always wins — a user who was told "black tie" should
    not be talked out of it by the occasion's usual level.
    """
    if dress_code and dress_code in DRESS_CODES:
        return DRESS_CODES[dress_code]
    return occasion.formality


def questions_for(key: str) -> list[dict[str, object]]:
    """The follow-up questions worth asking for this occasion, in order."""
    occasion = get_occasion(key)
    options: dict[str, tuple[str, ...]] = {
        "setting": SETTINGS,
        "time_of_day": TIMES_OF_DAY,
        "comfort_preference": COMFORT_LEVELS,
        "weather": WEATHER_CONDITIONS,
        "dress_code": occasion.dress_codes,
    }
    return [
        {
            "key": question,
            "label": QUESTION_LABELS[question],
            "options": list(options.get(question, ())),
            "required": False,
        }
        for question in occasion.questions
    ]


def serialize_occasion(occasion: Occasion) -> dict[str, object]:
    return {
        "key": occasion.key,
        "label": occasion.label,
        "formality": occasion.formality,
        "dress_codes": list(occasion.dress_codes),
        "default_dress_code": occasion.dress_codes[0],
        "default_setting": occasion.default_setting,
        "required_slots": list(occasion.required_slots),
        "optional_slots": list(occasion.optional_slots),
        "questions": questions_for(occasion.key),
        "notes": occasion.notes,
    }


def catalogue() -> list[dict[str, object]]:
    return [serialize_occasion(occasion) for occasion in OCCASIONS.values()]
