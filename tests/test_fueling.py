"""Fueling amounts (modules/nutrition/fueling.py): pure math over config/fueling.yaml. Every range cites a
GSSI article from docs/gssi_sources.txt; grams only with a known weight; the run's minutes from their
words, their pace or their longest recent run."""
from datetime import date

from mcp_servers.gssi.ingest import normalize, read_sources
from modules.nutrition import fueling
from tests.test_gssi_index import SOURCES

MON = date(2026, 11, 9)
SAT = date(2026, 11, 14)


def test_every_range_cites_an_article_from_the_source_list():
    rules = fueling.rules()
    listed = {normalize(u) for u in read_sources(SOURCES)}
    assert {normalize(u) for u in rules["sources"].values()} <= listed
    named = [r["source"] for key in ("day_before", "race_day_before", "before", "during", "fluids", "sodium")
             for r in rules[key]] + [rules[key]["source"] for key in ("caffeine", "after", "recovery")] + [
             rules["caffeine"]["timing_source"]]
    assert set(named) == set(rules["sources"])                             # each source is used, none is missing


def test_ranges_cover_every_run_length():
    for key in ("day_before", "race_day_before", "before", "during", "fluids", "sodium"):
        for minutes in (10, 44, 45, 74, 75, 89, 90, 119, 120, 149, 150, 300):
            fueling.pick(fueling.rules()[key], minutes)                    # never StopIteration


def test_a_long_saturday_run_with_a_known_weight():
    result = fueling.plan(110, "your longest run in the last 4 weeks", 70, None, SAT, MON,
                          {"average_g": 180, "days": 5})
    a = result["amounts"]
    assert a["day_before_g"] == [490, 840] and a["before_g"] == [70, 280] and a["during_g"] == [55, 110]
    assert a["max_loss_kg"] == 1.4 and a["sodium_mg_per_liter"] == [460, 920] and a["after_protein_g"] == [20, 25]
    assert a["recovery_g_per_hour"] == [70, 85]
    lines = result["text"].splitlines()
    assert lines[0] == "🏃 Long run Sat Nov 14 · about 110 min (your longest run in the last 4 weeks)"
    assert lines[1] == ("Fri: carbs 7–12 g/kg → 490–840 g over the day, more for a longer run; lately about 180 g a "
                        "day (5 days logged).")
    assert "no weather data" in result["text"] and not result["per_kg"]


def test_under_90_minutes_no_extra_carbs_the_day_before_and_drink_to_thirst():
    result = fueling.plan(85, "x", 70, None, SAT, MON, {"average_g": 180, "days": 5})
    assert result["amounts"]["day_before_g"] is None and "max_loss_kg" not in result["amounts"]
    assert "Fri: no extra carbs needed, just your usual meals with carbs; lately about 180 g a day" in result["text"]
    assert "Drink: to thirst; water is fine" in result["text"] and result["amounts"]["during_g"] == [40, 85]
    short = fueling.plan(40, "x", 70, ["before"], SAT, MON)
    assert short["amounts"]["before_g"] is None                            # 1-4 g/kg is for runs over 60 min
    assert short["text"] == "Sat, before: your usual meal or snack is enough for a run under an hour."


def test_without_a_weight_everything_stays_per_kg_and_asks_once():
    result = fueling.plan(140, "as you said", None, None, SAT, MON, ask_weight=True)
    assert result["per_kg"] and "→" not in result["text"].split("\n", 2)[1]
    assert result["amounts"]["day_before_g"] is None and result["amounts"]["max_loss_kg"] is None
    assert "under 2% of your body weight" in result["text"]
    assert result["text"].endswith("Tell me your weight and I'll turn these into grams.")
    assert not fueling.plan(140, "x", None, None, SAT, MON, ask_weight=False)["text"].endswith("grams.")


def test_a_run_today_has_no_day_before_line():
    result = fueling.plan(110, "x", 70, None, MON, MON)
    assert "day_before_g" not in result["amounts"] and "over the day" not in result["text"]


