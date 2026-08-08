# V3 Context Engine

**Phase:** V3-01 — Context Foundation (branch `v3/v3-01-context-foundation`, PR #58)

---

## Purpose

The Context Engine is the environmental layer that every GlamGenius expert
system reads before it acts.  Its contract is simple:

> *Produce a trustworthy, deterministic, cache-safe snapshot of the user's day.*

GlamGenius does **not** forecast weather or measure air quality.  External
providers or the user herself supply the raw facts.  The engine's job is:

```
raw input → normalise → record provenance → interpret → derive cache key
```

---

## Architecture

### DayContext

`DayContext` (in `app/domains/planning/context.py`) is the single object passed
to the compiler.  It aggregates:

| Field | Type | Source |
|---|---|---|
| `account_id` | UUID | token |
| `plan_date` | `date` | query param / today |
| `timezone_name` | str | profile |
| `lat`, `lon` | float \| None | profile |
| `weather` | `WeatherReading \| None` | provider registry |
| `air_quality` | `AirQualityReading \| None` | provider registry |
| `events` | list[CalendarEvent] | database |
| `climate` | `ClimateContext` | `resolve_climate_context()` |

`DayContext.gather(session, account_id, plan_date)` is the single entry point.
It calls each provider once and composes the snapshot.

### Cache key

`cache_key(ctx)` hashes every field that materially affects recommendations.
Raw provider payloads are **never** part of the key — only the normalised
values that reach the compiler:

* plan date + timezone
* weather condition + temp band + humidity band + UV band
* AQI category (not the raw integer)
* `ClimateContext.season` + `observed_signals`
* event titles + occasion keys

This means:
- Two fetches returning numerically different AQIs in the same category hit
  the same cache entry.
- A `uv_index` change that moves the plan across a UV band invalidates the
  cache.

---

## Provider Registry

Both weather and air-quality use a unified provider abstraction defined in
`app/domains/planning/providers/`.

```python
class WeatherProvider(Protocol):
    name: str
    async def fetch(self, account_id, for_date) -> WeatherReading | None: ...

class AirQualityProvider(Protocol):
    name: str
    async def fetch(self, account_id, for_date) -> AirQualityReading | None: ...
```

`KNOWN_WEATHER_PROVIDERS` and `KNOWN_AIR_QUALITY_PROVIDERS` are registries
keyed by name string.  `DayContext.gather()` resolves the configured provider
by name at runtime, making it trivially mockable in tests
(`fake_provider` fixture).

### Manual / StoredWeatherProvider

`providers/manual.py` contains `StoredWeatherProvider` and
`StoredAirQualityProvider`, which read from `WeatherSnapshot` and
`AirQualitySnapshot` rows written by the user via:

- `POST /api/v2/today/weather`
- `POST /api/v2/today/air-quality`

Every reading carries `provenance`:

```python
{
  "provider": "manual",
  "source": "user_declared",
  "recorded_at": "2026-02-16T08:00:00Z"
}
```

Provenance propagates all the way to `DayContext.weather.provenance` so the
compiler can distinguish a user-declared forecast from an inferred default.

---

## UV Index — End-to-End Path

```
WeatherInput.uv_index (schema)
  → service.record_weather()
  → WeatherSnapshot.uv_index (nullable DB column, migration ee2713cab5de)
  → StoredWeatherProvider.fetch()
  → WeatherReading.uv_index
  → DayContext.weather.uv_index
  → cache_key() — UV band (≥3 / ≥6 / ≥8 / ≥11)
  → compiler receives uv_index
```

`uv_index` is nullable at every layer.  Absence is never silently treated as
zero — it is absent.

---

## Air Quality

### Schema

`AirQualityInput` (validated by Pydantic, `extra="forbid"`):

| Field | Constraint |
|---|---|
| `aqi` | 0 – 2000 |
| `index_system` | `"india_naqi"` or `"unknown"` |
| `prominent_pollutant` | optional, max 32 chars |
| `pm2_5`, `pm10` | optional, non-negative |

Only `india_naqi` is normalised to named categories.

### India NAQI categories

| AQI range | Category |
|---|---|
| 0 – 50 | Good |
| 51 – 100 | Satisfactory |
| 101 – 200 | Moderate |
| 201 – 300 | Poor |
| 301 – 400 | Very Poor |
| 401 + | Severe |

### Database

`AirQualitySnapshot` (migration `ee2713cab5de`):

- `account_id`, `for_date` — unique per user-day
- `aqi`, `index_system`, `prominent_pollutant`, `pm2_5`, `pm10`
- `source` — always `"user_declared"` for manual entries

---

## Climate Context

`resolve_climate_context(plan_date, lat, lon, weather)` in
`app/domains/planning/environment.py` returns a `ClimateContext`:

```python
@dataclass
class ClimateContext:
    season: Season          # calendar-prior season
    calendar_prior: Season  # unmodified calendar season
    reason: str             # human-readable explanation
    confidence: float       # 0.0 – 1.0
    observed_signals: frozenset[str]  # {"humid", "dry", "rain_likely", "hot"}
```

### Conservative season rule

> *One abnormal day's weather must not redefine India's season.*

The engine uses calendar priors as the anchor.  Weather signals add
`observed_signals` (e.g. `"humid"`) that the compiler uses to adjust clothing
choices, but they do **not** override the season unless the signal is extreme
and sustained (defined by configurable thresholds, not live forecasts).

### South India autumn

Regions south of 14 °N skip `winter` and transition from `monsoon` (Oct–Nov
NE monsoon) directly to `spring`.  The NE monsoon window (Oct 15 – Dec 15)
is mapped to `autumn` rather than `monsoon` for styling purposes.

---

## API Surface (V3-01 additions)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v2/today/weather` | Record user-declared weather; triggers replan |
| `POST` | `/api/v2/today/air-quality` | Record user-declared AQI; triggers replan |

Both return `{ "weather"/"air_quality": <serialised row>, "plan": <DayResponse> }`.

---

## Database Migrations

All V3-01 schema changes live in a **single** migration:

```
ee2713cab5de — add_air_quality_snapshots_and_system_context
```

It adds:
- `air_quality_snapshots` table
- `weather_snapshots.uv_index` nullable column

The migration is linear (no branch points) and is safe to apply in a
rolling deployment (all columns are nullable or have defaults).

---

## Testing

| Test file | What it covers |
|---|---|
| `test_domain_planning_environment.py` | `resolve_climate_context` — conservative season rules, observed signals, South India autumn, confidence decay |
| `test_domain_planning.py` | End-to-end: weather POST, AQI POST, UV range enforcement, plan caching, AQI validation rejections |

Run locally:

```bash
docker compose exec backend pytest tests/test_domain_planning_environment.py \
    tests/test_domain_planning.py -v
```

---

## Constraints (permanent)

- **No live third-party weather/AQI API** is called in V3-01.  Provider
  integration is a future phase.
- **No production dependency** was added.  The feature uses only packages
  already in `requirements.txt`.
- **`package.json` and `yarn.lock` are untouched.**
- The raw provider payload never enters the cache key.

---

*Last updated: V3-01.3 closure (2026-08-08)*
