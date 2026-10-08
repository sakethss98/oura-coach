"""Run the morning coach once in the terminal.

Usage: python run.py [--fixtures [--simulate-hard-yesterday]] [--energy N] [--soreness N]
Food is logged separately with: python log_food.py "what you had"
"""
import argparse
import sqlite3
from datetime import date, datetime, timedelta

from langgraph.checkpoint.sqlite import SqliteSaver

import db
from config import CHECKPOINT_DB_PATH, DEMO_CHECKPOINT_DB_PATH, DEMO_DB_PATH, OPENAI_MODEL, TZ
from graph import build_graph
from oura_client import latest_day, load_fixtures

LABELS = {
    "readiness_score": "Readiness",
    "hrv_ms": "HRV (ms)",
    "resting_hr": "Resting HR",
    "sleep_score": "Sleep score",
}


def fmt(value) -> str:
    return "-" if value is None else str(value)


def simulate_hard_yesterday() -> date:
    """Demo only: write a hard plan for the day before the fixtures' "today" into demo.db.

    main() deletes the row after the run, so later demo runs are not affected.
    """
    yesterday = latest_day(load_fixtures()) - timedelta(days=1)
    db.upsert_daily_log(yesterday, plan={
        "workout_type": "HIIT", "intensity": 5, "time_slot": "06:30-07:10", "duration_min": 40,
        "reasoning": "Simulated by --simulate-hard-yesterday.", "nutrition_note": "", "rules_applied": [],
    })
    print(f"[demo] Saved a simulated HIIT (intensity 5) plan for {yesterday} in demo.db.")
    print("[demo] The LLM will not be shown earlier plans, so policy_check has to enforce the rule.")
    return yesterday


def print_report(state: dict, demo: bool) -> None:
    today, comparison = state["today"], state["baseline"]["comparison"]
    final, overrides = state["final_plan"], state["overrides"]
    food, checkin = state["baseline"]["food"], state["checkin"]

    title = f"Plan for {state['day']}" + ("  [fixtures / demo.db]" if demo else "")
    print(f"\n{title}\n{'=' * len(title)}")

    print(f"\n{'Metric':<13}{'Today':>7}{'7d avg':>8}{'Delta':>7}  Status")
    for key, label in LABELS.items():
        c = comparison[key]
        print(f"{label:<13}{fmt(c['today']):>7}{fmt(c['avg_7d']):>8}{fmt(c['delta']):>7}  {c['status']}")
    band = state["baseline"]["band"]
    print(f"Band: {band['band'].upper()} ({band['reason']})")
    print(f"Sleep {fmt(today['sleep_hours'])} h | stress {fmt(today['stress_summary'])} | steps {fmt(today['steps'])}")

    schedule = state["schedule"]
    print(f"\nCalendar:  {schedule['source']}")
    print("Free:      " + (", ".join(f"{s}-{e}" for s, e in schedule["free"]) or "none"))
    if schedule["all_day"]:
        print("All-day:   " + ", ".join(schedule["all_day"]))

    print("\nCheck-in: " + ", ".join(
        f"{k} {v}/5" if v is not None else f"{k} not provided" for k, v in checkin.items()
    ))
    for label in ("yesterday", "today"):
        f = food[label]
        if f["entries"]:
            print(f"Food {label}: {f['entries']} entries, ~{f['calories']:.0f} kcal, protein {f['protein_g']:.0f} g, "
                  f"fiber {f['fiber_g']:.0f} g, caffeine {f['caffeine_mg']:.0f} mg, alcohol {f['alcohol_drinks']:g}")
        else:
            print(f"Food {label}: nothing logged")
    if state["context_notes"]:
        print("Context: " + "; ".join(state["context_notes"]))
    yesterday = state["yesterday_plan"]
    if yesterday is None:
        print("Yesterday: no plan logged (treated as not a hard day)")
    else:
        print(f"Yesterday: {yesterday['workout_type']}, intensity {yesterday['intensity']}/5")

    print(f"\nWorkout:   {final['workout_type']}  (intensity {final['intensity']}/5)")
    print(f"When:      {final['time_slot']}  ({final['duration_min']} min)")
    print(f"Why:       {final['reasoning']}")
    print(f"Nutrition: {final['nutrition_note']}")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan today's workout.")
    parser.add_argument("--fixtures", action="store_true",
                        help="use fixtures/ instead of live Oura and Calendar; writes to demo.db")
    parser.add_argument("--energy", type=int, choices=range(1, 6), help="1 (drained) to 5 (great)")
    parser.add_argument("--soreness", type=int, choices=range(1, 6), help="1 (none) to 5 (very sore)")
    parser.add_argument("--simulate-hard-yesterday", action="store_true",
                        help="demo only (needs --fixtures): log a hard plan for yesterday in demo.db")
    args = parser.parse_args()
    if args.simulate_hard_yesterday and not args.fixtures:
        parser.error("--simulate-hard-yesterday is only allowed with --fixtures")

    if args.fixtures:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    simulated_day = simulate_hard_yesterday() if args.simulate_hard_yesterday else None
    checkpoint_path = DEMO_CHECKPOINT_DB_PATH if args.fixtures else CHECKPOINT_DB_PATH

    conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
    try:
        app = build_graph(SqliteSaver(conn))
        thread_id = f"plan-{datetime.now(TZ).strftime('%Y-%m-%d-%H%M%S')}"
        state = app.invoke(
            {"use_fixtures": args.fixtures, "checkin": {"energy": args.energy, "soreness": args.soreness},
             "hide_history_from_llm": args.simulate_hard_yesterday},
            config={"configurable": {"thread_id": thread_id}},
        )
    finally:
        conn.close()
        if simulated_day:
            db.delete_daily_log(simulated_day)
    print_report(state, demo=args.fixtures)


if __name__ == "__main__":
    main()
