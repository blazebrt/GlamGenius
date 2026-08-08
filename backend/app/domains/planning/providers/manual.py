"""The providers that ship working in this phase.

Both read what the user or the app already stored, so the whole Today engine and
weekly planner work end to end with no outside account connected and no API key.
That is a product decision as much as a technical one: connecting a calendar is
something plenty of people will never want to do, and the app should be fully
useful to them.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.planning.providers.base import (
    CalendarEventReading,
    ProviderUnavailable,
    WeatherReading,
    AirQualityReading,
)

PROVIDER_MANUAL = "manual"


class StoredWeatherProvider:
    """Weather the user entered, read back out of ``weather_snapshots``.

    Someone who says "it will be hot on Friday" has given us better information
    than a forecast API would for the purposes of choosing a shirt, and it costs
    nothing.
    """

    name = PROVIDER_MANUAL

    def __init__(self, session: AsyncSession, account_id) -> None:
        self._session = session
        self._account_id = account_id

    def is_configured(self) -> bool:
        return True

    async def forecast(
        self, *, location: str | None, dates: list[date], timezone_name: str
    ) -> list[WeatherReading]:
        from app.domains.planning.models import WeatherSnapshot

        if not dates:
            return []
        rows = (await self._session.execute(
            select(WeatherSnapshot).where(
                WeatherSnapshot.account_id == self._account_id,
                WeatherSnapshot.for_date.in_(list(dates)),
            ).order_by(WeatherSnapshot.for_date, WeatherSnapshot.created_at.desc())
        )).scalars().all()

        # Most recent entry per date wins — a user correcting themselves should
        # take effect immediately.
        latest: dict[date, WeatherSnapshot] = {}
        for row in rows:
            latest.setdefault(row.for_date, row)
        return [
            WeatherReading(
                for_date=row.for_date, condition=row.condition,
                temp_min_c=row.temp_min_c, temp_max_c=row.temp_max_c,
                precipitation_chance=row.precipitation_chance, humidity=row.humidity,
                uv_index=row.uv_index,
                location=row.location, provider=row.provider, source=row.source,
            )
            for row in latest.values()
        ]


class StoredCalendarProvider:
    """Events already in ``calendar_events``, whatever put them there.

    A manually added event and a synced one are the same shape by the time they
    reach here, which is the point of the abstraction.
    """

    name = PROVIDER_MANUAL

    def __init__(self, session: AsyncSession, account_id) -> None:
        self._session = session
        self._account_id = account_id

    def is_configured(self) -> bool:
        return True

    async def events(
        self, *, since: datetime, until: datetime, credential_ref: str | None = None
    ) -> list[CalendarEventReading]:
        from app.domains.planning.models import CalendarEvent

        rows = (await self._session.execute(
            select(CalendarEvent).where(
                CalendarEvent.account_id == self._account_id,
                CalendarEvent.status == "active",
                CalendarEvent.starts_at >= since,
                CalendarEvent.starts_at < until,
            ).order_by(CalendarEvent.starts_at)
        )).scalars().all()
        return [
            CalendarEventReading(
                external_id=row.external_id, title=row.title, starts_at=row.starts_at,
                ends_at=row.ends_at, all_day=row.all_day, location=row.location,
                provider=row.provider, source=row.source,
            )
            for row in rows
        ]


class StoredAirQualityProvider:
    """Air quality the user entered, read back out of ``air_quality_snapshots``.
    
    Similar to StoredWeatherProvider, an explicit user entry overrides external data.
    """

    name = PROVIDER_MANUAL

    def __init__(self, session: AsyncSession, account_id) -> None:
        self._session = session
        self._account_id = account_id

    def is_configured(self) -> bool:
        return True

    async def air_quality(
        self, *, location: str | None, dates: list[date], timezone_name: str
    ) -> list[AirQualityReading]:
        from app.domains.planning.models import AirQualitySnapshot

        if not dates:
            return []
        rows = (await self._session.execute(
            select(AirQualitySnapshot).where(
                AirQualitySnapshot.account_id == self._account_id,
                AirQualitySnapshot.for_date.in_(list(dates)),
            ).order_by(AirQualitySnapshot.for_date, AirQualitySnapshot.created_at.desc())
        )).scalars().all()

        latest: dict[date, AirQualitySnapshot] = {}
        for row in rows:
            latest.setdefault(row.for_date, row)
            
        return [
            AirQualityReading(
                for_date=row.for_date, aqi=row.aqi, index_system=row.index_system,
                category=row.category, location=row.location,
                prominent_pollutant=row.prominent_pollutant,
                pm2_5=row.pm2_5, pm10=row.pm10,
                provider=row.provider, source=row.source,
            )
            for row in latest.values()
        ]


class UnconfiguredProvider:
    """A named seam for a live service that is not wired up.

    It exists so the registry can offer the provider by name, report honestly
    that it is unavailable, and be replaced by a real adapter later without any
    caller changing. It never returns data.
    """

    def __init__(self, name: str, kind: str) -> None:
        self.name = name
        self._kind = kind

    def is_configured(self) -> bool:
        return False

    def _unavailable(self) -> ProviderUnavailable:
        return ProviderUnavailable(
            f"The {self.name} {self._kind} provider is not connected. "
            f"You can enter {self._kind} details yourself instead.",
            provider=self.name,
            reason="not_configured",
        )

    async def forecast(self, **_: object) -> list[WeatherReading]:
        raise self._unavailable()

    async def events(self, **_: object) -> list[CalendarEventReading]:
        raise self._unavailable()
        
    async def air_quality(self, **_: object) -> list[AirQualityReading]:
        raise self._unavailable()
