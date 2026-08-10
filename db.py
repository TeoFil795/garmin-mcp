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
    anaerobic_training_effect REAL,
    training_effect_label TEXT,
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
    _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    # CREATE TABLE IF NOT EXISTS doesn't add columns to a table that
    # already existed from an earlier schema version — patch those in.
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(activities)")}
    for col, col_type in (
        ("anaerobic_training_effect", "REAL"),
        ("training_effect_label", "TEXT"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE activities ADD COLUMN {col} {col_type}")
    conn.commit()


def _upsert(conn, table, pk_col, pk_val, fields: dict):
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        # No real data to write. Skip the insert entirely so days with no
        # data stay absent, rather than creating a hollow all-NULL row.
        return
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
                     calories=None, training_effect=None,
                     anaerobic_training_effect=None, training_effect_label=None,
                     load=None):
    fields = dict(
        date=date, type=type, duration_min=duration_min,
        distance_km=distance_km, avg_hr=avg_hr, max_hr=max_hr,
        calories=calories, training_effect=training_effect,
        anaerobic_training_effect=anaerobic_training_effect,
        training_effect_label=training_effect_label, load=load,
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
