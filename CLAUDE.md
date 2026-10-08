# oura-coach — Project Brief (source of truth)

## Goal
Every morning the agent pulls my Oura data (sleep, readiness, HRV, resting HR, intraday heart rate, stress, activity, workouts) and my Google Calendar. It compares today against my 7-day baseline and my goals in goals.yaml (half-marathon plan, body-composition goal, training rules), checks in with me on Telegram (energy, soreness, food), then gives a plan: workout type (HIIT / easy run / long run / walk / rest), time slot (I prefer mornings), and a short nutrition note. It remembers temporary context with an expiry (e.g., "traveling Thu-Sat, keep it light") and logs every day so it can learn patterns over time.

**Food logging (core requirement):** I log everything I consume, several times a day: meals, snacks, drinks, coffee, alcohol, supplements. Each entry gets a rough LLM estimate (protein, fiber, carbs, calories, caffeine, alcohol drinks, entry type, confidence) saved to `food_log`. Every entry is saved, repeats included, and confirmed back with the estimate. Today: `python log_food.py "text"`. M3: free-form Telegram messages that describe food go through the same `food.log_food()`.

## Stack
Python 3.11+, LangGraph (langgraph, langgraph-checkpoint-sqlite), langchain-openai (ChatOpenAI), python-telegram-bot, httpx, icalendar (+ recurring-ical-events), apscheduler, python-dotenv, pyyaml, SQLite.

## Already done
.env (OPENAI_API_KEY, OURA_CLIENT_ID, OURA_CLIENT_SECRET, OURA_ACCESS_TOKEN, OURA_REFRESH_TOKEN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GCAL_ICAL_URL), .gitignore, and goals.yaml. Don't modify .env or goals.yaml without asking.

## Milestones
- **M1 Data layer:** Oura client, calendar free slots, SQLite, smoke test
- **M2 Agent brain:** LangGraph fetch -> assess -> plan -> log, runs in the terminal
- **M3 Telegram:** morning check-in with interrupt, free-form messages, context notes with expiry
- **M4 Always-on:** one long-running process (Telegram listener + 7am APScheduler job), error alerts to Telegram, deployable to a Raspberry Pi or a small cloud host
- **M5 Learning:** weekly reflection that saves insights, food/sleep correlations

## Rules
- Code must be simple, readable, and actually working (interview demo). Clarity beats cleverness.
- Work on ONE milestone at a time. Never start the next one without the user's go-ahead.
- Never invent API fields or endpoints. For Oura, save real responses to fixtures/ and build parsing from those. If unsure, say so and ask.
- Deterministic code for math and time (baselines, free slots); the LLM only for reasoning and wording.
- Never print, log, or commit secrets (this includes the iCal URL).
- After each milestone: give the exact command to verify it, update the Status section below, and list any assumptions made.

