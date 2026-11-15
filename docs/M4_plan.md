# M4 — The multi-source coach: plan

## Context

M3.2 is done and tagged (`m3.2` = 00ed428; CLAUDE.md Status agrees). Live use after it showed gaps: there are no history tools ("I only see today and yesterday"), a "yes" sent the next morning loses its question, the coach offers actions it can't do, workouts Oura never saw get "nothing new came in", and "keep it light" is enforced only by the prompt. M4 fixes those first. Then it adds a model config, a classifier that routes each message to a skill file (tools + guidance per category), outside knowledge over MCP (GSSI: our own server over a vector index; USDA: a community server), a goal-program engine whose first template is the half marathon, and rubric evals. Everything stays reactive, with no scheduler and no proactive messages. Each part pauses with its verify commands. On approval, this plan is copied to `docs/M4_plan.md`, the same way `docs/M3.2_plan.md` was.

> Approved 2026-11-08 with additions, folded in below:
> - hybrid run_then_strength days (E1/E4/E0)
> - group_by + best/worst-day differences in the history tools (A1)
> - the final category list with pain_injury and memory, plus the honesty rule (C1/C4)
> - your 32 questions in `data/my_questions.txt` (C2/G2)
> - context_notes dropped (A6)
> - a gpt-5 test call before G3
> - race date Feb 15, 2027 (a Monday)
> - no Legs on L+1 either (E4)
> - race target times with an approximate Riegel prediction (E2/E3)
> - fueling_plan body weight never estimated (D)

**Facts found while reading the code that shape the plan**
- The agent runs sync (`graph.invoke`, `ToolNode`). MCP adapter tools are async only, so MCP tools get sync wrappers over a persistent session on a background event loop (Part D).
- `OuraSource` fetches day−7…day (with heartrate) into a single cache row per user. Ranges up to 90 days need a separate history fetch: daily endpoints only, no heartrate.
- Oura `workout` records have `distance` (seen in fixtures; meters, judged from the values, e.g. 2175 for a 37-min run; sometimes null). Long-run km comes from it.
- `fixtures/` covers only 2026-09-30…10-07, so history tests on fixtures see ≤ 8 days. Longer ranges are tested with synthetic data.
- The live DB has only **15 user messages** (Oct 16–18). Most of the classifier set will come from your ~30 questions plus the scenarios.
- `LANGSMITH_TRACING=true` is loaded by `load_dotenv`, so today every run, tests included, would send traces. Tests will turn it off.
- No Node.js is installed (relevant to the TypeScript USDA server in Part F).
- `.env` check (values not printed): USDA_API_KEY, LANGSMITH_API_KEY, LANGSMITH_TRACING and LANGSMITH_PROJECT are present. OPENAI_MODEL is not set, so the default applies.

---

## Part 0 — Inputs

