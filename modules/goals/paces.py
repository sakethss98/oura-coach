"""Paces and finish times from their own runs or their target, never a fixed number. Pure.

A pace is minutes per km. Runs come as [{"minutes", "distance_km"}] (modules/goals/signals.runs).
"""
from statistics import median

# A run's pace is used only inside this range (min per km): a GPS-less record of 2 km in 37 min would make a 10 km
# take almost 3 hours.
PLAUSIBLE_PACE = (3, 12)
RIEGEL_EXPONENT = 1.06            # Riegel (1981): T2 = T1 x (D2 / D1) ^ 1.06
QUALIFYING_KM = 5                 # a run must be at least this long to predict a race from it


def paces(runs: list[dict]) -> list[float]:
    found = (r["minutes"] / r["distance_km"] for r in runs if r.get("distance_km") and r.get("minutes"))
    return [p for p in found if PLAUSIBLE_PACE[0] <= p <= PLAUSIBLE_PACE[1]]


def easy_pace(runs: list[dict]) -> float | None:
    """Their usual pace: the median of their recent runs, or None."""
    found = paces(runs)
    return median(found) if found else None


def riegel(minutes: float, distance_km: float, race_km: float) -> float:
    return minutes * (race_km / distance_km) ** RIEGEL_EXPONENT


def predict(runs: list[dict], race_km: float) -> dict | None:
    """The fastest Riegel projection from a qualifying run (>= 5 km, a plausible pace): {"minutes", "from"}, or None.
    Approximate: device durations can include pauses, and Riegel assumes a trained, evenly paced runner."""
    qualifying = [r for r in runs if (r.get("distance_km") or 0) >= QUALIFYING_KM and paces([r])]
    if not qualifying:
        return None
    best = min(qualifying, key=lambda r: riegel(r["minutes"], r["distance_km"], race_km))
    return {"minutes": riegel(best["minutes"], best["distance_km"], race_km), "from": best}


def parse_time(text: str) -> float | None:
    """ "1:45" or "1:45:00" (h:mm[:ss]) -> minutes; None if unreadable."""
    parts = text.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    h, m, s = (int(p) for p in parts + ["0"] * (3 - len(parts)))
    return h * 60 + m + s / 60 if m < 60 and s < 60 else None


def race_pace(target_minutes: float | None, race_km: float, runs: list[dict]) -> float | None:
    """Goal race pace: their target time over the distance, else predicted from their runs, else None."""
    if target_minutes:
        return target_minutes / race_km
    found = predict(runs, race_km)
    return found["minutes"] / race_km if found else None
