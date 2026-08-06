"""What "today" means for a particular person.

This is the least glamorous file in Phase 5 and the one most likely to cause a
visible bug, so it is deliberate rather than incidental.

The product is for India. At 20:00 UTC it is already 01:30 tomorrow in Kolkata,
so a plan generated from ``date.today()`` on a UTC server would show a user the
wrong day's outfit for five and a half hours out of every twenty-four. Every
date in this domain is therefore resolved through :func:`local_today`, never
from ``date.today()``.

``zoneinfo`` is in the standard library on Python 3.11, so none of this adds a
dependency.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.shared.database.base import utcnow

# India has a single timezone. It is the default for every account, and the
# only one most users will ever need.
DEFAULT_TIMEZONE = "Asia/Kolkata"

# Cities we can resolve without asking. Everything here maps to the same zone —
# that is the point, and it lets a user who typed their city get correct dates
# without a separate timezone question during onboarding.
INDIAN_CITIES = {
    "mumbai", "delhi", "new delhi", "bengaluru", "bangalore", "hyderabad",
    "chennai", "kolkata", "calcutta", "pune", "ahmedabad", "jaipur", "surat",
    "lucknow", "kanpur", "nagpur", "indore", "thane", "bhopal", "visakhapatnam",
    "patna", "vadodara", "ghaziabad", "ludhiana", "agra", "nashik", "faridabad",
    "meerut", "rajkot", "varanasi", "srinagar", "amritsar", "noida", "gurugram",
    "gurgaon", "chandigarh", "coimbatore", "kochi", "cochin", "guwahati",
    "bhubaneswar", "dehradun", "mysuru", "mysore", "goa", "panaji",
}


def resolve_timezone(name: str | None, city: str | None = None) -> str:
    """Pick a timezone from what we actually know about the user.

    Order: an explicit timezone, then a recognised city, then India. An
    unrecognised timezone falls back rather than raising — a bad string in a
    profile must not stop someone seeing their day.
    """
    if name:
        try:
            ZoneInfo(name)
            return name
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            pass
    if city and " ".join(city.lower().split()) in INDIAN_CITIES:
        return DEFAULT_TIMEZONE
    return DEFAULT_TIMEZONE


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_now(timezone_name: str | None, *, moment: datetime | None = None) -> datetime:
    """The current wall-clock time where this person is."""
    return (moment or utcnow()).astimezone(zone(timezone_name))


def local_today(timezone_name: str | None, *, moment: datetime | None = None) -> date:
    """The calendar date it is *for them*, which is the only one that matters."""
    return local_now(timezone_name, moment=moment).date()


def day_bounds(plan_date: date, timezone_name: str | None) -> tuple[datetime, datetime]:
    """The UTC instants a local calendar day starts and ends.

    Calendar events are stored as timezone-aware instants, so "which events are
    on Tuesday" is a range query and the range has to be built in the user's
    zone, not the server's.
    """
    tz = zone(timezone_name)
    start = datetime.combine(plan_date, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return start, end


def week_start(plan_date: date) -> date:
    """The Monday of the week containing this date.

    The planner runs Monday to Sunday, as the brief specifies.
    """
    return plan_date - timedelta(days=plan_date.weekday())


def week_dates(start: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(7)]


def part_of_day(moment: datetime) -> str:
    """Morning / afternoon / evening / night, for wording and for defaults."""
    hour = moment.hour
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    if hour < 21:
        return "evening"
    return "night"


def is_weekend(plan_date: date) -> bool:
    return plan_date.weekday() >= 5


def season_for(plan_date: date) -> str:
    """The Indian season, which is what the wardrobe is tagged against.

    Four seasons rather than the meteorological six: these are the words people
    actually use when they tag a kurta, and they line up with the season values
    the inventory already accepts.
    """
    month = plan_date.month
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "summer"
    if month in (6, 7, 8, 9):
        return "monsoon"
    return "autumn"
