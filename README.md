# oura-coach

A personal running, strength and nutrition coach you text on Telegram. It reads your Oura ring
(sleep, readiness, HRV, workouts, steps) and your Google Calendar, plans a workout for right now,
logs what you eat, remembers what you tell it, and gives each day a score you can compare to your own
recent average.

**The rule the whole design follows: the LLM proposes and words things; code decides.** Every number,
time, date and training rule is computed in plain Python and tested. The model understands messages,
picks tools, proposes plans and meal ideas, and writes the sentences, inside limits that code checks
before a reply is sent.

---

## Architecture

```mermaid
flowchart LR
    TG["channels/telegram.py<br/>(Telegram)"] --> COACH["core/coach.py<br/>message in, reply out"]
    COACH --> AGENT["core/agent.py<br/>LangGraph agent"]
    AGENT --> CORE_TOOLS["core tools<br/>memory, today, score, refresh"]
    AGENT --> TRAIN["modules/training<br/>check-in, planner, policy, score"]
    AGENT --> NUTR["modules/nutrition<br/>food log, meal ideas, score"]
    CORE_TOOLS --> HEALTH["connectors/health.py"]
    TRAIN --> HEALTH
    NUTR --> HEALTH
    HEALTH --> OURA["connectors/oura.py<br/>(Oura API, 10-min cache)"]
    HEALTH --> MANUAL["connectors/manual.py<br/>(workouts added in chat)"]
    TRAIN --> CAL["connectors/calendar_feed.py<br/>(iCal free time)"]
    AGENT -.-> DB[("SQLite<br/>core/db.py")]
```

| Layer | What lives there | Knows about |
| --- | --- | --- |
| `channels/` | Telegram glue: who is writing, text in, text out | `core.coach` only |
| `core/` | The agent, users, chat sessions, saved memory, reply rules, the Oura-defined day, the daily score, storage | modules through the registry |
| `modules/` | One coaching area each (`training`, `nutrition`): tools, deterministic rules, a score | `connectors/health.py`, never a device API |
| `connectors/` | Where data comes from: Oura, workouts added in chat, the calendar | nothing above them |
| `config/` | Score weights and targets (`score.yaml`), fallback meals (`meals.yaml`) | |
| `goals.yaml` | Your goals and training rules (readiness floor, hard days per week, rest days, food targets) | |

Each layer only imports the one below it. A module can be turned on or off per person
(`user_settings.enabled_modules`); the agent is only shown the tools of enabled modules.

### How one message flows

Say you send **"done, what should I eat now?"** at 1:10pm.

1. **`channels/telegram.py`** checks the chat belongs to a known user and hands the text to `Coach`.
2. **`core/coach.py`** logs the message to your chat session (a new session starts after 15 quiet
   minutes; each session is one LangGraph thread) and runs the agent.
3. **`ingest`** (code): expires a "still want HIIT?" question left unanswered for 2 hours, finalizes the
   score of any day whose 21:00 cutoff has passed, and drops a check-in question left in an earlier session.
4. **`agent`** (LLM): sees a system message with the time, *which day it is by your sleep*, your saved
   facts and a short summary from each module, plus this session's conversation. It calls tools:
   `mark_done` and `suggest_meal`.
5. **`tools`** (code): `mark_done` fetches Oura again and marks the plan done **with Oura's exact times**,
   or says Oura has no matching workout (it never makes times up). `suggest_meal` sizes one meal from
   what's left of today's targets, asks the LLM for 1–2 dishes, drops anything suggested in the last
   5 days, and formats portions in grams and katoris.
6. **`respond`** (code): text built by tools is sent exactly as built. The model's own words go through
   the reply rules: at most 3 lines and about 60 words, no internal terms, and no "I logged" / "I synced"
   unless that tool really ran this turn. A broken draft is rewritten once, then trimmed by code.

```
Marked done: HIIT 12:12–1:03pm (from Oura). How did it feel?

For lunch, about 776 kcal and 44 g protein:
1. Dal palak with rice · about 776 kcal, 41 g protein
   cooked dal with spinach 380 g (1 3/4 cups) · cooked rice 150 g (3/4 cup) · ghee 13 g (1 tbsp)
2. Egg bhurji with dal and rice · about 772 kcal, 46 g protein
   ...
```

### The two graphs

| The agent (`core/agent.py`) | The workout planner (`modules/training/planner.py`) |
| --- | --- |
| ![Agent graph](docs/graph.png) | ![Planner graph](docs/planner.png) |

