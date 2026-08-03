"""Stage 7 and 8: the model writes prose, and the prose is validated.

The decision has already been made before anything here runs. Items were chosen
from confirmed inventory, the verdict came out of the ROI arithmetic, and both
are already on their way to the database. This module asks a model to say it
better, and applies four gates before any of it is used:

1. **Schema.** Handled by the gateway; a malformed reply never gets here.
2. **Addressing.** A narrative is keyed by variant. One naming a variant we did
   not ask about is dropped.
3. **Language.** Anything matching the banned-term list is dropped — the same
   boundary Phase 1 drew around diagnostic wording, extended to body comment
   and to the "Money Wasted" framing this product does not use.
4. **Availability.** If the provider is unavailable or every narrative is
   rejected, the deterministic text stands and ``explanation_source`` says so.

Point 4 is the one that matters. A failed model call degrades the wording of a
real look; it can never produce a fabricated one, because the model is not the
thing that chose the look.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional, Sequence

from app.domains.ai_gateway import gateway
from app.domains.recommendation.candidates import OutfitCandidate
from app.domains.recommendation.context import StyleContext
from app.domains.recommendation.ranking import RankedLook
from app.domains.recommendation.roi import Candidate, ROIResult
from app.domains.recommendation.schemas import (
    SCHEMA_VERSION_LOOK_EXPLANATION, SCHEMA_VERSION_PURCHASE_EXPLANATION, SCHEMA_VERSION_SHOPPING_ITEM,
    ExtractedShoppingItem, LookExplanationResponse, PurchaseNarrative, narrative_is_safe,
)
from app.shared.errors.exceptions import AnalysisUnavailableError

logger = logging.getLogger(__name__)

PROMPT_VERSION_LOOKS = "style-explain-v1"
PROMPT_VERSION_SHOPPING_EXTRACT = "shopping-extract-v1"
PROMPT_VERSION_PURCHASE = "purchase-explain-v1"

SOURCE_DETERMINISTIC = "deterministic"
SOURCE_AI = "ai_validated"

LOOK_SYSTEM = """You write short, warm explanations for outfit choices that have already been made.

You are not choosing anything. The clothes, shoes and accessories are fixed and
were selected from what this person owns. Your only job is to explain, in plain
English, why the combination works for the occasion.

Rules you must follow:
- Never name an item that is not in the list you were given.
- Never suggest buying anything.
- Never comment on the person's body, weight, shape, size or attractiveness.
- Never use the words 'flattering', 'slimming', 'hide', 'problem area' or 'body type'.
- Never give medical, diagnostic or treatment advice.
- Never claim a trend, a brand's reputation, or anything you were not told.
- Write for an Indian audience. Everyday, respectful language. No hype.
Return one JSON object matching the requested schema and nothing else.
"""

SHOPPING_SYSTEM = """You read product details off a screenshot or a photo of a single item.

Report only what is visibly written or plainly visible. Rules:
- Never invent a brand, a price, a size or a fabric. Omit what you cannot see.
- Only report a price if it is printed in the image.
- Never recommend whether to buy it. That decision is made elsewhere.
- Never comment on a person, a body, or health.
- Use one of these categories: wardrobe, shoes, accessories, beauty, hair, perfumes, supplements.
Confidence describes the whole extraction and must be honest.
Return one JSON object matching the requested schema and nothing else.
"""

PURCHASE_SYSTEM = """You explain a shopping decision that has already been calculated.

The verdict and every number were produced by a deterministic model before you
were called. You must not change, question or restate the verdict differently.
Explain what the numbers mean in plain English so the person can act on it.

