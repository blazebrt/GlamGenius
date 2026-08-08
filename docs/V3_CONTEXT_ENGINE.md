# V3 Context Engine

## Overview

The Context Engine is the environmental foundation that future GlamGenius expert systems will consume. Its primary role is to establish a trustworthy internal representation of the user's environment. 

We do **not** build weather forecasting, UV forecasting, or AQI measurement systems. Instead, external providers supply the raw environmental facts. The core responsibility of the Context Engine in GlamGenius is:

**Normalization → Provenance → Interpretation → Caching → Product Intelligence**

## Key Entities

### DayContext
The `DayContext` acts as the deterministic snapshot of the user's current environment for a given day. It is used to influence recommendations, routines, and rules. It captures:
* Local date and time
* Location (latitude/longitude, timezone)
* Weather (conditions, temperature, humidity, precipitation)
* UV Index
* Indian seasonal and climate context
* Air Quality Index (AQI)

#### Climate Normalization
The engine interprets standard weather and translates it into regional context (e.g., Indian seasons). It features dynamic overrides based on real-time weather:
* High temperature/humidity forces a transition to `summer` or `monsoon` regardless of the calendar month.
* South Peninsular India bypasses the `winter` season and transitions directly to `autumn`.
* Low temperatures forcibly override the season to `winter` when applicable.

### Air Quality (AQI)
AQI provides a measure of air quality, primarily focusing on pollutants such as PM2.5 and PM10, which are highly relevant in Indian urban contexts. 
* Represented via `AirQualitySnapshot` in the database.
* Exposed via `AirQualityInput` and integrated into the `DayContext`.
* Serialized alongside the user's plan via the `serialize_air_quality` helper.
* Normalizes data exclusively for the `india_naqi` system (categories: `good`, `satisfactory`, `moderate`, `poor`, `very_poor`, `severe`). Other systems or negative indices fall back to `unknown`.

### Provenance & Fallbacks
The system must gracefully handle missing environmental information. Provenance is tracked to understand whether a specific data point (like AQI or Weather) came from a real-time provider, a historical average, or was missing entirely.

## Deterministic Caching
The context directly impacts caching behavior. The `cache_key` computed for a plan incorporates deterministic factors of the `DayContext`, including the user's location, date, weather, and AQI severity level. This ensures that expert systems yield deterministic and cacheable outputs for the same contextual inputs.

## Future Integration
Currently, the raw data inputs are supplied via our endpoints (e.g., `POST /today/air-quality` and `POST /today/weather`). In future phases, these inputs will be fed by actual integrations with third-party providers.
