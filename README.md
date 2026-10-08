# oura-coach
Personal Agent to improve over personal lifestyle

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` in the repo root with these keys:

```
OPENAI_API_KEY=
OURA_CLIENT_ID=
OURA_CLIENT_SECRET=
OURA_ACCESS_TOKEN=
OURA_REFRESH_TOKEN=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
GCAL_ICAL_URL=        # Google Calendar > Settings > "Secret address in iCal format"
```

The Oura tokens in `.env` are only used once, to seed the SQLite database
(`oura_coach.db`). Oura refresh tokens are single-use, so after that the
database holds the current pair and updates it on every refresh. To start over
with new tokens, put them in `.env` and delete `oura_coach.db`.

## Smoke test (Milestone 1)

```bash
python scripts/smoke_test.py
```

This pulls the last 7 days from Oura, saves the raw responses to `fixtures/`
(gitignored), prints a per-day summary, and lists today's free slots
(06:00–21:00 America/Chicago).

## Agent brain (Milestone 2)

```bash
python log_food.py "chicken rice bowl and a diet coke"   # log anything you eat or drink
python run.py --energy 4 --soreness 2                     # today's plan (live Oura + Calendar)
python run.py --fixtures                                  # offline demo from fixtures/, writes demo.db
python run.py --fixtures --simulate-hard-yesterday        # demo: code overrides a back-to-back hard day
python scripts/show_runs.py -n 2                          # LLM inputs/outputs for the last runs
pytest -q
```

The model is read from `OPENAI_MODEL` (default `gpt-5-mini`). Food estimates are
rough LLM guesses. For a fixtures demo with food, log it with `log_food.py --demo`.

## Files

| File | Purpose |
| --- | --- |
| `config.py` | Settings: time zone, DB path, free-slot window |
| `db.py` | SQLite tables: `tokens`, `daily_log`, `context_notes`, `food_log`, `runs` |
| `oura_client.py` | Oura API v2 client (refresh on 401) and `daily_metrics()` parser |
| `calendar_client.py` | iCal feed → busy blocks, all-day events, free slots |
| `scripts/smoke_test.py` | End-to-end check of the data layer (also saves `fixtures/calendar.ics`) |
| `baseline.py` | Today vs 7-day baseline, recommended slot, food sums (pure functions) |
| `policy.py` | Hard training rules enforced in code after the LLM plan |
| `schemas.py` | Pydantic models for structured LLM output (`Plan`, `FoodEstimate`) |
| `llm.py` | ChatOpenAI wrapper; records every call in the `runs` table |
| `graph.py` | LangGraph: fetch_data → compute_baseline → plan → policy_check → log |
| `food.py` / `log_food.py` | Food logging with rough macro estimates |
| `prompts/` | Prompt text (`plan.md`, `food.md`) |
| `run.py` | Terminal entry point |
| `scripts/show_runs.py` | Print LLM traces for demos |
| `tests/` | Unit tests (no API calls) |
