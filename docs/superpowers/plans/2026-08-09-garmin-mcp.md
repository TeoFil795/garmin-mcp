# Garmin MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local MCP server that syncs Garmin Connect data (sleep, HR, HRV, stress, VO2max, activities) into a local SQLite cache and exposes it to Claude as tools, including statistical correlation and inferred "night out" detection.

**Architecture:** Four independent Python modules — `db.py` (SQLite schema/queries), `stats.py` (pure statistical functions), `garmin_client.py` (Garmin login/fetch, with parsing logic separated from network calls for testability), `server.py` (MCP tool layer wiring the three together, using FastMCP over stdio transport). Each module is unit-testable without the others; `garmin_client.py`'s network calls are the only untested-by-CI part (per spec — mocked fixtures cover parsing logic instead).

**Tech Stack:** Python 3.11+, `garminconnect` (unofficial Garmin API lib), `mcp` (official MCP Python SDK, `FastMCP`), `pytest`, stdlib `sqlite3`.

## Global Constraints

- DB file lives at `~/.garmin-mcp/data.db` (spec: "Data storage").
- Credentials via `GARMIN_EMAIL` / `GARMIN_PASSWORD` env vars or local `.env`, never committed (spec: "Credentials").
- Transport is stdio only — no HTTP/network port (spec: "Why stdio over HTTP").
- `sync_garmin_data` commits per-day, not batched, and skips already-synced days unless `force=True` (spec: "Error handling", "MCP tools exposed").
- `correlate` must error explicitly on fewer than 5 data points (spec: "Error handling").
- Missing days are absent rows, never forced NULLs (spec: "Schema").
- All table/column names must match the spec schema exactly (spec: "Schema").

---

## File Structure

```
garmin-mcp/
  requirements.txt
  .env.example
  .gitignore
  README.md
  db.py
  stats.py
  garmin_client.py
  server.py
  tests/
    __init__.py
    test_db.py
    test_stats.py
    test_garmin_client.py
    test_server.py
```

---

### Task 1: Project scaffolding + db.py

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `db.py`
- Create: `tests/__init__.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing (foundation module).
- Produces:
  - `DEFAULT_DB_PATH: str` (`~/.garmin-mcp/data.db`, expanded via `os.path.expanduser`)
  - `get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection`
  - `init_db(conn: sqlite3.Connection) -> None`
  - `upsert_daily_stats(conn, date: str, resting_hr=None, stress_avg=None, stress_max=None, body_battery_max=None, body_battery_min=None, steps=None, vo2max_running=None, vo2max_cycling=None, weight=None) -> None`
  - `upsert_sleep(conn, date: str, bedtime=None, wake_time=None, duration_min=None, deep_min=None, light_min=None, rem_min=None, awake_min=None, sleep_score=None, avg_hrv=None) -> None`
  - `upsert_activity(conn, activity_id: str, date: str, type=None, duration_min=None, distance_km=None, avg_hr=None, max_hr=None, calories=None, training_effect=None, load=None) -> None`
  - `upsert_hrv(conn, date: str, overall_hrv=None, status=None, baseline_low=None, baseline_high=None) -> None`
  - `upsert_night_out(conn, date: str, source: str, confidence: float, signals_json: str, note: str = None) -> None`
  - `mark_synced(conn, date: str, status: str) -> None`
  - `is_synced(conn, date: str) -> bool`
  - `get_daily_stats(conn, start: str, end: str) -> list[dict]`
  - `get_sleep(conn, start: str, end: str) -> list[dict]`
  - `get_activities(conn, start: str, end: str) -> list[dict]`
  - `get_hrv(conn, start: str, end: str) -> list[dict]`
  - `get_nights_out(conn, start: str, end: str) -> list[dict]`
  - `get_metric_series(conn, table: str, column: str, start: str, end: str) -> list[tuple[str, float]]` (generic `(date, value)` pairs, skips NULL values, ordered by date)

- [ ] **Step 1: Write scaffolding files**

`requirements.txt`:
```
garminconnect>=0.2.20
mcp>=1.0.0
python-dotenv>=1.0.0
pytest>=8.0.0
```

`.env.example`:
```
GARMIN_EMAIL=your_email@example.com
GARMIN_PASSWORD=your_password
```

`.gitignore`:
```
.env
__pycache__/
*.pyc
.pytest_cache/
*.db
```

- [ ] **Step 2: Write the failing test for schema init and upsert**

```python
# tests/test_db.py
import sqlite3
import pytest
from db import (
    get_connection, init_db, upsert_daily_stats, upsert_sleep,
    upsert_activity, upsert_hrv, upsert_night_out, mark_synced,
    is_synced, get_daily_stats, get_sleep, get_activities, get_hrv,
    get_nights_out, get_metric_series,
)


@pytest.fixture
def conn(tmp_path):
    db_path = str(tmp_path / "test.db")
    c = get_connection(db_path)
    init_db(c)
    yield c
    c.close()


def test_init_db_creates_tables(conn):
    tables = {
        row[0] for row in
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "daily_stats", "sleep", "activities", "hrv", "nights_out", "sync_log"
    }.issubset(tables)


def test_upsert_daily_stats_then_query(conn):
    upsert_daily_stats(conn, "2026-08-01", resting_hr=52, steps=8000)
    rows = get_daily_stats(conn, "2026-08-01", "2026-08-01")
    assert len(rows) == 1
    assert rows[0]["resting_hr"] == 52
    assert rows[0]["steps"] == 8000


def test_upsert_daily_stats_is_idempotent(conn):
    upsert_daily_stats(conn, "2026-08-01", resting_hr=52)
    upsert_daily_stats(conn, "2026-08-01", resting_hr=55)
    rows = get_daily_stats(conn, "2026-08-01", "2026-08-01")
    assert len(rows) == 1
    assert rows[0]["resting_hr"] == 55


