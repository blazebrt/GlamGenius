"""Step 7B — reading a printed ingredient list, and everything it refuses to do.

The layer under test turns a transcribed ingredient string into ordered
candidate names and asks Step 7A what each one denotes. That is all it does, and
most of what follows proves the absence of the things it would be convenient to
add.

Four things shape every group below:

* **Only a top-level comma separates entries.** Semicolons, newlines, slashes,
  hyphens, ampersands and the word "and" all occur *inside* real INCI names, so
  splitting on them would shatter one ingredient into fragments that name
  nothing. A list that really uses one of those parses as a single entry and
  does not resolve — which is the intended failure.
* **Malformed means the whole list, not the tail.** Returning the well-formed
  prefix would hand a caller an analysis that looks complete and silently omits
  everything after the first unclosed bracket.
* **Step 7A is the only identity authority.** No Care ontology fallback, no
  fuzzy retry, no substring match, no model, no network. UNRESOLVED is an
  answer.
* **Nothing here is a verdict.** No score, grade, action, positive or negative,
  and printed order is never read as concentration.
"""
from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path

import pytest
from app.domains.evidence import authoring as evidence_authoring
from app.domains.evidence.enums import EvidenceStrength, SourceType
from app.domains.evidence.models import EvidenceClaim
from app.domains.formulas import parser as formula_parser
from app.domains.formulas.parser import (
    MAX_FORMULA_TOKENS,
    MAX_INGREDIENTS_TEXT_LENGTH,
    ParseStatus,
    parse_formula,
)
from app.domains.formulas.service import (
    FormulaIngredientResolution,
    FormulaResolution,
    resolve_formula,
)
from app.domains.substances import authoring as substance_authoring
from app.domains.substances.enums import EntityKind, NameNamespace, SubstanceStatus
from app.domains.substances.models import Substance
from app.domains.substances.service import MAX_BATCH_NAMES, ResolutionStatus
from app.shared.database import sql
from app.shared.database.sql import get_sessionmaker
from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
FORMULAS_DIR = BACKEND_ROOT / "app" / "domains" / "formulas"

VERIFIED = evidence_authoring.VerificationInput(
    source_opened=True,
    founder_verified_fact=True,
    claude_review_completed=True,
    codex_review_completed=True,
    independent_reviews_agree=True,
    adversarial_review_passed=True,
    unresolved_doubt=False,
)


def _code_only(body: str) -> str:
    """The module with docstrings, strings and comments removed.

    Several tests assert a word appears nowhere in this domain — ``score``,
    ``concentration``, ``benefit``. Those same words appear constantly in the
    prose explaining why the code refuses them, so a raw text scan would flag
    the explanation and force the documentation to be written around the test.
    """
    import io as _io
    import token as _token
    import tokenize as _tokenize

    kept: list[str] = []
    for tok in _tokenize.generate_tokens(_io.StringIO(body).readline):
        if tok.type in {_token.STRING, _token.COMMENT}:
            continue
        if tok.type == getattr(_token, "FSTRING_START", -1):
            continue
        kept.append(tok.string)
    return " ".join(kept)


def _name(text_value: str, *, namespace: str = NameNamespace.INCI.value, preferred: bool = False):
    return {
        "name": text_value,
        "namespace": namespace,
        "language_tag": "und",
        "is_preferred": preferred,
    }


async def _draft(
    session,
    *,
    substance_key: str,
    names: list[dict],
    entity_kind: str = EntityKind.DEFINED_SUBSTANCE.value,
    evidence_strength: str = EvidenceStrength.STRONG.value,
    source_type: str = SourceType.INGREDIENT_REFERENCE_DATABASE.value,
    source_url: str = "https://example.org/reference/entry",
) -> uuid.UUID:
    """Author one Step 7A identity draft through the real, narrow adapter."""
    result = await substance_authoring.create_identity_draft(
        session,
        substance_key=substance_key,
        entity_kind=entity_kind,
        names=names,
        summary=f"Names recorded for {substance_key}.",
        scope="Nomenclature only.",
        evidence_strength=evidence_strength,
        strength_rationale="A named reference work records this nomenclature directly.",
        source_title="Reference entry",
        source_publisher="Example Reference",
        source_type=source_type,
        source_url=source_url,
        license_or_use_note="Reproduced under the publisher's stated terms of use.",
        author="tester",
    )
    return uuid.UUID(result["claim_id"])


async def _published(session, **kwargs) -> uuid.UUID:
    """Author and walk the real evidence workflow all the way to published.

    Deliberately the full path — approve, record verification, publish — rather
    than a fixture that writes a published row directly. A shortcut here would
    stop the eligibility rules being exercised at all, and those rules are the
    thing standing between a reviewed identity and an invented one.
    """
    claim_id = await _draft(session, **kwargs)
    await evidence_authoring.approve(session, claim_id, reviewer="reviewer")
    await evidence_authoring.record_publication_verification(
        session, claim_id, verification=VERIFIED, actor="founder",
    )
    await evidence_authoring.publish(session, claim_id, publisher="founder")
    await session.commit()
    return claim_id


async def _publish_simple(session, key: str, printed: str, **kwargs) -> uuid.UUID:
    return await _published(
        session, substance_key=key, names=[_name(printed, preferred=True)], **kwargs
    )