Rules you must follow:
- Never contradict the verdict you were given.
- Never invent a number, a price, or an item the person owns.
- Never comment on the person's body, weight, shape or attractiveness.
- Never use the phrase 'money wasted' or any shaming framing about spending.
- Never give medical or diagnostic advice.
Return one JSON object matching the requested schema and nothing else.
"""


def _look_lines(look: RankedLook) -> str:
    candidate: OutfitCandidate = look.candidate
    owned = "; ".join(
        f"{row.item.display_name} ({row.item.category}"
        + (f", {row.item.details.get('colour')}" if row.item.details.get("colour") else "")
        + ")"
        for row in candidate.owned_items()
    ) or "nothing"
    optional = "; ".join(add.description for add in candidate.optional_additions) or "none"
    return (
        f"- variant: {look.variant}\n"
        f"  intent: {look.title}\n"
        f"  owned items (these are fixed): {owned}\n"
        f"  optional additions the person does NOT own: {optional}\n"
        f"  computed weather note: {look.weather_note}\n"
        f"  computed dress code note: {look.dress_code_note}\n"
    )


def _look_prompt(looks: Sequence[RankedLook], context: StyleContext) -> str:
    body = "".join(_look_lines(look) for look in looks)
    style = context.confirmed_attributes.get("preferred_style") or "not recorded"
    return (
        f"Occasion: {context.occasion.label}. Dress code: {context.dress_code}. "
        f"Setting: {context.setting}. Weather: {context.weather or 'not given'}. "
        f"Comfort preference: {context.comfort_preference or 'not given'}. "
        f"The person's recorded style preference: {style}.\n\n"
        f"Explain each of these {len(looks)} looks:\n{body}\n"
        "For every look return why_it_works (2-4 sentences), weather_note, dress_code_note "
        "and up to four short preparation_steps. Use only the items listed for that look."
    )


async def explain_looks(
    looks: Sequence[RankedLook], context: StyleContext, *, account_id_str: str
) -> tuple[Dict[str, Any], Optional[Any], str]:
    """Better wording for looks that already exist.

    Returns ``(narratives_by_variant, ai_run_id, source)``. On any failure the
    dictionary is empty and the caller keeps its deterministic text.
    """
    if not looks:
        return {}, None, SOURCE_DETERMINISTIC
    wanted = {look.variant for look in looks}
    try:
        result = await gateway.run_structured(
            feature="style_occasion_explanation",
            prompt=_look_prompt(looks, context),
            system=LOOK_SYSTEM,
            schema=LookExplanationResponse,
            prompt_version=PROMPT_VERSION_LOOKS,
            schema_version=SCHEMA_VERSION_LOOK_EXPLANATION,
            account_id_str=account_id_str,
        )
    except AnalysisUnavailableError as exc:
        # Expected and survivable. The looks are already complete.
        logger.info("style_explanation_unavailable failure=%s", exc.extra.get("failure_type"))
        return {}, None, SOURCE_DETERMINISTIC

    narratives: Dict[str, Any] = {}
    for narrative in result.data.looks:
        if narrative.variant not in wanted:
            logger.warning("style_explanation_unknown_variant variant=%s", narrative.variant)
            continue
        text = " ".join([narrative.why_it_works, narrative.weather_note, narrative.dress_code_note, *narrative.preparation_steps])
        if not narrative_is_safe(text):
            logger.warning("style_explanation_rejected_language variant=%s", narrative.variant)
            continue
        narratives[narrative.variant] = narrative
    source = SOURCE_AI if narratives else SOURCE_DETERMINISTIC
    return narratives, result.run_id, source


async def extract_shopping_item(image_bytes: bytes, *, account_id_str: str) -> tuple[ExtractedShoppingItem, Any, str, str, str]:
    """Read a product screenshot. Raises ``AnalysisUnavailableError`` on failure.

    This one is allowed to fail loudly: there is nothing to fall back to, because
    the user asked us to read an image and no image was read. Inventing an item
    to evaluate would be the worst possible fabrication.
    """
    result = await gateway.run_structured(
        feature="shopping_candidate_extract",
        prompt=(
            "Read this single product screenshot or item photo. Report the category, a short "
            "display_name, and only the brand, colour, size, fabric, fit, formality, occasion_tags, "
            "season_tags and price that are actually visible. Omit anything you cannot see. "
            "List anything you were unsure about in uncertain_fields."
        ),
        system=SHOPPING_SYSTEM,
        schema=ExtractedShoppingItem,
        prompt_version=PROMPT_VERSION_SHOPPING_EXTRACT,
        schema_version=SCHEMA_VERSION_SHOPPING_ITEM,
        account_id_str=account_id_str,
        image_base64=base64.b64encode(image_bytes).decode("ascii"),
    )
    return result.data, result.run_id, result.model, result.prompt_version, result.schema_version


def _purchase_prompt(result: ROIResult, candidate: Candidate) -> str:
    factors = "\n".join(
        f"- {factor.label}: value {factor.raw_value:.2f}, weight {factor.weight:.2f}. {factor.explanation}"
        for factor in result.factors
    )
    return (
        f"Item being considered: {candidate.display_name}"
        + (f" by {candidate.brand}" if candidate.brand else "")
        + f" ({candidate.category}).\n"
        f"Verdict already decided: {result.verdict.upper()}. "
        f"Appearance ROI score: {result.score:.2f} out of 1.00 ({result.version}).\n"
        f"New outfit combinations it would create: {result.new_combinations}.\n"
        f"Very similar things already owned: {len(result.duplicate_item_ids)}. "
        f"Other things owned that could do a similar job: {len(result.alternative_item_ids)}.\n"
        f"Factors:\n{factors}\n"
        f"Missing information: {'; '.join(result.missing_information) or 'none'}.\n\n"
        f"Write one summary of 3-5 sentences explaining why the answer is {result.verdict.upper()} "
        "and what the person should do next. Do not restate the verdict as anything else."
    )


# Words that would mean the summary is arguing with its own verdict.
_CONTRADICTIONS = {
    "buy": ("don't buy", "do not buy", "skip this", "you should skip", "not worth buying"),
    "wait": ("buy it now", "buy this now", "definitely buy"),
    "skip": ("you should buy", "worth buying", "go ahead and buy", "definitely buy"),
}


def purchase_summary_is_consistent(summary: str, verdict: str) -> bool:
    lowered = (summary or "").lower()
    return not any(phrase in lowered for phrase in _CONTRADICTIONS.get(verdict, ()))


async def explain_purchase(
    result: ROIResult, candidate: Candidate, *, account_id_str: str
) -> tuple[Optional[str], Optional[Any], str]:
    """Better wording for a verdict that is already decided.

    Returns ``(summary, ai_run_id, source)``. The summary is discarded if it is
    unsafe or if it argues with the verdict.
    """
    try:
        ai = await gateway.run_structured(
            feature="shopping_evaluation_explanation",
            prompt=_purchase_prompt(result, candidate),
            system=PURCHASE_SYSTEM,
            schema=PurchaseNarrative,
            prompt_version=PROMPT_VERSION_PURCHASE,
            schema_version=SCHEMA_VERSION_PURCHASE_EXPLANATION,
            account_id_str=account_id_str,
        )
    except AnalysisUnavailableError as exc:
        logger.info("purchase_explanation_unavailable failure=%s", exc.extra.get("failure_type"))
        return None, None, SOURCE_DETERMINISTIC

    summary = ai.data.summary
    if not narrative_is_safe(summary):
        logger.warning("purchase_explanation_rejected_language verdict=%s", result.verdict)
        return None, ai.run_id, SOURCE_DETERMINISTIC
    if not purchase_summary_is_consistent(summary, result.verdict):
        logger.warning("purchase_explanation_contradicted_verdict verdict=%s", result.verdict)
        return None, ai.run_id, SOURCE_DETERMINISTIC
    return summary, ai.run_id, SOURCE_AI