def test_missing_day_is_absent_not_null(conn):
    upsert_daily_stats(conn, "2026-08-01", resting_hr=52)
    rows = get_daily_stats(conn, "2026-08-01", "2026-08-03")
    assert len(rows) == 1


def test_sleep_and_activities_and_hrv_roundtrip(conn):
    upsert_sleep(conn, "2026-08-01", duration_min=420, sleep_score=78)
    upsert_activity(
        conn, "act-1", "2026-08-01", type="running", duration_min=45
    )
    upsert_hrv(conn, "2026-08-01", overall_hrv=55, status="balanced")

    assert get_sleep(conn, "2026-08-01", "2026-08-01")[0]["sleep_score"] == 78
    assert get_activities(conn, "2026-08-01", "2026-08-01")[0]["type"] == "running"
    assert get_hrv(conn, "2026-08-01", "2026-08-01")[0]["overall_hrv"] == 55


def test_night_out_manual_log(conn):
    upsert_night_out(
        conn, "2026-08-01", source="manual", confidence=1.0,
        signals_json="{}", note="birthday party"
    )
    rows = get_nights_out(conn, "2026-08-01", "2026-08-01")
    assert rows[0]["note"] == "birthday party"
    assert rows[0]["source"] == "manual"


def test_sync_log_tracks_synced_days(conn):
    assert is_synced(conn, "2026-08-01") is False
    mark_synced(conn, "2026-08-01", status="ok")
    assert is_synced(conn, "2026-08-01") is True


def test_get_metric_series_skips_nulls_and_orders(conn):
    upsert_daily_stats(conn, "2026-08-03", resting_hr=50)
    upsert_daily_stats(conn, "2026-08-01", resting_hr=48)
    upsert_daily_stats(conn, "2026-08-02")  # resting_hr NULL
    series = get_metric_series(conn, "daily_stats", "resting_hr", "2026-08-01", "2026-08-03")
    assert series == [("2026-08-01", 48), ("2026-08-03", 50)]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 4: Implement db.py**

```python
# db.py
import os
import sqlite3

DEFAULT_DB_PATH = os.path.expanduser("~/.garmin-mcp/data.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_stats (
    date TEXT PRIMARY KEY,
    resting_hr INTEGER,
    stress_avg INTEGER,
    stress_max INTEGER,
    body_battery_max INTEGER,
    body_battery_min INTEGER,
    steps INTEGER,
    vo2max_running REAL,
    vo2max_cycling REAL,
    weight REAL
);

CREATE TABLE IF NOT EXISTS sleep (
    date TEXT PRIMARY KEY,
    bedtime TEXT,
    wake_time TEXT,
    duration_min INTEGER,
    deep_min INTEGER,
    light_min INTEGER,
    rem_min INTEGER,
    awake_min INTEGER,
    sleep_score INTEGER,
    avg_hrv REAL
);

CREATE TABLE IF NOT EXISTS activities (
    activity_id TEXT PRIMARY KEY,
    date TEXT,
    type TEXT,
    duration_min REAL,
    distance_km REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    calories INTEGER,
    training_effect REAL,
    load REAL
);

CREATE TABLE IF NOT EXISTS hrv (
    date TEXT PRIMARY KEY,
    overall_hrv REAL,
    status TEXT,
    baseline_low REAL,
    baseline_high REAL
);

CREATE TABLE IF NOT EXISTS nights_out (
    date TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    confidence REAL,
    signals_json TEXT,
    note TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    date TEXT PRIMARY KEY,
    synced_at TEXT,
    status TEXT
);
"""


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def _upsert(conn, table, pk_col, pk_val, fields: dict):
    fields = {k: v for k, v in fields.items() if v is not None}
    columns = [pk_col] + list(fields.keys())
    placeholders = ", ".join(["?"] * len(columns))
    updates = ", ".join(f"{col}=excluded.{col}" for col in fields)
    values = [pk_val] + list(fields.values())
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    if updates:
        sql += f" ON CONFLICT({pk_col}) DO UPDATE SET {updates}"
    else:
        sql += f" ON CONFLICT({pk_col}) DO NOTHING"
    conn.execute(sql, values)
    conn.commit()


def upsert_daily_stats(conn, date, resting_hr=None, stress_avg=None,
                        stress_max=None, body_battery_max=None,
                        body_battery_min=None, steps=None,
                        vo2max_running=None, vo2max_cycling=None,
                        weight=None):
    _upsert(conn, "daily_stats", "date", date, dict(
        resting_hr=resting_hr, stress_avg=stress_avg, stress_max=stress_max,
        body_battery_max=body_battery_max, body_battery_min=body_battery_min,
        steps=steps, vo2max_running=vo2max_running,
        vo2max_cycling=vo2max_cycling, weight=weight,
    ))


def upsert_sleep(conn, date, bedtime=None, wake_time=None, duration_min=None,
                  deep_min=None, light_min=None, rem_min=None, awake_min=None,
                  sleep_score=None, avg_hrv=None):
    _upsert(conn, "sleep", "date", date, dict(
        bedtime=bedtime, wake_time=wake_time, duration_min=duration_min,
        deep_min=deep_min, light_min=light_min, rem_min=rem_min,
        awake_min=awake_min, sleep_score=sleep_score, avg_hrv=avg_hrv,
    ))


def upsert_activity(conn, activity_id, date, type=None, duration_min=None,
                     distance_km=None, avg_hr=None, max_hr=None,
                     calories=None, training_effect=None, load=None):
    fields = dict(
        date=date, type=type, duration_min=duration_min,
        distance_km=distance_km, avg_hr=avg_hr, max_hr=max_hr,
        calories=calories, training_effect=training_effect, load=load,
    )
    _upsert(conn, "activities", "activity_id", activity_id, fields)


def upsert_hrv(conn, date, overall_hrv=None, status=None,
                baseline_low=None, baseline_high=None):
    _upsert(conn, "hrv", "date", date, dict(
        overall_hrv=overall_hrv, status=status,
        baseline_low=baseline_low, baseline_high=baseline_high,
    ))


def upsert_night_out(conn, date, source, confidence=None,
                      signals_json=None, note=None):
    _upsert(conn, "nights_out", "date", date, dict(
        source=source, confidence=confidence,
        signals_json=signals_json, note=note,
    ))


def mark_synced(conn, date, status):
    import datetime
    _upsert(conn, "sync_log", "date", date, dict(
        synced_at=datetime.datetime.now().isoformat(), status=status,
    ))


def is_synced(conn, date) -> bool:
    row = conn.execute(
        "SELECT status FROM sync_log WHERE date = ?", (date,)
    ).fetchone()
    return row is not None and row["status"] == "ok"


def _query_range(conn, table, start, end):
    rows = conn.execute(
        f"SELECT * FROM {table} WHERE date BETWEEN ? AND ? ORDER BY date",
        (start, end),
    ).fetchall()
    return [dict(r) for r in rows]


def get_daily_stats(conn, start, end):
    return _query_range(conn, "daily_stats", start, end)


def get_sleep(conn, start, end):
    return _query_range(conn, "sleep", start, end)


def get_activities(conn, start, end):
    return _query_range(conn, "activities", start, end)


def get_hrv(conn, start, end):
    return _query_range(conn, "hrv", start, end)


def get_nights_out(conn, start, end):
    return _query_range(conn, "nights_out", start, end)


def get_metric_series(conn, table, column, start, end):
    rows = conn.execute(
        f"SELECT date, {column} AS value FROM {table} "
        f"WHERE date BETWEEN ? AND ? AND {column} IS NOT NULL ORDER BY date",
        (start, end),
    ).fetchall()
    return [(r["date"], r["value"]) for r in rows]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_db.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt .env.example .gitignore db.py tests/__init__.py tests/test_db.py
git commit -m "Add project scaffolding and db.py with SQLite schema"
```