# ---------------------------------------------------------------------------
# A. The parser — the §28 matrix, case by case
# ---------------------------------------------------------------------------
class TestParserMatrix:
    def test_a_plain_three_ingredient_list(self):
        result = parse_formula("Water, Niacinamide, Glycerin")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == ["Water", "Niacinamide", "Glycerin"]
        assert [t.position for t in result.tokens] == [1, 2, 3]

    def test_b_surrounding_whitespace_is_trimmed_order_kept(self):
        result = parse_formula(" Parfum , Limonene")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == ["Parfum", "Limonene"]

    def test_c_a_comma_inside_parentheses_is_not_a_separator(self):
        result = parse_formula("Parfum (Fragrance, Aroma), Limonene")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == ["Parfum (Fragrance, Aroma)", "Limonene"]

    def test_d_a_parenthetical_is_not_expanded_into_several_identities(self):
        result = parse_formula("Water (Aqua/Eau), Glycerin")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == ["Water (Aqua/Eau)", "Glycerin"]

    def test_e_a_slash_inside_a_polymer_name_survives(self):
        result = parse_formula("Acrylates/C10-30 Alkyl Acrylate Crosspolymer, Glycerin")
        assert [t.raw_name for t in result.tokens] == [
            "Acrylates/C10-30 Alkyl Acrylate Crosspolymer", "Glycerin",
        ]

    def test_f_a_hyphen_is_preserved(self):
        result = parse_formula("PEG-40 Hydrogenated Castor Oil, Water")
        assert [t.raw_name for t in result.tokens] == ["PEG-40 Hydrogenated Castor Oil", "Water"]

    def test_g_a_colour_index_group_is_one_entry(self):
        result = parse_formula("CI 77491/CI 77492/CI 77499, Mica")
        assert [t.raw_name for t in result.tokens] == ["CI 77491/CI 77492/CI 77499", "Mica"]

    def test_h_a_semicolon_is_not_guessed_as_a_separator(self):
        result = parse_formula("Water; Glycerin")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == ["Water; Glycerin"]

    def test_i_a_newline_is_not_guessed_as_a_separator(self):
        result = parse_formula("Water\nGlycerin")
        assert result.status is ParseStatus.PARSED
        assert len(result.tokens) == 1
        assert result.tokens[0].raw_name == "Water\nGlycerin"

    @pytest.mark.parametrize("text", [
        "Water,,Glycerin", ",Water", "Water,", "Water,   ,Glycerin",
        "Water, ,Glycerin", ",", ",,", "Water, Glycerin,",
    ])
    def test_jkl_an_empty_entry_is_malformed_never_dropped(self, text):
        result = parse_formula(text)
        assert result.status is ParseStatus.MALFORMED
        assert result.tokens == ()

    @pytest.mark.parametrize("text", [
        "Water (Aqua, Glycerin",
        "Water], Niacinamide",
        "Water (Aqua], Niacinamide",
        "Water ((Aqua), Glycerin",
        "Water [Aqua, Glycerin",
        "Water {Aqua, Glycerin",
        "Water (Aqua}, Glycerin",
        "Water )Aqua(, Glycerin",
        ")",
    ])
    def test_mn_malformed_grouping_fails_closed(self, text):
        result = parse_formula(text)
        assert result.status is ParseStatus.MALFORMED
        assert result.tokens == ()

    @pytest.mark.parametrize("text", ["", "   ", "\t", "\n", "  \t\n  ", None, 42, b"Water"])
    def test_o_nothing_to_read_is_empty(self, text):
        result = parse_formula(text)
        assert result.status is ParseStatus.EMPTY
        assert result.tokens == ()

    def test_p_an_oversized_string_is_refused_not_truncated(self):
        assert parse_formula("W" * MAX_INGREDIENTS_TEXT_LENGTH).status is ParseStatus.PARSED
        result = parse_formula("W" * (MAX_INGREDIENTS_TEXT_LENGTH + 1))
        assert result.status is ParseStatus.TOO_LONG
        assert result.tokens == ()

    def test_q_too_many_entries_is_refused_not_truncated(self):
        exactly = ",".join(f"Item{i}" for i in range(MAX_FORMULA_TOKENS))
        assert len(parse_formula(exactly).tokens) == MAX_FORMULA_TOKENS
        over = ",".join(f"Item{i}" for i in range(MAX_FORMULA_TOKENS + 1))
        result = parse_formula(over)
        assert result.status is ParseStatus.TOO_MANY_ITEMS
        assert result.tokens == ()


