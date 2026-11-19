"""Fueling amounts (modules/nutrition/fueling.py): pure math over config/fueling.yaml. Every range cites a
GSSI article from docs/gssi_sources.txt; grams only with a known weight; the run's minutes from their
words, their pace or their longest recent run."""
from datetime import date, datetime

from mcp_servers.gssi.ingest import normalize, read_sources
from modules.nutrition import caffeine, fueling, fueling_foods
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


def test_grams_from_a_stored_weight_say_where_it_came_from():
    noted = fueling.plan(110, "x", 69.9, None, SAT, MON, weight_from="from your Oura profile")
    assert noted["text"].endswith("\n(using 70 kg from your Oura profile; tell me if that's off)")
    assert noted["text"].count("using 70 kg") == 1
    assert "using" not in fueling.plan(110, "x", 69.9, None, SAT, MON)["text"]          # not asked to say it
    assert "using" not in fueling.plan(140, "x", None, None, SAT, MON, weight_from="x")["text"]    # per kg
    short = fueling.plan(40, "x", 69.9, ["before"], SAT, MON, weight_from="from your Oura profile")
    assert "using" not in short["text"]                                  # nothing in it was made from the weight


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
    assert caffeine["amounts"] == {"caffeine_mg": [105, 210], "caffeine_minutes_before": 60, "caffeine_for_you_mg": 110}
    assert caffeine["text"] == ("Caffeine, if you use it: about 110 mg for you today, about 60 min before the run (or "
                                "late in a long one). Start low (1.5–3 mg/kg → 105–210 mg); more doesn't help. Try it "
                                "in training first.")
    assert "about 100 mg for you today" in fueling.plan(40, "x", None, ["caffeine"], None, MON)["text"]
    fluids = fueling.plan(62, "10 km at your usual 6:12 per km", 70, ["fluids"], None, MON, distance_km=10)
    assert fluids["text"].splitlines()[0] == "🏃 Run of 10 km · about 62 min (10 km at your usual 6:12 per km)"
    assert "water is fine" in fluids["text"] and "sodium_mg_per_liter" not in fluids["amounts"]


def test_run_minutes_their_words_then_pace_then_longest_run():
    runs = [{"minutes": 62, "distance_km": 10.0}, {"minutes": 95, "distance_km": 14.0}, {"minutes": 30, "distance_km": None}]
    assert fueling.run_minutes(80, 21.1, runs) == (80, "as you said")
    assert fueling.run_minutes(None, 10, runs) == (65, "10 km at your usual 6:30 per km")    # median pace
    assert fueling.run_minutes(None, None, runs) == (95, "your longest run in the last 4 weeks; tell me if it's longer")
    no_gps = [{"minutes": 37, "distance_km": 2.2}]                         # 17 min/km: not a usable pace
    assert fueling.run_minutes(None, 10, no_gps) == (None, "10 km, but I don't know your usual pace yet")   # no guess
    assert fueling.run_minutes(None, 10, no_gps, asked_pace=6.5) == (65, "10 km at your usual 6:30 per km")
    assert fueling.run_minutes(None, None, [])[0] == fueling.TYPICAL_LONG_RUN_MIN
    assert fueling.run_minutes(None, 1, [{"minutes": 6.999, "distance_km": 1.0}])[1].endswith("7:00 per km")


def test_recent_carbs_count_only_logged_days():
    assert fueling.recent_carbs({"a": 200, "b": None, "c": 160}) == {"average_g": 180, "days": 2}
    assert fueling.recent_carbs({"a": 200, "b": None}) is None


# --- H-6: the caffeine line is theirs (weight, caffeine so far, hours to their usual bedtime) -------------------

BED = datetime(2026, 11, 9, 23, 30)


def caffeine_text(kg=70, **day) -> str:
    return fueling.plan(40, "x", kg, ["caffeine"], None, MON, caffeine_day={"today": True, "so_far_mg": 0,
                                                                             "hours_to_bed": 14, "bedtime": BED,
                                                                             **day})["text"]


def test_the_dose_for_them_never_rounds_below_the_range():
    assert caffeine.low_end([105, 210]) == 110 and caffeine.low_end([100, 200]) == 100 and caffeine.low_end([91, 182]) == 100
    assert "about 110 mg for you today" in caffeine_text(70)          # 1.5 mg/kg x 70 kg = 105 mg -> 110, not 100
    assert "about 90 mg for you today" in caffeine_text(60)           # 90 exactly


def test_caffeine_so_far_and_their_bedtime_change_the_line():
    assert "about 110 mg for you today (with the 80 mg you've had today)" in caffeine_text(so_far_mg=80)
    assert caffeine_text(so_far_mg=350) == "Caffeine: skip more today: you've had about 350 mg already."
    assert caffeine_text(hours_to_bed=4) == ("Caffeine: skip it today: your usual bedtime (11:30pm) is about 4 h away, "
                                             "and caffeine this late can cut into your sleep.")
    later = fueling.plan(40, "x", 70, ["caffeine"], SAT, MON, caffeine_day={"today": False, "so_far_mg": 300,
                                                                            "hours_to_bed": None, "bedtime": BED})
    assert "about 110 mg for you on Sat," in later["text"] and later["text"].endswith(
        "Keep it at least 6 h before your usual 11:30pm bedtime.")       # another day: today's caffeine doesn't count


