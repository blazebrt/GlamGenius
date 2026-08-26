# VC-09 — Cross-domain attention and notifications

VC-09 adds a read-only Attention Agenda over the existing DailyPlan and Event
Ready authorities. It uses deterministic typed tiers (blocking, event, today,
upkeep, optional), a seven-local-day event horizon, stable semantic keys, and a
maximum of one featured item plus two secondary items. The agenda never creates
Style, Care, weather, maintenance, or purchase decisions.

`GET /api/v2/today/agenda` is intentionally separate from `GET /today`, so
opening Today does not queue or send a notification. Completing an agenda item
continues to use the original Today/Event Ready completion endpoint.

## Notification delivery

Native push is opt-in independently of the in-app preference. The app requests
OS permission only after the customer enables native notifications, registers an
Expo token through the authenticated device endpoint, and never returns or logs
the token. The PostgreSQL delivery table is a durable outbox with truthful
`suppressed`, `queued`, `provider_accepted`, and `provider_failed` states.

Run the bounded worker with:

```text
python -m app.workers.notifications
```

The worker processes accounts during their preferred local hour only, observes
quiet hours, daily caps, module and maintenance consent, and deliberately skips
late catch-up. This repository does not contain a production scheduler; a host
cron/managed scheduler must invoke the command hourly. CI and tests mock the
Expo transport and do not claim real-device delivery.

Notification devices are classified as secret-excluded from privacy export;
preferences and safe delivery history are included. Account deletion removes
preferences, deliveries, and devices through explicit cleanup and cascading
foreign keys.
