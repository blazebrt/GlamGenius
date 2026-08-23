"""VC-04 weekly network-boundary regression coverage."""
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.domains.planning import weekly


class _Session:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prefetch_result",
    ["normal", "aqi_out_of_horizon", "weather_out_of_horizon", "weather_failure", "air_failure", "geocode_failure"],
)
async def test_weekly_prefetch_is_the_only_live_network_pass(monkeypatch, prefetch_result):
    session = _Session()
    account_id = uuid4()
    week = date(2026, 8, 24)
    dates = [date.fromordinal(week.toordinal() + offset) for offset in range(7)]
    calls = {"geocode": 0, "weather": 0, "air": 0}
    gather_calls = []
    plan = SimpleNamespace(
        id=uuid4(), repetition_window_days=7, version=0, generated_at=None, status=None,
    )

    async def prefetch(*args, **kwargs):
        calls["geocode"] += 1
        if prefetch_result in {"normal", "weather_out_of_horizon", "air_failure", "geocode_failure"}:
            calls["weather"] += 1
        if prefetch_result in {"normal", "aqi_out_of_horizon", "weather_failure"}:
            calls["air"] += 1
        return prefetch_result not in {"weather_failure", "air_failure", "geocode_failure"}

    async def gather(*args, **kwargs):
        gather_calls.append(kwargs)
        return SimpleNamespace()

    async def compile_day(*args, **kwargs):
        return SimpleNamespace(id=uuid4()), None

    async def attributes(*args, **kwargs):
        return {"city": "Delhi"}

    monkeypatch.setattr(weekly, "get_or_create_week", _return(plan))
    monkeypatch.setattr(weekly, "_day_rows", _empty_rows)
    monkeypatch.setattr(weekly.clock, "week_dates", lambda _: dates)
    monkeypatch.setattr(weekly.context_stage.style_context, "confirmed_attributes", attributes)
    monkeypatch.setattr(weekly.context_stage, "prefetch_environment", prefetch)
    monkeypatch.setattr(weekly.context_stage, "gather", gather)
    monkeypatch.setattr(weekly.compiler, "compile_day", compile_day)
    monkeypatch.setattr(weekly, "utcnow", lambda: datetime(2026, 8, 24))

    await weekly.generate(session, account_id=account_id, week_start=week, timezone_name="Asia/Kolkata")

    assert calls["geocode"] == 1
    assert calls["weather"] <= 1
    assert calls["air"] <= 1
    assert len(gather_calls) == 7
    assert all(call["skip_live_environment"] is True for call in gather_calls)


def _return(value):
    async def inner(*args, **kwargs):
        return value

    return inner


async def _empty_rows(*args, **kwargs):
    return {}
