"""Eating and drinking around a run: the amounts, computed in code from config/fueling.yaml (GSSI ranges).

Pure: `plan(...)` takes the run's minutes, the body weight (or None), the parts asked for and the days, and
returns the lines sent to the person as built, plus the numbers. The tool (modules/nutrition/tools.py)
gathers the inputs: the run day from their words, minutes from their words, their pace, or their longest
recent run; body weight from goals.yaml / Oura / a saved fact, never estimated; recent carbs from food_log.

Without a weight every amount stays per kg ("5-7 g per kg") and the person is asked once for it. Grams made from
a stored weight say so once: "(using 70 kg from your Oura profile; tell me if that's off)".
"""
from datetime import date, timedelta
from functools import lru_cache

import yaml

from core.config import ROOT
from core.labels import clock
from modules.goals.paces import easy_pace
from modules.nutrition import caffeine, fueling_foods

RULES_PATH = ROOT / "config" / "fueling.yaml"
PARTS = ("day_before", "before", "during", "fluids", "caffeine", "after")
DEFAULT_PARTS = ("day_before", "before", "during", "fluids", "after")
DURATION_PARTS = {"day_before", "during", "fluids"}     # parts whose amounts depend on how long the run is
TYPICAL_LONG_RUN_MIN = 75                               # blocks.DEFAULT_MINUTES["long_run"]
LONG_RUN_MIN = 60
SODIUM_MG_PER_MMOL = 23
RECENT_DAYS = 7
ASK_WEIGHT = "Tell me your weight and I'll turn these into grams."
ASK_TIME = ("🏃 Run of {km:g} km · I don't know your usual pace yet (no run with a usable pace in your data). "
            "Roughly how long will it take you? Then I'll work out what you need.")
WEIGHT_NOTE = "(using {kg:.0f} kg {source}; tell me if that's off)"
FROM_WEIGHT = ("day_before_g", "before_g", "max_loss_kg", "caffeine_mg", "recovery_g_per_hour")   # amounts × kg
MIN_RECENT_DAYS_LOGGED = 2


@lru_cache(maxsize=1)
def rules() -> dict:
    return yaml.safe_load(RULES_PATH.read_text())


def pick(ranges: list[dict], minutes: float, distance_km: float | None = None) -> dict:
    """The range for a run of `minutes`: min_minutes <= minutes < max_minutes, and longer than `longer_than_km` when
    a range asks for it (only with a known distance). The last match wins (the more specific range)."""
    return [r for r in ranges if r.get("min_minutes", 0) <= minutes < r.get("max_minutes", float("inf"))
            and (r.get("longer_than_km") is None or (distance_km or 0) > r["longer_than_km"])][-1]


def _round(value: float, step: int) -> int:
    return int(round(value / step) * step)


def _span(lo: float, hi: float, unit: str = "") -> str:
    lo_s, hi_s = f"{lo:,.0f}" if lo >= 1000 else f"{lo:g}", f"{hi:,.0f}" if hi >= 1000 else f"{hi:g}"
    return f"{lo_s}{unit}" if lo == hi else f"{lo_s}–{hi_s}{unit}"


def _grams(per_kg: list[float], kg: float | None, step: int = 5) -> list[int] | None:
    return [_round(per_kg[0] * kg, step), _round(per_kg[1] * kg, step)] if kg else None


def run_minutes(said_minutes: float | None, distance_km: float | None, recent_runs: list[dict],
                asked_pace: float | None = None) -> tuple[int | None, str]:
    """(minutes, how we know). recent_runs: [{"minutes", "distance_km"}] of the last weeks (Oura + manual).
    Their words first; a distance at their usual pace (their runs, else the pace they told us); else their longest
    recent run; else a typical long run. A distance with no pace known: (None, ...), never a made-up pace."""
    if said_minutes:
        return round(said_minutes), "as you said"
    pace = easy_pace(recent_runs) or asked_pace
    if distance_km and pace:
        seconds = round(pace * 60)
        return round(distance_km * pace), f"{distance_km:g} km at your usual {seconds // 60}:{seconds % 60:02d} per km"
    if distance_km:
        return None, f"{distance_km:g} km, but I don't know your usual pace yet"
    if recent_runs:
        return max(r["minutes"] for r in recent_runs), "your longest run in the last 4 weeks; tell me if it's longer"
    return TYPICAL_LONG_RUN_MIN, "a typical long run; tell me if yours is different"


def recent_carbs(carbs_by_day: dict[str, float | None]) -> dict | None:
    """{"average_g", "days"} over the logged days (None = nothing logged that day), or None with too few days."""
    logged = [v for v in carbs_by_day.values() if v is not None]
    if len(logged) < MIN_RECENT_DAYS_LOGGED:
        return None
    return {"average_g": _round(sum(logged) / len(logged), 5), "days": len(logged)}


