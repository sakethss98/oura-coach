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
- **M3 Telegram:** check-in any time with interrupt (plan for now), free-form messages (food, notes, workouts done, questions), context notes with expiry
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
- Oura parsing is built only from fields observed in fixtures/. `personal_info` is fetched on every run (age, weight in kg, height in m; units judged from the values).
- Calendar recurrence is expanded with `recurring-ical-events`.
- Free slots: gaps of at least 30 min within 06:00–21:00 America/Chicago. All-day events are listed for information and do not block slots. Events marked "free" (TRANSP:TRANSPARENT) are ignored.
- fixtures/ is gitignored (personal health data). fixtures/calendar.ics holds the raw feed content (saved by the smoke test), never the URL.
- Model: `OPENAI_MODEL` env var, default `gpt-5-mini`, set only in `config.py`. No temperature is set (gpt-5 models reject non-default values).
- LLM proposes, code enforces: `policy.py` applies the hard rules after the LLM plan and records every override. Code computes all numbers, times, dates and rules; the LLM only proposes, explains and words things.
- **Plan for now (M3):** every plan is for a workout starting now. Plan window (`calendar_client.plan_window`) = from now (or the end of the current event, chained through back-to-back events) until the next event or 21:00. A gap under 30 min jumps to the next free gap; nothing left = rest. The plan may start later inside the window; otherwise it moves to the window start; duration is clamped to the window. Rest = slot "none", 0 min. `preferred_time` in goals.yaml is no longer used for planning.
- **Sessions table:** one row per check-in (status awaiting_checkin → planned, or abandoned) and per unplanned workout (status unplanned). Per-plan data (check-in, plan, LLM plan, overrides, completed, feeling, actual times) lives here; `daily_log` keeps day-level data only (metrics_json, food_targets_json).
- **Migration:** `db.init_db()` detects an older schema, copies the file to `<name>.backup-YYYYMMDD-HHMMSS.db` first (prints the path; aborts if the backup fails), then moves daily_log plans into sessions, drops the old daily_log columns, and adds `food_log.logged_at`. Never migrate without the backup.
- **Activity (`activity.py`):** today's activity = Oura workouts merged with sessions marked done or logged as unplanned; an Oura workout that overlaps a session in time is the same activity. A plan never marked done does not count. A done session with no known length is given the 60 min before it was reported, for overlap only.
- **Hard day** = HIIT, long_run, intensity >= 4, an Oura `running` of 60+ min, or a chat-reported run of 60+ min (`activity.hard_oura` in goals.yaml). Used by "no back-to-back hard days" (yesterday's merged activity) and the weekly cap (`max_hard_days_per_week` over today + the previous 6 days of merged activity). A downgraded hard day becomes easy_run, intensity <= 3.
- **Walk rule:** a walk of 20+ min (Oura `walking` or a chat-logged walk) is a light workout, never hard. It counts toward "already worked out today" only when today's band is not PUSH (`activity.walk_counts` in goals.yaml). Shorter walks never count. Every non-walk workout (Oura or done session) counts.
- **Rule a, already worked out today:** anything but walk / mobility / rest becomes walk; intensity <= 2.
- **Rule c, recent food (`recent_food_caps` in goals.yaml):** an entry with at least `min_calories` eaten within `within_min` caps intensity at `max_intensity`; the strictest match wins. Cap <= 2 = walk / mobility / rest only; cap 3 = HIIT becomes easy_run. Calories, not entry_type, so coffee and supplements never cap.
- Policy order: readiness floor → band → already worked out → back-to-back → weekly cap → long run on weekends → recent food → plan window.
- `mobility` is a workout type (never hard), allowed wherever walk is.
- Readiness band (`baseline.readiness_band`, deterministic) cuts LLM variance. "Worse than baseline" = readiness/HRV/sleep below, or resting HR above (lower RHR is good).
  - recover: readiness < readiness_floor, or 2+ metrics worse. Policy: walk, mobility or rest only (intensity <= 2).
  - push: readiness and HRV normal or above, nothing worse. Policy: anything the other rules allow. The prompt steers push to HIIT on weekdays / long_run on weekends unless other rules or the check-in say otherwise.
  - maintain: everything else, including missing readiness or HRV. Policy: no HIIT, intensity <= 3.
  - The band and its reason go to the LLM and are printed in the report.
- Readiness floor comes from `goals.yaml` (`schedule.readiness_floor`, 70): below it, anything other than walk/mobility/rest becomes walk, intensity <= 2.
- Long run only on Sat/Sun; on weekdays it becomes easy_run.
- Baseline = average of the 7 days before today, skipping missing values. Status thresholds: readiness and sleep score ±5 points, HRV ±10%, resting HR ±3 bpm (lower resting HR is good).
- **Food targets (`nutrition.py`):** Mifflin-St Jeor BMR from Oura personal_info (goals.yaml `nutrition.weight_kg` overrides the weight if set) × `activity_factor` − `deficit_kcal` (never below BMR); protein = kg × `protein_g_per_kg`; fat = `fat_pct` of calories; carbs = the rest; fiber per 1000 kcal. Same targets every day (no active-calorie adjustment). Remaining = targets − food so far, floored at 0 (no fat: food logs have no fat estimate). First plan of the day shows full targets + LLM meal ideas (vegetarian, preferably Indian); later plans show remaining + a pre/post note.
- Food: yesterday's and today's entries are summed in code. If today has no entries, the plan says so and food does not change the intensity.
- **Food time:** `food_log.timestamp` = when it was eaten: the message time, or a stated clock time (`timeparse.parse_clock_time`: the most recent past occurrence within 12 h; "1" at 14:10 = 13:00, at 09:00 = 01:00). `logged_at` = when it was saved (used by /undo).
- **Dates for context notes:** the router copies phrases ("Thu", "Sat"); `timeparse.resolve_dates` resolves them (weekday = next occurrence on/after today, the end on/after the start; "next Fri" = a week after that; "this weekend" = Sat–Sun; "Oct 12", "10/12"; missing = today). Unreadable dates are not saved; the bot asks again.
- **Router:** one structured LLM call (`prompts/router.md`) returns a list of actions (food, context_note, workout_done, start_checkin, question, unclear), dispatched in order by `chat.py`. While a check-in waits, a reply that parses as energy/soreness (`chat.parse_checkin_reply`: "4 2", "4/5, 2/5", "energy 4 soreness 2", "skip"; numbers must lead or be labeled) resumes it without the router; anything else goes to the router with a reminder.
- `workout_done`: `refers_to_plan` + an open plan today = mark that session done (times: the plan's duration before the message); otherwise an unplanned session.
- **Check-in threads:** thread_id `checkin-YYYY-MM-DD-HHMM` (`-2`, `-3`... if taken). A new check-in abandons any pending one; a pending one from an earlier day is abandoned. `now` is passed in (input or resume value), never read inside nodes. /plan runs the graph with today's latest answered check-in (labeled with its time) or "check-in skipped", so it never waits.
- **SQLite across threads:** every `db.py` call opens and closes its own connection (timeout 10 s, WAL), so `asyncio.to_thread` workers never share one. The checkpointer uses one `check_same_thread=False` connection; SqliteSaver locks around it.
- **Telegram:** handlers filter on `TELEGRAM_CHAT_ID`; slow calls run in `asyncio.to_thread`; errors reply with the exception type only; logs go through `config.redact()`; httpx/telegram loggers at WARNING (Telegram URLs contain the bot token).
- `--fixtures` reads fixtures/ (Oura incl. personal_info + calendar.ics, or an empty calendar if it is missing) and writes only to `demo.db` / `demo_checkpoints.db`, never to `oura_coach.db`. The OpenAI call is still live. "Today" in fixtures mode = newest day in daily_readiness.json, with the current clock time (or `run.py --at HH:MM`). `log_food.py --demo` logs into demo.db on that day. `langgraph dev` (`studio.py`) always uses fixtures + demo.db.
- `run.py --simulate-hard-yesterday` (only with `--fixtures`): saves a done HIIT session for the fixtures' yesterday in demo.db, hides earlier activity from the LLM (so it can propose a hard day), and deletes the session after the run. Purpose: show the back-to-back override firing.
- Every LLM call (plan, food, router, answer) is saved to the `runs` table (system prompt, input, parsed output). View with `scripts/show_runs.py`.

## Status
- **M1 Data layer: done (2026-10-07).** Verify with `python scripts/smoke_test.py`.
  - `oura_client.py`: endpoints daily_readiness, daily_sleep, sleep, daily_stress, daily_activity, workout, heartrate, personal_info; pagination via `next_token`; refresh on 401 tested live.
  - `daily_metrics(raw, day)` gives a flat per-day dict, built from fields seen in fixtures/.
  - `calendar_client.py`: tested on the real feed and on a synthetic .ics (RRULE, EXDATE, moved instance, all-day, TRANSPARENT, UTC, overnight event).
  - `db.py`: tokens, daily_log (upsert keeps earlier fields), context_notes (`active_context_notes(day)`, end date inclusive).
- **M2 Agent brain: done (2026-10-07).** Graph fetch -> assess -> plan -> policy -> log, terminal `run.py`, food logging, readiness bands. (Per-day plan replaced by plan-for-now in M3.)
- **M3 Telegram: done (2026-10-07).** Verify with:
  - `pytest -q` (109 tests, LLM stubbed: time/date parsing, check-in replies, plan window incl. mid-event, activity merge + walk rule, every policy rule, targets/remaining, migration + backup, router dispatch, graph interrupt/resume/restart on fixtures, run.py)
  - `RUN_LIVE_LLM=1 pytest -q tests/test_router_live.py` (8 live router tests)
  - `python run.py --fixtures --at 07:00 --energy 4 --soreness 2`
  - `python run.py --fixtures --at 07:00 --simulate-hard-yesterday` (back-to-back override)
  - `python run.py --energy 3 --soreness 3` (live)
  - `python scripts/draw_graph.py` (docs/graph.mmd + docs/graph.png), `langgraph dev` (Studio)
  - `python bot.py` (live) / `python bot.py --fixtures`, then the Telegram script in README
  - Graph: fetch_data -> compute_baseline -> checkin (interrupt) -> gather_today -> plan -> policy_check -> log. Bot: `bot.py` (glue) -> `chat.Coach` -> `router.route` / graph.
- M4–M5: not started.

## Oura data notes (observed in fixtures, 2026-10-07)
- Daily endpoints return one record per `day`. Querying start=D-7, end=D+1 returned D-7..D.
- `workout` can return records dated before `start_date`, so always filter by `day`.
- `sleep` has several periods per day (`type`: `long_sleep`, `sleep`). Main night = `long_sleep`. HRV = `average_hrv`, resting HR = `lowest_heart_rate`.
- Durations (`total_sleep_duration`, `stress_high`, `recovery_high`) are in seconds.
- `heartrate` samples: `timestamp` in UTC, `bpm`, `source` in {awake, rest, workout, live}. Workout samples are much denser, so `hr_avg` leaves them out.
- `daily_readiness.contributors.resting_heart_rate` is a 0–100 score, not bpm.