---

### Task 2: stats.py

**Files:**
- Create: `stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: `list[tuple[str, float]]` shape produced by `db.get_metric_series` (date, value pairs); `list[dict]` shape from `db.get_sleep`/`get_hrv`/`get_daily_stats`.
- Produces:
  - `pearson_correlation(series_a: list[float], series_b: list[float]) -> float`
  - `moving_average(values: list[float], window: int) -> list[float]`
  - `compute_baseline(values: list[float]) -> float` (mean, ignores nothing — caller filters None first)
  - `detect_night_out(day: dict, baseline: dict) -> tuple[bool, float, dict]` — `day` has keys `bedtime_hour` (float, e.g. 1.5 for 1:30am), `sleep_duration_min`, `overnight_hr`, `hrv`; `baseline` has keys `avg_bedtime_hour`, `avg_sleep_duration_min`, `avg_overnight_hr`, `avg_hrv`. Returns `(is_night_out, confidence, signals_dict)` where `signals_dict` lists which thresholds were crossed.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats.py
import pytest
from stats import pearson_correlation, moving_average, compute_baseline, detect_night_out


def test_pearson_correlation_perfect_positive():
    a = [1, 2, 3, 4, 5]
    b = [2, 4, 6, 8, 10]
    assert pearson_correlation(a, b) == pytest.approx(1.0)


def test_pearson_correlation_perfect_negative():
    a = [1, 2, 3, 4, 5]
    b = [10, 8, 6, 4, 2]
    assert pearson_correlation(a, b) == pytest.approx(-1.0)


def test_pearson_correlation_requires_min_points():
    with pytest.raises(ValueError, match="at least 5"):
        pearson_correlation([1, 2, 3], [1, 2, 3])


def test_pearson_correlation_requires_equal_length():
    with pytest.raises(ValueError, match="same length"):
        pearson_correlation([1, 2, 3, 4, 5], [1, 2, 3])


def test_moving_average_basic():
    values = [1, 2, 3, 4, 5]
    assert moving_average(values, window=2) == [1.5, 2.5, 3.5, 4.5]


def test_moving_average_window_larger_than_data_raises():
    with pytest.raises(ValueError, match="window"):
        moving_average([1, 2], window=5)


def test_compute_baseline_mean():
    assert compute_baseline([50, 52, 54]) == pytest.approx(52.0)


def test_detect_night_out_flags_multiple_signals():
    day = dict(
        bedtime_hour=2.0, sleep_duration_min=300,
        overnight_hr=68, hrv=35,
    )
    baseline = dict(
        avg_bedtime_hour=23.5, avg_sleep_duration_min=440,
        avg_overnight_hr=52, avg_hrv=55,
    )
    is_out, confidence, signals = detect_night_out(day, baseline)
    assert is_out is True
    assert confidence > 0.5
    assert signals["late_bedtime"] is True
    assert signals["short_sleep"] is True
    assert signals["elevated_overnight_hr"] is True
    assert signals["low_hrv"] is True


def test_detect_night_out_normal_day_not_flagged():
    day = dict(
        bedtime_hour=23.0, sleep_duration_min=430,
        overnight_hr=53, hrv=54,
    )
    baseline = dict(
        avg_bedtime_hour=23.5, avg_sleep_duration_min=440,
        avg_overnight_hr=52, avg_hrv=55,
    )
    is_out, confidence, signals = detect_night_out(day, baseline)
    assert is_out is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'stats'`

- [ ] **Step 3: Implement stats.py**