def test_carbs_during_by_run_length():
    during = lambda minutes: fueling.plan(minutes, "x", 70, ["during"], None, MON)["amounts"]
    assert during(40)["during_g"] is None and during(55)["during_g"] is None
    assert during(120)["during_g_per_hour"] == [30, 60]
    marathon = fueling.plan(180, "x", 70, ["during"], None, MON, distance_km=30)["amounts"]
    half = fueling.plan(170, "x", 70, ["during"], None, MON, distance_km=21.1)["amounts"]
    assert marathon["during_g_per_hour"] == [60, 90]
    assert half["during_g_per_hour"] == [30, 60] and during(180)["during_g_per_hour"] == [30, 60]   # a half: 30-60
    for minutes in (60, 90, 120, 149, 150, 170, 200):                    # a half marathon at any pace
        assert fueling.plan(minutes, "x", 70, ["during"], None, MON, distance_km=21.1)["amounts"]["during_g_per_hour"] == [30, 60]


def test_recovery_carbs_after_a_hard_session_for_a_second_one_that_day():
    result = fueling.plan(110, "x", 70, ["after"], None, MON)
    assert result["amounts"]["recovery_g_per_hour"] == [70, 85]
    assert result["text"].endswith("Carbs 1–1.2 g/kg an hour → 70–85 g an hour for the first 4 h.")


def test_a_race_of_90_minutes_or_more_is_carb_loading():
    result = fueling.plan(150, "x", 70, ["day_before"], date(2027, 2, 15), MON, race=True)
    assert result["amounts"]["day_before_g"] == [700, 840] and result["text"].startswith("🏃 Race Mon Feb 15")
    assert "Sat–Sun: carb loading" in result["text"]
    short = fueling.plan(30, "x", 70, ["day_before"], date(2027, 2, 15), MON, race=True)
    assert short["amounts"]["day_before_g"] is None and "no carb loading needed" in short["text"]


def test_only_the_parts_asked_for():
    caffeine = fueling.plan(40, "x", 70, ["caffeine"], None, MON)
    assert caffeine["amounts"] == {"caffeine_mg": [100, 210], "caffeine_minutes_before": 60}
    assert caffeine["text"] == ("Caffeine, if you use it: start low, 1.5–3 mg/kg → 100–210 mg; more doesn't help. Take "
                                "it about 60 min before the run (or late in a long one). Try it in training first.")
    assert "about 100–200 mg" in fueling.plan(40, "x", None, ["caffeine"], None, MON)["text"]
    fluids = fueling.plan(62, "10 km at your usual 6:12 per km", 70, ["fluids"], None, MON, distance_km=10)
    assert fluids["text"].splitlines()[0] == "🏃 Run of 10 km · about 62 min (10 km at your usual 6:12 per km)"
    assert "water is fine" in fluids["text"] and "sodium_mg_per_liter" not in fluids["amounts"]


def test_run_minutes_their_words_then_pace_then_longest_run():
    runs = [{"minutes": 62, "distance_km": 10.0}, {"minutes": 95, "distance_km": 14.0}, {"minutes": 30, "distance_km": None}]
    assert fueling.run_minutes(80, 21.1, runs) == (80, "as you said")
    assert fueling.run_minutes(None, 10, runs) == (65, "10 km at your usual 6:30 per km")    # median pace
    assert fueling.run_minutes(None, None, runs) == (95, "your longest run in the last 4 weeks; tell me if it's longer")
    no_gps = [{"minutes": 37, "distance_km": 2.2}]                         # 17 min/km: not a usable pace
    assert fueling.run_minutes(None, 10, no_gps) == (70, "10 km at about 7 min per km; no usable run pace from Oura")
    assert fueling.run_minutes(None, None, [])[0] == fueling.TYPICAL_LONG_RUN_MIN
    assert fueling.run_minutes(None, 1, [{"minutes": 6.999, "distance_km": 1.0}])[1].endswith("7:00 per km")


def test_recent_carbs_count_only_logged_days():
    assert fueling.recent_carbs({"a": 200, "b": None, "c": 160}) == {"average_g": 180, "days": 2}
    assert fueling.recent_carbs({"a": 200, "b": None}) is None
