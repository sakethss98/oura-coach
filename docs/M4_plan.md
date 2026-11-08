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

**C2** `tests/data/classifier_set.jsonl`: every line of `data/my_questions.txt`, the exported messages, and **every scenario message**, as `{text, prior_turns?, categories}`. I propose the labels and flag the doubtful ones for you (e.g. "Had a paneer wrap, what's left and should I still run tonight?" = logging + nutrition, or + training).

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

**Verify D:**
- `python scripts/ingest_gssi.py`, twice (the second run is a no-op).
- `pytest -q tests/test_gssi_retrieval.py`: 5 known questions → the expected URL in the top 4; skipped without an index.
- Live scenario M4-11: "My long run is Saturday. What should I eat and drink Friday and Saturday?" Checks: health_range / get_today + food_totals + search_gssi + fueling_plan are called, the amounts equal fueling_plan's, and the link is present.

## Part E — Goal programs (half marathon first)

**New module `modules/goals/`** (registered; added to user 1's enabled_modules by a backup-first data migration). New tables (CREATE IF NOT EXISTS): `goals`, `goal_answers(goal_id, key, value, source user|data, updated_at)`, `program_days(goal_id, date, discipline, session_type, target_km, target_min, phase, week, status planned/done/missed/moved/skipped, done_ref, details_json)`, `program_adjustments(goal_id, day, kind, reason, created_at)`, `direction_goals`, `direction_rules`.

**Template schema (`programs/schema.py`, pydantic).**
- Fields: `asks_user`, `phases`, `disciplines[]` (each with `session_types`, `progression`, `weekly` values per week), `shared` (weekly load/hard-day cap, rest budget, max sessions), `milestones`, `progress_signals[]` (`oura_workout`, `manual_workout`, `checkin`, plus declared-only `milestone_test`, `class_attended`, `coach_verdict` for skill goals like a belt), `safety`.
- `programs/half_marathon.yaml` is wired. `programs/examples/ironman.yaml` (swim/bike/run/strength) validates in a test and isn't wired.
- The `goal_program` skill and tools speak of "sessions" and "disciplines", never "runs".

**E1 Running numbers: proposed from Hal Higdon's Novice 1 Half Marathon (12 weeks), checked today on halhigdon.com, converted to km.**

| Wk | Phase | Easy run A | Short run | Easy run B | Long run |
|---|---|---|---|---|---|
| 1 | base | 5 | 3 | 5 | 6.5 |
| 2 | base | 5 | 3 | 5 | 6.5 |
| 3 | base | 5.5 | 3 | 5.5 | 8 |
| 4 | base | 5.5 | 3 | 5.5 | 8 |
| 5 | build | 6.5 | 3 | 6.5 | 9.5 |
| 6 | build (lighter) | 6.5 | 3 | 6.5 | 5 (Higdon's 5K week) |
| 7 | build | 7 | 5 | 7 | 11 |
| 8 | build | 7 | 5 | 7 | 13 |
| 9 | build (lighter) | 8 | 5 | 8 | 10 (Higdon's 10K week) |
| 10 | peak | 8 | 5 | 8 | 14.5 |
| 11 | peak | 8 | 5 | 8 | 16 (longest) |
| 12 | taper | 6.5 | 5 | 3 | race 21.1 |

- **Rest:** the day before the long run (Jeff Galloway's half-marathon guidance). The long-run day is asked (Sat/Sun).
- **Entry:** a long run of at least 5 km in the last 4 weeks (Galloway's Finish program asks for 3 mi in the last 2 weeks).
- **Pace for minutes:** the median of their Oura running workouts (distance/duration); else asked.
- **Strength defaults:** 2/week in base and build, 1 maintenance at peak, 1 light in the taper. This follows Higdon's note for experienced lifters (lift after runs, cut back at peak). His beginners wait until after the plan; your direction goal is why we don't.
- **Post-race:** a proposed 6–8-week lifting block with running at maintenance. It's created only on your yes.
- **Hybrid days:** session type `run_then_strength` = one session with two blocks, run first, reusing the M3.2 blocks (`plan_json.blocks`, per-block done, Oura auto-complete per block). Strength can sit on a run day as a run followed by a lift, which matches Higdon's "lift after runs". Pairing rules, in code:
  - Only an easy or short run gets a lift; never the long run.
  - Never Legs on L−1 or L−2 (the existing rule), and never on L+1.
  - L+1 (the day after the long run) is rest or an easy session only: a walk, mobility, or upper body at most.
  - Legs go mid-week by preference (L−4 or L−5).
  - Upper body or core after any harder run (the longer easy run of the week).
  - The total fits the plan's time window; the existing `_fit_window` cuts the last block.
- **Race day:** Feb 15, 2027 is a Monday. The generator anchors the last week to the race date: week 12's long-run slot becomes the race on Monday, the Sunday before it (L−1) is rest, and taper runs count back from race day. 14 weeks remain from Mon Nov 9, so the extra weeks repeat the entry week at maintenance (E3).
- **Lighter weeks:** the race weeks act as a lighter week every 3 weeks.
- **Progression (decided: Higdon as published):** each long run is held for 2 weeks, then grows ~1.6 km. That's about 10%/week averaged over each pair. The template's progression rule says exactly that: "+≤ 1.6 km after a 2-week hold, at most +25% in one week; a lighter week every 3 weeks; a 1-week taper". The adjustment code (E6) uses the same rule.

**E0 Direction goal.**
- A closed rule catalog, `config/direction_rules.yaml`, gives each key a unit, an allowed range and its enforcer:
  - `strength_sessions_per_week {min,max}` (generator, all phases)
  - `min_strength_in_taper` (generator)
  - `post_race_strength_block_weeks` (generator proposal)
  - `protein_g_per_kg` (nutrition targets; clamped 1.2–2.2; calories never change)
  - `max_run_days_per_week` (generator)
  - `hybrid_days_per_week {min,max}` (generator)
  - `keep_strength_on_downgrade` (policy_check)
- Flow:
  1. `set_direction_goal(text)` → one structured call (prompts/direction_rules.md) proposes 3–6 rules **from the catalog only** (enum keys, so code can enforce every one).
  2. The proposal goes into `direction_rules` with `confirmed_at = NULL` and is shown as code text.
  3. `confirm_direction_rules(edits)` takes one round of edits and sets `confirmed_at`.
- Only confirmed rules are read. A change re-runs propose → confirm.
- `direction_check(question)` for "hybrid athlete by December?":
  - The verdict sentence is the model's, from general knowledge, marked approximate.
  - The 8 weekly milestones are code: each rule's target vs their last 4 weeks (strength sessions/week from merged activity, protein days hit from food_log, longest run from Oura).
  - Lifts are left out with "no lift log yet", since we don't record weights.

**E2 Questionnaire.**
- A fixed core (goal + date, current level, days and time per week, injuries/limits) plus the template's `asks_user` (long-run day, easy pace).
- `create_goal` asks 2 open keys at a time and `answer_goal(fields)` stores them in `goal_answers`.
- Data overwrites the answer when it exists: longest run in the last 4 weeks and pace from Oura/manual runs.
- A race goal may carry a **target time** ("under 2:30"), stored as `goal_answers.target_time` (parsed in code to seconds; source user). There are no pace workouts in M4: the plan stays finish-focused.

**E3 Feasibility (pure).** The start week = the last template week whose long run ≤ their longest recent run; the weeks needed = 12 − start + 1. If weeks left < weeks needed, say so and offer the earliest feasible date or a 10K (offered in words; no 10K template). Extra weeks repeat the entry week at maintenance. Nothing is written to `program_days` until they say yes.
- **Predicted finish (only when a target time is set):** pure `feasibility.predict_finish(runs, race_km)`.
  - It uses Riegel's formula, T2 = T1 × (D2 / D1)^1.06, on Oura running workouts of ≥ 5 km in the last 6 weeks (`distance` + start/end).
  - The run with the fastest projection is used.
  - The result is marked approximate: Oura durations can include pauses, and Riegel assumes trained, evenly paced runs. It's compared with the target in plain words ("about 2:24 predicted, so 2:30 looks within reach").
  - With no qualifying run: "Not enough run data to predict a time yet. A 5 km or longer run with the ring on would let me."
  - Unit tests: known Riegel values, no qualifying run, and the fastest projection picked.

**E4 Generator (pure: template + answers + confirmed direction rules + start date → days).**
- It places runs on the training days, avoiding back-to-back run days where it can.
- Strength goes on non-run days per the phase and direction rules.
- Rest on L−1, plus the rest budget (`goals.yaml schedule.rest_days_per_week`; see assumptions).
- Long run = the only hard day; ≤ max sessions/week.
- A strength day that would be Legs on L−1 or L−2 takes the next split (the existing `next_split_day` and split list).
- Hybrid days: up to `hybrid_days_per_week.max` strength sessions move onto easy/short run days (≥ `min`). When hybrid days allow it, the generator frees at least one full rest day per week beyond L−1. Example week with a Sunday long run:
  - Mon: rest (L+1)
  - Tue: easy run + upper
  - Wed: strength (Legs allowed, L−4)
  - Thu: short run
  - Fri: easy run (+ core, L−2: no Legs)
  - Sat: rest (L−1)
  - Sun: long run
- Unit tests:
  - 2 strength days in every build week
  - no Legs the day before a long run (or on L−2), and none on L+1
  - L+1 is only rest, a walk, mobility or upper body
  - Legs land on L−4/L−5 when those days are training days
  - ≥ 1 rest day/week
  - the long-run sequence equals the table
  - no `run_then_strength` on the long-run day
  - a hybrid lift after the harder easy run is upper or core
  - an extra rest day is freed when hybrid days allow
  - the race lands on Monday Feb 15 with Sunday rest

**E5 Tools.**
- `create_goal`, `answer_goal`, `goal_today` ("what's today's session?"), `goal_status` (done vs planned to date, long-run progress vs plan, weeks left).
- Done paths: `today.sync_oura_completions`, `mark_done` and `add_manual_workout` call `goals.sync_done(user, day)`, which matches merged activity to the day's program session (run types ↔ running/manual run with ≥ 70% of target km or min; strength ↔ strength).
- `start_checkin`, `plan_workout` and `submit_checkin` use today's program session as the request when they named none, so a low-readiness day still gets the consult-once question. A `run_then_strength` day becomes a two-block request.
- An ad-hoc "give me a run followed by weights" at check-in keeps working as today. On a program day, the run block's type and minutes come from that day's session, the strength block from their request, and code picks the split (no Legs on L−1/L−2).

**E6 Adjustments (pure `adjust.py`, run from `ingest` like `finalize_quietly`, recorded with reasons).**
- Past planned days with nothing matched → missed.
- Missed easy run → dropped (never two runs on one day).
- Missed long run → the next free weekend day before the next long run, else next week's long run is held (no progression).
- 2+ misses in a week → that week repeats.
- Low readiness on a program day → the planner's normal downgrade, and the program day is marked `moved`/easy.
- An active fact with `intensity_cap` → program days in its range are `skipped` (paused), and the plan resumes the same week number afterwards.

**Scenarios E (scripted + live):** E-1 feasible date, E-2 infeasible date, E-3 "what's today's run", E-4 missed long run, E-5 two misses in a week, E-6 "am I on track", E-7 direction goal → rules → confirmed fields, E-8 two strength days in build, E-9 no heavy legs before a long run, E-10 "hybrid athlete by December?" (honest no + milestones; muscle-gain honesty rule), E-11 a hybrid day (easy run then upper body) in a build week, E-12 an ad-hoc run + lift on a program day uses that day's run, E-13 "I want to run a half marathon on Feb 15 under 2:30, can I?" → the distance feasibility plus a predicted time vs 2:30, honest either way (scripted with and without a ≥ 5 km run). The scripted race date is Feb 15, 2027.

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
- **G4:** `docs/demo.md`, a 5-minute script: the Saturday long-run question with its trace, a new category added live, and the eval table.

**After M4:** CLAUDE.md Status + Decisions, README (classifier, skills, MCP servers, program engine; how to add a category, a template, re-ingest GSSI), `scripts/draw_graph.py` redrawn, `git tag m4`.

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
