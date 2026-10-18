# Replay scenarios (eval set)

Saved 2026-10-18 from the brief, word for word. IDs 1–14 keep their numbers; new ones start at 15. Exact times
and values are adapted to the fixtures; the intent is kept. Tests: `tests/test_replay.py`.

## How to grade

- Each scenario starts from a clean demo.db, fixtures mode, fixed clock. No state shared between scenarios.
- Grade the outcome first, in code: DB rows, tool calls, plan fields. Don't require an exact tool order unless the order matters (e.g. remove before re-log).
- Reply checks in code on EVERY scenario: ≤3 lines / ≤60 words, no internal terms, no first-person claim without the matching tool (reply_rules). Use an LLM judge only for "did it answer what was asked", with a yes/no rubric.
- "Must not" lines are checked as hard failures.
- Scripted layer (FakeChatModel): every scenario, always runs. Live layer (RUN_LIVE_LLM=1): run each scenario 3 times. ★ = core: must pass 3/3. Others: report the pass rate.
- Regression = must pass. Capability = target for a later milestone, marked xfail with the milestone name.
- Every real failure from Telegram becomes a new scenario, with the date it happened.

## Greeting and replies

- **1 ★** "hi" → one line with readiness + what I can do. No error text.
- **1b** "hi, had eggs for breakfast" → NOT the greeting path: eggs logged.
- **11** Voice note → fixed "can't understand voice notes yet" reply. Nothing logged.
- **12 ★** Applies to every scenario: reply rules above. Not a separate conversation.
- **13 ★** "update from oura" with no new data → says nothing new came in. Must not: "I synced" or any claim beyond the tool result.
- **13b** Same, with a new workout in the fixtures → names the workout and its time.

## Food

- **2 ★** Log "poppi strawberry lemon soda small can", then "correction on that it is 3g fiber and 9g carbs. 30cal" → same row updated: fiber 3, carbs 9, 30 kcal, user numbers recorded; today's totals use 30 kcal. Must not: a second row, or a note.
- **3 ★** "I ate 5 slices of pizza at 3pm", then "it was cheese pizza with olives and onions, I don't think it's 60g protein. Edit that" → same row updated, description changed, fields I didn't state re-estimated (protein well under 60 g). Must not: a second row.
- **4** "remove the pizza" → row gone, totals updated, reply names what was removed.
- **4b** Two pizza entries today, "remove the pizza" → asks which one. Must not: delete anything.
- **5** "simple dinner ideas, I have dal, rice and spinach" → 1–2 vegetarian options using those, sized as one meal. Then "give me the second one" → that recipe. Then "make it higher protein" → a revised version of the same dish. Must not: a workout plan, a different dish.
- **15** Question, not a log: "should I have a Celsius right now?" → answers. Must not: create a food_log row.
- **16** Future intent: "I'm going to have pizza later" → no row.
- **17** Two times in one message: "coffee at 8 and a banana at 10:30" → two rows, at 08:00 and 10:30.
- **18** Eaten together: "2 eggs, toast and coffee for breakfast" → one row.
- **19** Day boundary: food logged at 00:50, before the night's main sleep starts → counts toward the previous day's totals.
- **20** "delete the last two" → removes the two newest entries only.

## Training

- **6 ★** Check-in "5 0 give me run + strength" → soreness 0 accepted, one plan with two blocks (run + strength), total fits the window.
- **7 ★** Recovery-band day + "want HIIT" → one nudge with the reason in plain words and a suggestion. "yes" → HIIT plan, skipped rules recorded as user_choice.
- **7b** Same, reply "no" → the suggested plan. Must not: a second nudge.
- **8** Open check-in question, then 16 min later "what's left to eat today" → answered + "Still want to check in today?"
- **10 ★** "done" with a fixture Oura workout 12:12–13:03 → marked done with exactly those times, done_by oura.
- **10b** "done" with no matching Oura workout → asks for the time or offers to add it. Must not: invent start/end times.
- **21** "how hard can I go today?" → answered from readiness. Must not: start a check-in.
- **22** Two check-ins today (11:00 and 11:20) → one open plan (the newer); the older is abandoned.
- **23** HIIT done in the morning, evening check-in → walk or mobility only, reason in plain words.
- **24** Check-in after the cutoff (e.g. 22:30) → rest + sleep note. Must not: an LLM plan call.
- **25** "skip" as the check-in reply → plan made, labeled check-in skipped.

