# Garmin MCP Server — Design

Date: 2026-08-09

## Purpose

Personal MCP server exposing Garmin Connect data (sleep, HR, HRV, stress, VO2max,
activities, daily stats) to Claude, so Claude can correlate metrics across
days — e.g. sleep quality vs training performance, or inferred "night out"
patterns vs recovery metrics. Runs locally, for single-user (owner) use.

## Non-goals

- Not a public/multi-user service.
- Not using the official Garmin Developer API (requires business approval,
  not suited for personal single-account use).
- No web UI — interaction happens entirely through Claude via MCP tools.

## Architecture

```
garmin-mcp/
  garmin_client.py   # Garmin login + data fetch (python-garminconnect)
  db.py               # SQLite schema + upsert/query helpers
  stats.py            # pure functions: correlation, moving average, anomaly detection
  server.py           # MCP server: tool definitions, thin orchestration layer
  tests/
  .env.example
  requirements.txt
```

Language: Python. Transport: stdio (local subprocess, launched by Claude
Desktop/Code per its config — no network port, no auth needed since both
processes run on the same machine under the same user).

### Why stdio over HTTP

stdio: client (Claude Desktop) spawns the server as a subprocess and talks
over stdin/stdout using JSON-RPC (MCP protocol). No open port, no network
auth, simplest and most secure option for local personal tools. HTTP
transport would be needed only if the server had to be reachable from a
different machine or by multiple concurrent MCP clients — not the case here,
noted for future reference only.

## Data storage

SQLite file at `~/.garmin-mcp/data.db`. Local only, never leaves the
machine. Chosen over live-fetch-per-query because:

- Garmin API/session has rate limits and login overhead.
- Historical correlation needs data older than what's convenient to
  re-fetch live each time.
- SQLite requires no separate DB server/install.

### Schema

```
daily_stats(date PK, resting_hr, stress_avg, stress_max, body_battery_max,
            body_battery_min, steps, vo2max_running, vo2max_cycling, weight)
sleep(date PK, bedtime, wake_time, duration_min, deep_min, light_min,
      rem_min, awake_min, sleep_score, avg_hrv)
activities(activity_id PK, date, type, duration_min, distance_km, avg_hr,
           max_hr, calories, training_effect, load)
hrv(date PK, overall_hrv, status, baseline_low, baseline_high)
nights_out(date PK, source ENUM('inferred','manual'), confidence,
           signals_json, note)
sync_log(date PK, synced_at, status)
```

`date` is the join key across all tables for cross-metric correlation.
Missing rows (e.g. no workout that day) are simply absent — no forced NULLs.
`sync_log` prevents redundant re-fetching of already-synced days.

## Credentials

`GARMIN_EMAIL` / `GARMIN_PASSWORD` via environment variables or a local
`.env` file (gitignored, never committed). Garmin session/token caching is
handled by the `garminconnect` library itself to avoid re-login on every
call.

## MCP tools exposed

- `sync_garmin_data(start_date, end_date, force=False)` — fetch from Garmin,
  upsert into SQLite. Commits per-day (not one big batch) so partial
  failures don't lose progress. Skips already-synced days unless `force`.
- `get_daily_summary(date)` / `get_range_summary(start, end)` — merged view
  across daily_stats, sleep, hrv for a date or range.
- `get_sleep(start, end)`
- `get_activities(start, end)`
- `get_hrv(start, end)`
- `get_vo2max(start, end)`
- `get_stress(start, end)`
- `detect_nights_out(start, end)` — infers nights out from signal
  combinations (late/short sleep, elevated overnight HR, HRV below personal
  baseline). Returns dates with confidence + which signals triggered it.
- `log_night_out(date, note)` — manual override/annotation, stored with
  `source='manual'`.
- `correlate(metric_a, metric_b, start, end)` — Pearson correlation between
  two metrics over a date range.
- `moving_average(metric, window, start, end)`

## Error handling

- Garmin login failure (bad creds, 2FA prompt) → clear error in tool
  result, no retry loop.
- Rate limiting / Garmin outage during sync → per-day commits mean partial
  progress is saved; failed days reported back explicitly.
- Missing day for a metric → absent row, not a forced NULL; queries handle
  this naturally (no special-casing needed).
- `correlate` with fewer than 5 data points → explicit error instead of a
  statistically meaningless result.

## Testing

- `stats.py`: unit tests with synthetic data, no Garmin dependency.
- `db.py`: tests against a temporary SQLite file, verifying upsert
  idempotency.
- `garmin_client.py`: manual one-off testing only (requires real
  credentials), not part of automated CI.
- `server.py`: lightweight integration test — start server, call tools
  through a test MCP client, assert response shape.

## Learning goals (secondary)

Alongside building this, the user wants to understand MCP fundamentals:
stdio vs HTTP transport, how Claude Desktop/Code discovers and launches
local servers via config, and what changes for a networked/remote
deployment. These are covered inline above and can be expanded during
implementation walkthroughs.