The **planner** runs once a check-in is answered. Only `plan` calls the LLM; everything else is code:
- `rest_plan`: it's late (after 21:00 or before 06:00) or there is no free time before your next event.
- `consult`: you asked for a workout the rules would change. It asks once ("Readiness is 64 today, lower than
  usual, so I'd keep it light. I'd suggest a 30-min walk instead. Still want HIIT?") and stops.
- `suggested_plan`: you said no, so it plans the suggestion as offered.
- `policy_check`: enforces the rules from `goals.yaml` after the LLM proposes: readiness floor,
  how recovered you are, already trained today, no back-to-back hard days, the weekly cap on hard days,
  long runs only on weekends, recent meals and free time.

Redraw both with `python scripts/draw_graph.py`; explore them with `langgraph dev` (always on fixtures).

---

## What you can say

Plain messages; the only commands are `/start` and `/help`.

| You send | What happens |
| --- | --- |
| `hi` | One line with today's readiness. No LLM call. |
| `want to work out now` / `want HIIT` | Asks energy (1–5) and soreness (0–5), then plans a workout for right now, until your next calendar event. |
| `4 2 legs heavy` / `5 0 give me run + strength` / `skip` | Your check-in answer. A request becomes the plan's parts, in your order. |
| `yes` / `no` | Your answer to "Still want HIIT?" (good for 2 hours). |
| `done` | Marks the plan done with Oura's times, or offers to add it with a time. |
| `did 40 min HIIT at 7` | Adds a workout Oura missed. |
| `why?` | The reasoning, the numbers and every rule code applied. |
| `had poha and chai at 8` / `a 30 cal soda` | Logs it with a rough estimate; your numbers win over the estimate. |
| `correction, it's 9g carbs` / `it was cheese pizza, edit that` | Fixes that entry in place, never a second row. |
| `remove the pizza` / `delete the last two` | Removes entries; asks which one if it's unclear. |
| `what did I eat on Oct 10?` / `last Monday` | That day's entries and totals. |
| `dinner ideas, I have dal, rice and spinach` → `the second one` → `make it higher protein` | Two dishes sized for one meal, then the recipe, then a higher-protein version of the same dish. |
| `how's my day?` | Your score so far vs your own 7-day average, and one kind suggestion ("a 20-min walk would round today out"). |
| `remember I'm traveling Thu–Sat, keep it light` / `what do you know about me?` / `forget that` | Saved facts, with dates resolved in code. |
| `update from Oura` | Fetches again and says honestly whether anything new came in. |

---

## Interfaces

### Module (`modules/base.py`)

```python
class Module(Protocol):
    name: str
    def setup_questions(self) -> list[str]: ...          # onboarding (M5)
    def tools(self) -> list: ...                          # LangChain tools the agent may call
    def context(self, user, day) -> str: ...              # a few lines for the agent's system message
    def nudges(self) -> list[NudgeSpec]: ...              # declared only; M4 sends them
    def score(self, user, day, now=None) -> ScoreComponent: ...   # deterministic share of the daily score
    def help_lines(self) -> list[str]: ...                # plain-language lines for /help
```

`ScoreComponent(name, fraction 0–1 or None, note, details, next)`: `None` means "no data, leave me out"
(the other components are re-weighted); `next` is a plain fact about what would still help today.

### HealthSource (`connectors/base.py`)

```python
class HealthSource(Protocol):
    def readiness(self, user, day) -> dict | None: ...    # {"score", "temperature_deviation"}
    def sleep(self, user, day) -> dict | None: ...        # the main sleep that ended on `day`
    def workouts(self, user, day) -> list[dict]: ...      # start, end, minutes, activity
    def activity(self, user, day) -> dict | None: ...     # steps, active calories
```

`OuraSource` and `ManualSource` implement it; `connectors/health.py` combines them and is the only thing
modules call. Oura fields are parsed only from responses saved in `fixtures/` (never invented).

### How to add a module

Say, a `sleep` module that coaches wind-down routines:

1. Create `modules/sleep/module.py` with a class that has the six methods above (copy
   `modules/nutrition/module.py` as a start). Keep its rules as plain functions in the same package,
   with their own tests.
2. Put its tools in `modules/sleep/tools.py`. Tools read who/when from the run config with
   `core.tools.turn(config)`, return short JSON for the model, and put any text you want sent exactly as
   built in the artifact `{"verbatim": text}`.
3. Register it in `modules/registry.py`: `"sleep": "modules.sleep.module:SleepModule"`.
4. Turn it on for a person: add `"sleep"` to their `user_settings.enabled_modules`.
5. To count it in the daily score, return a `ScoreComponent` from `score()` and give it a weight in
   `config/score.yaml` under the person's template.
6. Add its scenarios to `docs/scenarios.md` and a scripted and a live test to `tests/test_replay.py`
   (the coverage test fails until you do).

---

## The daily score

`core/score.py` adds four components with weights from `config/score.yaml`. At moderate rigor:

| Component | Weight | Measures |
| --- | --- | --- |
| Training | 35 | The plan happened (minutes per part vs planned). Rest days are inferred: a day without a workout is full credit within your weekly rest days, or when readiness was low or a saved fact (travel, sick) covers it. |
| Nutrition | 30 | Protein vs target, calories within ±10% (never rewards eating far too little), meals logged. |
| Recovery | 20 | What you control: time in bed vs target, bedtime vs your usual, and taking it easy on a low-readiness day. Low readiness or a restless night alone never lowers it. |
| Movement | 15 | Steps vs 8,000. |

A component with no data (no wearable, no steps) is left out and the rest re-weighted, never scored 0.
The score is final at 21:00 and never changes after that. "How's my day" compares it to your own average:
"74 so far, up from your 68 average". There are no streaks and no comparisons to anyone else.

---

## Setup

Python 3.11+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

`.env` in the repo root (never committed, never logged):

```
OPENAI_API_KEY=
OURA_CLIENT_ID=
OURA_CLIENT_SECRET=
OURA_ACCESS_TOKEN=
OURA_REFRESH_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GCAL_ICAL_URL=        # Google Calendar > Settings > "Secret address in iCal format"
OPENAI_MODEL=         # optional, default gpt-5-mini
```

Oura refresh tokens are single-use: the `.env` pair only seeds the database, which then keeps the
current pair. For Telegram, create a bot with [@BotFather](https://t.me/BotFather), message it, and put
your chat id (from `https://api.telegram.org/bot<token>/getUpdates`) in `TELEGRAM_CHAT_ID`.

## Run

```bash
python scripts/smoke_test.py         # pull 7 days of Oura into fixtures/ (gitignored), print a summary
python bot.py                        # the Telegram coach (live Oura + calendar, oura_coach.db)
python bot.py --fixtures             # same, on fixtures/ and demo.db
python run.py --fixtures --at 07:00 --energy 4 --soreness 2      # one planner run in the terminal
python run.py --fixtures --at 07:00 --simulate-hard-yesterday --request "45 min HIIT" --choose yes
python log_food.py "2 eggs, toast and chai"                     # log food from the terminal
python scripts/show_runs.py -n 3     # the last LLM calls: prompt, input, parsed output
langgraph dev                        # LangGraph Studio on fixtures
```

## Tests

```bash
pytest -q                                              # everything, LLM stubbed (about 15 s)
RUN_LIVE_LLM=1 pytest -q -s tests/test_replay.py -k live           # every scenario against the real model
RUN_LIVE_LLM=1 LIVE_RUNS=3 pytest -q tests/test_replay.py -k live  # 3 runs each, with a pass-rate table
```

`docs/scenarios.md` is the eval set: real conversations, each with what must and must not happen. Every
scenario has a **scripted** test (preset model turns, so it checks the code around the model: tools,
database rows, reply rules) and a **live** test (the real model, so it checks the understanding).
★ scenarios must pass every live run; an LLM judge is used only for "did it answer what was asked".
Scenarios for the next milestone are marked `xfail("M4")`.

## Files

| Path | Purpose |
| --- | --- |
| `core/agent.py` | The LangGraph agent: ingest → greet / agent ⇄ tools → respond |
| `core/coach.py` | Message in, reply out (no Telegram code, so it's tested offline) |
| `core/tools.py` | Core tools: remember, forget, list_memory, get_today, get_score, refresh_health |
| `core/reply_rules.py` | Length, plain words, no claims without the tool |
| `core/score.py` | The daily score: components, re-weighting, final after the cutoff, own average |
| `core/day.py` | Which day an event belongs to: a day starts when your main sleep starts |
| `core/memory.py`, `core/chat_sessions.py`, `core/users.py` | Saved facts, 15-minute sessions, people and their settings |
| `core/db.py` | SQLite (every user-owned row has `user_id`), backups before any migration |
| `core/timeparse.py` | "at 1", "8am", "Thu–Sat", "last Monday" → times and dates |
| `core/labels.py` | Plain labels, 12-hour times, workout phrases |
| `modules/training/` | Check-in parsing, the planner graph, policy rules, plan parts, activity merge, score |
| `modules/nutrition/` | Food logging and edits, targets, meal ideas, score |
| `connectors/` | Oura client and parsers, manual workouts, calendar, the combined health view |
| `channels/telegram.py` | Telegram glue (`bot.py` is the entry point) |
| `today.py` | One view of a day (Oura, calendar, food, activity, targets) shared by the planner and tools |
| `prompts/` | Prompt text: `agent`, `plan`, `checkin`, `food`, `meal`, `recipe` |
| `config/` | `score.yaml`, `meals.yaml` |
| `docs/` | `scenarios.md` (eval set), `M3.2_plan.md`, graph drawings |
| `tests/` | Unit tests, scripted + live replay scenarios (`test_replay.py`) |
