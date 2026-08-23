"""The context service: everything the planner is allowed to know about a day.

One object, gathered once, hashed into a cache key. If the hash has not moved,
nothing about the day can have changed in a way that would change the plan, so
the stored plan is served straight back. That is what makes Today cheap.

The hash covers only **material** inputs. The current time is not in it —
otherwise every request would look like a change and the cache would never hit.
An event's title is in it, because renaming "coffee" to "client dinner" should
absolutely rebuild the plan.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    ENVIRONMENT_CACHE_TTL_SECONDS,
    ENVIRONMENT_STALE_MAX_SECONDS,
    LIVE_ENVIRONMENT_PROVIDER,
)
from app.domains.planning import clock
from app.domains.planning.environment import ClimateContext, resolve_climate_context
from app.domains.planning.models import (
    LAUNDRY_IN_WASH,
    LAUNDRY_UNAVAILABLE,
    AirQualitySnapshot,
    CalendarEvent,
    LaundryStateEvent,
    OutfitSchedule,
    WeatherSnapshot,
)
from app.domains.planning.providers import ProviderUnavailable, air_quality_provider, weather_provider
from app.domains.planning.providers.base import AirQualityReading, WeatherReading
from app.domains.planning.providers.open_meteo import OpenMeteoProvider, ResolvedLocation, location_identity
from app.domains.recommendation import context as style_context
from app.domains.recommendation.context import OwnedItem
from app.domains.recommendation.occasions import OCCASIONS
from app.shared.database.base import utcnow

# --- Inferring an occasion from an event title -------------------------------
# Deliberately conservative. A wrong guess here silently changes what someone
# wears to work, so a match must be a real word match, and a weak match is
# reported as low confidence rather than acted on silently.
EVENT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "interview": ("interview", "hiring panel", "screening round"),
    "wedding": ("wedding", "shaadi", "baraat", "reception", "sangeet", "mehendi", "nikah", "haldi"),
    "festival": ("diwali", "holi", "eid", "pongal", "onam", "navratri", "puja", "pooja", "ganesh", "durga", "christmas", "festival"),
    "business_meeting": ("client meeting", "board meeting", "investor", "client call", "stakeholder", "vendor meeting"),
    "conference": ("conference", "summit", "seminar", "convention", "symposium", "meetup"),
    "photoshoot": ("photoshoot", "photo shoot", "shoot", "headshot", "portrait session"),
    "birthday": ("birthday", "bday", "cake cutting"),
    "party": ("party", "night out", "celebration", "farewell", "housewarming"),
    "date": ("date night", "dinner date", "anniversary"),
    "gym": ("gym", "workout", "training session", "yoga", "pilates", "run club"),
    "travel": ("flight", "train", "airport", "railway", "travel", "commute to"),
    "vacation": ("vacation", "holiday trip", "resort", "beach"),
    "college": ("lecture", "class", "seminar hall", "campus", "viva", "exam"),
    "office": ("standup", "stand-up", "sprint", "1:1", "one on one", "review meeting", "office", "work"),
}

# How confident a keyword match makes us. Anything below the clarification
# threshold gets asked about rather than assumed.
STRONG_MATCH = 0.85
WEAK_MATCH = 0.5
CLARIFY_BELOW = 0.6


def infer_occasion(title: str, *, is_weekend: bool = False) -> tuple[str | None, float]:
    """Guess what kind of event this is, and say how sure we are."""
    text = " ".join((title or "").lower().split())
    if not text:
        return None, 0.0
    for occasion_key, keywords in EVENT_KEYWORDS.items():
        for keyword in keywords:
            # Word boundaries, not substrings. "networking" contains "work"
            # and "shooting" contains "shoot"; matching those would silently
            # change the formality of someone's day.
            if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text):
                # A multi-word phrase is a much stronger signal than one common
                # word like "work" appearing somewhere in a title.
                confidence = STRONG_MATCH if " " in keyword or len(keyword) > 7 else WEAK_MATCH
                return occasion_key, confidence
    return None, 0.0


def default_occasion_for(plan_date: date, work_context: str | None) -> str:
    """What a day is when nothing on the calendar says otherwise."""
    if clock.is_weekend(plan_date):
        return "everyday"
    if work_context and any(word in str(work_context).lower() for word in ("office", "corporate", "work from office", "hybrid")):
        return "office"
    if work_context and "student" in str(work_context).lower():
        return "college"
    return "everyday"


# --- Value objects ----------------------------------------------------------


@dataclass
class DayEvent:
    """A commitment, as the compiler sees it."""

    id: uuid.UUID | None
    title: str
    starts_at: datetime
    ends_at: datetime | None
    all_day: bool
    location: str | None
    occasion_key: str | None
    dress_code_hint: str | None
    confidence: float
    user_confirmed: bool

    def local_time(self, timezone_name: str) -> str:
        return clock.local_now(timezone_name, moment=self.starts_at).strftime("%H:%M")


@dataclass
class DayContext:
    """Everything known about one person on one local day."""

    account_id: uuid.UUID
    plan_date: date
    timezone_name: str
    now_local: datetime

    weather: WeatherReading | None = None
    weather_snapshot_id: uuid.UUID | None = None
    weather_unavailable_reason: str | None = None

    air_quality: AirQualityReading | None = None
    air_quality_snapshot_id: uuid.UUID | None = None

    events: list[DayEvent] = field(default_factory=list)
    occasion_key: str = "everyday"
    occasion_confidence: float = 1.0
    dress_code: str | None = None

    owned: list[OwnedItem] = field(default_factory=list)
    unavailable_item_ids: list[uuid.UUID] = field(default_factory=list)
    draft_count: int = 0

    recent_look_ids: list[uuid.UUID] = field(default_factory=list)
    item_last_worn: dict[uuid.UUID, date] = field(default_factory=dict)
    repetition_window_days: int = 7

    profile: dict[str, Any] = field(default_factory=dict)
    missing_information: list[str] = field(default_factory=list)

    @property
    def primary_event(self) -> DayEvent | None:
        """The commitment that should drive the outfit.

        The most formal one, then the earliest — dressing for the 9am standup
        when there is a 6pm client dinner is the wrong way round.
        """
        if not self.events:
            return None
        def rank(event: DayEvent) -> tuple[int, datetime]:
            occasion = OCCASIONS.get(event.occasion_key or "")
            return (-(occasion.formality if occasion else 0), event.starts_at)
        return sorted(self.events, key=rank)[0]

    @property
    def climate(self) -> ClimateContext:
        condition = self.weather.condition if self.weather else None
        temp_max_c = self.weather.temp_max_c if self.weather else None
        location = (
            self.weather.location
            if self.weather and self.weather.location
            else self.profile.get("city")
        )
        humidity = self.weather.humidity if self.weather else None
        precip = self.weather.precipitation_chance if self.weather else None
        return resolve_climate_context(
            self.plan_date, temp_max_c, condition,
            location=location, humidity=humidity, precipitation_chance=precip
        )

    @property
    def season(self) -> str:
        return self.climate.season

    def available_owned(self) -> list[OwnedItem]:
        blocked = set(self.unavailable_item_ids)
        return [item for item in self.owned if item.id not in blocked]

    @property
    def recent_item_ids(self) -> list[uuid.UUID]:
        return list(self.item_last_worn)

    def days_since_worn(self, item_id: uuid.UUID) -> int | None:
        worn = self.item_last_worn.get(item_id)
        return None if worn is None else (self.plan_date - worn).days

    def worn_within(self, days: int) -> set:
        """Items worn in the last ``days`` days — the set worth avoiding."""
        return {
            item_id for item_id in self.item_last_worn
            if (self.days_since_worn(item_id) or 0) <= days
        }


# --- Gathering --------------------------------------------------------------


async def resolve_timezone_for(session: AsyncSession, account_id: uuid.UUID) -> str:
    attributes = await style_context.confirmed_attributes(session, account_id)
    return clock.resolve_timezone(None, city=attributes.get("city"))


async def unavailable_items(session: AsyncSession, account_id: uuid.UUID, plan_date: date) -> list[uuid.UUID]:
    """Items that cannot be worn on this date.

    The latest event per item wins. ``available_from`` lets a user say "it is in
    the wash until Thursday" once, instead of remembering to clear it.
    """
    rows = (await session.execute(
        select(LaundryStateEvent)
        .where(LaundryStateEvent.account_id == account_id)
        .order_by(LaundryStateEvent.item_id, LaundryStateEvent.created_at.desc())
    )).scalars().all()
    latest: dict[uuid.UUID, LaundryStateEvent] = {}
    for row in rows:
        latest.setdefault(row.item_id, row)
    blocked: list[uuid.UUID] = []
    for item_id, row in latest.items():
        if row.state not in (LAUNDRY_IN_WASH, LAUNDRY_UNAVAILABLE):
            continue
        if row.available_from and row.available_from <= plan_date:
            continue
        blocked.append(item_id)
    return blocked


async def recent_wear(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date, window_days: int
) -> tuple[list[uuid.UUID], dict[uuid.UUID, date]]:
    """Looks worn in the window, and when each item was last worn.

    The *date* matters, not just the fact. Treating everything worn in the last
    seven days as equally "recent" means that by Thursday the whole wardrobe is
    recent and the signal is worthless. Wearing the same trousers again after
    five days is completely normal; wearing them again tomorrow is the thing
    worth avoiding.
    """
    if window_days <= 0:
        return [], {}
    since = plan_date - timedelta(days=window_days)
    rows = (await session.execute(
        select(OutfitSchedule).where(
            OutfitSchedule.account_id == account_id,
            OutfitSchedule.plan_date >= since,
            OutfitSchedule.plan_date < plan_date,
            OutfitSchedule.status.in_(["planned", "worn"]),
        ).order_by(OutfitSchedule.plan_date)
    )).scalars().all()
    looks: list[uuid.UUID] = []
    last_worn: dict[uuid.UUID, date] = {}
    for row in rows:
        if row.look_id and row.look_id not in looks:
            looks.append(row.look_id)
        for raw in row.item_ids or []:
            try:
                value = uuid.UUID(str(raw))
            except (ValueError, AttributeError, TypeError):
                continue
            # Rows are ordered by date, so the last write is the most recent.
            last_worn[value] = row.plan_date
    return looks, last_worn


async def day_events(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date, timezone_name: str
) -> list[DayEvent]:
    start, end = clock.day_bounds(plan_date, timezone_name)
    rows = (await session.execute(
        select(CalendarEvent).where(
            CalendarEvent.account_id == account_id,
            CalendarEvent.status == "active",
            CalendarEvent.starts_at >= start,
            CalendarEvent.starts_at < end,
        ).order_by(CalendarEvent.starts_at)
    )).scalars().all()
    return [
        DayEvent(
            id=row.id, title=row.title, starts_at=row.starts_at, ends_at=row.ends_at,
            all_day=row.all_day, location=row.location, occasion_key=row.occasion_key,
            dress_code_hint=row.dress_code_hint, confidence=row.inference_confidence,
            user_confirmed=row.user_confirmed,
        )
        for row in rows
    ]


async def latest_weather(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date
) -> WeatherSnapshot | None:
    return (await session.execute(
        select(WeatherSnapshot)
        .where(WeatherSnapshot.account_id == account_id, WeatherSnapshot.for_date == plan_date,
               WeatherSnapshot.provider == "manual")
        .order_by(WeatherSnapshot.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


async def latest_air_quality(
    session: AsyncSession, account_id: uuid.UUID, plan_date: date
) -> AirQualitySnapshot | None:
    from app.domains.planning.models import AirQualitySnapshot
    return (await session.execute(
        select(AirQualitySnapshot)
        .where(AirQualitySnapshot.account_id == account_id, AirQualitySnapshot.for_date == plan_date,
               AirQualitySnapshot.provider == "manual")
        .order_by(AirQualitySnapshot.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()


def _fresh(row: Any, now: datetime) -> bool:
    created = row.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds() <= ENVIRONMENT_CACHE_TTL_SECONDS


def _within_stale(row: Any, now: datetime) -> bool:
    created = row.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds() <= ENVIRONMENT_STALE_MAX_SECONDS


def _vague_location(location: str | None) -> bool:
    if not location or not location.strip():
        return False
    normalized = " ".join(location.casefold().split())
    return normalized in {"hall", "venue", "home", "office", "campus", "hotel", "club", "restaurant"}


async def _live_weather_row(session: AsyncSession, account_id: uuid.UUID, target: date, identity: str | None) -> WeatherSnapshot | None:
    if identity is None:
        return None
    rows = (await session.execute(
        select(WeatherSnapshot).where(
            WeatherSnapshot.account_id == account_id, WeatherSnapshot.for_date == target,
            WeatherSnapshot.provider == "open_meteo", WeatherSnapshot.source == "external_provider",
        ).order_by(WeatherSnapshot.created_at.desc())
    )).scalars().all()
    return next((row for row in rows if isinstance(row.raw, dict) and row.raw.get("location_identity") == identity), None)


async def _live_air_row(session: AsyncSession, account_id: uuid.UUID, target: date, identity: str | None) -> AirQualitySnapshot | None:
    if identity is None:
        return None
    rows = (await session.execute(
        select(AirQualitySnapshot).where(
            AirQualitySnapshot.account_id == account_id, AirQualitySnapshot.for_date == target,
            AirQualitySnapshot.provider == "open_meteo", AirQualitySnapshot.source == "external_provider",
        ).order_by(AirQualitySnapshot.created_at.desc())
    )).scalars().all()
    return next((row for row in rows if isinstance(row.raw, dict) and row.raw.get("location_identity") == identity), None)


def _weather_from_snapshot(row: WeatherSnapshot, *, stale: bool = False) -> WeatherReading:
    return WeatherReading(
        for_date=row.for_date, condition=row.condition, temp_min_c=row.temp_min_c,
        temp_max_c=row.temp_max_c, precipitation_chance=row.precipitation_chance,
        humidity=row.humidity, uv_index=row.uv_index, location=row.location,
        provider="open_meteo", source="external_provider",
        attribution="Weather data · Open-Meteo", is_stale=stale,
    )


def _air_from_snapshot(row: AirQualitySnapshot, *, stale: bool = False) -> AirQualityReading:
    return AirQualityReading(
        for_date=row.for_date, aqi=row.aqi, index_system=row.index_system,
        category=row.category, location=row.location,
        prominent_pollutant=row.prominent_pollutant, pm2_5=row.pm2_5, pm10=row.pm10,
        provider="open_meteo", source="external_provider",
        attribution="Air quality · Open-Meteo / CAMS", is_stale=stale,
    )


async def _resolve_weather_for_day(
    session: AsyncSession, account_id: uuid.UUID, target: date, manual: WeatherReading | None,
    cached: WeatherSnapshot | None, provider: OpenMeteoProvider | None, location: ResolvedLocation | None,
    timezone_name: str, identity: str | None, now: datetime, resolution_reason: str | None = None,
) -> tuple[WeatherReading | None, uuid.UUID | None, str | None]:
    if manual is not None:
        return manual, None, None
    if cached is not None and _fresh(cached, now):
        return _weather_from_snapshot(cached), cached.id, None
    if provider is None or location is None:
        reason = resolution_reason or ("environment_location_unresolved" if identity is None else "environment_provider_not_configured")
        if cached is not None and _within_stale(cached, now):
            return _weather_from_snapshot(cached, stale=True), cached.id, reason
        return None, None, reason
    try:
        readings = await provider.forecast_resolved(target=location, dates=[target], timezone_name=timezone_name)
        if not readings:
            raise ProviderUnavailable("Live weather is outside the forecast horizon.", provider=provider.name, reason="outside_forecast_horizon")
        reading = readings[0]
        snapshot = WeatherSnapshot(
            account_id=account_id, for_date=reading.for_date, location=reading.location,
            provider=reading.provider, source=reading.source, condition=reading.condition,
            temp_min_c=reading.temp_min_c, temp_max_c=reading.temp_max_c,
            precipitation_chance=reading.precipitation_chance, humidity=reading.humidity,
            uv_index=reading.uv_index,
            raw={"adapter": "open_meteo", "version": "v1", "location_identity": identity},
        )
        session.add(snapshot)
        await session.flush()
        return reading, snapshot.id, None
    except ProviderUnavailable as exc:
        if cached is not None and _within_stale(cached, now):
            return _weather_from_snapshot(cached, stale=True), cached.id, exc.reason
        return None, None, exc.reason


async def _resolve_air_quality_for_day(
    session: AsyncSession, account_id: uuid.UUID, target: date, manual: AirQualityReading | None,
    cached: AirQualitySnapshot | None, provider: OpenMeteoProvider | None, location: ResolvedLocation | None,
    timezone_name: str, identity: str | None, now: datetime, resolution_reason: str | None = None,
) -> tuple[AirQualityReading | None, uuid.UUID | None, str | None]:
    if manual is not None:
        return manual, None, None
    if cached is not None and _fresh(cached, now):
        return _air_from_snapshot(cached), cached.id, None
    if provider is None or location is None:
        reason = resolution_reason or ("environment_location_unresolved" if identity is None else "environment_provider_not_configured")
        if cached is not None and _within_stale(cached, now):
            return _air_from_snapshot(cached, stale=True), cached.id, reason
        return None, None, reason
    try:
        readings = await provider.air_quality_resolved(target=location, dates=[target], timezone_name=timezone_name)
        if not readings:
            raise ProviderUnavailable("Live air quality is outside the forecast horizon.", provider=provider.name, reason="outside_forecast_horizon")
        reading = readings[0]
        snapshot = AirQualitySnapshot(
            account_id=account_id, for_date=reading.for_date, location=reading.location,
            provider=reading.provider, source=reading.source, aqi=reading.aqi,
            index_system=reading.index_system, category=reading.category,
            prominent_pollutant=reading.prominent_pollutant, pm2_5=reading.pm2_5,
            pm10=reading.pm10,
            raw={"adapter": "open_meteo", "version": "v1", "location_identity": identity},
        )
        session.add(snapshot)
        await session.flush()
        return reading, snapshot.id, None
    except ProviderUnavailable as exc:
        if cached is not None and _within_stale(cached, now):
            return _air_from_snapshot(cached, stale=True), cached.id, exc.reason
        return None, None, exc.reason


async def _environment_readings(
    session: AsyncSession, account_id: uuid.UUID, target: date, city: str | None,
    timezone_name: str, *, explicit_location: bool = False, skip_live: bool = False,
) -> tuple[WeatherReading | None, uuid.UUID | None, str | None, AirQualityReading | None, uuid.UUID | None]:
    """Resolve Weather and AQI independently while sharing one target geocode."""
    manual_weather_rows = await weather_provider("manual", session, account_id).forecast(
        location=city, dates=[target], timezone_name=timezone_name,
    )
    manual_air_rows = await air_quality_provider("manual", session, account_id).air_quality(
        location=city, dates=[target], timezone_name=timezone_name,
    )
    manual_weather = manual_weather_rows[0] if manual_weather_rows else None
    manual_air = manual_air_rows[0] if manual_air_rows else None
    if manual_weather is not None and manual_air is not None:
        return manual_weather, None, None, manual_air, None

    identity = location_identity(city)
    if explicit_location and _vague_location(city):
        identity = None
    now = utcnow()
    weather_cached = await _live_weather_row(session, account_id, target, identity)
    air_cached = await _live_air_row(session, account_id, target, identity)
    provider: OpenMeteoProvider | None = None
    location: ResolvedLocation | None = None
    resolution_reason: str | None = None
    if not skip_live and LIVE_ENVIRONMENT_PROVIDER == "open_meteo":
        candidate = weather_provider("open_meteo", session, account_id)
        if isinstance(candidate, OpenMeteoProvider):
            provider = candidate
            weather_needed = manual_weather is None and not (weather_cached is not None and _fresh(weather_cached, now))
            air_needed = manual_air is None and not (air_cached is not None and _fresh(air_cached, now))
            if identity is not None and (weather_needed or air_needed):
                try:
                    location = await provider.resolve_location(city or "")
                except ProviderUnavailable as exc:
                    location = None
                    if exc.reason == "location_unresolved":
                        resolution_reason = "environment_location_unresolved"
                    elif exc.reason == "not_configured":
                        resolution_reason = "environment_provider_not_configured"
                    else:
                        resolution_reason = "environment_provider_error"

    weather, weather_id, weather_reason = await _resolve_weather_for_day(
        session, account_id, target, manual_weather, weather_cached, provider, location,
        timezone_name, identity, now, resolution_reason,
    )
    air, air_id, air_reason = await _resolve_air_quality_for_day(
        session, account_id, target, manual_air, air_cached, provider, location,
        timezone_name, identity, now, resolution_reason,
    )
    return weather, weather_id, weather_reason or air_reason, air, air_id


async def prefetch_environment(
    session: AsyncSession, account_id: uuid.UUID, dates: list[date], city: str | None,
    timezone_name: str, *, explicit_location: bool = False,
) -> bool:
    """Batch a weekly environment read into one geocode and one call/domain."""
    if not dates or LIVE_ENVIRONMENT_PROVIDER != "open_meteo":
        return True
    identity = location_identity(city)
    if explicit_location and _vague_location(city):
        return True
    provider = weather_provider("open_meteo", session, account_id)
    if not isinstance(provider, OpenMeteoProvider) or identity is None:
        return True
    manual_weather = await weather_provider("manual", session, account_id).forecast(
        location=city, dates=dates, timezone_name=timezone_name,
    )
    manual_air = await air_quality_provider("manual", session, account_id).air_quality(
        location=city, dates=dates, timezone_name=timezone_name,
    )
    manual_weather_dates = {row.for_date for row in manual_weather}
    manual_air_dates = {row.for_date for row in manual_air}
    now = utcnow()
    weather_cached = {day: await _live_weather_row(session, account_id, day, identity) for day in dates}
    air_cached = {day: await _live_air_row(session, account_id, day, identity) for day in dates}
    weather_dates = [day for day in dates if day not in manual_weather_dates and not (_fresh(weather_cached[day], now) if weather_cached[day] else False)]
    air_dates = [day for day in dates if day not in manual_air_dates and not (_fresh(air_cached[day], now) if air_cached[day] else False)]
    if not weather_dates and not air_dates:
        return True
    try:
        location = await provider.resolve_location(city)
    except ProviderUnavailable:
        return False
    failed = False
    if weather_dates:
        try:
            for reading in await provider.forecast_resolved(target=location, dates=weather_dates, timezone_name=timezone_name):
                session.add(WeatherSnapshot(
                    account_id=account_id, for_date=reading.for_date, location=reading.location,
                    provider=reading.provider, source=reading.source, condition=reading.condition,
                    temp_min_c=reading.temp_min_c, temp_max_c=reading.temp_max_c,
                    precipitation_chance=reading.precipitation_chance, humidity=reading.humidity,
                    uv_index=reading.uv_index,
                    raw={"adapter": "open_meteo", "version": "v1", "location_identity": identity},
                ))
        except ProviderUnavailable:
            failed = True
    if air_dates:
        try:
            for reading in await provider.air_quality_resolved(target=location, dates=air_dates, timezone_name=timezone_name):
                session.add(AirQualitySnapshot(
                    account_id=account_id, for_date=reading.for_date, location=reading.location,
                    provider=reading.provider, source=reading.source, aqi=reading.aqi,
                    index_system=reading.index_system, category=reading.category,
                    prominent_pollutant=reading.prominent_pollutant, pm2_5=reading.pm2_5,
                    pm10=reading.pm10,
                    raw={"adapter": "open_meteo", "version": "v1", "location_identity": identity},
                ))
        except ProviderUnavailable:
            failed = True
    await session.flush()
    return not failed


async def gather(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    plan_date: date | None = None,
    timezone_name: str | None = None,
    repetition_window_days: int = 7,
    moment: datetime | None = None,
    environment_location: str | None = None,
    explicit_environment_location: bool = False,
    skip_live_environment: bool = False,
) -> DayContext:
    """Build the full picture of one day."""
    attributes = await style_context.confirmed_attributes(session, account_id)
    resolved_tz = timezone_name or clock.resolve_timezone(None, city=attributes.get("city"))
    target = plan_date or clock.local_today(resolved_tz, moment=moment)

    owned, drafts = await style_context.confirmed_inventory(session, account_id)
    blocked = await unavailable_items(session, account_id, target)
    events = await day_events(session, account_id, target, resolved_tz)
    looks, last_worn = await recent_wear(session, account_id, target, repetition_window_days)

    context = DayContext(
        account_id=account_id, plan_date=target, timezone_name=resolved_tz,
        now_local=clock.local_now(resolved_tz, moment=moment),
        owned=owned, draft_count=drafts, unavailable_item_ids=blocked,
        events=events, recent_look_ids=looks, item_last_worn=last_worn,
        repetition_window_days=repetition_window_days, profile=attributes,
    )

    requested_location = environment_location if explicit_environment_location else (environment_location or attributes.get("city"))
    context.weather, context.weather_snapshot_id, weather_reason, context.air_quality, context.air_quality_snapshot_id = await _environment_readings(
        session, account_id, target, requested_location, resolved_tz,
        explicit_location=explicit_environment_location, skip_live=skip_live_environment,
    )
    if context.weather is not None and context.weather.provider == "manual":
        manual_snapshot = await latest_weather(session, account_id, target)
        context.weather_snapshot_id = manual_snapshot.id if manual_snapshot else None
    if context.air_quality is not None and context.air_quality.provider == "manual":
        manual_air_snapshot = await latest_air_quality(session, account_id, target)
        context.air_quality_snapshot_id = manual_air_snapshot.id if manual_air_snapshot else None
    context.weather_unavailable_reason = weather_reason

    _resolve_occasion(context)
    _note_gaps(context)
    return context


def _resolve_occasion(context: DayContext) -> None:
    """Decide what kind of day this is, and how sure we are."""
    event = context.primary_event
    if event is None:
        context.occasion_key = default_occasion_for(context.plan_date, context.profile.get("work_context"))
        # A default is a real decision, but a weak one — it is a guess from the
        # day of the week, not from anything the user told us about today.
        context.occasion_confidence = 0.65 if context.profile.get("work_context") else 0.5
        return

    if event.occasion_key:
        context.occasion_key = event.occasion_key
        context.occasion_confidence = 1.0 if event.user_confirmed else max(event.confidence, WEAK_MATCH)
    else:
        guess, confidence = infer_occasion(event.title, is_weekend=clock.is_weekend(context.plan_date))
        context.occasion_key = guess or default_occasion_for(context.plan_date, context.profile.get("work_context"))
        context.occasion_confidence = confidence if guess else 0.5
    context.dress_code = event.dress_code_hint


def _note_gaps(context: DayContext) -> None:
    reason_copy = {
        "environment_provider_not_configured": "Weather is not connected for today, so no weather-based assumptions were used.",
        "environment_provider_error": "Weather is temporarily unavailable, so no weather-based assumptions were used.",
        "environment_location_unresolved": "Weather could not be located for today, so no weather-based assumptions were used.",
        "outside_forecast_horizon": "Weather is not available that far ahead, so no weather-based assumptions were used.",
        "invalid_provider_response": "Weather is unavailable right now, so no weather-based assumptions were used.",
        "provider_error": "Weather is temporarily unavailable, so no weather-based assumptions were used.",
        "not_configured": "Weather is not connected for today, so no weather-based assumptions were used.",
    }
    gaps: list[str] = []
    if context.weather is None:
        gaps.append(reason_copy.get(
            context.weather_unavailable_reason or "",
            "No weather recorded for today, so nothing was ruled in or out on that basis.",
        ))
    if not context.events:
        gaps.append("Nothing on your calendar for today, so this is planned as a normal day.")
    if context.draft_count:
        gaps.append(f"{context.draft_count} inventory draft{'s are' if context.draft_count > 1 else ' is'} waiting to be confirmed and was not used.")
    if context.unavailable_item_ids:
        gaps.append(f"{len(context.unavailable_item_ids)} item{'s are' if len(context.unavailable_item_ids) > 1 else ' is'} marked unavailable and was skipped.")
    context.missing_information = gaps


# --- The cache key ----------------------------------------------------------


def cache_key(
    context: DayContext, *, material_extensions: dict[str, Any] | None = None
) -> str:
    """A hash of every input that could change the plan.

    Sorted and explicit rather than hashing the object: two contexts that mean
    the same thing must produce the same key regardless of row ordering, or the
    cache never hits and Today is slow for everyone.
    """
    payload = {
        "version": "phase5-v2",
        "date": context.plan_date.isoformat(),
        "timezone": context.timezone_name,
        # The compiler's routine modules branch on the time of day — skincare is
        # a morning action. Without this, a plan first built in the evening stays
        # a cache hit all the next morning and never gains its morning routine.
        # Four buckets a day, not a clock reading, so the cache still holds.
        "part_of_day": clock.part_of_day(context.now_local),
        "occasion": context.occasion_key,
        "dress_code": context.dress_code,
        "weather": None if context.weather is None else {
            "condition": context.weather.condition,
            "min": context.weather.temp_min_c,
            "max": context.weather.temp_max_c,
            "rain": context.weather.precipitation_chance,
            "humidity": context.weather.humidity,
            "uv_index": context.weather.uv_index,
        },
        "climate": {
            "climate_region": context.climate.climate_region,
            "season": context.climate.season,
            "daily_regime": context.climate.daily_regime,
            "temperature_band": context.climate.temperature_band,
            "moisture_regime": context.climate.moisture_regime,
            "observed_signals": sorted(context.climate.observed_signals),
        },
        "air_quality": None if context.air_quality is None else {
            "aqi": context.air_quality.aqi,
            "index_system": context.air_quality.index_system,
            "category": context.air_quality.category,
        },
        "events": sorted(
            f"{event.starts_at.isoformat()}|{event.title}|{event.occasion_key or ''}|{event.dress_code_hint or ''}"
            for event in context.events
        ),
        # Item *content*, not just identity. The compiler scores on colour,
        # fabric, formality and condition, so editing a garment has to
        # invalidate the day — an id list alone would serve the old outfit and
        # the old reasoning until something unrelated changed.
        "available_items": sorted(
            "|".join([
                str(item.id), item.display_name, item.category, item.condition,
                json.dumps(item.details, sort_keys=True, default=str),
            ])
            for item in context.available_owned()
        ),
        "unavailable_items": sorted(str(value) for value in context.unavailable_item_ids),
        # Drafts drive a "confirm these" action, so their count is material too.
        "draft_count": context.draft_count,
        "recent_looks": sorted(str(value) for value in context.recent_look_ids),
        "recent_items": sorted(str(value) for value in context.recent_item_ids),
        "repetition_window": context.repetition_window_days,
        "profile": {key: context.profile.get(key) for key in sorted(context.profile)},
    }
    if material_extensions:
        payload["material_extensions"] = material_extensions
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def changed_keys(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    return sorted({key for key in set(previous) | set(current) if previous.get(key) != current.get(key)})


def input_rows(context: DayContext) -> list[dict[str, Any]]:
    """The audit trail written to ``daily_plan_inputs``."""
    rows: list[dict[str, Any]] = [
        {"input_type": "date", "input_key": "plan_date", "value": context.plan_date.isoformat(), "source": "derived"},
        {"input_type": "date", "input_key": "timezone", "value": context.timezone_name, "source": "profile_confirmed" if context.profile.get("city") else "default"},
        {"input_type": "climate", "input_key": "climate_region", "value": context.climate.climate_region, "source": context.climate.region_source},
        {"input_type": "climate", "input_key": "calendar_prior", "value": context.climate.calendar_prior, "source": context.climate.season_source},
        {"input_type": "climate", "input_key": "season", "value": context.climate.season, "source": context.climate.season_source},
        {"input_type": "climate", "input_key": "season_source", "value": context.climate.season_source, "source": "derived"},
        {"input_type": "climate", "input_key": "confidence", "value": context.climate.confidence, "source": "derived"},
        {"input_type": "climate", "input_key": "temperature_band", "value": context.climate.temperature_band, "source": "observed"},
        {"input_type": "climate", "input_key": "moisture_regime", "value": context.climate.moisture_regime, "source": "observed"},
        {"input_type": "climate", "input_key": "daily_regime", "value": context.climate.daily_regime, "source": "observed"},
        {"input_type": "climate", "input_key": "reason", "value": context.climate.reason, "source": "derived"},
        {"input_type": "climate", "input_key": "observed_signals", "value": context.climate.observed_signals, "source": "observed"},
        {"input_type": "occasion", "input_key": "occasion_key", "value": context.occasion_key, "source": "calendar" if context.events else "derived"},
        {"input_type": "occasion", "input_key": "occasion_confidence", "value": context.occasion_confidence, "source": "derived"},
        {"input_type": "weather", "input_key": "condition", "value": context.weather.condition if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "weather", "input_key": "temp_min_c", "value": context.weather.temp_min_c if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "weather", "input_key": "temp_max_c", "value": context.weather.temp_max_c if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "weather", "input_key": "precipitation_chance", "value": context.weather.precipitation_chance if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "weather", "input_key": "humidity", "value": context.weather.humidity if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "weather", "input_key": "uv_index", "value": context.weather.uv_index if context.weather else None, "source": context.weather.source if context.weather else "unavailable"},
        {"input_type": "air_quality", "input_key": "aqi", "value": context.air_quality.aqi if context.air_quality else None, "source": context.air_quality.source if context.air_quality else "unavailable"},
        {"input_type": "air_quality", "input_key": "index_system", "value": context.air_quality.index_system if context.air_quality else None, "source": context.air_quality.source if context.air_quality else "unavailable"},
        {"input_type": "air_quality", "input_key": "category", "value": context.air_quality.category if context.air_quality else None, "source": context.air_quality.source if context.air_quality else "unavailable"},
        {"input_type": "air_quality", "input_key": "prominent_pollutant", "value": context.air_quality.prominent_pollutant if context.air_quality else None, "source": context.air_quality.source if context.air_quality else "unavailable"},
        {"input_type": "calendar", "input_key": "event_count", "value": len(context.events), "source": "calendar"},
        {"input_type": "inventory", "input_key": "available_item_count", "value": len(context.available_owned()), "source": "inventory"},
        {"input_type": "inventory", "input_key": "unavailable_item_count", "value": len(context.unavailable_item_ids), "source": "laundry"},
        {"input_type": "history", "input_key": "recent_look_count", "value": len(context.recent_look_ids), "source": "outfit_schedule"},
        {"input_type": "history", "input_key": "repetition_window_days", "value": context.repetition_window_days, "source": "preference"},
    ]
    for key, value in sorted(context.profile.items()):
        rows.append({"input_type": "profile", "input_key": key, "value": value, "source": "profile_confirmed"})
    for event in context.events:
        rows.append({
            "input_type": "calendar_event", "input_key": event.title[:64],
            "value": {"starts_at": event.starts_at.isoformat(), "occasion_key": event.occasion_key, "confidence": event.confidence},
            "source": "user_declared" if event.user_confirmed else "inferred",
        })
    return rows
