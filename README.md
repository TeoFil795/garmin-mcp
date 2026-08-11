# garmin-mcp

A local MCP server that exposes Garmin Connect data — sleep, heart rate, HRV,
stress, VO2max, activities — to Claude, plus the statistical analysis built on
top of it.

It was written to answer one question: **does how hard I train change how well
I sleep?** The short answer, on a year of one person's data, is no. What the
analysis actually turned up is in [Findings](#findings).

> The data stays on your machine. Nothing is uploaded anywhere; the server runs
> as a local subprocess and reads a SQLite file in your home directory.

## What it does

`sync_garmin_data` pulls a date range from Garmin Connect into a local SQLite
cache (`~/.garmin-mcp/data.db`). Everything else reads from that cache, so
queries are fast and you are not re-hitting Garmin's API — which rate-limits
aggressively — every time you ask a question.

### Tools

| Tool | What it returns |
|---|---|
| `sync_garmin_data(start, end, force=False)` | Fetches and caches. Commits per day, so a failure partway through keeps what it already wrote. Skips days already synced unless `force`. |
| `get_daily_summary(date)` | Everything for one day, merged |
| `get_range_summary(start, end)` | The same across a range |
| `get_sleep` / `get_activities` / `get_hrv` / `get_vo2max` / `get_stress` | One table each |
| `detect_nights_out(start, end)` | Nights flagged as "was out late", inferred from physiology alone |
| `log_night_out(date, note)` | Manual override |
| `correlate(metric_a, metric_b, start, end)` | Spearman correlation between two metrics |
| `moving_average(metric, window, start, end)` | Rolling mean |

`detect_nights_out` compares four signals — bedtime, sleep duration, resting
heart rate and HRV — against your own 30-day baseline, and flags a night when
at least two of them deviate. It knows nothing about the calendar.

## Setup

```bash
python3 -m venv venv && source venv/bin/activate    # Python 3.10+
pip install -r requirements.txt
cp .env.example .env                                 # then fill in your credentials
pytest -v
```

`.env` is gitignored. Credentials are read from the environment; they are never
written to the database or logged.

### Register with Claude Code

```bash
claude mcp add garmin -s user \
  -- /absolute/path/to/garmin-mcp/venv/bin/python /absolute/path/to/garmin-mcp/server.py
```

Restart Claude Code, then ask it to sync a range and query away.

No credentials go on that command line. `server.py` loads the `.env` sitting
next to it, anchored to the script's own path rather than the working
directory — an MCP client launches the server with a CWD you do not control.
Passing `-e GARMIN_PASSWORD=…` would work too, but it writes your password in
plaintext into the client's config file, so prefer the `.env`.

### Register with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp/venv/bin/python",
      "args": ["/absolute/path/to/garmin-mcp/server.py"]
    }
  }
}
```

Credentials come from `.env`, for the same reason as above.

Both use **stdio**: the client launches the server as a subprocess and talks to
it over stdin/stdout. No port is opened and no authentication layer is needed,
because both processes run as you on your own machine. MCP also supports an
HTTP transport, which you would need only to reach the server from another
machine — not the case here.

## Findings

One person, 349 nights, 175 workouts. Full write-up with charts:
**[the analysis](https://claude.ai/code/artifact/5f89d1f5-a704-4b2b-a6df-cb973fff8ed1)**.

- **Training intensity barely touches sleep.** Spearman ρ = −0.18 against the
  following night's sleep score — statistically detectable, but it explains 3%
  of the variance. Splitting by workout type (zone 2, threshold, HIIT, etc.)
  makes even that disappear (Kruskal-Wallis p = 0.58).
- **Bedtime is what moves the needle** — ρ = −0.68 — but the mechanism is
  mundane: with a fixed alarm, a later bedtime *is* less sleep. The two are
  collinear at ρ = −0.945, so they cannot be separated statistically.
- **The threshold is worth knowing anyway.** Crossing midnight (personal to a
  06:40 wake time) takes the share of nights scoring under 60 from 1% to 29%.
- **The night-out detector rediscovered the weekend on its own.** It flagged
  37% of Fridays and 2% of Sundays (odds ratio 6.4, p = 4·10⁻⁸) using only
  heart rate, HRV, and sleep timing.

### Three bugs worth knowing about

If you build something similar against the Garmin API, these will bite you:

1. **`sleepStartTimestampLocal` already has the UTC offset applied.** Reading it
   with `datetime.fromtimestamp()` applies your machine's offset a second time —
   every bedtime lands 1h late in winter and 2h in summer.
2. **A sleep record is labelled with the wake-up date**, not the evening you
   went to bed. Pair training on day D with `sleep[D+1]`, or you will correlate
   a workout with the sleep that came before it.
3. **`bodyBatteryMostRecentValue` is the end-of-day reading**, not the day's
   peak. That is `bodyBatteryHighestValue`.

A fourth mistake was mine, not the API's, and is the one I would warn hardest
about: I concluded that bedtime mattered *independently of hours slept*, based
on a partial correlation. With a fixed wake time those two variables are
near-identical, and partial correlation in that regime is unstable. Every
statistical check passed. The common-sense objection — "if I always get up at
6:40, how can going to bed later not mean less sleep?" — did not.

## Layout

```
server.py           MCP tools (FastMCP, stdio)
garmin_client.py    Garmin API wrapper + response parsing
db.py               SQLite schema and queries
stats.py            Pure functions: correlation, moving average, night-out signals
analysis/           Standalone analysis scripts; report.html is the write-up
tests/              Unit tests, no network or credentials required
```

The analysis scripts read your local database and print to stdout. They are
research code, kept as they were run, and the docstring at the top of each says
what it was trying to find out.

## Notes and limits

- Uses [`garminconnect`](https://github.com/cyberjunky/python-garminconnect),
  which talks to Garmin's private API. It is not an officially supported
  interface and can break when Garmin changes something.
- Garmin rate-limits by IP. Sync a long range in one go rather than in many
  small calls, and expect 429s if you retry too quickly.
- Sleep score, stress, and body battery are Garmin's own algorithms, not
  measurements. Stress in particular is derived from HRV, so correlating the
  two mostly measures Garmin's formula rather than your physiology.
- `n = 1`. These findings are one person's; the code is what generalises.

## Licence

MIT
