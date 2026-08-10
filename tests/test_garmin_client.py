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


def test_parse_vo2max_empty_list_returns_none():
    # Garmin's max-metrics endpoint returns [] (a list, not a dict) when
    # no vo2max data exists for the date.
    result = parse_vo2max([])
    assert result == dict(vo2max_running=None, vo2max_cycling=None)


def test_parse_vo2max_nonempty_list_uses_last_record():
    raw = [{"generic": {"vo2MaxPreciseValue": 48.5}, "cycling": {"vo2MaxValue": 44.0}}]
    result = parse_vo2max(raw)
    assert result == dict(vo2max_running=48.5, vo2max_cycling=44.0)


def test_parse_sleep():
    raw = {
        "dailySleepDTO": {
            # Garmin returns epoch milliseconds here, not an ISO string.
            "sleepStartTimestampLocal": 1785719334000,
            "sleepEndTimestampLocal": 1785739254000,
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
    # Expected values recomputed with the same tz-naive conversion as the
    # parser, to avoid hardcoding a timezone-dependent ISO string.
    import datetime
    assert result["bedtime"] == datetime.datetime.fromtimestamp(1785719334000 / 1000).isoformat()
    assert result["wake_time"] == datetime.datetime.fromtimestamp(1785739254000 / 1000).isoformat()
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
        "aerobicTrainingEffect": 3.2,
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
