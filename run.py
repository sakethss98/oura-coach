"""Run one check-in in the terminal (the same graph the Telegram bot uses).

Usage: python run.py [--fixtures [--simulate-hard-yesterday]] [--at HH:MM] [--energy N] [--soreness N]
Food is logged separately with: python log_food.py "what you had"

The graph pauses for the check-in; run.py answers it straight away with --energy/--soreness.
"""
import argparse
import sqlite3
from datetime import datetime, time, timedelta

from langgraph.checkpoint.sqlite import SqliteSaver

import db
from chat import format_activity, format_food_today, format_remaining, format_targets
from config import CHECKPOINT_DB_PATH, DEMO_CHECKPOINT_DB_PATH, DEMO_DB_PATH, OPENAI_MODEL, TZ
from graph import build_graph, resume_run, start_run
from today import fixtures_now

LABELS = {
    "readiness_score": "Readiness",
    "hrv_ms": "HRV (ms)",
    "resting_hr": "Resting HR",
    "sleep_score": "Sleep score",
}


def fmt(value) -> str:
    return "-" if value is None else str(value)


def simulate_hard_yesterday(now: datetime) -> int:
    """Demo only: save a done HIIT session for the day before the fixtures' "today" in demo.db.

    main() deletes it after the run, so later demo runs are not affected.
    """
    yesterday = now.date() - timedelta(days=1)
    start = datetime.combine(yesterday, time(6, 30), TZ)
    session = db.add_unplanned(yesterday, "HIIT", 40, start, start + timedelta(minutes=40),
                               "simulated by --simulate-hard-yesterday", start + timedelta(minutes=40))
    print(f"[demo] Saved a simulated done HIIT session for {yesterday} in demo.db.")
    print("[demo] The LLM will not be shown earlier activity, so policy_check has to enforce the rule.")
    return session["id"]


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
    band = state["baseline"]["band"]
    print(f"Band: {band['band'].upper()} ({band['reason']})")
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
    if c["context_notes"]:
        print("Context: " + "; ".join(c["context_notes"]))

    print(f"\nWorkout:   {final['workout_type']}  (intensity {final['intensity']}/5)")
    print(f"When:      {final['time_slot']}  ({final['duration_min']} min)")
    print(f"Why:       {final['reasoning']}")
    print(f"Nutrition: {final['nutrition_note']}")
    if final["meal_ideas"]:
        print("Meals:     " + "\n           ".join(final["meal_ideas"]))
    if final["rules_applied"]:
        print("Rules:     " + "\n           ".join(final["rules_applied"]))

    if overrides:
        llm = state["llm_plan"]
        print(f"\nPolicy override (LLM proposed {llm['workout_type']}, intensity {llm['intensity']}, "
              f"{llm['time_slot']}):")
        for reason in overrides:
            print(f"  - {reason}")
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
    parser.add_argument("--soreness", type=int, choices=range(1, 6), help="1 (none) to 5 (very sore)")
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
    simulated = simulate_hard_yesterday(now) if args.simulate_hard_yesterday else None
    checkpoint_path = DEMO_CHECKPOINT_DB_PATH if args.fixtures else CHECKPOINT_DB_PATH

    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        app = build_graph(SqliteSaver(conn))
        waiting = start_run(app, now, args.fixtures, hide_history_from_llm=args.simulate_hard_yesterday)
        answer = {"energy": args.energy, "soreness": args.soreness, "note": None, "label": "from run.py flags"}
        state = resume_run(app, waiting["thread_id"], answer, now)
    finally:
        conn.close()
        if simulated:
            db.delete_session(simulated)
    print_report(state, demo=args.fixtures)
    return state


if __name__ == "__main__":
    main()
