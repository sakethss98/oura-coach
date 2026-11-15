# Demo: 5 minutes

Three parts: one question end to end with its LangSmith trace, a new category added live by dropping in a
skill file, and the eval table. Everything runs on fixtures (`--fixtures`: Oura data saved in `fixtures/`, an
empty calendar, `demo.db`), so it works without the ring and never touches real history. The model calls are live.

## Before (10 minutes earlier)

```bash
source .venv/bin/activate
python -c "from core.config import check_keys; check_keys()"   # OPENAI_API_KEY, USDA_API_KEY, LANGSMITH_* present
bash scripts/install_usda_mcp.sh                               # once: the pinned USDA server
python scripts/ingest_gssi.py                                  # a no-op when the index is current
grep LANGSMITH_TRACING .env                                    # must say true for the trace
python bot.py --fixtures                                       # leave it running
```

Open: Telegram (the bot chat), LangSmith (the project in `LANGSMITH_PROJECT`, Traces view), the latest
`docs/evals/<date>.md`, and `skills/fueling_hydration.md` in the editor. Fixtures' today is Wed Oct 7, 2026.
Send `hi` once so the first real question doesn't pay for cold starts (the MCP servers start on first use).

## 1. The Saturday long-run question, end to end (2 minutes)

Send in Telegram:

> My long run is Saturday. What should I eat and drink Friday and Saturday?

Point out in the reply:
- **The plan is code's.** The first lines ("🏃 Run Sat Oct 10 · about 37 min…", Fri / before / drink / after)
  come from `fueling_plan`: GSSI's ranges in `config/fueling.yaml`, their weight from the Oura profile, the run
  length from their longest recent run. "Sat" was resolved by code, not the model.
- **The coach's own line has no numbers and no [n].** It's about their week ("Readiness is solid and the week
  was light"); every amount is in the plan, and the plan's `Amounts:` line links the GSSI articles behind it.

Then the trace in LangSmith (newest run), top to bottom:
1. `classify`: the categories it picked (`fueling_hydration`): only that skill's tools are offered.
2. `agent` → tools: `get_today` (readiness), `workouts` (the last week), `search_gssi` (our MCP server, over
   stdio, to a local index), `fueling_plan`.
3. `respond`: reply rules, the citation check, the source lines. No secret appears in any trace: keys are
   redacted in code before upload.

Optional: `python scripts/show_runs.py --last 5 --brief` shows each model call's job, model, tokens, cost and latency.

## 2. A new category, live (1.5 minutes)

Send:

> How often should I replace my running shoes?

There is no gear category yet, so the classifier says "other": the agent gets every tool and every skill's
guidance, and answers from general knowledge without looking at their runs. Now drop in a skill file (in a
terminal, the bot keeps running):

```bash
cp docs/demo/gear.md skills/gear.md
```

Show the file: a description, three examples, its tools (`workouts`), a few lines of guidance and a 4-check
rubric. Skills are read on every message, so there's no restart. Send the same question again: now `classify`
picks `gear`, the agent gets `workouts` and the gear guidance, and the answer uses their recent running
("about 500–800 km for most road shoes; you've run about … lately"). Show the new trace: `classify` → `gear`.

Afterwards: `rm skills/gear.md` (the tests expect labeled messages and eval cases for every category).

## 3. The eval table (1.5 minutes)

Open `docs/evals/<date>.md`:
- One row per category: **routing** (the classifier picked the right skills), **checks** (yes / checks that
  applied), **trials passed** (every check yes), **pass^3** (all three trials of a case passed), latency,
  tokens and cost per reply.
- The checks come from each skill's `rubric` (show the block in `skills/fueling_hydration.md`): code checks
  where code can tell (every number is in a tool result, the right tools ran, links present, reply rules), a
  judge model for the rest, each a yes or no with a one-line reason.
- The judge was calibrated against hand marks (`scripts/calibrate_judge.py`); a check under 85% agreement is
  reworded before it's trusted, and every rubric change is logged with its reason in
  `docs/evals/rubric_changes.md`.
- Privacy: the cases include the owner's own questions, so the repo only has totals; per-case replies and the
  judge's reasons are in `data/evals/` (gitignored) and LangSmith.

To show one case running: `python scripts/run_evals.py --only G-8 --trials 1` (about a minute).

## If something goes wrong

- No trace: `LANGSMITH_TRACING=true` must be in `.env` before `bot.py` starts.
- "approximate (not from USDA FoodData Central)": the USDA server didn't answer; run `bash scripts/install_usda_mcp.sh`.
- An answer without GSSI links: the GSSI index is missing; run `python scripts/ingest_gssi.py`.