def test_hours_until_their_usual_bedtime():
    assert caffeine.hours_until(BED, datetime(2026, 11, 9, 19, 30)) == 4.0
    assert caffeine.hours_until(datetime(2026, 11, 9, 1, 19), datetime(2026, 11, 9, 9, 0)) == 16.3
    assert caffeine.hours_until(BED, datetime(2026, 11, 10, 0, 30)) == 0.0      # past bedtime tonight
    assert caffeine.hours_until(None, datetime(2026, 11, 9, 9, 0)) is None


# --- H-3: should I have it now? (code decides where a rule applies) --------------------------------------------

RULES = fueling.rules()["caffeine_sleep"]
CAPS = [{"within_min": 60, "min_calories": 400, "max_intensity": 2}, {"within_min": 30, "min_calories": 150,
                                                                       "max_intensity": 3}]


def should(item_mg=200, kcal=10, so_far=0, hour=9, run=None, bedtime=BED):
    return caffeine.call(item_mg, kcal, so_far, datetime(2026, 11, 9, hour, 0), bedtime, run, RULES, 60, CAPS)


def test_a_caffeinated_drink_is_yes_in_the_morning_no_near_bedtime_wait_before_a_run():
    assert should() == {"call": "yes", "reason": "it's 9:00am, well before your usual 11:30pm bedtime, and you've had "
                                                 "no caffeine yet today"}
    assert should(hour=20)["call"] == "no" and "usual bedtime (11:30pm) is about 4 h away" in should(hour=20)["reason"]
    assert should(so_far=300) == {"call": "no", "reason": "you've had about 300 mg of caffeine today, and this would "
                                                          "make 500 mg"}
    run = {"label": "easy run", "start": datetime(2026, 11, 9, 11, 30)}
    assert should(run=run) == {"call": "wait", "reason": "have it around 10:30am, about 60 min before your easy run at "
                                                         "11:30am"}
    assert should(run={"label": "easy run", "start": datetime(2026, 11, 9, 10, 0)})["call"] == "yes"   # in an hour: fine
    assert should(bedtime=None)["reason"] == "it's 9:00am, and you've had no caffeine yet today"


def test_food_without_caffeine_waits_only_before_a_run():
    run = {"label": "HIIT", "start": datetime(2026, 11, 9, 9, 45)}
    assert should(item_mg=0, kcal=600, run=run) == {"call": "wait", "reason": "your HIIT starts at 9:45am, so have it after"}
    assert should(item_mg=0, kcal=100, run=run) is None                # a small snack: no rule of ours
    assert should(item_mg=0, kcal=600) is None


# --- H-7: real foods in the fueling lines ----------------------------------------------------------------------

FOOD_RULES = fueling.rules()["foods"]


def entry(text, hour, kind="meal", carbs=60, kcal=500, fiber=4, protein=12):
    return {"raw_text": text, "timestamp": f"2026-11-0{hour % 9 + 1}T{hour:02d}:00:00-06:00", "entry_type": kind,
            "est_carbs_g": carbs, "est_calories": kcal, "est_fiber_g": fiber, "est_protein_g": protein}


def test_their_most_logged_foods_are_picked_else_the_diet_fallbacks():
    log = [entry("had dal and rice for dinner", 20), entry("dal and rice", 19), entry("pasta", 20),
           entry("banana", 8, "snack", carbs=27, kcal=105, fiber=3), entry("2 eggs", 8, "snack", carbs=1, protein=13),
           entry("Greek yogurt", 16, "snack", carbs=6, kcal=150, protein=17)]
    picked = fueling_foods.pick(log, FOOD_RULES, "vegetarian", eats_eggs=True)
    assert picked == {"dinner": {"food": "dal and rice", "usual": True}, "snack": {"food": "banana", "usual": True},
                      "after": {"food": "Greek yogurt", "usual": True}}
    empty = fueling_foods.pick([], FOOD_RULES, "vegetarian", eats_eggs=True)
    assert [fueling_foods.phrase(f) for f in empty.values()] == ["dal with rice", "a banana or toast",
                                                                 "2 eggs or Greek yogurt"]
    assert fueling_foods.pick([], FOOD_RULES, "vegetarian", eats_eggs=False)["after"]["food"] == "Greek yogurt"


def test_a_short_saturday_run_names_real_foods_and_timing_and_no_carb_loading():
    foods = fueling_foods.pick([entry("dal + rice", 20)], FOOD_RULES, "vegetarian", eats_eggs=True)
    result = fueling.plan(37, "x", 70, None, SAT, MON, foods=foods)
    lines = result["text"].splitlines()
    assert "Fri dinner: your usual dal + rice (no extra carbs needed for a run under 90 min)." in lines
    assert "Sat, 60–90 min before: a banana or toast, if you want something." in lines
    assert any(line.startswith("After: 20–25 g protein (2 eggs or Greek yogurt works), with carbs") for line in lines)
    assert result["carb_load"] is False
    long = fueling.plan(100, "x", 70, ["day_before"], SAT, MON, foods=foods)
    assert long["text"].endswith("; at dinner, your usual dal + rice.") and long["carb_load"] is True
