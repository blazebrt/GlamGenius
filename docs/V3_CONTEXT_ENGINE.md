# V3 Context Engine

**Phase:** V3-01 — Context Foundation
**Branch:** `v3/v3-01-context-foundation`
**PR:** #58

## Purpose and boundary

The Context Engine produces a deterministic, auditable description of the
user's day. It persists user-supplied weather and air-quality snapshots,
normalizes those observations, resolves conservative regional context, and
records the facts used to compile a daily plan.

Three rules are permanent:

> Reliable current observed conditions outrank seasonal assumptions for appearance decisions.

> Regional climatology outranks generic India-wide calendar assumptions.

> Unknown data remains unknown.

Context does not choose clothing or score fabrics. The product boundary is:

```text
Context Engine (environmental facts)
    ↓
future Garment/Fabric Intelligence
    ↓
future Style Suitability Engine
```

## Current environmental providers

V3-01 ships only manual, stored providers:

- `POST /api/v2/today/weather` stores `WeatherSnapshot` rows.
- `POST /api/v2/today/air-quality` stores `AirQualitySnapshot` rows.
- `StoredWeatherProvider` and `StoredAirQualityProvider` read the latest
  snapshot for the requested account and date.

There is no live weather, IMD, or AQI integration. A missing snapshot produces
no reading; it is never replaced with invented normal weather or good air.

Provider readings retain normalized fields plus the stored raw provider
payload. Raw payloads remain at the provider/snapshot boundary and are not
copied into `DailyPlanInput` or cache keys.

## Regional climate architecture

`ClimateRegion` is a product-level routing enum:

```text
north_plains
northwest_arid
western_himalaya
central_india
west_coast
east_gangetic
northeast
deccan_interior
southeast_peninsula
islands
unknown_india
```

These are conservative GlamGenius product profiles. They do not replace IMD
meteorological subdivisions and are not a complete climatology database.

`resolve_climate_region(location)` uses centralized, reviewed city and state
alias tables. City matches take precedence over broad state matches, which
allows a known coastal city to resolve correctly even when its state spans
more than one climate pattern. Inputs that do not match the small reviewed
tables resolve to `unknown_india`; the resolver does not guess.

Location comes from the stored weather reading when it is present, otherwise
from the confirmed profile city.

### Reviewed seasonal profiles

Only `north_plains` has an explicitly reviewed regional seasonal profile in
V3-01:

| Months | Calendar prior |
|---|---|
| December–February | `winter` |
| March–June | `summer` |
| July–September | `monsoon` |
| October–November | `autumn` |

The profile is a regional styling/climate prior for the North Indian plains,
not a scientific declaration for India as a whole.

`REGIONAL_SEASON_PROFILES` is intentionally small and can grow only as new
profiles are reviewed. Other regions use `season_source=generic_fallback` with
reduced confidence to preserve the existing season vocabulary required by
inventory/style code. They never silently borrow the `north_plains` profile.

Examples:

- Chennai → `southeast_peninsula`, generic fallback.
- Mumbai → `west_coast`, generic fallback.
- Jaipur → `northwest_arid`, generic fallback.
- Bengaluru → `deccan_interior`, generic fallback.
- Unmapped location → `unknown_india`, generic fallback, reduced confidence.

## ClimateContext contract

The final context separates four concepts that must not be conflated:

- `climate_region`: broad geographic product profile.
- `calendar_prior`: expected seasonal label from a reviewed regional profile
  or the labeled generic fallback.
- `season`: backward-compatible seasonal value exposed to existing code. It is
  currently the calendar prior and is never rewritten by one day's weather.
- `daily_regime`: what the observed conditions behave like today.

The complete object contains:

```text
season
calendar_prior
climate_region
region_source
season_source
temperature_band
moisture_regime
daily_regime
condition
confidence
reason
location
signals
observed_signals
```

### Temperature band

`temp_max_c` remains an observed daily fact:

| Range | Band |
|---|---|
| missing | `unknown` |
| below 15°C | `cold` |
| 15°C to below 25°C | `mild` |
| 25°C to below 35°C | `warm` |
| 35°C and above | `hot` |

Temperature does not redefine season. A 36°C January day in Delhi remains
`season=winter`, while producing `temperature_band=hot`, an `unusually_hot`
observed signal, a hot daily regime, and reduced confidence.

### Moisture regime

Thresholds are centralized in `environment.py`:

- rainy condition or precipitation probability at least 60% → `wet`
- otherwise humidity at least 70% → `humid`
- otherwise humidity at most 35% → `dry`
- other known humidity → `normal`
- insufficient information → `unknown`

Rain never redefines season. Heavy April rain in Delhi remains
`season=summer`, but the daily regime becomes wet and confidence is reduced.

### Daily regime

The daily regime combines the independently derived temperature and moisture
states, for example `cold_dry`, `warm_humid`, `warm_wet`, or `hot_dry`.
Normal moisture uses the temperature label (`mild`, `warm`, and so on).
Missing temperature or moisture produces `unknown` rather than an invented
default.

This field is more useful than season for future appearance decisions. For
example, Delhi in August at 32°C, 85% humidity, and likely rain remains in its
monsoon calendar prior and resolves to `warm_wet` from observations.

### Confidence

Confidence uses a small deterministic hierarchy rather than fake precision:

- reviewed regional profile plus observations → `0.9`
- reviewed regional profile without current weather → `0.6`
- generic fallback plus observations → `0.6`
- generic fallback without current weather → `0.4`
- strong conflict between observations and the calendar prior reduces a
  reviewed result to `0.6` and a fallback result to `0.4`

The human-readable `reason` identifies whether the result used a reviewed
profile, generic fallback, missing observations, or conflicting observations.

## UV and air quality

`uv_index` is optional and validated in the range 0–20. Its end-to-end path is:

```text
WeatherInput
  → WeatherSnapshot
  → StoredWeatherProvider
  → WeatherReading / DayContext.weather
  → DailyPlanInput and serialized Today response
```

Missing UV remains `None`. Context provides no UV medical advice.

Manual AQI accepts 0–2000 and supports the Indian NAQI vocabulary. The stored
reading preserves AQI, index system, category, prominent pollutant, PM2.5,
PM10, source, provider, and raw payload. `DailyPlan.air_quality_snapshot_id`
links the compiled plan to the exact stored snapshot used by Today.

V3-01 records air quality context only. It does not invent an AQI card,
recommendation, skin rule, hair rule, or clothing rule.

## Cache materiality

The deterministic daily cache includes normalized values that can materially
change context:

- weather: condition, minimum/maximum temperature, humidity, precipitation
  probability, and UV
- climate: region, season, daily regime, temperature band, moisture regime,
  and material observed signals
- air quality: AQI, index system, and category

Raw payload, timestamps, ingestion metadata, JSON key order, and irrelevant
provider metadata do not participate. PM fields are retained in the reading
but are not cache-material until a later domain intentionally consumes them.

## Audit provenance

`DailyPlanInput` records normalized environmental decisions:

- climate region, calendar prior, season, season source, confidence,
  temperature band, moisture regime, daily regime, reason, observed signals
- weather condition, minimum/maximum temperature, precipitation, humidity, UV
- AQI, index system, category, prominent pollutant

The row `source` records whether the input was derived, observed, unavailable,
user-declared, or resolved from a known city/state. Raw payload is excluded.

## Future garment/fabric boundary

Future garment suitability needs more than a fabric name. Its inputs may
include fabric composition, weight, weave or knit, lining, fit, layering role,
water sensitivity, temperature, humidity, rain, daily regime, UV, event, and
exposure. Heavy cotton denim and light cotton voile must not receive identical
thermal or breathability treatment.

No fabric suitability, garment scoring, outfit-ranking change, or rule such as
`winter → wool` / `summer → cotton` is implemented in V3-01.

The weather provider value object can be extended later with optional
provider-supplied `feels_like_temperature` and `wind_speed`. V3-01 does not
fabricate or calculate either value and does not require a database migration
for them now.

## Persistence and migration

Migration `ee2713cab5de` remains directly after `0001`. It creates:

- `air_quality_snapshots`
- `daily_plans.air_quality_snapshot_id`
- `weather_snapshots.uv_index`

The migration is reversible. No migration is required for in-memory
`ClimateContext` fields.

## Limitations

- no complete climatology database
- no live IMD, weather, or AQI provider
- no fine-grained elevation model
- no feels-like temperature unless a future provider supplies it
- no wind speed unless a future provider supplies it
- no fabric suitability engine
- no Style ranking changes in this phase
- no medical AQI or UV interpretation
- only the North Plains seasonal profile has been explicitly reviewed