```python
# stats.py
import math

MIN_CORRELATION_POINTS = 5


def pearson_correlation(series_a: list[float], series_b: list[float]) -> float:
    if len(series_a) != len(series_b):
        raise ValueError("series must have the same length")
    if len(series_a) < MIN_CORRELATION_POINTS:
        raise ValueError(f"need at least {MIN_CORRELATION_POINTS} data points")

    n = len(series_a)
    mean_a = sum(series_a) / n
    mean_b = sum(series_b) / n

    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(series_a, series_b))
    var_a = sum((a - mean_a) ** 2 for a in series_a)
    var_b = sum((b - mean_b) ** 2 for b in series_b)

    denom = math.sqrt(var_a * var_b)
    if denom == 0:
        return 0.0
    return cov / denom


def moving_average(values: list[float], window: int) -> list[float]:
    if window > len(values):
        raise ValueError("window larger than data length")
    return [
        sum(values[i:i + window]) / window
        for i in range(len(values) - window + 1)
    ]


def compute_baseline(values: list[float]) -> float:
    return sum(values) / len(values)


def detect_night_out(day: dict, baseline: dict) -> tuple[bool, float, dict]:
    signals = {
        "late_bedtime": day["bedtime_hour"] >= baseline["avg_bedtime_hour"] + 1.5,
        "short_sleep": day["sleep_duration_min"] <= baseline["avg_sleep_duration_min"] - 90,
        "elevated_overnight_hr": day["overnight_hr"] >= baseline["avg_overnight_hr"] + 10,
        "low_hrv": day["hrv"] <= baseline["avg_hrv"] * 0.85,
    }
    triggered = sum(signals.values())
    confidence = triggered / len(signals)
    is_out = triggered >= 2
    return is_out, confidence, signals
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_stats.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add stats.py tests/test_stats.py
git commit -m "Add stats.py with correlation, moving average, night-out detection"
```

---

### Task 3: garmin_client.py

**Files:**
- Create: `garmin_client.py`
- Test: `tests/test_garmin_client.py`

**Interfaces:**
- Consumes: raw dicts as returned by the `garminconnect` library's `get_stats`, `get_sleep_data`, `get_activities_by_date`, `get_hrv_data`, `get_max_metrics` methods.
- Produces:
  - `parse_daily_stats(raw: dict) -> dict` — keys match `db.upsert_daily_stats` kwargs (minus `date`): `resting_hr, stress_avg, stress_max, body_battery_max, body_battery_min, steps, weight`
  - `parse_vo2max(raw: dict) -> dict` — keys `vo2max_running, vo2max_cycling`
  - `parse_sleep(raw: dict) -> dict` — keys match `db.upsert_sleep` kwargs (minus `date`)
  - `parse_activity(raw: dict) -> dict` — keys `activity_id, date` + match `db.upsert_activity` kwargs (minus those two)
  - `parse_hrv(raw: dict) -> dict` — keys match `db.upsert_hrv` kwargs (minus `date`)
  - `class GarminClient`:
    - `__init__(self, email: str = None, password: str = None)` — reads `GARMIN_EMAIL`/`GARMIN_PASSWORD` from env if not passed, raises `ValueError` if still missing
    - `login(self) -> None` — instantiates and logs in the underlying `garminconnect.Garmin` client, stored as `self._api`
    - `fetch_daily_stats(self, date: str) -> dict` — calls `self._api.get_stats(date)`, returns `parse_daily_stats(raw)`
    - `fetch_vo2max(self, date: str) -> dict` — calls `self._api.get_max_metrics(date)`, returns `parse_vo2max(raw)`
    - `fetch_sleep(self, date: str) -> dict` — calls `self._api.get_sleep_data(date)`, returns `parse_sleep(raw)`
    - `fetch_activities(self, start: str, end: str) -> list[dict]` — calls `self._api.get_activities_by_date(start, end)`, returns `[parse_activity(a) for a in raw]`
    - `fetch_hrv(self, date: str) -> dict` — calls `self._api.get_hrv_data(date)`, returns `parse_hrv(raw)`

- [ ] **Step 1: Write the failing tests for parsing functions**

```python
# tests/test_garmin_client.py
import os
import pytest
from garmin_client import (
    parse_daily_stats, parse_vo2max, parse_sleep, parse_activity, parse_hrv,
    GarminClient,
)


def test_parse_daily_stats():
    raw = {
        "restingHeartRate": 52,
        "averageStressLevel": 28,
        "maxStressLevel": 74,
        "bodyBatteryMostRecentValue": 65,
        "bodyBatteryLowestValue": 20,
        "totalSteps": 8421,
        "weight": 74500,  # grams, Garmin returns grams
    }
    result = parse_daily_stats(raw)
    assert result == dict(
        resting_hr=52, stress_avg=28, stress_max=74,
        body_battery_max=65, body_battery_min=20,
        steps=8421, weight=74.5,
    )


def test_parse_vo2max():
    raw = {"generic": {"vo2MaxPreciseValue": 48.5}, "cycling": {"vo2MaxValue": 44.0}}
    result = parse_vo2max(raw)
    assert result == dict(vo2max_running=48.5, vo2max_cycling=44.0)


def test_parse_vo2max_missing_fields_returns_none():
    result = parse_vo2max({})
    assert result == dict(vo2max_running=None, vo2max_cycling=None)


def test_parse_sleep():
    raw = {
        "dailySleepDTO": {
            "sleepStartTimestampLocal": "2026-08-01T23:15:00",
            "sleepEndTimestampLocal": "2026-08-02T07:00:00",
            "sleepTimeSeconds": 27900,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 16200,
            "remSleepSeconds": 5400,
            "awakeSleepSeconds": 900,
            "sleepScores": {"overall": {"value": 82}},
        },
        "avgOvernightHrv": 58,
    }
    result = parse_sleep(raw)
    assert result["bedtime"] == "2026-08-01T23:15:00"
    assert result["wake_time"] == "2026-08-02T07:00:00"
    assert result["duration_min"] == 465
    assert result["deep_min"] == 90
    assert result["light_min"] == 270
    assert result["rem_min"] == 90
    assert result["awake_min"] == 15
    assert result["sleep_score"] == 82
    assert result["avg_hrv"] == 58


def test_parse_activity():
    raw = {
        "activityId": 12345,
        "startTimeLocal": "2026-08-01 07:00:00",
        "activityType": {"typeKey": "running"},
        "duration": 2700.0,
        "distance": 8000.0,
        "averageHR": 152,
        "maxHR": 178,
        "calories": 620,
        "trainingEffect": 3.2,
        "activityTrainingLoad": 145.0,
    }
    result = parse_activity(raw)
    assert result["activity_id"] == "12345"
    assert result["date"] == "2026-08-01"
    assert result["type"] == "running"
    assert result["duration_min"] == 45.0
    assert result["distance_km"] == 8.0
    assert result["avg_hr"] == 152
    assert result["max_hr"] == 178
    assert result["calories"] == 620
    assert result["training_effect"] == 3.2
    assert result["load"] == 145.0


def test_parse_hrv():
    raw = {
        "hrvSummary": {
            "lastNightAvg": 57,
            "status": "BALANCED",
            "baseline": {"lowUpper": 45, "balancedUpper": 65},
        }
    }
    result = parse_hrv(raw)
    assert result == dict(
        overall_hrv=57, status="BALANCED",
        baseline_low=45, baseline_high=65,
    )


def test_garmin_client_requires_credentials(monkeypatch):
    monkeypatch.delenv("GARMIN_EMAIL", raising=False)
    monkeypatch.delenv("GARMIN_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="credentials"):
        GarminClient()


def test_garmin_client_reads_env_credentials(monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    client = GarminClient()
    assert client.email == "a@b.com"
    assert client.password == "secret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_garmin_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'garmin_client'`

