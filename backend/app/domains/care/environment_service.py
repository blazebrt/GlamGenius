"""Reading the stored environment and asking the rules what to do about it.

This is the seam between the planning domain, which stores what the weather and
the air were, and the pure rules engine, which has no idea a database exists.

The evidence gate lives here too. A rule whose reviewed source is missing,
retired or unapproved never speaks — the same fail-closed behaviour Care
guidance already has, for the same reason.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.care.environment_decision import (
    EnvironmentDay,
    EnvironmentDecision,
    EnvironmentWindow,
    evaluate_environment,
)
from app.domains.care.environment_rules import ENVIRONMENT_RULES
from app.domains.care.evidence_applicability import resolve_care_evidence_applicability
from app.domains.evidence.service import assess_rule_evidence
from app.domains.planning.models import AirQualitySnapshot, WeatherSnapshot

#: How far back the streak rules can see. Five consecutive Poor days plus the
#: two clean days that end them, plus room for a gap.
HISTORY_DAYS = 10


async def allowed_environment_rule_ids(session: AsyncSession) -> frozenset[str]:
    """The rules with a complete reviewed evidence path behind them today."""
    allowed: set[str] = set()
    for rule in ENVIRONMENT_RULES:
        assessment = await assess_rule_evidence(
            session,
            domain=rule.domain,
            rule_kind=rule.rule_kind,
            rule_id=rule.rule_id,
            rule_version=rule.rule_version,
        )
        if not assessment.behavior_evidence_eligible:
            continue
        applicability = resolve_care_evidence_applicability(assessment, rule.applicability_signals)
        if not applicability.applicable:
            continue
        allowed.add(rule.rule_id)
    return frozenset(allowed)


async def load_window(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date,
    history_days: int = HISTORY_DAYS,
) -> EnvironmentWindow:
    """Assemble today plus recent days from what planning already stored.

    One row per day: the most recently written snapshot for that date wins,
    because that is the freshest reading we hold.
    """
    earliest = plan_date - timedelta(days=history_days)
    air_rows = (await session.execute(
        select(AirQualitySnapshot)
        .where(
            AirQualitySnapshot.account_id == account_id,
            AirQualitySnapshot.for_date >= earliest,
            AirQualitySnapshot.for_date <= plan_date,
        )
        .order_by(AirQualitySnapshot.for_date.asc(), AirQualitySnapshot.created_at.asc())
    )).scalars().all()
    weather_rows = (await session.execute(
        select(WeatherSnapshot)
        .where(
            WeatherSnapshot.account_id == account_id,
            WeatherSnapshot.for_date >= earliest,
            WeatherSnapshot.for_date <= plan_date,
        )
        .order_by(WeatherSnapshot.for_date.asc(), WeatherSnapshot.created_at.asc())
    )).scalars().all()

    air_by_date = {row.for_date: row for row in air_rows}
    weather_by_date = {row.for_date: row for row in weather_rows}

    def _day(for_date: date) -> EnvironmentDay:
        air = air_by_date.get(for_date)
        weather = weather_by_date.get(for_date)
        return EnvironmentDay(
            for_date=for_date,
            aqi=air.aqi if air else None,
            index_system=air.index_system if air else None,
            category=air.category if air else None,
            humidity=weather.humidity if weather else None,
            temp_max_c=weather.temp_max_c if weather else None,
            uv_index=weather.uv_index if weather else None,
            precipitation_chance=weather.precipitation_chance if weather else None,
            condition=weather.condition if weather else None,
        )

    history = tuple(
        _day(plan_date - timedelta(days=offset))
        for offset in range(history_days, 0, -1)
    )
    return EnvironmentWindow(today=_day(plan_date), history=history)


async def decide_for_day(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date,
) -> EnvironmentDecision | None:
    """One decision for one person on one day, or nothing at all."""
    window = await load_window(session, account_id=account_id, plan_date=plan_date)
    allowed = await allowed_environment_rule_ids(session)
    return evaluate_environment(window, allowed_rule_ids=allowed)


__all__ = [
    "HISTORY_DAYS",
    "allowed_environment_rule_ids",
    "decide_for_day",
    "load_window",
]
