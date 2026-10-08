"""Minimal Oura API v2 client with token refresh.

Oura refresh tokens are single-use, so the current token pair lives in SQLite
and every refresh saves the new pair before doing anything else.
"""
import json
from datetime import date, datetime, time, timedelta

import httpx

import db
from config import FIXTURES_DIR, TZ, env

API_BASE = "https://api.ouraring.com/v2/usercollection"
TOKEN_URL = "https://api.ouraring.com/oauth/token"

DAILY_ENDPOINTS = [
    "daily_readiness",
    "daily_sleep",
    "sleep",
    "daily_stress",
    "daily_activity",
    "workout",
]


class OuraAuthError(RuntimeError):
    pass


class OuraClient:
    def __init__(self):
        db.init_db()
        if db.get_tokens() is None:
            # First run: seed tokens from .env. After this, SQLite is the source of truth.
            db.save_tokens(env("OURA_ACCESS_TOKEN"), env("OURA_REFRESH_TOKEN"))
        self.http = httpx.Client(timeout=30)

    # --- auth ---------------------------------------------------------------

    def _refresh(self) -> None:
        tokens = db.get_tokens()
        resp = self.http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": env("OURA_CLIENT_ID"),
                "client_secret": env("OURA_CLIENT_SECRET"),
            },
        )
        if resp.status_code != 200:
            raise OuraAuthError(
                f"Oura token refresh failed (HTTP {resp.status_code}). "
                "Re-authorize the app and put fresh tokens in .env, then delete the tokens row."
            )
        body = resp.json()
        expires_at = None
        if "expires_in" in body:
            expires_at = (datetime.now() + timedelta(seconds=body["expires_in"])).isoformat(timespec="seconds")
        # Save immediately: the old refresh token is now dead.
        db.save_tokens(body["access_token"], body["refresh_token"], expires_at)

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET with bearer auth; on 401 refresh once and retry."""
        for attempt in range(2):
            token = db.get_tokens()["access_token"]
            resp = self.http.get(
                f"{API_BASE}/{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            resp.raise_for_status()
            return resp.json()
        raise OuraAuthError("Oura still returned 401 after refreshing the token.")

    # --- data ---------------------------------------------------------------

    def get_collection(self, endpoint: str, params: dict) -> dict:
        """Fetch every page of a collection endpoint and return {"data": [...]}."""
        items, params = [], dict(params)
        while True:
            page = self._get(endpoint, params)
            items.extend(page.get("data", []))
            if not page.get("next_token"):
                return {"data": items}
            params["next_token"] = page["next_token"]

    def daily(self, endpoint: str, start: date, end: date) -> dict:
        """Daily-style endpoints filtered by start_date/end_date."""
        return self.get_collection(endpoint, {"start_date": start.isoformat(), "end_date": end.isoformat()})

    def heartrate(self, start: date, end: date) -> dict:
        """Intraday heart rate; this endpoint takes datetimes, not dates."""
        start_dt = datetime.combine(start, time.min, TZ)
        end_dt = datetime.combine(end, time.min, TZ)
        return self.get_collection(
            "heartrate",
            {"start_datetime": start_dt.isoformat(), "end_datetime": end_dt.isoformat()},
        )

    def personal_info(self) -> dict:
        return self._get("personal_info")

    def fetch_range(self, start: date, end: date) -> dict:
        """All raw collections for [start, end), keyed by endpoint name, plus personal_info."""
        raw = {name: self.daily(name, start, end) for name in DAILY_ENDPOINTS}
        raw["heartrate"] = self.heartrate(start, end)
        raw["personal_info"] = self.personal_info()
        return raw


def load_fixtures() -> dict:
    """Raw responses saved by scripts/smoke_test.py, same shape as `fetch_range`."""
    names = [*DAILY_ENDPOINTS, "heartrate", "personal_info"]
    return {name: json.loads((FIXTURES_DIR / f"{name}.json").read_text()) for name in names}


def latest_day(raw: dict) -> date:
    """The newest day with a readiness record (used as "today" in fixtures mode)."""
    return max(date.fromisoformat(r["day"]) for r in raw["daily_readiness"]["data"])


# --- parsing (fields taken from real responses saved in fixtures/) -----------

def body_metrics(raw: dict) -> dict:
    """Age, weight and height from personal_info (fields seen in fixtures/personal_info.json).

    Oura gives weight in kg and height in meters (judged from the values; the response has no units).
    """
    info = raw.get("personal_info") or {}
    height_m = info.get("height")
    return {
        "age": info.get("age"),
        "weight_kg": info.get("weight"),
        "height_cm": round(height_m * 100, 1) if height_m is not None else None,
        "sex": info.get("biological_sex"),
    }


def _by_day(records: list[dict]) -> dict[str, dict]:
    return {r["day"]: r for r in records}


def daily_metrics(raw: dict, day: date) -> dict:
    """One flat dict of the metrics we care about for a single day.

    Missing data comes back as None. Durations from Oura are in seconds.
    HRV and resting HR come from the night's main sleep (type "long_sleep").
    """
    key = day.isoformat()
    readiness = _by_day(raw["daily_readiness"]["data"]).get(key, {})
    sleep_score = _by_day(raw["daily_sleep"]["data"]).get(key, {})
    stress = _by_day(raw["daily_stress"]["data"]).get(key, {})
    activity = _by_day(raw["daily_activity"]["data"]).get(key, {})
    main_sleep = next(
        (s for s in raw["sleep"]["data"] if s["day"] == key and s["type"] == "long_sleep"), {}
    )

    hr_samples = [
        s for s in raw["heartrate"]["data"]
        if datetime.fromisoformat(s["timestamp"]).astimezone(TZ).date() == day
    ]
    bpms = [s["bpm"] for s in hr_samples]
    # Workout samples are much denser than awake/rest ones, so leave them out of the average.
    everyday_bpms = [s["bpm"] for s in hr_samples if s["source"] != "workout"]

    def hours(seconds):
        return round(seconds / 3600, 1) if seconds is not None else None

    def minutes(seconds):
        return round(seconds / 60) if seconds is not None else None

    def local(iso: str) -> datetime:
        return datetime.fromisoformat(iso).astimezone(TZ)

    return {
        "day": key,
        "readiness_score": readiness.get("score"),
        "temperature_deviation": readiness.get("temperature_deviation"),
        "sleep_score": sleep_score.get("score"),
        "sleep_hours": hours(main_sleep.get("total_sleep_duration")),
        "hrv_ms": main_sleep.get("average_hrv"),
        "resting_hr": main_sleep.get("lowest_heart_rate"),
        "stress_summary": stress.get("day_summary"),
        "stress_high_min": minutes(stress.get("stress_high")),
        "recovery_high_min": minutes(stress.get("recovery_high")),
        "activity_score": activity.get("score"),
        "steps": activity.get("steps"),
        "active_calories": activity.get("active_calories"),
        "high_activity_min": minutes(activity.get("high_activity_time")),
        "medium_activity_min": minutes(activity.get("medium_activity_time")),
        "hr_avg": round(sum(everyday_bpms) / len(everyday_bpms)) if everyday_bpms else None,
        "hr_max": max(bpms) if bpms else None,
        "workouts": [
            {
                "activity": w["activity"],
                "intensity": w["intensity"],
                "start": local(w["start_datetime"]).isoformat(timespec="minutes"),
                "end": local(w["end_datetime"]).isoformat(timespec="minutes"),
                "minutes": round((local(w["end_datetime"]) - local(w["start_datetime"])).total_seconds() / 60),
            }
            for w in raw["workout"]["data"] if w["day"] == key
        ],
    }