- [ ] **Step 3: Implement garmin_client.py**

```python
# garmin_client.py
import os


def parse_daily_stats(raw: dict) -> dict:
    weight_g = raw.get("weight")
    return dict(
        resting_hr=raw.get("restingHeartRate"),
        stress_avg=raw.get("averageStressLevel"),
        stress_max=raw.get("maxStressLevel"),
        body_battery_max=raw.get("bodyBatteryMostRecentValue"),
        body_battery_min=raw.get("bodyBatteryLowestValue"),
        steps=raw.get("totalSteps"),
        weight=(weight_g / 1000) if weight_g is not None else None,
    )


def parse_vo2max(raw: dict) -> dict:
    generic = raw.get("generic") or {}
    cycling = raw.get("cycling") or {}
    return dict(
        vo2max_running=generic.get("vo2MaxPreciseValue"),
        vo2max_cycling=cycling.get("vo2MaxValue"),
    )


def parse_sleep(raw: dict) -> dict:
    dto = raw.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}

    def to_min(seconds):
        return seconds // 60 if seconds is not None else None

    return dict(
        bedtime=dto.get("sleepStartTimestampLocal"),
        wake_time=dto.get("sleepEndTimestampLocal"),
        duration_min=to_min(dto.get("sleepTimeSeconds")),
        deep_min=to_min(dto.get("deepSleepSeconds")),
        light_min=to_min(dto.get("lightSleepSeconds")),
        rem_min=to_min(dto.get("remSleepSeconds")),
        awake_min=to_min(dto.get("awakeSleepSeconds")),
        sleep_score=overall.get("value"),
        avg_hrv=raw.get("avgOvernightHrv"),
    )


def parse_activity(raw: dict) -> dict:
    start_local = raw.get("startTimeLocal", "")
    date = start_local.split(" ")[0].split("T")[0] if start_local else None
    duration_sec = raw.get("duration")
    distance_m = raw.get("distance")
    return dict(
        activity_id=str(raw.get("activityId")),
        date=date,
        type=(raw.get("activityType") or {}).get("typeKey"),
        duration_min=(duration_sec / 60) if duration_sec is not None else None,
        distance_km=(distance_m / 1000) if distance_m is not None else None,
        avg_hr=raw.get("averageHR"),
        max_hr=raw.get("maxHR"),
        calories=raw.get("calories"),
        training_effect=raw.get("trainingEffect"),
        load=raw.get("activityTrainingLoad"),
    )


def parse_hrv(raw: dict) -> dict:
    summary = raw.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    return dict(
        overall_hrv=summary.get("lastNightAvg"),
        status=summary.get("status"),
        baseline_low=baseline.get("lowUpper"),
        baseline_high=baseline.get("balancedUpper"),
    )


class GarminClient:
    def __init__(self, email: str = None, password: str = None):
        self.email = email or os.environ.get("GARMIN_EMAIL")
        self.password = password or os.environ.get("GARMIN_PASSWORD")
        if not self.email or not self.password:
            raise ValueError(
                "Garmin credentials missing: set GARMIN_EMAIL and "
                "GARMIN_PASSWORD env vars"
            )
        self._api = None

    def login(self) -> None:
        import garminconnect
        self._api = garminconnect.Garmin(self.email, self.password)
        self._api.login()

    def fetch_daily_stats(self, date: str) -> dict:
        raw = self._api.get_stats(date)
        return parse_daily_stats(raw)

    def fetch_vo2max(self, date: str) -> dict:
        raw = self._api.get_max_metrics(date)
        return parse_vo2max(raw)

    def fetch_sleep(self, date: str) -> dict:
        raw = self._api.get_sleep_data(date)
        return parse_sleep(raw)

    def fetch_activities(self, start: str, end: str) -> list[dict]:
        raw = self._api.get_activities_by_date(start, end)
        return [parse_activity(a) for a in raw]

    def fetch_hrv(self, date: str) -> dict:
        raw = self._api.get_hrv_data(date)
        return parse_hrv(raw)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_garmin_client.py -v`
Expected: PASS (9 tests)

