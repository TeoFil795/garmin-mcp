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
