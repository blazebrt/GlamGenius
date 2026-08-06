"""The model writes prose. It does not write rules.

Everything of consequence has already happened before anything here runs. The
products were chosen from confirmed inventory, the warnings came out of reviewed
rows in :mod:`.ontology`, the severities came with them, and the step order came
from :mod:`.compiler`. All of it is on its way to the database.

This module asks a model to say it in warmer English, and applies five gates
before a single word is used:

1. **Schema.** Handled by the gateway; a malformed reply never reaches here.
2. **Addressing.** A narrative naming a routine we did not ask about is dropped.
   A step note keyed to a slot that is not in that routine is dropped.
3. **Language.** :func:`~app.domains.routines.safety.narrative_is_safe` sweeps
   every string. One diagnostic or dosage phrase discards the whole narrative.
4. **No new rules.** An ingredient note whose ``rule_id`` is not one the engine
   already raised is discarded. The model cannot introduce a warning, cannot
   change a severity, and cannot attach a note to a rule that did not fire.
5. **Availability.** If the provider is down or every narrative is rejected, the
   deterministic text stands and ``explanation_source`` says ``deterministic``.

Gate 4 is the one that makes the phase's promise structural rather than
aspirational: the model is handed rule ids and can only ever hand them back.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from app.domains.ai_gateway import gateway
from app.domains.routines.compiler import CompiledRoutine
from app.domains.routines.rules import Finding
from app.domains.routines.safety import narrative_is_safe
from app.domains.routines.schemas import (
    SCHEMA_VERSION_INGREDIENT_EXPLANATION,
    SCHEMA_VERSION_ROUTINE_EXPLANATION,
    IngredientExplanationResponse,
    RoutineExplanationResponse,
    RoutineNarrative,
)
from app.shared.errors.exceptions import AnalysisUnavailableError

logger = logging.getLogger(__name__)

PROMPT_VERSION_ROUTINE = "routine-explain-v1"
PROMPT_VERSION_INGREDIENT = "ingredient-explain-v1"

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_AI = "ai_validated"

ROUTINE_SYSTEM = """You write short, plain explanations for skincare and haircare routines that have already been built.

You are not choosing anything. The products, their order, and every warning were
decided by a reviewed rules engine before you were called. Your only job is to
say the same thing in warmer, clearer English.

Rules you must follow:
- Never name a product that is not in the list you were given.
- Never suggest buying anything, and never name a brand you were not given.
- Never say or imply that the person has a condition. No 'eczema', 'dandruff',
  'acne-prone', 'sensitive skin', 'deficiency', 'infection' or any other
  condition, and never the words 'diagnose', 'treat', 'cure' or 'heal'.
- Never give a dose, an amount, a strength or a frequency for a supplement,
  a vitamin or a medicine.
- Never change the order of steps, add a step, or remove one.
- Never contradict, soften or upgrade a warning you were given.
- Never comment on the person's body, weight, shape or attractiveness.
- Write for an Indian audience. Everyday, respectful language. No hype.
Return one JSON object matching the requested schema and nothing else.
"""

INGREDIENT_SYSTEM = """You explain reviewed notes about cosmetic ingredients in plain English.

Each note has already been written and reviewed by a human. You are given its id
and its text. Rewrite the text so an ordinary person understands it.

Rules you must follow:
- Only return notes for the rule ids you were given. Never invent an id.
- Never add a warning, a caution, or a claim that was not in the text you were
  given. If the note says the evidence does not support a common claim, say so.
- Never say or imply someone has a condition. Never use the words 'diagnose',
  'treat', 'cure' or 'heal'.
