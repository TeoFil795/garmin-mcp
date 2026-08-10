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


def parse_vo2max(raw) -> dict:
    # Garmin's max-metrics endpoint returns a list of per-day records (empty
    # when no data exists for the date), not a single dict.
    if isinstance(raw, list):
        raw = raw[-1] if raw else {}
    generic = raw.get("generic") or {}
    cycling = raw.get("cycling") or {}
    return dict(
        vo2max_running=generic.get("vo2MaxPreciseValue"),
        vo2max_cycling=cycling.get("vo2MaxValue"),
    )


def _epoch_ms_to_iso(ms):
    """Convert Garmin's *Local epoch fields to a local wall-clock ISO string.

    Garmin's ...TimestampLocal values already have the local UTC offset baked
    in, so they must be read as UTC to recover wall-clock time. Using
    fromtimestamp() here would apply the machine's offset a second time.
    """
    if ms is None:
        return None
    import datetime
    return (datetime.datetime
            .fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
            .replace(tzinfo=None)
            .isoformat())


def parse_sleep(raw: dict) -> dict:
    dto = raw.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    overall = scores.get("overall") or {}

    def to_min(seconds):
        return seconds // 60 if seconds is not None else None

    return dict(
        bedtime=_epoch_ms_to_iso(dto.get("sleepStartTimestampLocal")),
        wake_time=_epoch_ms_to_iso(dto.get("sleepEndTimestampLocal")),
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
        training_effect=raw.get("aerobicTrainingEffect"),
        anaerobic_training_effect=raw.get("anaerobicTrainingEffect"),
        training_effect_label=raw.get("trainingEffectLabel"),
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