**Note:** field names in `parse_*` functions are based on the `garminconnect` library's commonly observed response shapes. Once real credentials are available, run a one-off manual script (`python -c "from garmin_client import GarminClient; c = GarminClient(); c.login(); print(c._api.get_stats('2026-08-01'))"`) and diff actual field names against these parsers — adjust key lookups if Garmin's raw shape differs. This is expected and doesn't require rewriting tests, just the `raw.get(...)` keys.

- [ ] **Step 5: Commit**

```bash
git add garmin_client.py tests/test_garmin_client.py
git commit -m "Add garmin_client.py with parsing functions and GarminClient"
```

---

### Task 4: server.py — MCP tool layer

**Files:**
- Create: `server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes:
  - `db`: `get_connection`, `init_db`, `upsert_daily_stats`, `upsert_sleep`, `upsert_activity`, `upsert_hrv`, `upsert_night_out`, `mark_synced`, `is_synced`, `get_daily_stats`, `get_sleep`, `get_activities`, `get_hrv`, `get_nights_out`, `get_metric_series`
  - `stats`: `pearson_correlation`, `moving_average`, `compute_baseline`, `detect_night_out`
  - `garmin_client`: `GarminClient`
- Produces: module-level FastMCP tool functions (decorated, but plain callables underneath — tested by calling `.fn(...)` directly, which is how `FastMCP` exposes the undecorated function): `sync_garmin_data`, `get_daily_summary`, `get_range_summary`, `get_sleep`, `get_activities`, `get_hrv`, `get_vo2max`, `get_stress`, `detect_nights_out`, `log_night_out`, `correlate`, `moving_average_tool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_server.py
import pytest
import db as db_module
import server


