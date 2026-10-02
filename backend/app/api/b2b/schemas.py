"""The ``b2b-product-truth-v1`` response contract, as explicit models.

Every model forbids extra fields. FastAPI validates each response against
them, so a field the contract does not name cannot leave this server: the
request fails closed with a 500 instead. A new field is therefore always a
deliberate contract change, made here, in review.

These models describe the B2B contract only. They are not ORM models and
expose nothing of the database.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quantity(_Contract):
    value: float
    unit: str
    basis: str = Field(description="per_100_g or per_100_ml. Never a pack size.")


class FactorEvidence(_Contract):
    status: Literal["published"] = Field(
        description="Only published evidence is ever distributed; anything else makes the answer "
        "not_enough_information.",
    )
    rule_version: str
    evidence_claim_ids: list[str]
    evidence_claim_version: int | None


class FactorSource(_Contract):
    name: str
    url: str = Field(description="An openable source the rule rests on.")
    publisher: str | None
    identifier: str


class Factor(_Contract):
    key: str = Field(description="Stable machine key, e.g. 'sugar' or 'additive:322'.")
    label: str = Field(description="Stable label key. Not prose.")
    status: str
    band: Literal["green", "yellow", "red"]
    explanation: str = Field(description="Stable explanation key. Not prose.")
    quantity: Quantity | None
    rule: str | None = Field(description="The grading rule id. Null for a label declaration.")
    evidence: FactorEvidence | None = Field(description="Null for a label declaration, which makes no claim.")
    sources: list[FactorSource]


class Factors(_Contract):
    negatives: list[Factor]
    positives: list[Factor]


class ProductIdentity(_Contract):
    name: str | None = Field(description="As printed on the confirmed label. Null when not stated.")
    brand: str | None = Field(description="As printed on the confirmed label. Null when not stated.")


class LabelIdentity(_Contract):
    version_number: int
    content_fingerprint: str
    completeness: Literal["complete_for_grading"]


class Confidence(_Contract):
    level: Literal["verified", "community", "unverified", "not_enough_information"]


class GradeBlock(_Contract):
    outcome: Literal["graded"]
    grade: Literal["A", "B", "C", "D", "E"]
    band: Literal["green", "yellow", "red"]
    engine_version: str


class Decision(_Contract):
    state: Literal["decided"]
    verdict: Literal["buy", "wait", "skip"]
    reason_key: str


class Ruleset(_Contract):
    rule_version: str
    fingerprint: str = Field(description="Identifies which rules were published, on which claims.")


class Scope(_Contract):
    physical_pack_context: Literal[False] = Field(
        description="Always false: a B2B caller is not holding a pack, so no pack, lot or batch is established.",
    )
    official_records: Literal["not_evaluated"] = Field(
        description="Lot-specific official records need a physical pack and are not evaluated in V1.",
    )


class Truth(_Contract):
    product: ProductIdentity
    facts_provenance: Literal["confirmed_label_snapshot"]
    label: LabelIdentity
    confidence: Confidence
    grade: GradeBlock
    decision: Decision
    factors: Factors
    ruleset: Ruleset
    scope: Scope


class Meta(_Contract):
    request_id: str
    generated_at: datetime


class ProductTruthResponse(_Contract):
    contract_version: Literal["b2b-product-truth-v1"]
    barcode: str
    state: Literal["available", "not_enough_information"]
    reason: Literal[
        "no_confirmed_label",
        "label_unreadable",
        "label_incomplete",
        "not_graded",
        "label_facts_insufficient",
        "evidence_unpublished",
    ] | None = Field(description="Null when available; otherwise why the verified-data requirement is not met.")
    truth: Truth | None
    truth_fingerprint: str = Field(
        description="SHA-256 of the canonical JSON of contract_version, barcode, state, reason and truth. "
        "Excludes meta. Identical for every client.",
    )
    meta: Meta


class ErrorDetail(_Contract):
    code: str
    message: str
    request_id: str


class ErrorResponse(_Contract):
    detail: ErrorDetail


__all__ = ["ErrorResponse", "ProductTruthResponse"]
