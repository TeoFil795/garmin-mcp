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