def plan(minutes: int, how: str, weight_kg: float | None, parts: list[str] | None, run_day: date | None,
         today: date, carbs_lately: dict | None = None, race: bool = False, ask_weight: bool = False,
         distance_km: float | None = None, weight_from: str | None = None, caffeine_day: dict | None = None,
         foods: dict | None = None) -> dict:
    """{"text": lines sent as built, "amounts": the numbers, "sources": {part: url}, "source_list": [{title, url}]
    (the articles behind the amounts, in order), "carb_load": True/False when the day before is in the plan
    (False = no extra carbs), "per_kg": bool}. weight_from: where a stored weight came from ("from your Oura
    profile"), said once under amounts made from it; None = don't say it (they just told us).
    caffeine_day: {"today": the run is today, "so_far_mg", "hours_to_bed" (when they'd take it), "bedtime": their
    usual bedtime (a datetime) or None}. foods: real foods for the lines (fueling_foods.pick), or None."""
    r = rules()
    kg = weight_kg
    parts = [p for p in (parts or DEFAULT_PARTS) if p in PARTS]
    if run_day is not None and run_day <= today:
        parts = [p for p in parts if p != "day_before"]          # the day before has passed
    lines, amounts, sources = [], {}, {}
    day = f"{run_day:%a}" if run_day else None

    if set(parts) & DURATION_PARTS:
        what = "Race" if race else ("Long run" if minutes >= LONG_RUN_MIN and not distance_km else "Run")
        if distance_km:
            what = f"{what} of {distance_km:g} km"
        when = f" {run_day:%a %b} {run_day.day}" if run_day else ""
        lines.append(f"🏃 {what}{when} · about {minutes} min ({how})")

    def per(g_per_kg, grams_):
        return f"{_span(*g_per_kg)} g/kg → {_span(*grams_)} g" if grams_ else f"{_span(*g_per_kg)} g per kg"

    if "day_before" in parts:
        rule = pick(r["race_day_before" if race else "day_before"], minutes)
        grams_ = _grams(rule["g_per_kg"], kg, 10) if rule["g_per_kg"] else None
        amounts["day_before_g"] = grams_
        sources["day_before"] = r["sources"][rule["source"]]
        loading = race and rule["g_per_kg"]
        if loading:
            start = run_day - timedelta(days=2) if run_day else None
            label = f"{start:%a}–{run_day - timedelta(days=1):%a}" if run_day else "The 2 days before"
            line = f"{label}: carb loading, {per(rule['g_per_kg'], grams_)} a day"
        else:
            label = f"{run_day - timedelta(days=1):%a}" if run_day else "The day before"
            if rule["g_per_kg"]:
                line = f"{label}: carbs {per(rule['g_per_kg'], grams_)} over the day, {rule['note']}"
                if foods:
                    line += f"; at dinner, {fueling_foods.phrase(foods['dinner'])}"
            elif foods:          # no extra carbs: a real dinner, and why nothing more is needed
                under = f"a {'race' if race else 'run'} under {rule['max_minutes']} min"
                line = (f"{label} dinner: {fueling_foods.phrase(foods['dinner'])} (no "
                        f"{'carb loading' if race else 'extra carbs'} needed for {under})")
            else:
                line = f"{label}: {rule['note']}"
        if carbs_lately:
            line += f"; lately about {carbs_lately['average_g']} g a day ({carbs_lately['days']} days logged)"
        lines.append(line + ".")

    if "before" in parts:
        rule = pick(r["before"], minutes)
        sources["before"] = r["sources"][rule["source"]]
        if rule["g_per_kg"]:
            grams_ = _grams(rule["g_per_kg"], kg)
            amounts["before_g"] = grams_
            lines.append(f"{day + ', ' if day else ''}{_span(*rule['hours'])} h before: carbs "
                         f"{per(rule['g_per_kg'], grams_)}, less the closer to the start; easy on fat and fiber"
                         + (f" ({fueling_foods.phrase(foods['snack'])} works)" if foods else "") + ".")
        else:
            amounts["before_g"] = None
            if foods and rule.get("snack_minutes_before"):
                lines.append(f"{day + ', ' if day else ''}{_span(*rule['snack_minutes_before'])} min before: "
                             f"{fueling_foods.phrase(foods['snack'])}, if you want something.")
            else:
                lines.append(f"{day + ', before' if day else 'Before'}: {rule['note']}.")

    if "during" in parts:
        rule = pick(r["during"], minutes, distance_km)
        sources["during"] = r["sources"][rule["source"]]
        if rule["g_per_hour"]:
            lo, hi = rule["g_per_hour"]
            total = [_round(lo * minutes / 60, 5), _round(hi * minutes / 60, 5)]
            amounts["during_g_per_hour"], amounts["during_g"] = rule["g_per_hour"], total
            lines.append(f"During: {lo}–{hi} g carbs an hour → {_span(*total)} g in all"
                         + (f" ({rule['note']})" if rule.get("note") else "") + ".")
        else:
            amounts["during_g"] = None
            lines.append(f"During: {rule['note']}.")

    if "fluids" in parts:
        rule = pick(r["fluids"], minutes)
        sodium = pick(r["sodium"], minutes)
        sources["fluids"] = r["sources"][rule["source"]]
        if rule["plan"] == "thirst":
            line = "Drink: to thirst"
        else:
            pct = rule["max_loss_pct"]
            loss = f"{pct}% of your weight ({kg * pct / 100:.1f} kg)" if kg else f"{pct}% of your body weight"
            amounts["max_loss_kg"] = round(kg * pct / 100, 1) if kg else None
            line = (f"Drink: plan it, so you lose under {loss} and never gain weight (weigh yourself before and "
                    "after to learn your sweat loss: 1 kg is about 1 L)")
        if sodium["mmol_per_liter"]:
            sources["sodium"] = r["sources"][sodium["source"]]
            mg = [n * SODIUM_MG_PER_MMOL for n in sodium["mmol_per_liter"]]
            amounts["sodium_mg_per_liter"] = mg
            line += f", with {_span(*mg)} mg sodium per liter"
        else:
            line += f"; {sodium['note']}"
        lines.append(line + ". I have no weather data: drink more on a hot day.")

    if "caffeine" in parts:
        rule, sleep = r["caffeine"], r["caffeine_sleep"]
        sources["caffeine"] = r["sources"][rule["source"]]
        mg = _grams(rule["mg_per_kg"], kg, 5)
        amounts["caffeine_mg"] = mg
        span = (f"{_span(*rule['mg_per_kg'])} mg/kg → {_span(*mg)} mg" if mg
                else f"about {_span(*rule['mg_fixed'])} mg, {_span(*rule['mg_per_kg'])} mg/kg")
        sources["caffeine_timing"] = r["sources"][rule["timing_source"]]
        amounts["caffeine_minutes_before"] = rule["minutes_before"]
        day = caffeine_day or {}
        on_the_day = day.get("today", True)          # the run is today (or no day given): their day so far counts
        so_far = (day.get("so_far_mg") or 0) if on_the_day else 0
        mine = caffeine.dose(mg or rule["mg_fixed"], so_far, day.get("hours_to_bed") if on_the_day else None, sleep)
        amounts["caffeine_for_you_mg"] = mine["mg"]
        bed = clock(day["bedtime"]) if day.get("bedtime") else None
        if mine["skip"] == "bedtime":
            lines.append(f"Caffeine: skip it today: your usual bedtime ({bed}) is "
                         f"{caffeine._hours(day['hours_to_bed'])} away, and caffeine this late can cut into your sleep.")
        elif mine["skip"] == "daily_max":
            lines.append(f"Caffeine: skip more today: you've had about {so_far:.0f} mg already.")
        else:
            when = "today" if on_the_day else f"on {run_day:%a}" if run_day else "for the run"
            had = f" (with the {so_far:.0f} mg you've had today)" if so_far else ""
            later = (f" Keep it at least {sleep['stop_hours_before_bed']} h before your usual {bed} bedtime."
                     if bed and not on_the_day else "")
            lines.append(f"Caffeine, if you use it: about {mine['mg']} mg for you {when}{had}, about "
                         f"{rule['minutes_before']} min before the run (or late in a long one). Start low ({span}); more "
                         f"doesn't help. Try it in training first.{later}")

    if "after" in parts:
        rule = r["after"]
        sources["after"] = r["sources"][rule["source"]]
        amounts["after_protein_g"] = rule["protein_g"]
        fast = r["recovery"]
        sources["recovery"] = r["sources"][fast["source"]]
        per_hour = [_round(fast["g_per_kg_per_hour"][0] * kg, 5), _round(fast["g_per_kg_per_hour"][1] * kg, 5)] if kg else None
        amounts["recovery_g_per_hour"] = per_hour
        rate = (f"{_span(*fast['g_per_kg_per_hour'])} g/kg an hour → {_span(*per_hour)} g an hour" if per_hour
                else f"{_span(*fast['g_per_kg_per_hour'])} g per kg an hour")
        food = f" ({fueling_foods.phrase(foods['after'])} works)" if foods else ""
        lines.append(f"After: {_span(*rule['protein_g'])} g protein{food}, with carbs, at your next meal or snack. Training "
                     f"hard again later that day? Carbs {rate} for the first {fast['hours']} h.")

    if kg is None and ask_weight:
        lines.append(ASK_WEIGHT)
    if kg and weight_from and any(amounts.get(name) for name in FROM_WEIGHT):
        lines.append(WEIGHT_NOTE.format(kg=kg, source=weight_from))
    titles = {url: r["titles"][key] for key, url in r["sources"].items()}
    source_list = [{"title": titles[url], "url": url} for url in dict.fromkeys(sources.values())]
    carb_load = (amounts["day_before_g"] is not None or bool(pick(r["race_day_before" if race else "day_before"],
                                                                   minutes)["g_per_kg"])) if "day_before" in parts else None
    return {"text": "\n".join(lines), "amounts": amounts, "sources": sources, "source_list": source_list,
            "carb_load": carb_load, "per_kg": kg is None, "minutes": minutes}