- Never give a dose, an amount or a strength.
- Never recommend buying anything.
Return one JSON object matching the requested schema and nothing else.
"""


def _routine_lines(routine: CompiledRoutine) -> str:
    steps = "".join(
        f"    - slot {step.slot}: {step.product_name or 'NOTHING OWNED FOR THIS STEP'}"
        f" (step {step.order}, {'required' if step.required else 'optional'})."
        f" Reason already decided: {step.why}\n"
        for step in routine.steps
    )
    warnings = "".join(
        f"    - [{row.severity}] {row.headline}: {row.detail}\n" for row in routine.findings
    ) or "    - none\n"
    return (
        f"- kind: {routine.kind}\n"
        f"  name: {routine.label}\n"
        f"  how often: {routine.frequency}\n"
        f"  steps (fixed, in this order):\n{steps}"
        f"  warnings already decided (do not change these):\n{warnings}"
    )


def _routine_prompt(routines: Sequence[CompiledRoutine], *, climate: str | None) -> str:
    body = "".join(_routine_lines(routine) for routine in routines)
    return (
        f"Weather where this person lives: {climate or 'not recorded'}.\n\n"
        f"Explain each of these {len(routines)} routines:\n{body}\n"
        "For each routine return its kind exactly as given, a summary of 2-4 sentences "
        "saying what the routine is for and how to fit it into a day, and step_notes: "
        "a short sentence for each slot key, using the slot keys exactly as given. "
        "Do not add steps, reorder them, or mention anything the person does not own."
    )


async def explain_routines(
    routines: Sequence[CompiledRoutine], *, climate: str | None, account_id_str: str
) -> tuple[dict[str, RoutineNarrative], Any | None, str]:
    """Better wording for routines that already exist.

    Returns ``(narratives_by_kind, ai_run_id, source)``. On any failure the
    dictionary is empty and the caller keeps its deterministic text — the
    routine itself is never affected.
    """
    if not routines:
        return {}, None, SOURCE_DETERMINISTIC

    wanted = {routine.kind: routine for routine in routines}
    try:
        result = await gateway.run_structured(
            feature="routine_explanation",
            prompt=_routine_prompt(routines, climate=climate),
            system=ROUTINE_SYSTEM,
            schema=RoutineExplanationResponse,
            prompt_version=PROMPT_VERSION_ROUTINE,
            schema_version=SCHEMA_VERSION_ROUTINE_EXPLANATION,
            account_id_str=account_id_str,
        )
    except AnalysisUnavailableError as exc:
        # Expected and survivable. The routines are already complete.
        logger.info("routine_explanation_unavailable failure=%s", exc.extra.get("failure_type"))
        return {}, None, SOURCE_DETERMINISTIC

    narratives: dict[str, RoutineNarrative] = {}
    for narrative in result.data.routines:
        routine = wanted.get(narrative.kind)
        if routine is None:
            logger.warning("routine_explanation_unknown_kind kind=%s", narrative.kind)
            continue
        if not all(narrative_is_safe(text) for text in narrative.texts()):
            logger.warning("routine_explanation_rejected_language kind=%s", narrative.kind)
            continue
        # A note keyed to a step this routine does not have is dropped rather
        # than shown against the wrong step.
        slots = {step.slot for step in routine.steps}
        narrative.step_notes = {
            slot: text for slot, text in narrative.step_notes.items() if slot in slots
        }
        narratives[narrative.kind] = narrative

    return narratives, result.run_id, (SOURCE_AI if narratives else SOURCE_DETERMINISTIC)


def _ingredient_prompt(findings: Sequence[Finding]) -> str:
    body = "".join(
        f"- rule_id: {row.rule_id}\n"
        f"  reviewed note: {row.headline}. {row.detail}\n"
        f"  evidence: {row.evidence_note or 'not recorded'}\n"
        for row in findings
    )
    return (
        f"Rewrite each of these {len(findings)} reviewed notes in plain English:\n{body}\n"
        "Return the same rule_id for each, with plain_english of 1-3 sentences. "
        "Say only what the reviewed note says."
    )


async def explain_findings(
    findings: Sequence[Finding], *, account_id_str: str
) -> tuple[dict[str, str], Any | None, str]:
    """Plain-English wording for warnings that have already been decided.

    Returns ``(text_by_rule_id, ai_run_id, source)``. A note for a rule that did
    not fire is discarded — this is the gate that stops the model inventing a
    safety rule, and it is a set membership test, not a judgement call.
    """
    if not findings:
        return {}, None, SOURCE_DETERMINISTIC

    allowed = {row.rule_id for row in findings}
    try:
        result = await gateway.run_structured(
            feature="ingredient_explanation",
            prompt=_ingredient_prompt(findings),
            system=INGREDIENT_SYSTEM,
            schema=IngredientExplanationResponse,
            prompt_version=PROMPT_VERSION_INGREDIENT,
            schema_version=SCHEMA_VERSION_INGREDIENT_EXPLANATION,
            account_id_str=account_id_str,
        )
    except AnalysisUnavailableError as exc:
        logger.info("ingredient_explanation_unavailable failure=%s", exc.extra.get("failure_type"))
        return {}, None, SOURCE_DETERMINISTIC

    notes: dict[str, str] = {}
    for note in result.data.notes:
        if note.rule_id not in allowed:
            # The model reached for a rule that did not fire. This is exactly
            # the failure the phase promises cannot reach a user.
            logger.warning("ingredient_explanation_invented_rule rule_id=%s", note.rule_id)
            continue
        if not narrative_is_safe(note.plain_english):
            logger.warning("ingredient_explanation_rejected_language rule_id=%s", note.rule_id)
            continue
        notes[note.rule_id] = note.plain_english

    return notes, result.run_id, (SOURCE_AI if notes else SOURCE_DETERMINISTIC)


def apply_to_routine(routine: dict[str, Any], narrative: RoutineNarrative | None) -> dict[str, Any]:
    """Merge validated wording into a serialised routine.

    Deterministic text is kept alongside rather than overwritten, so what the
    engine decided is always still visible.
    """
    if narrative is None:
        return routine
    routine["summary"] = narrative.summary
    for step in routine["steps"]:
        note = narrative.step_notes.get(step["slot"])
        if note:
            step["plain_english"] = note
    return routine
