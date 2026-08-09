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
    # Handle bedtime wrap-around (midnight crossing)
    day_bedtime = day["bedtime_hour"]
    baseline_bedtime = baseline["avg_bedtime_hour"]

    if baseline_bedtime > 20 and day_bedtime < 6:
        # Wrapped comparison: (24 - baseline) + day
        bedtime_diff = (24 - baseline_bedtime) + day_bedtime
    else:
        bedtime_diff = day_bedtime - baseline_bedtime

    signals = {
        "late_bedtime": bedtime_diff >= 1.5,
        "short_sleep": day["sleep_duration_min"] <= baseline["avg_sleep_duration_min"] - 90,
        "elevated_overnight_hr": day["overnight_hr"] >= baseline["avg_overnight_hr"] + 10,
        "low_hrv": day["hrv"] <= baseline["avg_hrv"] * 0.85,
    }
    triggered = sum(signals.values())
    confidence = triggered / len(signals)
    is_out = triggered >= 2
    return is_out, confidence, signals