## Memory

- **9 ★** "remember I eat eggs" → saved. New session: "what do you know about me" includes it. "forget the eggs thing" → removed.
- **26** Said on a Tuesday: "remember I'm traveling Thu–Sat, keep it light" → saved Thu–Sat. Friday's plan is light and mentions the trip. Sunday: the fact is no longer loaded.
- **27** "remember I'm away sometime soon" → not saved with made-up dates; asks which days.
- **28** Two diet facts saved, "forget my diet stuff" → asks which, or lists both. Must not: delete before it's clear.

## Daily score

- **14 ★** "how's my day" twice with the same data → identical score and breakdown.
- **29** No food logged → nutrition component 0, breakdown says "not logged".
- **30** Calories below the floor after the cutoff → that part of nutrition scores 0 (never reward under-eating).
- **31** After the cutoff the score is final: logging food at 21:30 does not change that day's score.

## Multi-user (needed for the app)

- **32 ★** User 2 asks "what did I eat today?" and "what do you know about me?" → only their own food and facts. Must not: any of user 1's data.

## Later — add now as xfail (capability, M4 Part A)

These came from live use on 2026-10-17.

- **M4-1** "How much protein did I have over the last week?" → total and per-day average over 7 days, matching the code's sum.
- **M4-2** "Summarize Oct 10" → that day's food totals, workouts, plan vs done, readiness/sleep.
- **M4-3** Bot asked "want an estimate?" at 11:08; next morning I reply "Yes" → understood as the answer to that question, or it confirms which question. Must not: start a check-in.
- **M4-4** "How much caffeine is in a 12 oz Celsius?" → a direct approximate answer. Must not: offer to look it up or browse.
- **M4-5** "I did 49 min HIIT at 10:37" with no Oura workout → says Oura only shares ring-recorded workouts and offers to log it. Must not: "nothing new came in" as the whole answer.

## Added during Phase 3 (2026-10-18)

- **7c** Same as 7, "yes" 20 min later in a new chat session → still answered (the question lasts 2 h, not one session).
- **7d** Same as 7, "yes" more than 2 h later → the question expired: no plan, nothing planned or scored. Must not: turn the suggestion into a plan.
- **10c** "done, what should I eat now?" → the code-built confirmation exactly as built, then the answer on its own line.

## Added during Phase 4 (2026-10-18)

- **5b** "dinner ideas" on two days in a row → no dish repeats (same main dish counts). Must not: the same dish both days.
- **4c** Two pizza entries, "remove the pizza", and the model tries to remove both → refused in code, it asks; "both" → both removed.
- **3b** "I don't think it's 60g protein" (a doubt, no number) → the estimate is redone with it; protein is not recorded as their number.

## Added during Phase 5 (2026-10-18)

- **14b** "how's my day" with 3+ final days stored → the headline compares to their own average ("74 so far, up from your 68 average").
- **33** No wearable data for the day → training, recovery and movement left out, nutrition re-weighted to 100. Must not: score a missing component 0. (tests/test_score.py)
- **34** Low readiness, no hard workout → recovery not lowered ("took it easy on a low-readiness day"). (tests/test_score.py)

## Added during Phase 6 (2026-10-18): inferred rest days

Rest days are inferred, never declared: a day with no plan and no workout is a rest day when the week's rest days stay within goals.yaml `rest_days_per_week`, or when it's a recovery rest (low readiness, or a saved context fact like travel or sickness), which doesn't use the budget.

- **35** One rest day within the weekly budget → full training credit, "Rest day — you've trained 5 of the last 7 days".
- **36** A second ordinary rest day in the same 7 days → training 0 for the day, said plainly. Must not: a question to the user.
- **37** A low-readiness rest day when the budget is already used → full credit, "Recovery rest — readiness was 64".
- **38** A rest day covered by a saved travel fact → full credit, "Recovery rest — you noted: …".
