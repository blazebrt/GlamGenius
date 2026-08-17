"""V3-04.0 metadata-only ICMR-NIN authority seed."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.evidence.models import EvidenceSource
from app.domains.nutrition import NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION
from app.domains.reference import SeedVersionRecord

NUTRITION_AUTHORITY_SEED_DOMAIN = "evidence_nutrition_authority"
NUTRITION_AUTHORITY_SEED_NOTE = "V3-04.0 ICMR-NIN authority metadata only"
NUTRITION_AUTHORITY_ACCESSED_AT = datetime(2026, 8, 17, tzinfo=UTC)

IFCT_SOURCE_IDENTIFIER = "icmr_nin.ifct.2017"
DIETARY_GUIDELINES_SOURCE_KEY = "icmr_nin.dietary_guidelines_for_indians.2024"
RDA_EAR_SOURCE_KEY = "icmr_nin.nutrient_requirements.rda_ear.2020"

SOURCE_DEFS: tuple[dict[str, Any], ...] = (
    {
        "source_key": IFCT_SOURCE_IDENTIFIER, "source_series_key": "icmr_nin.ifct",
        "source_type": "government_reference", "title": "Indian Food Composition Tables 2017",
        "publisher": "ICMR-National Institute of Nutrition", "jurisdiction": "India",
        "publication_date": None, "version_or_revision": "2017",
        "canonical_url": "https://www.nin.res.in/downloads/",
        "accessed_at": NUTRITION_AUTHORITY_ACCESSED_AT, "status": "active",
        "license_or_use_note": (
            "Metadata/provenance only in GlamGenius. The ICMR-NIN website states that "
            "reproduction, distribution, transmission, or scraping requires prior written "
            "permission. This seed does not include IFCT food-composition values."
        ),
        "last_reviewed_at": NUTRITION_AUTHORITY_ACCESSED_AT,
    },
    {
        "source_key": DIETARY_GUIDELINES_SOURCE_KEY,
        "source_series_key": "icmr_nin.dietary_guidelines_for_indians",
        "source_type": "official_guideline", "title": "Dietary Guidelines for Indians 2024",
        "publisher": "ICMR-National Institute of Nutrition", "jurisdiction": "India",
        "publication_date": None, "version_or_revision": "2024",
        "canonical_url": "https://www.nin.res.in/dietaryguidelines2024.html",
        "accessed_at": NUTRITION_AUTHORITY_ACCESSED_AT, "status": "active",
        "license_or_use_note": "Metadata/reference use only in V3-04.0. No guideline text is copied into the application in this phase.",
        "last_reviewed_at": NUTRITION_AUTHORITY_ACCESSED_AT,
    },
    {
        "source_key": RDA_EAR_SOURCE_KEY,
        "source_series_key": "icmr_nin.nutrient_requirements",
        "source_type": "government_reference", "title": "Nutrient Requirements for Indians: RDA and EAR 2020",
        "publisher": "ICMR-National Institute of Nutrition", "jurisdiction": "India",
        "publication_date": None, "version_or_revision": "RDA and EAR 2020",
        "canonical_url": "https://www.nin.res.in/RDA_Full_Report_2024.html",
        "accessed_at": NUTRITION_AUTHORITY_ACCESSED_AT, "status": "active",
        "license_or_use_note": "Metadata/reference use only in V3-04.0. No RDA/EAR/TUL table is copied into the application in this phase.",
        "last_reviewed_at": NUTRITION_AUTHORITY_ACCESSED_AT,
    },
)


async def run(session: AsyncSession) -> dict[str, int | str]:
    """Insert the three immutable source metadata rows idempotently."""
    for values in SOURCE_DEFS:
        existing = (await session.execute(
            select(EvidenceSource).where(EvidenceSource.source_key == values["source_key"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(EvidenceSource(**values))
            await session.flush()
        else:
            mismatch = [key for key, value in values.items() if getattr(existing, key) != value]
            if mismatch:
                raise ValueError(
                    f"nutrition authority source drift for {values['source_key']}: {', '.join(mismatch)}"
                )

    audit = (await session.execute(
        select(SeedVersionRecord).where(
            SeedVersionRecord.seed_domain == NUTRITION_AUTHORITY_SEED_DOMAIN,
            SeedVersionRecord.seed_version == NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
        )
    )).scalar_one_or_none()
    if audit is None:
        session.add(SeedVersionRecord(
            seed_domain=NUTRITION_AUTHORITY_SEED_DOMAIN,
            seed_version=NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
            applied_at=NUTRITION_AUTHORITY_ACCESSED_AT,
            rows_written=3,
            note=NUTRITION_AUTHORITY_SEED_NOTE,
        ))
    elif audit.rows_written != 3 or audit.note != NUTRITION_AUTHORITY_SEED_NOTE:
        raise ValueError("nutrition authority evidence seed audit drift")
    return {
        "seed_version": NUTRITION_AUTHORITY_EVIDENCE_SEED_VERSION,
        "sources": 3, "claims": 0, "claim_source_links": 0, "rule_links": 0,
        "rows_written": 3,
    }
