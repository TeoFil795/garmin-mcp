# server.py
import json
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import db as db_module
import stats as stats_module
from garmin_client import GarminClient

# Anchor to this file, not the working directory: an MCP client launches the
# server as a subprocess with a CWD we do not control, so a bare load_dotenv()
# would miss the .env sitting next to this script. Keeping it findable is what
# lets credentials live only here, instead of in the client's config file.
load_dotenv(Path(__file__).resolve().parent / ".env")

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

    result = {"synced": synced, "failed": failed, "activities_synced": 0}
    try:
        activities = client.fetch_activities(start_date, end_date)
        for act in activities:
            db_module.upsert_activity(conn, act["activity_id"], act["date"], **{
                k: v for k, v in act.items() if k not in ("activity_id", "date")
            })
        result["activities_synced"] = len(activities)
    except Exception as e:
        result["activities_error"] = str(e)

    return result


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


_BASELINE_LOOKBACK_DAYS = 30


@mcp.tool()
def detect_nights_out(start: str, end: str) -> list[dict]:
    conn = _get_conn()
    # Pull extra history before `start` so the baseline isn't limited to
    # just the (possibly narrow) requested evaluation window.
    lookback_start = (
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=_BASELINE_LOOKBACK_DAYS)
    ).strftime("%Y-%m-%d")

    sleep_rows = {r["date"]: r for r in db_module.get_sleep(conn, lookback_start, end)}
    hr_rows = {r["date"]: r for r in db_module.get_daily_stats(conn, lookback_start, end)}
    hrv_rows = {r["date"]: r for r in db_module.get_hrv(conn, lookback_start, end)}

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
        if dates[i] < start or dates[i] > end:
            continue
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


@mcp.tool(name="moving_average")
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
