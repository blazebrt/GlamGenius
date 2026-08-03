"""Versioned quiz question bank.

The schema version is bumped on any semantic change so old submissions remain
readable and comparable.
"""
from __future__ import annotations

from typing import Any, Dict, List

QUIZ_SCHEMA_VERSION = "quiz.v1"


QUESTIONS: List[Dict[str, Any]] = [
    {
        "id": "vibe",
        "prompt": "Which of these feels most like you on a good day?",
        "options": [
            {"value": "natural", "label": "Natural and unfussed"},
            {"value": "polished", "label": "Polished and put-together"},
            {"value": "trendy", "label": "Trendy and current"},
            {"value": "classic", "label": "Classic and timeless"},
            {"value": "festive", "label": "Festive and expressive"},
        ],
    },
    {
        "id": "occasion_focus",
        "prompt": "Where do you most want your look to shine?",
        "options": [
            {"value": "everyday", "label": "Everyday life"},
            {"value": "work", "label": "Work / studies"},
            {"value": "social", "label": "Meeting friends"},
            {"value": "special", "label": "Special occasions"},
        ],
    },
    {
        "id": "colour_energy",
        "prompt": "Which palette feels most like you?",
        "options": [
            {"value": "warm", "label": "Warm — terracotta, mustard, olive"},
            {"value": "cool", "label": "Cool — navy, forest, plum"},
            {"value": "bright", "label": "Bright — coral, emerald, cobalt"},
            {"value": "neutral", "label": "Neutral — cream, taupe, charcoal"},
        ],
    },
    {
        "id": "silhouette",
        "prompt": "Which silhouette do you reach for first?",
        "options": [
            {"value": "structured", "label": "Structured and defined"},
            {"value": "relaxed", "label": "Relaxed and easy"},
            {"value": "flowy", "label": "Flowy and layered"},
            {"value": "sharp", "label": "Sharp and minimal"},
        ],
    },
    {
        "id": "care_time",
        "prompt": "How much time do you have to get ready most days?",
        "options": [
            {"value": "under_10", "label": "Under 10 minutes"},
            {"value": "under_20", "label": "10–20 minutes"},
            {"value": "over_20", "label": "20+ minutes"},
        ],
    },
]


def all_question_ids() -> List[str]:
    return [q["id"] for q in QUESTIONS]