@pytest.fixture
def conn(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    c = db_module.get_connection(db_path)
    db_module.init_db(c)
    monkeypatch.setattr(server, "_get_conn", lambda: c)
    yield c
    c.close()


def test_get_daily_summary_merges_tables(conn):
    db_module.upsert_daily_stats(conn, "2026-08-01", resting_hr=52, steps=8000)
    db_module.upsert_sleep(conn, "2026-08-01", duration_min=420, sleep_score=80)
    db_module.upsert_hrv(conn, "2026-08-01", overall_hrv=55)

    result = server.get_daily_summary.fn("2026-08-01")
    assert result["date"] == "2026-08-01"
    assert result["daily_stats"]["resting_hr"] == 52
    assert result["sleep"]["sleep_score"] == 80
    assert result["hrv"]["overall_hrv"] == 55


def test_get_daily_summary_handles_missing_data(conn):
    result = server.get_daily_summary.fn("2026-08-01")
    assert result["daily_stats"] is None
    assert result["sleep"] is None
    assert result["hrv"] is None


def test_get_vo2max_reads_daily_stats(conn):
    db_module.upsert_daily_stats(
        conn, "2026-08-01", vo2max_running=48.5, vo2max_cycling=44.0
    )
    result = server.get_vo2max.fn("2026-08-01", "2026-08-01")
    assert result[0]["vo2max_running"] == 48.5


def test_get_stress_reads_daily_stats(conn):
    db_module.upsert_daily_stats(conn, "2026-08-01", stress_avg=28, stress_max=74)
    result = server.get_stress.fn("2026-08-01", "2026-08-01")
    assert result[0]["stress_avg"] == 28


def test_log_night_out_manual(conn):
    result = server.log_night_out.fn("2026-08-01", "birthday party")
    assert result["status"] == "logged"
    rows = db_module.get_nights_out(conn, "2026-08-01", "2026-08-01")
    assert rows[0]["source"] == "manual"
    assert rows[0]["note"] == "birthday party"


def test_correlate_two_metrics(conn):
    for i, (hr, steps) in enumerate([
        (50, 5000), (52, 6000), (54, 7000), (56, 8000), (58, 9000)
    ]):
        date = f"2026-08-0{i + 1}"
        db_module.upsert_daily_stats(conn, date, resting_hr=hr, steps=steps)

    result = server.correlate.fn("resting_hr", "steps", "2026-08-01", "2026-08-05")
    assert result["correlation"] == pytest.approx(1.0)
    assert result["n_points"] == 5


def test_correlate_too_few_points_errors(conn):
    db_module.upsert_daily_stats(conn, "2026-08-01", resting_hr=50, steps=5000)
    with pytest.raises(ValueError, match="at least 5"):
        server.correlate.fn("resting_hr", "steps", "2026-08-01", "2026-08-01")


def test_correlate_rejects_unknown_metric(conn):
    with pytest.raises(ValueError, match="unknown metric"):
        server.correlate.fn("not_a_metric", "steps", "2026-08-01", "2026-08-05")


def test_moving_average_tool(conn):
    for i, hr in enumerate([50, 52, 54, 56]):
        db_module.upsert_daily_stats(conn, f"2026-08-0{i + 1}", resting_hr=hr)
    result = server.moving_average_tool.fn("resting_hr", 2, "2026-08-01", "2026-08-04")
    assert result["values"] == [51.0, 53.0, 55.0]


def test_detect_nights_out_flags_and_stores(conn):
    baseline_days = [
        ("2026-07-2{}".format(i), 23.0, 440, 52, 55) for i in range(1, 6)
    ]
    for date, bedtime, dur, hr, hrv in baseline_days:
        db_module.upsert_sleep(conn, date, bedtime=f"{date}T23:00:00",
                                duration_min=dur)
        db_module.upsert_daily_stats(conn, date, resting_hr=hr)
        db_module.upsert_hrv(conn, date, overall_hrv=hrv)

    db_module.upsert_sleep(conn, "2026-08-01", bedtime="2026-08-01T02:00:00",
                            duration_min=300)
    db_module.upsert_daily_stats(conn, "2026-08-01", resting_hr=68)
    db_module.upsert_hrv(conn, "2026-08-01", overall_hrv=35)

    result = server.detect_nights_out.fn("2026-08-01", "2026-08-01")
    assert len(result) == 1
    assert result[0]["date"] == "2026-08-01"
    assert result[0]["is_night_out"] is True

    stored = db_module.get_nights_out(conn, "2026-08-01", "2026-08-01")
    assert stored[0]["source"] == "inferred"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Implement server.py**

```python
# server.py
import json
from datetime import datetime, timedelta

from mcp.server.fastmcp import FastMCP

import db as db_module
import stats as stats_module
from garmin_client import GarminClient

mcp = FastMCP("garmin-mcp")

_METRIC_TABLES = {
    "resting_hr": ("daily_stats", "resting_hr"),
    "stress_avg": ("daily_stats", "stress_avg"),
    "stress_max": ("daily_stats", "stress_max"),
    "steps": ("daily_stats", "steps"),
    "vo2max_running": ("daily_stats", "vo2max_running"),
    "vo2max_cycling": ("daily_stats", "vo2max_cycling"),
    "weight": ("daily_stats", "weight"),
    "body_battery_max": ("daily_stats", "body_battery_max"),
    "body_battery_min": ("daily_stats", "body_battery_min"),
    "sleep_duration_min": ("sleep", "duration_min"),
    "sleep_score": ("sleep", "sleep_score"),
    "overall_hrv": ("hrv", "overall_hrv"),
}


def _get_conn():
    conn = db_module.get_connection()
    db_module.init_db(conn)
    return conn


def _resolve_metric(name: str):
    if name not in _METRIC_TABLES:
        raise ValueError(f"unknown metric: {name}")
    return _METRIC_TABLES[name]


@mcp.tool()
def sync_garmin_data(start_date: str, end_date: str, force: bool = False) -> dict:
    conn = _get_conn()
    client = GarminClient()
    client.login()

    synced, failed = [], []
    current = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        if not force and db_module.is_synced(conn, date_str):
            current += timedelta(days=1)
            continue
        try:
            daily = client.fetch_daily_stats(date_str)
            vo2max = client.fetch_vo2max(date_str)
            sleep = client.fetch_sleep(date_str)
            hrv = client.fetch_hrv(date_str)

            db_module.upsert_daily_stats(conn, date_str, **{**daily, **vo2max})
            db_module.upsert_sleep(conn, date_str, **sleep)
            db_module.upsert_hrv(conn, date_str, **hrv)
            db_module.mark_synced(conn, date_str, status="ok")
            synced.append(date_str)
        except Exception as e:
            db_module.mark_synced(conn, date_str, status=f"error: {e}")
            failed.append({"date": date_str, "error": str(e)})
        current += timedelta(days=1)

    activities = client.fetch_activities(start_date, end_date)
    for act in activities:
        db_module.upsert_activity(conn, act["activity_id"], act["date"], **{
            k: v for k, v in act.items() if k not in ("activity_id", "date")
        })

    return {"synced": synced, "failed": failed, "activities_synced": len(activities)}


@mcp.tool()
def get_daily_summary(date: str) -> dict:
    conn = _get_conn()
    daily = db_module.get_daily_stats(conn, date, date)
    sleep = db_module.get_sleep(conn, date, date)
    hrv = db_module.get_hrv(conn, date, date)
    activities = db_module.get_activities(conn, date, date)
    return {
        "date": date,
        "daily_stats": daily[0] if daily else None,
        "sleep": sleep[0] if sleep else None,
        "hrv": hrv[0] if hrv else None,
        "activities": activities,
    }


@mcp.tool()
def get_range_summary(start: str, end: str) -> dict:
    conn = _get_conn()
    return {
        "daily_stats": db_module.get_daily_stats(conn, start, end),
        "sleep": db_module.get_sleep(conn, start, end),
        "hrv": db_module.get_hrv(conn, start, end),
        "activities": db_module.get_activities(conn, start, end),
    }


@mcp.tool()
def get_sleep(start: str, end: str) -> list[dict]:
    return db_module.get_sleep(_get_conn(), start, end)


@mcp.tool()
def get_activities(start: str, end: str) -> list[dict]:
    return db_module.get_activities(_get_conn(), start, end)


@mcp.tool()
def get_hrv(start: str, end: str) -> list[dict]:
    return db_module.get_hrv(_get_conn(), start, end)


@mcp.tool()
def get_vo2max(start: str, end: str) -> list[dict]:
    rows = db_module.get_daily_stats(_get_conn(), start, end)
    return [
        {"date": r["date"], "vo2max_running": r["vo2max_running"],
         "vo2max_cycling": r["vo2max_cycling"]}
        for r in rows
    ]


@mcp.tool()
def get_stress(start: str, end: str) -> list[dict]:
    rows = db_module.get_daily_stats(_get_conn(), start, end)
    return [
        {"date": r["date"], "stress_avg": r["stress_avg"],
         "stress_max": r["stress_max"]}
        for r in rows
    ]


@mcp.tool()
def detect_nights_out(start: str, end: str) -> list[dict]:
    conn = _get_conn()
    sleep_rows = {r["date"]: r for r in db_module.get_sleep(conn, start, end)}
    hr_rows = {r["date"]: r for r in db_module.get_daily_stats(conn, start, end)}
    hrv_rows = {r["date"]: r for r in db_module.get_hrv(conn, start, end)}

    def bedtime_hour(iso_ts):
        if not iso_ts:
            return None
        t = datetime.fromisoformat(iso_ts)
        hour = t.hour + t.minute / 60
        return hour if hour >= 12 else hour + 24

    dates = sorted(set(sleep_rows) & set(hr_rows) & set(hrv_rows))
    bedtimes = [bedtime_hour(sleep_rows[d]["bedtime"]) for d in dates]
    durations = [sleep_rows[d]["duration_min"] for d in dates]
    hrs = [hr_rows[d]["resting_hr"] for d in dates]
    hrvs = [hrv_rows[d]["overall_hrv"] for d in dates]

    valid = [
        i for i in range(len(dates))
        if None not in (bedtimes[i], durations[i], hrs[i], hrvs[i])
    ]
    if len(valid) < 3:
        return []

    baseline = dict(
        avg_bedtime_hour=stats_module.compute_baseline([bedtimes[i] for i in valid]),
        avg_sleep_duration_min=stats_module.compute_baseline([durations[i] for i in valid]),
        avg_overnight_hr=stats_module.compute_baseline([hrs[i] for i in valid]),
        avg_hrv=stats_module.compute_baseline([hrvs[i] for i in valid]),
    )

    results = []
    for i in valid:
        day = dict(
            bedtime_hour=bedtimes[i], sleep_duration_min=durations[i],
            overnight_hr=hrs[i], hrv=hrvs[i],
        )
        is_out, confidence, signals = stats_module.detect_night_out(day, baseline)
        results.append({
            "date": dates[i], "is_night_out": is_out,
            "confidence": confidence, "signals": signals,
        })
        if is_out:
            db_module.upsert_night_out(
                conn, dates[i], source="inferred", confidence=confidence,
                signals_json=json.dumps(signals),
            )
    return results


@mcp.tool()
def log_night_out(date: str, note: str) -> dict:
    conn = _get_conn()
    db_module.upsert_night_out(
        conn, date, source="manual", confidence=1.0,
        signals_json="{}", note=note,
    )
    return {"status": "logged", "date": date}


@mcp.tool()
def correlate(metric_a: str, metric_b: str, start: str, end: str) -> dict:
    conn = _get_conn()
    table_a, col_a = _resolve_metric(metric_a)
    table_b, col_b = _resolve_metric(metric_b)

    series_a = dict(db_module.get_metric_series(conn, table_a, col_a, start, end))
    series_b = dict(db_module.get_metric_series(conn, table_b, col_b, start, end))
    common_dates = sorted(set(series_a) & set(series_b))

    values_a = [series_a[d] for d in common_dates]
    values_b = [series_b[d] for d in common_dates]

    correlation = stats_module.pearson_correlation(values_a, values_b)
    return {"correlation": correlation, "n_points": len(common_dates)}


@mcp.tool()
def moving_average_tool(metric: str, window: int, start: str, end: str) -> dict:
    conn = _get_conn()
    table, col = _resolve_metric(metric)
    series = db_module.get_metric_series(conn, table, col, start, end)
    dates = [d for d, _ in series]
    values = [v for _, v in series]
    averaged = stats_module.moving_average(values, window)
    return {"dates": dates[window - 1:], "values": averaged}


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_server.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Run full test suite**

Run: `pytest -v`
Expected: all tests across `test_db.py`, `test_stats.py`, `test_garmin_client.py`, `test_server.py` PASS

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "Add server.py with MCP tools wiring db, stats, and garmin_client"
```

---

### Task 5: README + Claude Desktop config

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: nothing (documentation only).
- Produces: nothing consumed by other tasks — end of plan.

- [ ] **Step 1: Write README.md**

```markdown
# garmin-mcp

Local MCP server exposing Garmin Connect data (sleep, HR, HRV, stress,
VO2max, activities) to Claude, with correlation and inferred "night out"
detection.

## Setup

1. `python -m venv venv && source venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill in `GARMIN_EMAIL` / `GARMIN_PASSWORD`
4. Run tests: `pytest -v`

## Registering with Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

\`\`\`json
{
  "mcpServers": {
    "garmin": {
      "command": "/absolute/path/to/garmin-mcp/venv/bin/python",
      "args": ["/absolute/path/to/garmin-mcp/server.py"],
      "env": {
        "GARMIN_EMAIL": "your_email@example.com",
        "GARMIN_PASSWORD": "your_password"
      }
    }
  }
}
\`\`\`

Restart Claude Desktop. It launches `server.py` as a local subprocess and
talks to it over stdio (stdin/stdout, JSON-RPC) — no network port, no
separate auth layer, since both processes run under your own user account
on the same machine.

## Usage

1. Ask Claude to run `sync_garmin_data` for a date range — this logs into
   Garmin and populates the local SQLite cache at `~/.garmin-mcp/data.db`.
2. Ask Claude to correlate metrics, e.g. "correlate my sleep duration with
   resting heart rate over the last 30 days" — Claude calls `correlate`.
3. Ask Claude to find inferred nights out, or log one manually via
   `log_night_out`.

## Beyond stdio: HTTP/remote deployment (notes)

This server uses stdio because it's simplest for a single local user: no
port, no auth to manage, Claude Desktop owns the subprocess lifecycle. If
you ever needed the server reachable from another machine or by multiple
MCP clients at once, MCP also supports an HTTP transport (`streamable-http`
in the SDK) — the server would then bind a port, need its own auth (MCP
doesn't provide this for you), and run as a long-lived process instead of
a per-session subprocess. Not needed for this project; noted here as the
next step if requirements change.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Add README with setup and Claude Desktop config instructions"
```

---

## Self-Review Notes

- **Spec coverage:** all tools from spec's "MCP tools exposed" section have
  a task (Task 4). Schema matches spec exactly (Task 1). Credentials via
  env vars (Task 3). stdio transport, no HTTP (Task 4/5). Error handling —
  login failure, per-day commits, missing days as absent rows, `correlate`
  min-5-points — all implemented and tested. VO2max and stress added per
  user follow-up request, wired into `daily_stats` table and their own
  tools.
- **Type consistency:** `db.get_metric_series` return shape
  `list[tuple[str, float]]` matches usage in `server.correlate` and
  `server.moving_average_tool`. `stats.detect_night_out`'s `day`/`baseline`
  dict keys match what `server.detect_nights_out` builds.
- **Placeholder scan:** none found — every step has runnable code.
