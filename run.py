"""Run one check-in in the terminal (the same graph the Telegram bot uses).

Usage: python run.py [--fixtures [--simulate-hard-yesterday]] [--at HH:MM] [--energy N] [--soreness N]
Food is logged separately with: python log_food.py "what you had"

The check-in comes from --energy/--soreness (in chat, the agent asks it first).
"""
import argparse
from datetime import datetime, time, timedelta

from connectors.manual import ManualSource
from core import db, users
from modules.nutrition.messages import format_food_today, format_remaining, format_targets
from modules.training.messages import format_activity
from core.config import DEMO_DB_PATH, OPENAI_MODEL, TZ
from modules.training.planner import build_graph, run_plan
from today import fixtures_now

LABELS = {
    "readiness_score": "Readiness",
    "hrv_ms": "HRV (ms)",
    "resting_hr": "Resting HR",
    "sleep_score": "Sleep score",
}


def fmt(value) -> str:
    return "-" if value is None else str(value)


def simulate_hard_yesterday(user, now: datetime) -> int:
    """Demo only: save a manual HIIT workout for the day before the fixtures' "today" in demo.db.

    main() deletes it after the run, so later demo runs are not affected.
    """
    yesterday = now.date() - timedelta(days=1)
    start = datetime.combine(yesterday, time(6, 30), TZ)
    workout = ManualSource().add_workout(user, "HIIT", start, start + timedelta(minutes=40), 40,
                                         "simulated by --simulate-hard-yesterday")
    print(f"[demo] Saved a simulated HIIT workout for {yesterday} in demo.db.")
    print("[demo] The LLM will not be shown earlier activity, so policy_check has to enforce the rule.")
    return workout["id"]


def print_report(state: dict, demo: bool) -> None:
    today, comparison = state["oura"]["today"], state["baseline"]["comparison"]
    final, overrides, c = state["final_plan"], state["overrides"], state["context"]
    checkin = state["checkin"]

    title = f"Plan for {state['day']} at {c['now']}" + ("  [fixtures / demo.db]" if demo else "")
    print(f"\n{title}\n{'=' * len(title)}")

    print(f"\n{'Metric':<13}{'Today':>7}{'7d avg':>8}{'Delta':>7}  Status")
    for key, label in LABELS.items():
        m = comparison[key]
        print(f"{label:<13}{fmt(m['today']):>7}{fmt(m['avg_7d']):>8}{fmt(m['delta']):>7}  {m['status']}")
    band, effective = state["baseline"]["band"], state["band"]
    print(f"Band: {band['band'].upper()} ({band['reason']})")
    if effective["changed_by_checkin"]:
        print(f"After check-in: {effective['band'].upper()} ({effective['reason']})")
    print(f"Sleep {fmt(today['sleep_hours'])} h | stress {fmt(today['stress_summary'])} | steps {fmt(today['steps'])}")

    schedule = state["schedule"]
    print(f"\nCalendar:  {schedule['source']}")
    print("Free:      " + (", ".join(f"{s}-{e}" for s, e in schedule["free"]) or "none"))
    if schedule["all_day"]:
        print("All-day:   " + ", ".join(schedule["all_day"]))
    print("Window:    " + (f"{c['window'][0]}-{c['window'][1]}" if c["window"] else "none left today"))

    print("\nCheck-in: " + ", ".join(
        f"{k} {checkin[k]}/5" if checkin.get(k) is not None else f"{k} not provided" for k in ("energy", "soreness")
    ))
    print(f"Done today: {format_activity(c['activity_today'])}")
    print(f"Yesterday: {c['yesterday_hard'] or 'not a hard day'} | hard days in previous 6: {c['hard_days_prev6']}")
    if c["workout_records_fetched"] == 0:
        print("(Oura returned no workout records for the last 7 days.)")
    print(format_food_today(c["food_totals"]["today"]))
    print(format_targets(c["targets"]))
    print(format_remaining(c["remaining"]))
    if c["saved_memory"]:
        print("Remembered: " + "; ".join(c["saved_memory"]))

    split = f" ({final['split_day']})" if final.get("split_day") else ""
    print(f"\nWorkout:   {final['workout_type']}{split}  (intensity {final['intensity']}/5)"
          f"  [{state['plan_source']} plan]")
    print(f"When:      {final['time_slot']}  ({final['duration_min']} min)")
    for n, e in enumerate(final["exercises"], 1):
        print(f"           {n}. {e['name']} · {e['sets_reps']}")
    if final.get("exercises_note"):
        print(f"           {final['exercises_note']}")
    print(f"Why:       {final['why']}")
    print(f"Reasoning: {final['reasoning']}")
    print(f"Before:    {final['food_before'] or '-'}")
    print(f"After:     {final['food_after']}  [{final['food_source']}]")
    if final["follow_up"]:
        print(f"Question:  {final['follow_up']}")
    if final["rules_applied"]:
        print("Rules:     " + "\n           ".join(final["rules_applied"]))

    if overrides:
        llm = state["llm_plan"]
        print(f"\nPolicy override (LLM proposed {llm['workout_type']}, intensity {llm['intensity']}, "
              f"{llm['time_slot']}):")
        for o in overrides:
            print(f"  - {o['text']}")
    else:
        print("\nPolicy check: passed, no changes.")
    print(f"\n(model: {OPENAI_MODEL})")


def main(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(description="Plan a workout for now.")
    parser.add_argument("--fixtures", action="store_true",
                        help="use fixtures/ instead of live Oura and Calendar; writes to demo.db")
    parser.add_argument("--at", type=time.fromisoformat, metavar="HH:MM",
                        help="plan as if it were this time today (default: now)")
    parser.add_argument("--energy", type=int, choices=range(1, 6), help="1 (drained) to 5 (great)")
    parser.add_argument("--soreness", type=int, choices=range(0, 6), help="0 (none) to 5 (very sore)")
    parser.add_argument("--simulate-hard-yesterday", action="store_true",
                        help="demo only (needs --fixtures): log a done HIIT session for yesterday in demo.db")
    args = parser.parse_args(argv)
    if args.simulate_hard_yesterday and not args.fixtures:
        parser.error("--simulate-hard-yesterday is only allowed with --fixtures")

    if args.fixtures:
        db.set_db_path(DEMO_DB_PATH)
        now = fixtures_now(args.at)
    else:
        now = datetime.now(TZ).replace(second=0, microsecond=0)
        if args.at:
            now = datetime.combine(now.date(), args.at, TZ)
    db.init_db()
    user = users.default()
    simulated = simulate_hard_yesterday(user, now) if args.simulate_hard_yesterday else None
    answer = {"energy": args.energy, "soreness": args.soreness, "note": None, "label": "from the command line"}
    try:
        state = run_plan(build_graph(), user, now, args.fixtures, answer,
                         hide_history_from_llm=args.simulate_hard_yesterday)
    finally:
        if simulated:
            db.delete_manual_workout(simulated)
    print_report(state, demo=args.fixtures)
    return state


if __name__ == "__main__":
    main()
