from datetime import UTC, date, datetime, timedelta

import pytest
from app.domains.planning.models import AirQualitySnapshot
from app.domains.planning.providers.manual import StoredAirQualityProvider
from app.shared.database.sql import get_sessionmaker

pytestmark = pytest.mark.asyncio


async def test_stored_air_quality_provider_isolated_filtered_latest_and_lossless(
    db_clean, registered_supabase_user
):
    _, account_id = await registered_supabase_user()
    _, other_account_id = await registered_supabase_user()
    target = date(2026, 8, 8)
    other_date = target + timedelta(days=1)
    older_at = datetime(2026, 8, 8, 8, tzinfo=UTC)
    newer_at = older_at + timedelta(hours=1)

    factory = get_sessionmaker()
    async with factory() as session:
        session.add_all(
            [
                AirQualitySnapshot(
                    account_id=account_id,
                    for_date=target,
                    location="Delhi",
                    aqi=120,
                    index_system="india_naqi",
                    category="Moderate",
                    prominent_pollutant="PM10",
                    pm2_5=45.5,
                    pm10=130.0,
                    provider="manual",
                    source="first_entry",
                    raw={"revision": 1},
                    created_at=older_at,
                ),
                AirQualitySnapshot(
                    account_id=account_id,
                    for_date=target,
                    location="Delhi",
                    aqi=350,
                    index_system="india_naqi",
                    category="Very Poor",
                    prominent_pollutant="PM2.5",
                    pm2_5=141.5,
                    pm10=211.25,
                    provider="manual",
                    source="corrected_entry",
                    raw={"revision": 2, "source_detail": {"device": "user"}},
                    created_at=newer_at,
                ),
                AirQualitySnapshot(
                    account_id=account_id,
                    for_date=other_date,
                    aqi=42,
                    index_system="india_naqi",
                    category="Good",
                    provider="manual",
                    source="other_date",
                    raw={"not": "requested"},
                    created_at=newer_at,
                ),
                AirQualitySnapshot(
                    account_id=other_account_id,
                    for_date=target,
                    aqi=17,
                    index_system="india_naqi",
                    category="Good",
                    provider="manual",
                    source="other_account",
                    raw={"private": True},
                    created_at=newer_at,
                ),
            ]
        )
        await session.commit()

    async with factory() as session:
        provider = StoredAirQualityProvider(session, account_id)
        readings = await provider.air_quality(
            location="Delhi", dates=[target], timezone_name="Asia/Kolkata"
        )
        empty = await provider.air_quality(
            location="Delhi", dates=[], timezone_name="Asia/Kolkata"
        )

    assert len(readings) == 1
    reading = readings[0]
    assert reading.for_date == target
    assert reading.aqi == 350
    assert reading.index_system == "india_naqi"
    assert reading.category == "Very Poor"
    assert reading.prominent_pollutant == "PM2.5"
    assert reading.pm2_5 == 141.5
    assert reading.pm10 == 211.25
    assert reading.source == "corrected_entry"
    assert reading.provider == "manual"
    assert reading.raw == {"revision": 2, "source_detail": {"device": "user"}}
    assert empty == []

    async with factory() as session:
        other_readings = await StoredAirQualityProvider(
            session, other_account_id
        ).air_quality(
            location="Delhi", dates=[target], timezone_name="Asia/Kolkata"
        )
    assert [reading.aqi for reading in other_readings] == [17]
