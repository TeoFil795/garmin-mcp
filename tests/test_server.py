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

    result = server.get_daily_summary("2026-08-01")
    assert result["date"] == "2026-08-01"
    assert result["daily_stats"]["resting_hr"] == 52
    assert result["sleep"]["sleep_score"] == 80
    assert result["hrv"]["overall_hrv"] == 55


def test_get_daily_summary_handles_missing_data(conn):
    result = server.get_daily_summary("2026-08-01")
    assert result["daily_stats"] is None
    assert result["sleep"] is None
    assert result["hrv"] is None


def test_get_vo2max_reads_daily_stats(conn):
    db_module.upsert_daily_stats(
        conn, "2026-08-01", vo2max_running=48.5, vo2max_cycling=44.0
    )
    result = server.get_vo2max("2026-08-01", "2026-08-01")
    assert result[0]["vo2max_running"] == 48.5


def test_get_stress_reads_daily_stats(conn):
    db_module.upsert_daily_stats(conn, "2026-08-01", stress_avg=28, stress_max=74)
    result = server.get_stress("2026-08-01", "2026-08-01")
    assert result[0]["stress_avg"] == 28


def test_log_night_out_manual(conn):
    result = server.log_night_out("2026-08-01", "birthday party")
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

    result = server.correlate("resting_hr", "steps", "2026-08-01", "2026-08-05")
    assert result["correlation"] == pytest.approx(1.0)
    assert result["n_points"] == 5


def test_correlate_too_few_points_errors(conn):
    db_module.upsert_daily_stats(conn, "2026-08-01", resting_hr=50, steps=5000)
    with pytest.raises(ValueError, match="at least 5"):
        server.correlate("resting_hr", "steps", "2026-08-01", "2026-08-01")


def test_correlate_rejects_unknown_metric(conn):
    with pytest.raises(ValueError, match="unknown metric"):
        server.correlate("not_a_metric", "steps", "2026-08-01", "2026-08-05")


def test_moving_average_tool(conn):
    for i, hr in enumerate([50, 52, 54, 56]):
        db_module.upsert_daily_stats(conn, f"2026-08-0{i + 1}", resting_hr=hr)
    result = server.moving_average_tool("resting_hr", 2, "2026-08-01", "2026-08-04")
    assert result["values"] == [51.0, 53.0, 55.0]


def test_sync_garmin_data_survives_activities_fetch_error(conn, monkeypatch):
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")

    monkeypatch.setattr(server.GarminClient, "login", lambda self: None)
    monkeypatch.setattr(
        server.GarminClient, "fetch_daily_stats",
        lambda self, date: {"resting_hr": 50},
    )
    monkeypatch.setattr(
        server.GarminClient, "fetch_vo2max", lambda self, date: {}
    )
    monkeypatch.setattr(
        server.GarminClient, "fetch_sleep", lambda self, date: {}
    )
    monkeypatch.setattr(
        server.GarminClient, "fetch_hrv", lambda self, date: {}
    )

    def raise_rate_limit(self, start, end):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(
        server.GarminClient, "fetch_activities", raise_rate_limit
    )

    result = server.sync_garmin_data("2026-08-01", "2026-08-01")

    assert result["synced"] == ["2026-08-01"]
    assert result["failed"] == []
    assert result["activities_synced"] == 0
    assert "rate limited" in result["activities_error"]

    rows = db_module.get_daily_stats(conn, "2026-08-01", "2026-08-01")
    assert rows[0]["resting_hr"] == 50


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

    result = server.detect_nights_out("2026-08-01", "2026-08-01")
    assert len(result) == 1
    assert result[0]["date"] == "2026-08-01"
    assert result[0]["is_night_out"] is True

    stored = db_module.get_nights_out(conn, "2026-08-01", "2026-08-01")
    assert stored[0]["source"] == "inferred"