# ---------------------------------------------------------------------------
# B. The parser's boundaries, stated as their own guarantees
# ---------------------------------------------------------------------------
class TestParserGuarantees:
    def test_the_delimiter_is_only_the_comma(self):
        assert formula_parser.TOP_LEVEL_DELIMITER == ","

    @pytest.mark.parametrize("separator", [";", "/", "&", "+", "|", "\n", "\t", " and ", " AND "])
    def test_no_other_character_splits_a_list(self, separator):
        result = parse_formula(f"Water{separator}Glycerin")
        assert result.status is ParseStatus.PARSED
        assert len(result.tokens) == 1, f"{separator!r} was treated as a separator"

    @pytest.mark.parametrize("printed", [
        "Niacinamide 5%", "Sodium C14-16 Olefin Sulfonate", "Retinyl Palmitate",
        "Vitamin-E", "CI 77491", "Aqua/Water/Eau", "Butyrospermum Parkii (Shea) Butter",
        "Caprylic/Capric Triglyceride", "Tocopheryl Acetate", "sh-Oligopeptide-1",
    ])
    def test_internal_punctuation_and_casing_survive_exactly(self, printed):
        # Every name here is comma-free. A name whose own comma sits at top
        # level is a separate, documented case — see the locant test below.
        assert "," not in printed
        result = parse_formula(f"{printed}, Water")
        assert result.status is ParseStatus.PARSED
        assert result.tokens[0].raw_name == printed

    @pytest.mark.parametrize("printed", [
        "1,3-Butanediol", "1,2-Hexanediol", "2,6-Di-t-Butyl-4-Methylphenol",
    ])
    def test_a_locant_comma_splits_and_that_is_a_known_limitation(self, printed):
        """A real INCI name whose own comma is at top level gets split. Fail-safe.

        ``1,3-Butanediol`` prints a comma that is not inside any bracket, so the
        V1 rule — top-level comma is the delimiter — cannot tell it from the
        comma between two ingredients. It becomes ``1`` and ``3-Butanediol``.

        This is a **known and accepted** V1 limitation, recorded here so it is
        visible rather than latent. Three things make it the safe failure:

        * Neither fragment resolves, so no wrong identity is ever produced —
          the outcome is UNRESOLVED, which is this layer's honest answer.
        * The alternative is worse. Recognising ``digit,digit-`` as "inside a
          name" is exactly the pattern-guessing the parser refuses everywhere
          else, and it would mis-split the day a list legitimately prints
          ``Titanium Dioxide, 1,3-Butanediol`` against a name we guessed wrong.
        * The real fix is a reviewed identity claim recording the exact printed
          form, not a cleverer parser.

        What it does cost: the entry count and every position after it shift.
        A consumer must therefore never treat ``position`` as authoritative
        pack order — which it already must not, for separate reasons.
        """
        result = parse_formula(f"{printed}, Water")
        assert result.status is ParseStatus.PARSED
        assert len(result.tokens) == 3
        assert [t.raw_name for t in result.tokens] == [
            printed.split(",")[0], printed.split(",", 1)[1], "Water",
        ]

    async def test_a_split_locant_name_resolves_to_nothing(self, db_clean):
        """The fragments are meaningless, and meaningless is what they return."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
            await _publish_simple(session, "butanediol.1-3", "1,3-Butanediol")
        async with factory() as session:
            result = await resolve_formula(session, "1,3-Butanediol, Water")
        assert [r.status for r in result.ingredients] == [
            ResolutionStatus.UNRESOLVED,   # "1"
            ResolutionStatus.UNRESOLVED,   # "3-Butanediol"
            ResolutionStatus.RESOLVED,     # "Water"
        ]
        # The published identity exists and is reachable by its exact name —
        # it is only the *splitting* that prevents this formula reaching it.
        async with factory() as session:
            direct = await resolve_formula(session, "1,3-Butanediol")
        assert [r.raw_name for r in direct.ingredients] == ["1", "3-Butanediol"]

    def test_nested_grouping_is_tracked(self):
        result = parse_formula("Parfum (Fragrance (Aroma, Perfume), Linalool), Water")
        assert result.status is ParseStatus.PARSED
        assert [t.raw_name for t in result.tokens] == [
            "Parfum (Fragrance (Aroma, Perfume), Linalool)", "Water",
        ]

    @pytest.mark.parametrize("pair", ["()", "[]", "{}"])
    def test_every_grouping_pair_protects_its_commas(self, pair):
        opener, closer = pair
        result = parse_formula(f"Parfum {opener}Fragrance, Aroma{closer}, Water")
        assert [t.raw_name for t in result.tokens] == [
            f"Parfum {opener}Fragrance, Aroma{closer}", "Water",
        ]

    def test_bracketed_text_is_never_stripped_or_read(self):
        result = parse_formula("Water (Aqua/Eau)")
        assert result.tokens[0].raw_name == "Water (Aqua/Eau)"
        assert "Aqua" in result.tokens[0].raw_name

    def test_positions_are_one_based_and_contiguous(self):
        result = parse_formula(", ".join(f"Item{i}" for i in range(20)))
        assert [t.position for t in result.tokens] == list(range(1, 21))

    def test_a_failed_parse_never_returns_a_partial_prefix(self):
        """The well-formed head of a malformed list is not an answer."""
        result = parse_formula("Water, Glycerin, Niacinamide (Aqua, Parfum")
        assert result.status is ParseStatus.MALFORMED
        assert result.tokens == ()

    def test_the_token_ceiling_matches_step_7a_batch_ceiling(self):
        """A parsed formula must always fit exactly one batch resolution."""
        assert MAX_FORMULA_TOKENS == MAX_BATCH_NAMES

    def test_the_length_bound_matches_the_product_label_bound(self):
        """Anything the transcription schema accepted must be parseable here.

        Read from the product schema in the test rather than imported into the
        parser: importing it would couple this engine to the scan pipeline,
        which Step 7B must not touch.
        """
        from app.domains.product.extraction import ExtractedLabel

        field = ExtractedLabel.model_fields["ingredients_text"]
        bound = next(
            m.max_length for m in field.metadata if getattr(m, "max_length", None) is not None
        )
        assert MAX_INGREDIENTS_TEXT_LENGTH == bound == 4000

    def test_the_parser_is_pure(self):
        source = _code_only(inspect.getsource(parse_formula))
        for forbidden in ("await", "session", "random", "time", "datetime", "open("):
            assert forbidden not in source
        text = "Water, Niacinamide (Aqua, Eau), Glycerin"
        assert parse_formula(text) == parse_formula(text)

    def test_the_token_carries_no_interpretation(self):
        token = parse_formula("Water").tokens[0]
        for forbidden in ("concentration", "percentage", "percent", "amount", "function",
                          "benefit", "risk", "score", "grade", "rank"):
            assert not any(forbidden in field for field in vars(token)), forbidden


# ---------------------------------------------------------------------------
# C. Resolution against real published Step 7A identities
# ---------------------------------------------------------------------------
class TestFormulaResolution:
    async def test_a_all_three_resolve_in_printed_order(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
            await _publish_simple(session, "niacinamide", "Niacinamide")
            await _publish_simple(session, "glycerin", "Glycerin")
        async with factory() as session:
            result = await resolve_formula(session, "Water, Niacinamide, Glycerin")
        assert result.status is ParseStatus.PARSED
        assert [r.position for r in result.ingredients] == [1, 2, 3]
        assert [r.raw_name for r in result.ingredients] == ["Water", "Niacinamide", "Glycerin"]
        assert all(r.status is ResolutionStatus.RESOLVED for r in result.ingredients)
        assert [r.substance_key for r in result.ingredients] == [
            "water", "niacinamide", "glycerin",
        ]
        assert result.resolved_count == 3

    async def test_b_an_unknown_ingredient_is_unresolved_and_the_others_are_not(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
            await _publish_simple(session, "glycerin", "Glycerin")
        async with factory() as session:
            result = await resolve_formula(session, "Water, Mystery Ingredient, Glycerin")
        assert [r.status for r in result.ingredients] == [
            ResolutionStatus.RESOLVED, ResolutionStatus.UNRESOLVED, ResolutionStatus.RESOLVED,
        ]
        middle = result.ingredients[1]
        assert middle.substance_key is None
        assert middle.entity_kind is None
        assert middle.candidate_substance_keys == ()
        assert middle.raw_name == "Mystery Ingredient"

    async def test_c_an_ambiguous_name_stays_ambiguous(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
            await _publish_simple(session, "glycerin", "Glycerin")
            await _publish_simple(session, "entity.alpha", "Shared Name")
            await _publish_simple(session, "entity.beta", "Shared Name")
        async with factory() as session:
            result = await resolve_formula(session, "Water, Shared Name, Glycerin")
        middle = result.ingredients[1]
        assert middle.status is ResolutionStatus.AMBIGUOUS
        assert middle.substance_key is None
        assert middle.entity_kind is None
        assert middle.candidate_substance_keys == ("entity.alpha", "entity.beta")

    async def test_d_a_repeated_ingredient_appears_once_per_occurrence(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
            await _publish_simple(session, "glycerin", "Glycerin")
        async with factory() as session:
            result = await resolve_formula(session, "Water, Glycerin, Glycerin")
        assert len(result.ingredients) == 3
        assert [r.position for r in result.ingredients] == [1, 2, 3]
        assert [r.substance_key for r in result.ingredients] == ["water", "glycerin", "glycerin"]

    async def test_d2_many_duplicates_all_survive(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "glycerin", "Glycerin")
        async with factory() as session:
            result = await resolve_formula(session, ", ".join(["Glycerin"] * 10))
        assert len(result.ingredients) == 10
        assert [r.position for r in result.ingredients] == list(range(1, 11))
        assert all(r.substance_key == "glycerin" for r in result.ingredients)

    async def test_e_two_evidence_paths_to_one_entity_stay_resolved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "niacinamide", "Niacinamide")
            await _publish_simple(
                session, "niacinamide", "Niacinamide",
                source_type=SourceType.OFFICIAL_REGULATION.value,
                source_url="https://example.gov/register/entry",
            )
        async with factory() as session:
            result = await resolve_formula(session, "Niacinamide")
        row = result.ingredients[0]
        assert row.status is ResolutionStatus.RESOLVED
        assert row.substance_key == "niacinamide"
        assert row.candidate_substance_keys == ("niacinamide",)

    async def test_f_an_invalid_path_cannot_hide_a_valid_one(self, db_clean):
        """The Step 6B LIMIT-1 defect, reached through the formula layer."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "niacinamide", "Niacinamide")
            for index in range(5):
                await _draft(
                    session, substance_key=f"decoy.{index}",
                    names=[_name("Niacinamide", preferred=True)],
                )
            await session.commit()
        async with factory() as session:
            result = await resolve_formula(session, "Water, Niacinamide")
        assert result.ingredients[1].status is ResolutionStatus.RESOLVED
        assert result.ingredients[1].substance_key == "niacinamide"

    async def test_g_a_draft_only_name_is_unresolved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _draft(
                session, substance_key="glycerin", names=[_name("Glycerin", preferred=True)],
            )
            await session.commit()
        async with factory() as session:
            result = await resolve_formula(session, "Glycerin")
        assert result.ingredients[0].status is ResolutionStatus.UNRESOLVED

    async def test_h_a_retired_substance_is_unresolved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "glycerin", "Glycerin")
        async with factory() as session:
            substance = (await session.execute(select(Substance))).scalars().one()
            substance.status = SubstanceStatus.RETIRED.value
            await session.commit()
        async with factory() as session:
            result = await resolve_formula(session, "Glycerin")
        assert result.ingredients[0].status is ResolutionStatus.UNRESOLVED

    async def test_i_insufficient_evidence_does_not_establish_identity(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(
                session, "glycerin", "Glycerin",
                evidence_strength=EvidenceStrength.INSUFFICIENT.value,
            )
        async with factory() as session:
            result = await resolve_formula(session, "Glycerin")
        assert result.ingredients[0].status is ResolutionStatus.UNRESOLVED

    async def test_a_group_identity_is_preserved_not_expanded(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(
                session, "ceramides.group", "Ceramides",
                entity_kind=EntityKind.GROUP.value,
            )
        async with factory() as session:
            result = await resolve_formula(session, "Ceramides, Ceramide NP")
        group, member = result.ingredients
        assert group.status is ResolutionStatus.RESOLVED
        assert group.entity_kind == EntityKind.GROUP.value
        assert group.substance_key == "ceramides.group"
        # The group is not permission to decide which member was in the formula.
        assert member.status is ResolutionStatus.UNRESOLVED

    async def test_a_mixture_identity_is_preserved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(
                session, "supplied.blend", "Supplied Blend",
                entity_kind=EntityKind.MIXTURE.value,
            )
        async with factory() as session:
            result = await resolve_formula(session, "Supplied Blend")
        assert result.ingredients[0].entity_kind == EntityKind.MIXTURE.value

    @pytest.mark.parametrize("bad", [
        "Water,,Glycerin", "Water (Aqua, Glycerin", "   ", "Water,",
    ])
    async def test_a_failed_parse_returns_no_ingredients(self, db_clean, bad):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
        async with factory() as session:
            result = await resolve_formula(session, bad)
        assert not result.ok
        assert result.ingredients == ()
        assert result.resolved_count == 0

    async def test_an_oversized_formula_is_refused_not_partly_resolved(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
        async with factory() as session:
            over = ", ".join(["Water"] * (MAX_FORMULA_TOKENS + 1))
            result = await resolve_formula(session, over)
        assert result.status is ParseStatus.TOO_MANY_ITEMS
        assert result.ingredients == ()


# ---------------------------------------------------------------------------
# D. Exact tokens only — no substring, no fuzzy, no legacy fallback
# ---------------------------------------------------------------------------
class TestExactTokensOnly:
    @pytest.mark.parametrize("printed", [
        "Niacinamide 5%", "5% Niacinamide", "contains niacinamide",
        "niacinamide complex", "Niacinamide Solution", "Niacinamide (5%)",
        "Niacinamid", "Niacinamides", "NiacinamideX",
    ])
    async def test_a_near_miss_does_not_resolve(self, db_clean, printed):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "niacinamide", "Niacinamide")
        async with factory() as session:
            exact = await resolve_formula(session, "Niacinamide")
            assert exact.ingredients[0].status is ResolutionStatus.RESOLVED
            result = await resolve_formula(session, printed)
        assert result.ingredients[0].status is ResolutionStatus.UNRESOLVED, printed

    async def test_case_and_whitespace_variation_still_resolves(self, db_clean):
        """Step 7A's own conservative normalisation, reached through the formula."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "niacinamide", "Niacinamide")
        async with factory() as session:
            result = await resolve_formula(session, "NIACINAMIDE,   niacinamide  , Niacinamide")
        assert all(r.status is ResolutionStatus.RESOLVED for r in result.ingredients)
        assert {r.substance_key for r in result.ingredients} == {"niacinamide"}

    @pytest.mark.parametrize("legacy", [
        "Tocopheryl Acetate", "Tocopherol", "Vitamin E",
        "Ceramide NP", "Ceramide AP", "Ceramide EOP",
        "Hydrolyzed Keratin", "Hydrolyzed Silk", "Hydrolyzed Wheat Protein", "Keratin",
        "Peppermint Oil", "Menthol", "Vitamin B3", "Nicotinamide", "Glycerol",
    ])
    async def test_a_legacy_care_alias_is_not_a_canonical_identity(self, db_clean, legacy):
        """Seeing a name in a formula does not make the old family map canonical."""
        factory = get_sessionmaker()
        async with factory() as session:
            result = await resolve_formula(session, f"Water, {legacy}, Glycerin")
        assert result.ingredients[1].status is ResolutionStatus.UNRESOLVED

    async def test_the_care_ontology_still_matches_it_and_is_still_not_consulted(self, db_clean):
        """Both halves in one place: Care is intact, and Care is not the fallback."""
        from app.domains.routines.parser import parse_label

        # The legacy parser does recognise these, unchanged by Step 7B...
        assert {row.key for row in parse_label("Aqua, Tocopheryl Acetate")} & {"vitamin_e"}
        factory = get_sessionmaker()
        async with factory() as session:
            result = await resolve_formula(session, "Aqua, Tocopheryl Acetate")
        # ...and none of that reaches canonical identity.
        assert all(r.status is ResolutionStatus.UNRESOLVED for r in result.ingredients)

    async def test_publishing_one_ester_does_not_publish_the_family(self, db_clean):
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "tocopheryl.acetate", "Tocopheryl Acetate")
        async with factory() as session:
            result = await resolve_formula(session, "Tocopheryl Acetate, Tocopherol, Vitamin E")
        assert [r.status for r in result.ingredients] == [
            ResolutionStatus.RESOLVED, ResolutionStatus.UNRESOLVED, ResolutionStatus.UNRESOLVED,
        ]


# ---------------------------------------------------------------------------
# E. Bounded work — one batch call, never one query per ingredient
# ---------------------------------------------------------------------------
class TestBoundedQueries:
    @staticmethod
    def _recorder(statements: list[str]):
        def _record(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)
        return _record

    async def test_queries_do_not_scale_with_ingredient_count(self, db_clean):
        from sqlalchemy import event

        factory = get_sessionmaker()
        async with factory() as session:
            for index in range(30):
                await _publish_simple(session, f"entity.{index}", f"Ingredient Number {index}")

        statements: list[str] = []
        recorder = self._recorder(statements)
        sync_engine = sql.get_engine().sync_engine

        async with factory() as session:
            event.listen(sync_engine, "before_cursor_execute", recorder)
            try:
                statements.clear()
                one = await resolve_formula(session, "Ingredient Number 0")
                after_one = len(statements)
                statements.clear()
                many = await resolve_formula(
                    session, ", ".join(f"Ingredient Number {i}" for i in range(30)),
                )
                after_many = len(statements)
            finally:
                event.remove(sync_engine, "before_cursor_execute", recorder)

        assert all(r.status is ResolutionStatus.RESOLVED for r in one.ingredients)
        assert all(r.status is ResolutionStatus.RESOLVED for r in many.ingredients)
        assert len(many.ingredients) == 30
        # Step 7A's two bounded reads, for one ingredient and for thirty alike.
        assert after_one == 2, statements
        assert after_many == after_one, (after_one, after_many)

    async def test_a_failed_parse_issues_no_query_at_all(self, db_clean):
        from sqlalchemy import event

        factory = get_sessionmaker()
        statements: list[str] = []
        recorder = self._recorder(statements)
        sync_engine = sql.get_engine().sync_engine

        async with factory() as session:
            event.listen(sync_engine, "before_cursor_execute", recorder)
            try:
                statements.clear()
                for bad in ("Water,,Glycerin", "   ", "Water (Aqua", "W" * 4001):
                    assert not (await resolve_formula(session, bad)).ok
            finally:
                event.remove(sync_engine, "before_cursor_execute", recorder)
        assert statements == []

    def test_the_service_calls_the_batch_resolver_exactly_once(self):
        """Structural: no loop, and no single-name resolver, in this module."""
        body = _code_only((FORMULAS_DIR / "service.py").read_text(encoding="utf-8"))
        assert body.count("resolve_names") == 2      # the import, and the one call
        assert "resolve_name (" not in body
        tree = ast.parse((FORMULAS_DIR / "service.py").read_text(encoding="utf-8"))
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "resolve_names"
        ]
        assert len(calls) == 1
        # And that one call is not inside any loop.
        for node in ast.walk(tree):
            if isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                assert not any(
                    isinstance(inner, ast.Call)
                    and getattr(inner.func, "id", None) == "resolve_names"
                    for inner in ast.walk(node)
                )


# ---------------------------------------------------------------------------
# F. No AI, no network, no persistence
# ---------------------------------------------------------------------------
def _imported_modules(path: Path) -> set[str]:
    """Every module this file imports, read from the syntax tree, not the text."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = path.relative_to(BACKEND_ROOT).parts
                base = list(parts[:-1])
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                prefix = ".".join([*base, node.module] if node.module else base)
            else:
                prefix = node.module or ""
            if not prefix:
                continue
            found.add(prefix)
            found.update(f"{prefix}.{alias.name}" for alias in node.names)
    return found


#: Everything outside its own package the formula domain may import. Step 7A's
#: service is on the list precisely because it is this layer's identity
#: authority; nothing else that decides anything is.
ALLOWED_IMPORT_PREFIXES = (
    "app.domains.formulas",
    "app.domains.substances.service",
    "sqlalchemy.ext.asyncio",
    "dataclasses",
    "enum",
    "typing",
    "__future__",
)


class TestNoAINoNetworkNoPersistence:
    @pytest.mark.parametrize("path", sorted(FORMULAS_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_domain_imports_only_what_parsing_and_identity_need(self, path):
        for module in sorted(_imported_modules(path)):
            assert module.startswith(ALLOWED_IMPORT_PREFIXES), f"{path.name} imports {module}"

    @pytest.mark.parametrize("path", sorted(FORMULAS_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_domain_imports_none_of_the_forbidden_neighbours(self, path):
        imported = _imported_modules(path)
        for forbidden in (
            "app.domains.ai_gateway", "app.domains.routines", "app.domains.supplements",
            "app.domains.off", "app.domains.product", "app.domains.nutrition",
            "app.domains.care", "app.domains.alternatives", "app.domains.recommendation",
            "httpx", "requests", "aiohttp", "urllib", "socket", "google.genai",
        ):
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported
            ), f"{path.name} imports {forbidden}"

    @pytest.mark.parametrize("path", sorted(FORMULAS_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_no_ai_or_network_vocabulary_in_the_code(self, path):
        body = _code_only(path.read_text(encoding="utf-8"))
        for forbidden in (
            "ai_gateway", "genai", "gemini", "httpx", "requests", "aiohttp", "urllib",
            "socket", "openai", "embedding", "cosine", "levenshtein", "difflib",
            "SequenceMatcher", "fuzzy", "rapidfuzz", "get_close_matches",
        ):
            assert forbidden not in body, f"{path.name} references {forbidden}"

    async def test_resolving_a_formula_records_no_ai_run(self, db_clean):
        from app.domains.ai_gateway.models import AIRun

        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "water", "Water")
        async with factory() as session:
            await resolve_formula(session, "Water, Unknown Thing, Another Unknown")
        async with factory() as session:
            assert (await session.execute(select(AIRun))).scalars().all() == []

    async def test_an_unresolved_name_never_falls_back_to_anything(self, db_clean):
        """UNRESOLVED is terminal: no second attempt, no model, no lookup."""
        from app.domains.ai_gateway.models import AIRun

        factory = get_sessionmaker()
        async with factory() as session:
            result = await resolve_formula(session, "Completely Unknown Substance")
            assert result.ingredients[0].status is ResolutionStatus.UNRESOLVED
            assert (await session.execute(select(AIRun))).scalars().all() == []

    def test_no_socket_is_opened_during_parse_or_resolve(self):
        """Not just "it imports no HTTP client" — actually deny the network.

        The structural tests above prove the domain cannot *import* a client.
        This proves the behaviour: with every outbound socket refused, a formula
        still parses and resolves.

        Run in a **subprocess** on purpose. Patching ``socket`` in-process
        reaches the shared asyncpg pool this suite runs on, which left pooled
        connections holding locks and deadlocked the next test's TRUNCATE — the
        test corrupted the run it was meant to protect. A child process has its
        own pool and its own event loop, so the block is total and contained.
        """
        import os
        import subprocess
        import sys

        if not os.environ.get("POSTGRES_URL"):
            pytest.skip("POSTGRES_URL is required to exercise the resolution path")

        script = """
import asyncio, socket, sys

async def main():
    from app.domains.formulas import parse_formula, resolve_formula
    from app.shared.database.sql import get_sessionmaker
    factory = get_sessionmaker()
    async with factory() as session:      # establish the pool before blocking
        await resolve_formula(session, "Water")
    attempts = []
    def deny(self, address, *a, **k):
        attempts.append(address); raise AssertionError("network attempt")
    def deny_create(address, *a, **k):
        attempts.append(address); raise AssertionError("network attempt")
    socket.socket.connect = deny
    socket.create_connection = deny_create
    parsed = parse_formula("Water (Aqua/Eau), Niacinamide, Unknown Thing")
    assert parsed.status.value == "parsed", parsed.status
    assert len(parsed.tokens) == 3
    async with factory() as session:
        result = await resolve_formula(session, "Water, Unknown Thing")
    assert [r.status.value for r in result.ingredients] == ["unresolved", "unresolved"]
    assert attempts == [], attempts
    print("NO_NETWORK_OK")

asyncio.run(main())
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(BACKEND_ROOT), capture_output=True, text=True, timeout=180,
            env={**os.environ, "PYTHONPATH": str(BACKEND_ROOT)},
        )
        assert completed.returncode == 0, completed.stderr[-3000:]
        assert "NO_NETWORK_OK" in completed.stdout, completed.stdout

    def test_the_domain_declares_no_orm_model(self):
        """Step 7B adds no table, so it adds no migration."""
        from app.shared.database.registry import Base

        for name in Base.metadata.tables:
            assert "formula" not in name, name
        for path in sorted(FORMULAS_DIR.glob("*.py")):
            body = _code_only(path.read_text(encoding="utf-8"))
            for forbidden in ("__tablename__", "mapped_column", "DeclarativeBase", "Mapped"):
                assert forbidden not in body, f"{path.name} declares {forbidden}"

    def test_the_domain_is_not_in_the_model_registry(self):
        registry = (BACKEND_ROOT / "app" / "shared" / "database" / "registry.py").read_text(
            encoding="utf-8"
        )
        assert "formulas" not in registry

    def test_no_new_migration_was_added(self):
        versions = BACKEND_ROOT / "migrations" / "versions"
        assert not any("formula" in p.name for p in versions.glob("*.py"))


# ---------------------------------------------------------------------------
# G. No interpretation — the layer reads, it does not decide
# ---------------------------------------------------------------------------
class TestNoInterpretation:
    def test_the_result_carries_no_verdict(self):
        for model in (FormulaResolution, FormulaIngredientResolution):
            fields = set(model.__dataclass_fields__)
            for forbidden in (
                "score", "grade", "verdict", "action", "recommendation", "positive",
                "negative", "risk", "safety", "safe", "benefit", "efficacy", "function",
                "concentration", "percentage", "percent", "dose", "amount", "allergen",
                "irritation", "comedogenic", "rank", "confidence", "probability",
                "similarity",
            ):
                assert not any(forbidden in field for field in fields), (model.__name__, forbidden)

    def test_only_three_resolution_states_reach_the_formula_layer(self):
        assert {s.value for s in ResolutionStatus} == {"resolved", "ambiguous", "unresolved"}

    @pytest.mark.parametrize("path", sorted(FORMULAS_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_no_concentration_or_function_vocabulary_in_the_code(self, path):
        body = _code_only(path.read_text(encoding="utf-8"))
        for forbidden in (
            "percent", "ppm", "mg/", "threshold", "risk_tier", "safety_score", "hazard",
            "comedogenic", "irritant", "allergen", "brightening", "humectant", "exfoliant",
            "barrier_repair", "pregnancy",
        ):
            assert forbidden not in body, f"{path.name} references {forbidden}"

    async def test_position_changes_nothing_about_an_identity(self, db_clean):
        """The same name resolves identically first, middle and last."""
        factory = get_sessionmaker()
        async with factory() as session:
            await _publish_simple(session, "glycerin", "Glycerin")
            await _publish_simple(session, "water", "Water")
        async with factory() as session:
            first = await resolve_formula(session, "Glycerin, Water, Water")
            last = await resolve_formula(session, "Water, Water, Glycerin")
        head, tail = first.ingredients[0], last.ingredients[2]
        assert head.substance_key == tail.substance_key == "glycerin"
        assert head.status == tail.status
        assert head.entity_kind == tail.entity_kind
        assert head.candidate_substance_keys == tail.candidate_substance_keys
        # Position is the only thing that differs, and it is order, not meaning.
        assert (head.position, tail.position) == (1, 3)

    async def test_the_same_string_parses_identically_whatever_it_is_for(self, db_clean):
        """No category interpretation: a serum and a shampoo parse the same."""
        text = "Water (Aqua/Eau), Sodium Laureth Sulfate, Niacinamide, Parfum (Fragrance)"
        assert parse_formula(text) == parse_formula(text)
        signature = set(inspect.signature(resolve_formula).parameters)
        assert signature == {"session", "ingredients_text"}
        assert "category" not in signature
        assert "product_type" not in signature

    def test_resolution_takes_no_account_device_or_person(self):
        parameters = set(inspect.signature(resolve_formula).parameters)
        for forbidden in ("account_id", "device_id", "user_id", "profile", "profile_id"):
            assert forbidden not in parameters


# ---------------------------------------------------------------------------
# H. Nothing that already worked was changed
# ---------------------------------------------------------------------------
class TestExistingBehaviourUnchanged:
    def test_step_7a_is_untouched_by_this_branch(self):
        """The identity layer keeps its own contract, unedited."""
        from app.domains.substances.service import MAX_BATCH_NAMES as ceiling
        from app.domains.substances.service import resolve_names

        assert ceiling == 128
        assert set(inspect.signature(resolve_names).parameters) == {"session", "names"}

    def test_the_care_parser_and_ontology_still_work(self):
        from app.domains.routines.ontology import INGREDIENT_BY_KEY
        from app.domains.routines.parser import parse_label

        assert "tocopheryl acetate" in INGREDIENT_BY_KEY["vitamin_e"].aliases
        assert "ceramide np" in INGREDIENT_BY_KEY["ceramides"].aliases
        assert {row.key for row in parse_label("Aqua, Niacinamide, Glycerin")} >= {"niacinamide"}

    def test_nothing_outside_the_domain_imports_it_yet(self):
        """Step 7B ships the engine, wired to nothing."""
        app_root = BACKEND_ROOT / "app"
        for path in sorted(app_root.rglob("*.py")):
            if FORMULAS_DIR in path.parents:
                continue
            assert not any(
                module.startswith("app.domains.formulas")
                for module in _imported_modules(path)
            ), f"{path.relative_to(BACKEND_ROOT)} imports the formulas domain"

    def test_no_api_route_exposes_the_formula_engine(self):
        api_root = BACKEND_ROOT / "app" / "api"
        for path in sorted(api_root.rglob("*.py")):
            assert "formulas" not in path.read_text(encoding="utf-8"), path

    def test_the_product_scan_boundary_is_untouched(self):
        """§24: extraction.py and the label schema keep their current shape."""
        from app.domains.product.extraction import ExtractedLabel

        body = (BACKEND_ROOT / "app" / "domains" / "product" / "extraction.py").read_text(
            encoding="utf-8"
        )
        assert "formulas" not in body
        assert "resolve_formula" not in body
        # The transcription schema still carries exactly the fields it did.
        assert "ingredients_text" in ExtractedLabel.model_fields

    def test_the_odbl_wall_is_untouched(self):
        from app.domains.off.models import OffProduct

        columns = {c.name for c in OffProduct.__table__.columns}
        for forbidden in ("formula_id", "resolved_ingredients", "substance_key"):
            assert forbidden not in columns

    def test_the_domain_owns_exactly_these_modules(self):
        assert {p.name for p in FORMULAS_DIR.glob("*.py")} == {
            "__init__.py", "parser.py", "service.py",
        }

    async def test_no_substance_data_ships(self, db_clean):
        """Step 7B adds no canonical names, just as Step 7A shipped none."""
        from app.bootstrap import run as seed_reference_data

        factory = get_sessionmaker()
        async with factory() as session:
            await seed_reference_data(session)
            await session.commit()
        async with factory() as session:
            assert (await session.execute(select(Substance))).scalars().all() == []
            assert (await session.execute(select(EvidenceClaim).where(
                EvidenceClaim.domain == "substance"
            ))).scalars().all() == []