## Decisions
- Oura `/sleep` endpoint is fetched in addition to `daily_sleep`: HRV and lowest heart rate live on sleep periods, not on the daily score.
- Oura refresh tokens are single-use: tokens live in SQLite (`tokens` table, seeded from .env on first run) and each new pair is saved in one transaction right after a refresh.
- Oura parsing is built only from fields observed in fixtures/.
- Calendar recurrence is expanded with `recurring-ical-events`.
- Free slots: gaps of at least 30 min within 06:00–21:00 America/Chicago. All-day events are listed for information and do not block slots. Events marked "free" (TRANSP:TRANSPARENT) are ignored.
- fixtures/ is gitignored (personal health data). fixtures/calendar.ics holds the raw feed content (saved by the smoke test), never the URL.
- Model: `OPENAI_MODEL` env var, default `gpt-5-mini`, set only in `config.py`. No temperature is set (gpt-5 models reject non-default values).
- LLM proposes, code enforces: `policy.py` applies the hard rules after the LLM plan and records every override.
- Hard day = HIIT, long_run, or intensity >= 4. Used by both "no back-to-back hard days" (vs yesterday's daily_log plan) and the weekly cap (`max_hard_days_per_week` over today + the previous 6 days of daily_log). A downgraded hard day becomes easy_run, intensity <= 3. No daily_log row for yesterday = not hard.
- Readiness band (`baseline.readiness_band`, deterministic) cuts LLM variance. "Worse than baseline" = readiness/HRV/sleep below, or resting HR above (lower RHR is good).
  - recover: readiness < readiness_floor, or 2+ metrics worse. Policy: walk or rest only (intensity <= 2).
  - push: readiness and HRV normal or above, nothing worse. Policy: anything the other rules allow. The prompt steers push to HIIT on weekdays / long_run on weekends unless other rules or the check-in say otherwise.
  - maintain: everything else, including missing readiness or HRV. Policy: no HIIT, intensity <= 3.
  - The band and its reason go to the LLM and are printed in the report.
- Readiness floor comes from `goals.yaml` (`schedule.readiness_floor`, 70): below it, anything other than walk/rest becomes walk, intensity <= 2.
- Long run only on Sat/Sun; on weekdays it becomes easy_run.
- Plan time must sit inside a free slot, otherwise it moves to the recommended slot; duration is clamped to the slot. Rest = slot "none", 0 min.
- Baseline = average of the 7 days before today, skipping missing values. Status thresholds: readiness and sleep score ±5 points, HRV ±10%, resting HR ±3 bpm (lower resting HR is good).
- Recommended slot: earliest free slot starting before 12:00 when goals prefer mornings, else the earliest slot of the day.
- Food: yesterday's and today's entries are summed in code. If a day has no entries, the plan says so and food is left out of the decision (no intensity cap).
- `--fixtures` reads fixtures/ (Oura + calendar.ics, or an empty calendar if it is missing) and writes only to `demo.db` / `demo_checkpoints.db`, never to `oura_coach.db`. The OpenAI call is still live. "Today" in fixtures mode = newest day in daily_readiness.json. `log_food.py --demo` logs into demo.db.
- `run.py --simulate-hard-yesterday` (only with `--fixtures`): writes a HIIT/intensity-5 plan for the fixtures' yesterday into demo.db, hides earlier plans from the LLM (so it can propose a hard day), and deletes the row after the run. Purpose: show the back-to-back override firing. Without the hiding, the LLM follows the rule itself and nothing is overridden.
- Checkpointer: SqliteSaver on `checkpoints.db`; thread_id `plan-{YYYY-MM-DD-HHMMSS}`, so every run starts fresh.
- Every LLM call (plan and food) is saved to the `runs` table (system prompt, input, parsed output). View with `scripts/show_runs.py`.

## Status
- **M1 Data layer: done (2026-10-07).** Verify with `python scripts/smoke_test.py`.
  - `oura_client.py`: endpoints daily_readiness, daily_sleep, sleep, daily_stress, daily_activity, workout, heartrate, personal_info; pagination via `next_token`; refresh on 401 tested live.
  - `daily_metrics(raw, day)` gives a flat per-day dict, built from fields seen in fixtures/.
  - `calendar_client.py`: tested on the real feed and on a synthetic .ics (RRULE, EXDATE, moved instance, all-day, TRANSPARENT, UTC, overnight event).
  - `db.py`: tokens, daily_log (upsert keeps earlier fields), context_notes (`active_context_notes(day)`, end date inclusive).
- **M2 Agent brain: done (2026-10-07).** Verify with:
  - `pytest -q` (30 tests: baseline, readiness bands, policy incl. LLM override, food sums, no-food day plans normally)
  - `python log_food.py "3 eggs, toast, black coffee"` (add `--demo` to log into demo.db)
  - `python run.py --fixtures --energy 4 --soreness 2` (offline Oura/calendar, demo.db)
  - `python run.py --fixtures --simulate-hard-yesterday` (shows the policy override)
  - `python run.py --energy 3 --soreness 3` (live)
  - `python scripts/show_runs.py [--demo] -n 2` (LLM traces)
  - Graph (`graph.py`): fetch_data -> compute_baseline (`baseline.py`) -> plan (`prompts/plan.md`, `schemas.Plan`) -> policy_check (`policy.py`) -> log (daily_log incl. `policy_json`).
  - Readiness band added 2026-10-07: 3 fixtures runs (band PUSH) all gave HIIT; only intensity (4-5) and duration (30-35 min) varied.
  - Check-in for now: `--energy` / `--soreness` flags (M3 replaces with Telegram). Food is not a run.py flag.
- M3–M5: not started.

## Oura data notes (observed in fixtures, 2026-10-07)
- Daily endpoints return one record per `day`. Querying start=D-7, end=D+1 returned D-7..D.
- `workout` can return records dated before `start_date`, so always filter by `day`.
- `sleep` has several periods per day (`type`: `long_sleep`, `sleep`). Main night = `long_sleep`. HRV = `average_hrv`, resting HR = `lowest_heart_rate`.
- Durations (`total_sleep_duration`, `stress_high`, `recovery_high`) are in seconds.
- `heartrate` samples: `timestamp` in UTC, `bpm`, `source` in {awake, rest, workout, live}. Workout samples are much denser, so `hr_avg` leaves them out.
- `daily_readiness.contributors.resting_heart_rate` is a 0–100 score, not bpm.