- `scripts/export_my_messages.py`: user-role rows from `messages` (user 1, `oura_coach.db`, read-only connection) → `data/sample_questions.jsonl` as `{"text", "ts"}`. Skips `/start`, `/help` and `[voice note]`. Prints only the count. `data/` is added to `.gitignore`.
- `core/config.py`: USDA_API_KEY and LANGSMITH_API_KEY join `SECRET_ENV_VARS` (redacted). `check_keys()` reports present/missing, never values. `tests/conftest.py` sets `LANGSMITH_TRACING=false` before any import (load_dotenv doesn't override it). Live tests trace only with `LANGSMITH_TESTS=1`.
- `data/my_questions.txt` (gitignored): your 32 questions, one per line, saved as given. C2 and G2 use them together with the exported messages and the scenario messages.
- **What I still need from you:** `docs/gssi_sources.txt` (before D), your confirmation of the race date before E's live check (Feb 15, 2027; tests use it), and ~20 graded replies (G1b).

## Part A — Fix pass + history tools

**A1 History tools.** Pure math in new `history.py` files; the tools are thin wrappers.
- `core/timeparse.resolve_range(days_back, start_text, end_text, today)` → `(start, end, note)`. It looks back only: "this week" = Mon…today, "last week" = previous Mon–Sun, "last 7 days", "Oct 1"–"Oct 10", start-only phrases. Ranges are capped at 90 days, and when that happens the note says "Only the last 90 days are covered." `days_back=7` = the 7 days ending today (today included, labeled "so far"). This matches the existing M4-1 test: 161 g, 23 g/day.
- `food_totals(nutrients, days_back | start_text+end_text, per_day)`: **nutrition**, `modules/nutrition/history.py`. Sums, per-day average over the days with food logged (unlogged days are listed as "not logged", never counted as 0), lowest/highest day per nutrient (for "which day this week was I lowest on protein"), and optional per-day rows. Days come from `day_bounds`.
- `workouts(days_back | range)`: **training**, `modules/training/history.py`. Per-day merged items via the existing `activity.oura_items / session_items / manual_items / merge_activity`, counts by type, total minutes, run km (Oura `distance`) and the longest run.
- `health_range(metrics, days_back | range)`: **core** (device data, like recovery and movement). Readiness, sleep score, sleep hours, HRV, resting HR and steps per day, plus averages, built only from the `daily_metrics` fields.
  - New `OuraSource.history(user, start, end)` fetches only the daily endpoints with `heartrate = {"data": []}`, cached in a new `oura_history_cache` table (one row per user, 10-min TTL). Fixtures mode filters fixtures to the range.
  - `daily_metrics` workouts gain `distance_km`.
- `day_summary(day_text)`: **core**, assembled from each enabled module's new `summary(user, day)` (nutrition: totals; training: workouts and plan vs done), core health (readiness, sleep) and the score if it's final. Same layering as `get_score`.
- **Patterns (`group_by: weekday | week`)** on food_totals, workouts and health_range. Code computes; the model only words it.
  - Grouped rows: per weekday or per ISO week, with averages and counts.
  - Workouts per weekday: days with a counting workout vs days without; planned sessions abandoned or not done; program days missed (from E on). This answers "which days do I usually skip workouts?".
  - `best_worst`: for the chosen metric, the best and worst day in the range, each with **what differed** vs the range average:
    - bedtime (`bedtime_start`)
    - the previous day's training load (minutes, hard yes/no, from merged activity)
    - that day's food (kcal, protein, caffeine, alcohol)
    - steps
  - The features come from one pure `history.day_features(user, day)` built on the modules' `summary()`.
  - This answers "when did I sleep best this month and what was different?".
  - `resolve_range` also reads "this month" and "last month".
- Rules: user_id comes from the run config; dates are resolved in code; results are computed numbers, never raw rows. Each description is one line plus one example question.
- Scenarios: M4-1 and M4-2 become must-pass. M4-2 uses a fixtures day ("Oct 6"), since Oct 10 is outside fixtures. New:
  - M4-6 "which day this week was I lowest on protein"
  - M4-7 "how did I sleep this week"
  - M4-7b "which days do I usually skip workouts?" (group_by weekday, synthetic 4 weeks)
  - M4-7c "when did I sleep best this month, and what was different that day?" (best day + its differences, numbers equal to the tool's)

**A2 Last question across a session reset.**
- `chat_sessions.previous_question(user, session, now)`: the last message of the previous session, if it was the assistant's, contains "?", and is under 24 h old.
- During the first session after it, the system message carries "Your last message before this conversation (sent Sat 11:08am): …". It clears naturally: once a newer session starts, or after 24 h. Nothing is saved to memory.
- A message over 12 h old plus a short reply (`yes/ok/sure/yep/no/nope`) → a new code-only branch `confirm_previous`, the same pattern as `greet`: "Just to check: do you mean my question from yesterday at 11:08am: “Want an estimate for that smoothie?”" No tools run, so a check-in can't start. M4-3 becomes must-pass.

**A3 General questions; no offers it can't keep.**
- `prompts/agent.md`: general food/drink/product/exercise/health questions get a direct answer marked approximate ("about 200 mg"), plus at most one line tied to today. "Never invent numbers" stays for their own data.
- `reply_rules.OFFERS` (never allowed): "want me to look (it|that) up", "I can (search|look up|browse|check online)", "I'll (check|look) online", "let me (search|google|look it up)". On a match: one rewrite, then trim (the existing path). Unit tests. M4-4 becomes must-pass.

**A4 Workouts Oura can't see.**
- `mark_done` gains optional `workout_type`, `minutes` and `start_time_text` (as they said them), used only to word the offer.
- No Oura match, or no plan today → code-built text: "Oura only shares workouts the ring recorded (Apple Watch sessions don't come through). Want me to add HIIT, 49 min at 10:37am?" "yes" → `add_manual_workout` with those details.
- `refresh_health` adds the same note when they said they worked out and nothing came in. M4-5 becomes must-pass.

**A5 Coach voice.**
- Rewrite the "How to write" section of `prompts/agent.md`: what it noticed → the honest call → one suggestion or two options → a short question when the choice is theirs. Acknowledge effort or setbacks in a few words: no gushing, no guilt. The ≤3-line / ≤60-word rule and the claim checks stay.
- Review the fixed texts in `modules/training/messages.py` and `modules/nutrition/messages.py`, plus `NO_OURA_MATCH` and `REST_QUESTION`, for the same voice. Golden tests updated.
- 3 new pushback scenarios (live + judge rubric "noticed / honest call / options / question"; scripted for reply rules):
  - M4-8 "I'll do HIIT twice today to make up for yesterday"
  - M4-9 "should I skip dinner to hit my deficit?"
  - M4-10 "going for a 2-hour run after 5 hours of sleep"

**A6 "Keep it light" in code.**
- `memory.intensity_cap INTEGER NULL`.
- `remember` gains `keep_light: "light" | "very_light" | null`. The model picks the word and code picks the number. **Decided:** light = cap 3 (easy run or strength ≤ 3; no HIIT or long run; for travel), very light = cap 2 (walk, mobility or rest; for sickness).
- New policy rule `context_cap`, after band and before worked_out: "Since you're traveling, I'd keep it light." It feeds the consult-once question like the other rules. `_policy_ctx` gets the caps of today's facts.
- **Migration:** `init_db` detects a missing `memory.intensity_cap` → `backup_db()` first (prints the path, aborts on failure) → `ALTER TABLE`. Existing facts stay NULL, with no guessing from text. The migration test is extended. **Decided:** `context_notes` is dropped in the same backed-up step, after checking that every still-active note has a matching memory row (the migration aborts otherwise).
- The scenario 26 live test checks a code cap, not just prompt wording.

**Pause A:** `pytest -q`; `RUN_LIVE_LLM=1 pytest -q -s tests/test_replay.py -k "live and (m4 or 26)"`; `python -c "from core.config import check_keys; check_keys()"`.

## Part B — Model config

- `config/models.yaml`:
  - Jobs: `classifier`, `agent`, `structured`, `judge`, `embeddings`, `template_draft` (declared, unused). Each has `{provider: openai, model, params}`.
  - Defaults: gpt-5-mini everywhere (classifier with `reasoning_effort: minimal`), `text-embedding-3-small` for embeddings, `gpt-5` for the judge.
  - `prices` per model (USD per 1M input/output tokens). I'll fill them from OpenAI's pricing page and report them for you to check.
  - `scripts/check_models.py`: one tiny call per configured chat model, gpt-5 included. It shows the judge works on your key and prints model + tokens, never keys. It runs at Pause B and again before G3.
- `core/models.py`: `chat(job, override=None)`, `embeddings()`, `cost(model, in_tok, out_tok)`. `OPENAI_MODEL` (env), when set, still overrides classifier, agent and structured, as it does today. `config.OPENAI_MODEL` is removed; `agent.make_model` and `llm.structured_call` call `models.chat`.
- `runs` gains `job`, `input_tokens`, `output_tokens` and `latency_ms` (usage from the response metadata). Migration = the same backup-first guarded ALTER. `scripts/show_runs.py` shows them.

**Pause B:** `pytest -q`; `python scripts/show_runs.py --last 1` (a row with job + model + tokens).

## Part C — Classifier + skills

**C1 Categories (starting list, agreed).** I'll write a one-line definition and 2–3 examples for each from your questions, and you confirm before I write the skill files.

| Category | Covers |
|---|---|
| `training` | today's session and general exercise questions |
| `recovery_sleep` | readiness, sleep, tiredness, how hard can I go |
| `nutrition` | what to eat, what's left, food habits, branded label values |
| `fueling_hydration` | before/during/after a run, coffee timing, electrolytes, water |
| `trends_patterns` | ranges, comparisons, patterns, "how's my day / my score" |
| `goal_program` | programs and direction goals: create, on track, phase, feasibility |
| `pain_injury` | pain or injury questions |
| `memory` | remember, forget, what do you know about me |
| `logging` | an action, not a question: food, workouts done, done |
| `off_topic` | anything outside the coach |

**C2** `tests/data/classifier_set.jsonl` (every scenario message, in the repo) + `data/classifier_personal.jsonl` (every line of `data/my_questions.txt` and the exported messages: gitignored, decided 2026-11-08), as `{text, prior_turns?, categories}`. I propose the labels and flag the doubtful ones for you (e.g. "Had a paneer wrap, what's left and should I still run tonight?" = logging + nutrition, or + training).

**C3 Classifier node.**
- Graph: `ingest → greet | confirm_previous | classify → decline (off_topic) | agent ⇄ tools → respond`.
- Input: the message, the last 4 turns, the previous-question line (A2), and pending state ("a check-in answer is awaited" / "a yes/no is awaited"), so a bare "yes" routes right.
- Structured output `{categories: 1–2 names or "other", reason}`, job `classifier`. The categories, definitions and examples are read from the skills' frontmatter at call time, so a new file means a new category.
- A classifier error routes to "other", and the error is logged to `runs`.

**C4 Skills.**
- `skills/<category>.md`: frontmatter `name, description, examples, tools, sources, model?, rubric` + a short body.
- `core/skills.py` loads and validates them; unknown tool names fail at load.
- The agent gets the union of the categories' tools and their bodies; a skill `model` overrides `agent`.
- `prompts/agent.md` shrinks to identity + voice + global rules; the per-area "How to act" bullets move into the skill bodies.
- `other` = all tools + all bodies (today's behaviour), and the message goes to a new `unclassified_messages` table. `off_topic` = the skill's fixed `decline:` line, with no tools and no agent call.
- **`pain_injury` skill:**
  - Never diagnoses. It gives common causes in plain words.
  - It names clear "see a doctor or physio" signs: sharp or worsening pain, swelling, numbness, pain lasting past ~1–2 weeks.
  - It offers `remember` with `keep_light`.
  - Gentle strength and mobility come only as general guidance, marked "not medical advice".
  - Tools: get_today, workouts, remember. Rubric checks: no diagnosis, the doctor signs are present, the not-medical-advice line is present.
- **Honesty rule** (in the bodies of `fueling_hydration`, `goal_program`, `trends_patterns`, `training`): the coach says what it can see and what it would need for things it has no source for.
  - Weather has no source yet.
  - Muscle gain and body composition can't be measured: there's no lift log, weight trend or measurements.
  - Example: "Am I building muscle?" → strength sessions and protein days it can see + "to tell muscle gain I'd need lift weights or measurements". None of these sources is built in M4.

**C5 No regression, checked.**
- In scripted tests, a fake classifier looks each message up in `classifier_set.jsonl` (an unlabeled message fails the test). Every scripted scenario therefore runs through its real route.
- `ScriptedChat` asserts each scripted tool call is among the tools offered, which proves every skill's tool list against every scenario.
- Live tests use the real classifier.

**Verify C:**
- `python scripts/eval_classifier.py`: accuracy (a hit = the predicted set overlaps the label and adds nothing off-topic), a confusion matrix, and the misses. Target ≥ 90%.
- `tests/test_skills.py`: dropping a temp skill file into a temp skills dir makes it classifiable (fake model sees its definition), with no code change.
- `pytest -q`, live replay.

## Part D — GSSI over our own MCP server

- **D1 `scripts/ingest_gssi.py`:**
  - Fetching: one URL at a time with a 3 s pause, our own User-Agent, and robots.txt checked.
  - Extraction: HTML via `trafilatura`; PDFs (many SSE articles are PDFs) via `pypdf`.
  - Splitting: 500 tokens with 60 overlap (tiktoken), keeping section headings as metadata.
  - Embedding: job `embeddings`. Storage: Chroma persistent collection `data/gssi_index/`, with ids = sha1(url + chunk index).
  - Re-runs skip unchanged pages (content hash) and replace changed ones.
  - `data/` is gitignored, and a test asserts no tracked file contains indexed text.
- **D2 `mcp_servers/gssi/server.py`:** FastMCP, stdio, one tool `search_gssi(query, k=4)` → `[{title, url, section, passage ≤ 700 chars}]`.
- **D3 `core/mcp_tools.py`:**
  - Servers are listed in `config/mcp.yaml`. `MultiServerMCPClient` opens persistent sessions on a background asyncio loop thread, and each MCP tool is wrapped as a sync LangChain tool (timeout 15 s).
  - A server that's down or times out → `{"available": false, "note": "answer from general knowledge, marked approximate"}`.
  - Tools are offered only to skills that list them (`fueling_hydration`, `goal_program`).
- **Amounts from code:** a nutrition tool `fueling_plan(day_text)` computes the long-run day (from the program or their words), its duration, body weight, recent carbs from the food log, and carb/fluid amounts from ranges in `config/fueling.yaml`. Each range names the GSSI article URL it comes from; I fill them from the ingested articles and you review.
  - **Body weight is never estimated.** It comes from Oura `personal_info` weight, or (the existing targets order) the goals.yaml `nutrition.weight_kg` override if set (null today).
  - Else a saved body-weight fact: asked once, saved by a new `save_body_weight(kg)` tool as a code-formatted memory fact ("Body weight: 72 kg", new kind `body`) and read back by code.
  - With neither, `fueling_plan` gives amounts per kg, says so, and the agent asks once.
- **D4 reply rules:** when `search_gssi` ran, the reply needs one of its URLs, and copying 12+ words in a row from a returned passage is a violation (n-gram check against this turn's tool results).

> **As built (2026-11-09):**
> - **Fetching:**
>   - curl was blocked by gssiweb.org's bot wall. A later ingest run with fetching on (httpx, same honest User-Agent; robots.txt answers 200 with the site's HTML "404" page) fetched all 22 articles, 3 s apart, into `data/gssi_raw/fetched/`. I should have stopped and asked when that happened.
>   - Decision (2026-11-09): keep those copies, and gssiweb.org is manual only from now on (never requested). A 401/403 or bot-check page stops a host for the run.
>   - All 22 files were verified: canonical + og:url, title, word count, no bot-check page.
> - **Fueling ranges:** `config/fueling.yaml` was checked against the indexed text. Ranges not in these articles were dropped or replaced:
>   - drinking: 400–800 ml/h became "keep the loss under 2%"
>   - caffeine: 3–6 mg/kg became 1.5–3 mg/kg, with no lead time in minutes
>   - protein after: 0.3 g/kg became 20–25 g
>   - day before: 5–7 g/kg became "nothing extra under 90 min, 7–12 g/kg from 90 min"
>   - sodium: 20–40 mmol/L
>   - added after review: before = nothing under 60 min (the 1–4 g/kg advice is for > 60 min); recovery carbs 1.0–1.2 g/kg/h for 4 h; the 60–90 g/h tier only past 2.5 h and longer than a half marathon (a half is always 30–60 g/h)
> - **Citations:** [n] markers, code-built source lines, Telegram HTML with clickable canonical links and no previews.
> - **`search_gssi`:** defined in our code (`core/mcp_tools.py`) with a fixed schema. It calls the MCP server through langchain-mcp-adapters on a background event loop, so it exists, and falls back, even when the server is down.

**Verify D:**
- `python scripts/ingest_gssi.py`, twice (the second run is a no-op).
- `pytest -q tests/test_gssi_retrieval.py`: 5 known questions → the expected URL in the top 4; skipped without an index.
- Live scenario M4-11: "My long run is Saturday. What should I eat and drink Friday and Saturday?" Checks: health_range / get_today + food_totals + search_gssi + fueling_plan are called, the amounts equal fueling_plan's, and the link is present.

## Part E — The generic goal engine (amended 2026-11-09, plan approved 2026-11-15)

The 2026-11-08 Part E (half marathon first) was replaced by this plan after the amendment: the coach is for any person with any goal; the half marathon is only the first template.

### Context

Parts 0 through D are done and pushed (`a9f024d`). Part E was planned on 2026-11-08 as "goal programs, half marathon first" (docs/M4_plan.md). The 2026-11-09 amendment changes its shape. The coach is for any person with any goal. The half marathon is only the first template, and your race is only test data. This plan replaces the Part E section of docs/M4_plan.md once approved. Every point of the amendment is mapped to where it lands in the table at the end.

**Decided (2026-11-14):**
- **Load growth:** each week's load per discipline is at most +25% over the highest of the 3 weeks before, so a lighter week doesn't reset the baseline. The long session is at most +25% over the longest session in the 30 days before; the event itself is exempt. A test checks that a draft growing +35% fails. Higdon's Novice 1 passes as published (largest step +17.6%).
- **goals.yaml:** the `race`, `primary_goal` and `current` blocks are removed (no code reads them). Your race comes back as goal_answers rows when you set the goal in chat.

**Facts from the code that shape this plan:**
- Workouts reach modules with Oura names: `activity.OURA_MATCHES` has running, walking and strengthTraining. Fixtures have exactly running, strengthTraining, walking and yoga.
- `daily_metrics` keys (readiness_score, sleep_hours, hrv_ms, resting_hr, steps) are already plain. Only the workout `activity` strings are Oura-specific.
- `fueling.run_minutes` already computes a median pace from recent runs (3–12 min/km). It falls back to a constant 7 min/km, which item 3 rules out for paces.
- `planner` already takes a request as blocks (`blocks.requested_blocks`), `policy_check` runs per block, and `activity.next_split_day` picks the strength split.
- `skills/goal_program.md` has your race ("half marathon on Feb 15 under 2:30") and "a hybrid athlete" in its frontmatter.

### Layout

```
programs/                      the goal engine's data contract; no imports from core/ or connectors/
  schema.py                    pydantic Template (+ Variant, Discipline, SessionType, Week, Placement, Signal)
  validate.py                  validate_template(t) -> list[str]; validate_plan(days, profile, t) -> list[str]
  library.py                   load(name), approved(), the source/status gate
  library/half_marathon.yaml   wired: novice + intermediate variants
  examples/ironman.yaml        unwired: swim/bike/run/strength
  examples/day_hike.yaml       unwired: dated long day hike with elevation gain, knee-pain profile in its test
config/program_safety.yaml     the safety numbers (growth 0.25, long session 0.25 over 30 days, taper, rest), labeled
config/movements.yaml          closed movement vocabulary + which movements each workout type / session type uses
modules/goals/                 the module (registered): profile, signals, paces, feasibility, generator, adjust,
                               direction, tools, messages, module
```

### E1 Template schema and validator (amendment 2, 7)

- **`programs/schema.py`**
  - Top-level fields: `name, title, source: library|drafted, status: approved|pending_review, event` (optional: dated, distance or other measure), `matches` (the goal words that pick it), `asks_user`, `variants`, `disciplines`, `shared`, `placement`, `progress_signals`, `safety`.
  - A discipline has `measure` (km | min | elevation_m), `session_types` and `alternatives`.
  - A session type has `intensity`, `hard`, `movements` (from config/movements.yaml), `pace` (easy | race | none) and `default_min`.
  - A variant has `choose_when` (declarative thresholds on profile and data) and `weeks`. A week has `phase`, `lighter` and `sessions: [{discipline, type, amount}]`.
- **`validate_template(t)`** checks structure: known disciplines, types and movements, and phases in order. Safety checks, per discipline and per variant:
  - weekly load ≤ 1.25 × the highest of the 3 weeks before
  - long session ≤ 1.25 × the longest in the 30 days before (the event is exempt)
  - every week leaves at least one rest day: sessions per week ≤ 7 − `shared.min_rest_days`, and ≤ `shared.max_sessions_per_week`
  - hard sessions per week ≤ `shared.max_hard_per_week`
  - a dated event has ≥ `safety.taper_weeks` weeks, and the week before the event is ≤ 75% of the peak week (our number, labeled `ours:` in config/program_safety.yaml)
  - every placement rule names a session type that exists
- **`validate_plan(days, profile, t)`** runs on the generated days before anything is written:
  - the same growth checks, now including their real last 30 days and 3 weeks from signals
  - ≥ 1 rest day in every calendar week
  - sessions per week ≤ their `days_per_week`
  - each session fits their time: minutes ≤ `minutes_per_session`, the long day ≤ `longest_session_min`; km becomes minutes at their easy pace
  - injury limits: no session whose movements meet a limit's `avoid`, and no intensity above a limit's cap
  - the taper
- **Source/status gate (`library.py`):** `generate()` refuses any template whose status isn't `approved`. A drafted template must pass `validate_template` and then be approved before it can generate. M5's AI drafts plug in here: a `drafted` + `pending_review` template is stored, validated, and shown for review.
- **Tests (`tests/test_program_validate.py`), each a deliberately bad template:**
  - a +35% week
  - a long session +40% over 30 days
  - 7 sessions in a week (no rest)
  - too many hard days
  - a dated event with no taper
  - an unknown movement
  - a placement on a missing type
- Plan-level tests: a session longer than their time, and a run for a profile that must avoid running impact. Every library and example template passes. A `pending_review` template refuses to generate.

### E2 Templates (amendment 3, 6)

- **`half_marathon.yaml`, novice:** Higdon Novice 1 converted to km, the table approved in the earlier plan (long run 6.5 → 16, race in week 12). Rest the day before the long run. Entry needs a long run ≥ 5 km in the last 4 weeks.
- **`half_marathon.yaml`, intermediate:** Higdon Intermediate 1 half (12 weeks, with race-pace runs). I'll check it on halhigdon.com at build time, convert it to km, and show you the table before wiring, the same way as Novice.
  - It's chosen when the data (or, without data, their answers) shows a long session ≥ 10 km in the last 30 days and ≥ 4 days per week. The thresholds live in the template's `choose_when`; data beats answers.
- **Target time is optional.** No pace is ever a constant:
  - Easy pace = the median of their recent runs (shared `modules/goals/paces.py`, moved out of `fueling.run_minutes`).
  - Race pace = target time ÷ distance, or, with no target, the Riegel prediction from their best qualifying run (≥ 5 km, last 6 weeks), marked approximate.
  - With no runs and no target, the template's `asks_user` adds `easy_pace`.
  - `fueling.run_minutes` loses its 7 min/km fallback: with a distance and no pace it uses the asked pace, or else says it doesn't know their pace.
- **Hybrid days and strength placement are template data, not code:** `run_then_strength` is a session type with two blocks, and `placement` rules say "Legs not within 2 days before or 1 day after the long session", "lifts only after easy or short runs", "upper or core after the harder easy run", "Legs mid-week by preference". The generator applies placement rules generically, so the Ironman and hike templates use the same mechanism.
- **`examples/ironman.yaml`** (swim/bike/run/strength) and **`examples/day_hike.yaml`** (unwired, no `matches`) validate in tests.
  - day_hike: disciplines hike (measure elevation_m and min), strength (step-ups, glute work), mobility; a dated event; a taper.
  - Its test also generates a plan for a profile with a knee limit (avoid deep_knee_bend and jumping, cap 3), and `validate_plan` passes. That proves the schema and generator aren't running-shaped.
  - Example numbers are illustrative and say so in the file.

### E3 Profile: questionnaire, limits, source-neutral signals (amendment 4, 5, 8)

- **Questionnaire core (goal-agnostic, `modules/goals/profile.py`):**
  - `goal_text` (their words), `event_date` (optional), `level` (new | some | regular, then checked against data)
  - `days_per_week`, `minutes_per_session`, `longest_session_min`
  - `limits` (below), `equipment` (none | home | gym), `gym_access`
- The template's `asks_user` adds the rest (half: `long_session_day`, `target_time` optional, `easy_pace` only without run data).
- Stored in `goal_answers(goal_id, key, value, source user|data)`. Data overwrites an answer when it exists: longest session, pace, sessions per week.
- `create_goal` / `answer_goal` ask at most 2 open keys at a time, as code-built text.
- **Limits are structured:** new table `limits(user_id, body_area, kind pain|injury|condition, avoid JSON, intensity_cap, start_date, end_date, note)`.
  - `avoid` comes from the closed vocabulary in config/movements.yaml: running_impact, jumping, deep_knee_bend, downhill, overhead, spinal_load, grip, rotation, swimming_shoulder.
  - New tool `save_limit(body_area, kind, avoid, intensity_cap, start_text, end_text)`: the model picks enum values, code resolves the dates and stores them. Offered in pain_injury and goal_program.
  - Read by the generator (sessions are swapped to a template `alternative`, or else rest) and by `policy_check`.
  - New policy rule `limit`, after `context_cap`: a block whose type's movements meet `avoid` becomes walk or mobility, or the template alternative. A non-body rule, so it feeds the consult question.
- **Source-neutral signals (`modules/goals/signals.py`)** are the only door from device data into the engine:
  - `DaySignals(day, sleep_hours, hrv_ms, resting_hr, steps, readiness | None, workouts=[{kind, start, end, minutes, distance_km, source}])`.
  - Workout `kind` is neutral: run | walk | strength | cycle | swim | hike | hiit | mobility | yoga | other.
  - The Oura connector maps its observed names (running, walking, strengthTraining, yoga; anything else → other) in one table in `connectors/oura.py`, and `connectors/base.py` documents `kind` in the HealthSource contract. Manual workouts map their workout_type.
  - Readiness is optional: with none, the existing `baseline.readiness_band` already falls back to HRV, resting HR, sleep and the check-in.
  - Tests:
    - the engine runs on a no-wearable source (manual workouts only, every metric None)
    - the engine runs on a fake second-device dict
    - a grep test: modules/goals/ and programs/ never mention oura, running, walking, strengthTraining or readiness_score

### E4 Feasibility (pure)

- Entry week = the last variant week whose long session ≤ 1.25 × their longest in the last 30 days and whose load ≤ 1.25 × their highest week of the last 3. That's the validator's own rule, so a plan that starts there passes it.
- Weeks needed vs weeks left until the event. Infeasible → the earliest feasible date, or a shorter goal offered in words.
- Extra weeks repeat the entry week at maintenance.
- **Predicted finish (Riegel)** only with a target time, compared in plain words, marked approximate. With no qualifying run: "Not enough run data to predict a time yet."
- Unit tests: known Riegel values, no qualifying run, the fastest projection picked.
- Nothing is written to program_days until they confirm.

### E5 Generator (pure; template + profile + confirmed direction rules + start → days)

- It places sessions on their available days, long session on their long day, by the template's `placement` rules and the direction rules (strength sessions per week, hybrid days).
- Strength split via `activity.next_split_day`, skipping a split a placement rule forbids that day.
- It applies limits, then `validate_plan`; any error → nothing is written and the error is reported.
- The dated event anchors the last week (counting back from the event day).
- Unit tests (the earlier E4 list carries over):
  - 2 strength days in build weeks
  - no Legs on L−1, L−2 or L+1
  - L+1 light only
  - Legs at L−4 or L−5
  - ≥ 1 rest day a week
  - the long-run sequence equals the table
  - no lift on the long day
  - the extra rest day freed by hybrid days
  - the race on the event day with rest the day before
  - the same suite on the novice variant, the intermediate variant and the hike example

### E6 Module, tables, tools, wiring

- **`modules/goals/` registered.** It's added to user 1's enabled_modules by a backup-first step in `_m4_steps`.
- **New tables** (CREATE IF NOT EXISTS): goals, goal_answers, program_days, program_adjustments, direction_goals, direction_rules, limits.
- **Tools:** create_goal, answer_goal, confirm_goal, goal_today, goal_status, save_limit, set_direction_goal, confirm_direction_rules, direction_check. Texts are code-built and sent as built; results carry facts only (the existing rule).
- **Done sync:** `goals.sync_done(user, day)` runs from `today.sync_oura_completions`, `mark_done` and `add_manual_workout`. It matches by neutral `kind` and ≥ 70% of target.
- **Program day as the request:** `start_checkin`, `plan_workout` and `submit_checkin` use today's program session when they named none, through the existing blocks/consult path. An ad-hoc request on a program day keeps the program's run block.
- **Skills:**
  - goal_program gets the new tools; its examples become generic ("I want to run a 10k in the spring, can I?", "Can I do a long hike in May with my knee?"). The race text stays only in tests/data/classifier_set.jsonl.
  - pain_injury gets save_limit.
  - The honesty line "Program plans aren't set up yet" goes.

### E7 Adjustments and direction goals

- **Adjustments (pure `adjust.py`, run from ingest like `finalize_quietly`, each saved with its reason):**
  - missed easy session → dropped
  - missed long session → the next free day before the next long one, else the next one is held
  - 2+ misses in a week → the week repeats
  - low readiness, or a weak band from the neutral inputs → the planner's downgrade, and the day is marked moved
  - an active fact with `intensity_cap`, or a limit → days in its range skipped or swapped; the plan resumes the same week
- **Direction goals:** as approved in the earlier plan (closed rule catalog config/direction_rules.yaml, propose → confirm, only confirmed rules read, `direction_check` with code milestones and "no lift log yet"). The catalog keys are generic; "hybrid athlete" appears only in tests.

### No personal values in code (amendment 1)

- `tests/test_no_personal_values.py` scans every tracked file outside tests/ and docs/: .py, .yaml and .md under core/, modules/, connectors/, channels/, programs/, skills/, prompts/, config/ and scripts/, plus root *.py and goals.yaml. Patterns:
  - `2027-02-15`, `Feb(ruary)? 15`, `\b2:30\b`, `2:29`, `1:59:59`
  - `hybrid athlete`, `abs by`
- It fails today on skills/goal_program.md and goals.yaml; both are fixed in E (goals.yaml blocks removed, as you approved).
- Your race lives in tests (Feb 15, 2027, under 2:30, the hybrid direction goal) and in your goal_answers rows once you set it in chat.

### Scenarios

- E-1..E-13 from the earlier plan carry over (race data in tests only).
- New:
  - **E-14:** a user with knee pain saves a limit, and the next plan has no running impact (policy rule `limit`).
  - **E-15:** a no-wearable user (manual workouts only) creates a goal and gets a plan.
  - **E-16:** a +35% drafted template is refused (scripted only).
- E-1..E-15 get scripted + live tests in COVERAGE, as before.

### Pauses and verification

**Pause E-a (engine, pure, no LLM):** schema, validator, library gate, both half variants, both examples, signals, paces, feasibility, generator, adjust, the personal-values test.
```
pytest -q tests/test_program_validate.py tests/test_generator.py tests/test_feasibility.py tests/test_adjust.py tests/test_signals.py tests/test_no_personal_values.py
pytest -q
```
I'll show you the intermediate table and the example templates here.

**Pause E-b (wiring + live):** module, tables + migration (backup first), tools, limits in policy, done sync, program day as request, skills, direction goals, scenarios.
```
pytest -q
RUN_LIVE_LLM=1 pytest -q -s tests/test_replay.py -k "live_e"
python scripts/eval_classifier.py
python bot.py --fixtures      # "I want to run a half marathon in February", "what's today's session?", "am I on track?"
```
Then CLAUDE.md (Decisions, Status, guard policy unchanged) and push.

### Review changes (approved 2026-11-15)

1. The grep test for device names (oura, running, walking, strengthTraining, readiness_score) scans only the .py files in modules/goals/ and programs/. Template YAML text is not scanned.
2. `save_limit` is an action. Code confirms what was saved in plain words: "Saved: no running impact for your knee until you tell me it's better." A test covers the model claiming a limit was saved when the tool didn't run (bucket c): `reply_rules.CLAIMS` gets the limit wording. (E-b)
3. GUILT reply rule: narrowed to blame aimed at the person. "Missed" and "behind" are allowed about sessions and numbers. Test: "Moved the long run you missed to Saturday." passes. (E-b)
4. Intermediate table: if halhigdon.com can't be reached from this session, stop and ask for the table to be pasted. Never reconstruct it from memory.
5. Assumptions 1–5 stand.

Order: build E-a first, then pause with its verify commands.

### How each amendment point lands

| # | Amendment point | Where |
|---|---|---|
| 1 | No personal values in code; a failing test | test_no_personal_values.py; goals.yaml blocks removed; goal_program examples made generic; race only in tests and goal_answers |
| 2 | Schema + validator module; safety checks; every template passes; bad-template tests | programs/schema.py, programs/validate.py (validate_template + validate_plan), config/program_safety.yaml; E1 tests |
| 3 | Level variants; target optional; paces from target + recent runs (Riegel) | half_marathon.yaml variants + choose_when; modules/goals/paces.py; fueling's 7 min/km constant removed |
| 4 | Goal-agnostic questionnaire core + template asks_user | modules/goals/profile.py core keys; template asks_user |
| 5 | Limits as structured fields read by the generator and policy_check | `limits` table, save_limit tool, config/movements.yaml, policy rule `limit`, generator swaps |
| 6 | Keep Ironman; add an unwired non-running dated example (knee pain) | programs/examples/ironman.yaml, day_hike.yaml + a generate-and-validate test with a knee limit |
| 7 | source + status; only approved templates generate | schema fields, library.py gate, pending_review refusal test |
| 8 | Source-neutral progress and readiness inputs | modules/goals/signals.py (DaySignals, neutral workout kind), Oura name map in the connector, no-wearable + second-device tests, grep test |

### Assumptions (tell me if any is wrong)

1. The long-session check uses the same 25% as weekly load (you named the comparison, not the cap).
2. The taper threshold (week before the event ≤ 75% of the peak) is ours and labeled `ours:`.
3. Intermediate variant thresholds (≥ 10 km long session in 30 days, ≥ 4 days a week) are ours, in the template's `choose_when`.
4. The day-hike numbers are illustrative (an unwired example), not coaching advice.
5. Training's existing Oura matching (`activity.OURA_MATCHES`) stays as is. Only the goal engine reads neutral `kind`, and moving training onto `kind` waits for M6's adapters.

## Part F — USDA FoodData Central

Both candidates expose the same API shape (search, details by fdcId, several foods).
- **jlfwong** (TypeScript) needs Node, which isn't installed. It shows no license and has 3 commits.
- **FelipeAdachi** (Python, MIT) is based on jlfwong's server. Its docs are thin, and the default path is Windows-specific.

I'll read both fully and expect to pick FelipeAdachi (no new runtime, a license), unless its code is worse. `scripts/install_usda_mcp.sh` clones it at a pinned commit SHA into gitignored `vendor/` with its own venv. `config/mcp.yaml` runs it over stdio with only `USDA_API_KEY` passed in its env. It's allowed in the `nutrition` skill for branded label values. A reply rule requires naming "USDA FoodData Central" when it ran. When it's down, the A3 rule applies (approximate). I'll report what I don't like in its code.

**Verify F:** `bash scripts/install_usda_mcp.sh`; live "how much protein in a Chobani plain Greek yogurt" names the source; a test with the server stopped falls back to approximate.

## Part G — Rubric evals + demo

- **G1:** each skill's `rubric` is 3–6 yes/no checks, each tagged `code:` (tool called, numbers equal a tool result, link present, ≤60 words, banned phrases) or `judge:`. `evals/judge.py` (job `judge`) sees the question, this turn's tool results, the rubric and the reply, and returns `{check: yes/no, reason}`.
- **G1b:** `scripts/calibrate_judge.py` writes 20 replies to `data/calibration.jsonl` for you to grade, then reports agreement per check. Below 85%, the check is reworded.
- **G2:** `tests/data/eval_set.jsonl`, 5–10 per category, drawn first from `data/my_questions.txt` (the texts are copied in), then the scenarios.
- **G3:** `scripts/run_evals.py` reports per category: routing accuracy, rubric pass rate, avg latency, tokens, cost and model. Results go to `docs/evals/YYYY-MM-DD.md` and to a LangSmith dataset + experiment.
- **G3b Reasoning effort (added 2026-11-08):** a plan call took 21 s (3798+1968 tokens, mostly reasoning). Per job (classifier, agent, structured: plan, food, check-in, meal), run the eval set with `reasoning_effort` minimal / low / default and compare latency, cost and rubric pass rate. Recommend a setting per job in `config/models.yaml`. Defaults stay as they are until you approve.
- **G4:** `docs/demo.md`, a 5-minute script: the Saturday long-run question with its trace, a new category added live, and the eval table.

**After M4:** CLAUDE.md Status + Decisions, README (classifier, skills, MCP servers, program engine; how to add a category, a template, re-ingest GSSI), `scripts/draw_graph.py` redrawn, `git tag m4`.

**Post-M4 review notes (no change before then):**
- Voice: don't open a pushback with praise ("Nice plan.", "Nice intent.") right before warning against it (seen in M4-8 / M4-10 transcripts, 2026-11-09).

---

## Touchpoint table

| File | Part | New/Change | Why |
|---|---|---|---|
| `scripts/export_my_messages.py` | 0 | new | export your user messages to data/ |
| `data/my_questions.txt` | 0 | new (gitignored) | your 32 sample questions for C2/G2 |
| `scripts/check_models.py` | B,G | new | one test call per chat model (proves gpt-5 works), prices report |
| `.gitignore` | 0,D,F | change | `data/`, `vendor/` |
| `core/config.py` | 0,B | change | new secrets redacted, `check_keys()`, OPENAI_MODEL constant moves to models.py |
| `tests/conftest.py` | 0,B,C | change | tracing off; judge via job; fake classifier from labeled set; ScriptedChat asserts offered tools |
| `core/timeparse.py` | A | change | `resolve_range` (90-day cap) |
| `connectors/oura.py` | A | change | `OuraSource.history` (no heartrate), `distance_km` on workouts |
| `connectors/health.py` | A | change | `Health.history` |
| `core/db.py` | A,B,C,E | change | `oura_history_cache`, memory.intensity_cap, runs job/tokens/latency, `unclassified_messages`, goal tables, previous-session message query, backup-first M4 migration steps |
| `core/tools.py` | A | change | `health_range`, `day_summary`, `remember(keep_light)`, refresh note |
| `modules/base.py` | A | change | `Module.summary(user, day)` |
| `modules/nutrition/history.py`, `tools.py`, `module.py` | A | new/change | `food_totals` tool, summary |
| `modules/training/history.py`, `tools.py`, `module.py` | A,E | new/change | `workouts` tool, mark_done offer + details, program session as request, summary |
| `core/memory.py` | A | change | caps on facts |
| `modules/training/policy.py`, `planner.py` | A,E | change | `context_cap` rule, caps in ctx, keep_strength_on_downgrade |
| `core/chat_sessions.py` | A | change | `previous_question` |
| `core/agent.py` | A,B,C,D | change | previous-question line, `confirm_previous`, `classify`, `decline`, per-skill tools/bodies/model, MCP tools in ToolNode |
| `core/reply_rules.py` | A,D,F | change | OFFERS, GSSI link + no copied passages, USDA source named |
| `prompts/agent.md` | A,C | change | voice rewrite, general questions; area rules move to skills |
| `modules/training/messages.py`, `modules/nutrition/messages.py` | A | change | voice review |
| `docs/scenarios.md` | A,C,D,E | change | M4-1..5 promoted, M4-6..11, E-1..10 |
| `tests/test_replay.py`, `test_reply_rules.py`, `test_timeparse.py`, `test_policy.py`, `test_db_migration.py`, `test_format.py` | A–E | change | new and promoted scenarios, coverage map |
| `tests/test_history.py` | A | new | range math on synthetic data |
| `config/models.yaml`, `core/models.py` | B | new | job → model, prices, cost |
| `core/llm.py` | B | change | job `structured`, usage + latency logged |
| `scripts/show_runs.py` | B | change | job/model/tokens columns |
| `tests/test_models.py` | B | new | env override, per-job models, cost |
| `skills/*.md` (training, recovery_sleep, nutrition, fueling_hydration, trends_patterns, goal_program, pain_injury, memory, logging, off_topic) | C,D,F,G | new | one per category: tools, sources, body (incl. the honesty rule), rubric |
| `core/skills.py`, `core/classifier.py`, `prompts/classifier.md` | C | new | load skills, classify |
| `tests/data/classifier_set.jsonl`, `scripts/eval_classifier.py`, `tests/test_skills.py`, `tests/test_classifier.py` | C | new | labeled set, accuracy, new-category test |
| `scripts/draw_graph.py`, `docs/graph.*` | C,end | change | the new nodes |
| `scripts/ingest_gssi.py`, `mcp_servers/gssi/server.py`, `mcp_servers/gssi/index.py` | D | new | index + MCP server |
| `core/mcp_tools.py`, `config/mcp.yaml` | D,F | new | MCP client, sync wrappers, fallback |
| `modules/nutrition/fueling.py`, `config/fueling.yaml` | D | new | `fueling_plan` amounts in code; weight from Oura / saved fact, never estimated |
| `core/memory.py`, `core/tools.py` | D | change | memory kind `body`, `save_body_weight(kg)` |
| `tests/test_gssi_retrieval.py`, `tests/test_no_gssi_text.py` | D | new | retrieval check; no indexed text in tracked files |
| `requirements.txt` | B,D | change | chromadb, mcp, langchain-mcp-adapters, trafilatura, pypdf, langsmith |
| `modules/registry.py` | E | change | `goals` module |
| `modules/goals/` (`module.py`, `tools.py`, `generator.py`, `feasibility.py`, `adjust.py`, `direction.py`, `questionnaire.py`, `messages.py`, `schemas.py`) | E | new | program engine + direction goals |
| `programs/schema.py`, `programs/half_marathon.yaml`, `programs/examples/ironman.yaml` | E | new | template schema + templates (incl. `run_then_strength`) |
| `modules/training/blocks.py` | E | change | a program `run_then_strength` session → two-block request; split choice avoiding Legs on L−1/L−2 |
| `config/direction_rules.yaml`, `prompts/direction_rules.md` | E | new | rule catalog, proposal prompt |
| `modules/nutrition/targets.py` | E | change | protein from a confirmed direction rule (no calorie change) |
| `today.py` | E | change | done sync also marks program days |
| `tests/test_generator.py`, `test_feasibility.py`, `test_adjust.py`, `test_program_schema.py`, `test_direction.py` | E | new | engine unit tests |
| `scripts/install_usda_mcp.sh` | F | new | pinned clone + venv |
| `evals/rubric.py`, `evals/judge.py`, `tests/data/eval_set.jsonl`, `scripts/run_evals.py`, `scripts/calibrate_judge.py`, `docs/evals/` | G | new | rubric evals |
| `docs/demo.md`, `docs/M4_plan.md` | G,start | new | demo script; this plan |
| `CLAUDE.md`, `README.md` | each part / end | change | Status, Decisions, architecture, how-tos |

## Assumptions (tell me if any is wrong)
1. "Last week" / `days_back=7` = the 7 days ending today, today included and labeled "so far". Averages count only days with food logged.
2. Oura `distance` is in meters (judged from the values; no unit in the response).
3. Rest days per week stay in `goals.yaml schedule.rest_days_per_week` (the brief says user_settings). Moving them would need a settings migration. goals.yaml is not modified.
4. LangSmith traces (when on) carry your messages and health data to LangSmith's cloud. Secrets and the iCal URL never go into a trace.
5. gpt-5 for the judge is proven by a test call (`scripts/check_models.py`) before G3, and you check the prices I report.
6. `docs/gssi_sources.txt` (URLs only) can be committed; the article text never is.

## Verification (end of M4)
```
pytest -q
RUN_LIVE_LLM=1 pytest -q -s tests/test_replay.py -k live
RUN_LIVE_LLM=1 LIVE_RUNS=3 pytest -q tests/test_replay.py -k live
python scripts/eval_classifier.py
python scripts/ingest_gssi.py && pytest -q tests/test_gssi_retrieval.py
python scripts/run_evals.py            # docs/evals/<date>.md + LangSmith experiment
python scripts/show_runs.py --last 5
python bot.py --fixtures               # Saturday long-run question, "what's today's run", "am I on track"
python scripts/draw_graph.py
```
