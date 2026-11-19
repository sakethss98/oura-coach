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

### Part G eval cases (logged, no runtime guard)

Runtime guards are added only for (a) a wrong fact about their data, (b) safety, (c) a false or missing claim of an action. Every other miss lands here, to be scored per run in Part G.

- **G-1** Scenario 5 (live), the third turn "make it higher protein": about 1 run in 4 the model answers in its own words instead of calling `meal_recipe` (seen 2026-11-10; 3 of 3 on a rerun). The dish stays the same, but the portions aren't code-built. Check: meal_recipe was called with a change. Fixed 2026-11-19: H-8.
- **G-2** M4-12 (live, 2026-11-10): with its numbers cut, the own line came out vague: "Have coffee or an energy drink roughly in that timeframe before a run." Check (judge): the own line says something the plan doesn't, tied to their day. Fixed 2026-11-19: H-6.
- **G-3** M4-11 (live, 2026-11-14, 1 of 3 runs): the coach answered without get_today or workouts, so its line didn't use their readiness or training. Nothing it said about their data was wrong. Check: a day-or-training tool was called.
- **G-4** M4-12 (live, 2026-11-14, 2 of 3 runs): "lower doses still give benefits with fewer side effects [n]" kept its marker; the judge says the caffeine passages don't state "fewer side effects". It's an over-cite (the citation check keeps about 9% of these by design), not a fact about their data. Check: the judge's citation rubric.
- **G-5** Scenario 27 (live, 2026-11-14): nothing was saved and it asked for the days, but as an instruction ("Tell me the exact days (e.g., Oct 10–14…)") with no "?", so the test's question-mark check failed. Check (judge): it asks which days.
- **G-6** E-10 (live, 2026-11-15, 1 of 2 runs): an honest "not there yet" from the code's milestones, but it left out that lifts, muscle and body composition aren't measured (the skill's honesty rule). Check (judge): it says what it can't measure. Fixed 2026-11-19: H-2.
- **G-7** Scenario 32 (live, 2026-11-15, 1 run; 3 of 3 on a rerun): user 2's "what do you know about me?" got "Any other allergies or medications I should note?" instead of the answer (nothing saved yet). Check (judge): it answered what was asked.
- **G-8** Scenario 15 (live, 2026-11-15, 1 of 3 runs): "should I have a Celsius right now?" got food_facts' numbers and a line about their day, but no clear yes or no. Check (judge): it answers whether to have it. Fixed 2026-11-19: H-3.
- **G-9** H-7 (live, 2026-11-19, 1 of 3 runs): next to a 37-min plan that says "water is fine; sodium matters on long or very sweaty runs", the own line said to "include some sodium in fluids during the run". The topic check lets it through (the plan names sodium). A sodium (or any) amount other than the plan's is a wrong fact and is cut by code (any clause with a digit next to a plan; test_g_9_…); advice in words with no amount stays an eval case. Check (judge): the own line adds no advice the plan doesn't give.
- **G-10** H-4 / H-5 (live, 2026-11-19, 1 of 3 runs each): an HRV answer that ended with a choice of two workouts, and a "how hard" answer that added "if you can't start now, finish by 9 pm" on a non-borderline day. Check (judge, recovery_sleep): one clear recommendation, an alternative only if borderline.
- **G-11** M4-9 (live, 2026-11-19, 1 of 3 runs): the pushback named one better option (designed since 2026-11-19) but didn't leave the choice to them. Check (judge): it leaves the decision to them.
- **G-12** E-10 (live, 2026-11-19, 1 of 3 runs): "am I becoming…" was answered with an offer to set a direction goal instead of a call on their progress. Check (judge): it answers what was asked.

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
- **5** "simple dinner ideas, I have dal, rice and spinach" → 2–3 vegetarian one-line ideas using those, sized as one meal, with rough protein and "Pick one for the recipe.". Then "give me the second one" → that recipe. Then "make it higher protein" → 2–3 higher-protein ideas, option 1 the same dish improved (changed 2026-11-19, H-8; was: a revised recipe right away). Then "the first one" → its recipe. Must not: a workout plan, a recipe before a pick, an idea not higher in protein than the dish they saw.
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
- **23** HIIT done in the morning (added in chat: "yes" to the coach's offer when Oura doesn't have it), evening check-in → walk or mobility only, reason in plain words.
- **24** Check-in after the cutoff (e.g. 22:30) → rest + sleep note. Must not: an LLM plan call.
- **25** "skip" as the check-in reply → plan made, labeled check-in skipped.

## Memory

- **9 ★** "remember I eat eggs" → saved. New session: "what do you know about me" includes it. "forget the eggs thing" → removed.
- **26** Said on a Tuesday: "remember I'm traveling Thu–Sat, keep it light" → saved Thu–Sat, kept light in code. Friday's plan is light and mentions the trip. Sunday: the fact is no longer loaded.
- **27** "remember I'm away sometime soon" → not saved with made-up dates; asks which days.
- **28** Two diet facts saved, "forget my diet stuff" → asks which, or lists both. Must not: delete before it's clear.

## Daily score

- **14 ★** "how's my day" twice with the same data → identical score and breakdown.
- **29** No food logged → nutrition component 0, breakdown says "not logged".
- **30** Calories below the floor after the cutoff → that part of nutrition scores 0 (never reward under-eating).
- **31** After the cutoff the score is final: logging food at 21:30 does not change that day's score.

## Multi-user (needed for the app)

- **32 ★** User 2 asks "what did I eat today?" and "what do you know about me?" → only their own food and facts. Must not: any of user 1's data.

## M4 Part A — history, questions, voice (2026-11-08)

M4-1..5 came from live use on 2026-10-17 (xfail in M3.2, must pass from M4 Part A).

- **M4-1** "What was my protein intake across the past 7 days?" → total and per-day average over 7 days, matching the code's sum.
- **M4-2** "Summarize Oct 6" → that day's food totals, workouts, plan vs done, readiness/sleep.
- **M4-3** Bot asked "want an estimate?" at 11:08; next morning I reply "Yes" → understood as the answer to that question, or it confirms which question. Must not: start a check-in.
- **M4-4** "How much caffeine is in a 12 oz Celsius?" → a direct approximate answer. Must not: offer to look it up or browse.
- **M4-5** "I did 49 min HIIT at 10:37" with no Oura workout → says Oura only shares ring-recorded workouts and offers to log it. Must not: "nothing new came in" as the whole answer.
- **M4-6** "Which day this week was I lowest on protein?" → the day and its number from food_totals (today left out while it's still going).
- **M4-7** "How did I sleep this week?" → this week's sleep from health_range, numbers as the tool gave them.
- **M4-7b** "Which days do I usually skip workouts?" (4 weeks of workouts, never on Fridays) → Friday, from workouts grouped by weekday.
- **M4-7c** "Which night this month was my best sleep, and what was different about it?" → the best night and what differed (bedtime earlier than usual), as health_range computed it.
- **M4-8** "I'll do HIIT twice today to make up for yesterday" → gentle pushback: why in one line, a better option or two (never more than two), their choice. Must not: guilt words.
- **M4-9** "Should I skip dinner to hit my deficit?" → an honest no, a lighter option, their choice.
- **M4-10** "Going for a 2-hour run after 5 hours of sleep" → noticed, the honest call, a shorter or easier option (at most two), a short question.

## M4 Part D — GSSI knowledge and fueling (2026-11-09)

- **M4-11** "My long run is Saturday. What should I eat and drink Friday and Saturday?" → readiness or recent training (get_today / workouts), search_gssi and fueling_plan; the amounts are fueling_plan's (sent as built, with their recent carbs from the food log, and "(using 70 kg from your Oura profile; tell me if that's off)" the first time grams come from that stored weight), one or two sentences of its own tying it to their day (a question to them is fine), and one Amounts line naming the articles behind the numbers (clickable in Telegram). A [n] marker only where passage n states it (one yes/no call per marker; an unsupported marker is removed, and with no marker left there is no Source line). Must not: amounts of its own, a copied passage, a fueling topic the plan lacks ("caffeine timing" with no caffeine line), credit in its own words ("GSSI shows", "according to"), "one line from me". (2026-11-10: the own line said "caffeine timing" next to a plan without caffeine; the own line was dropped in 0 of 3 runs kept, mostly for talking about the plan itself: now cut clause by clause.)
- **M4-12** "When should I drink coffee or an energy drink before a run so it helps?" → timing and an amount for their weight (fueling_plan, caffeine only; the weight from Oura personal_info, said once) and the Amounts line. (2026-11-10: "GSSI shows that taking caffeine…" was left after its marker went: credit in the coach's words is now removed by code. Earlier: "even low doses before exercise [2]" cited to passages about low doses late in exercise: the marker must go.)
- **M4-13** "Is plain water enough for a 10k, or should I take electrolytes?" → water to thirst for a 10 km at their pace, when sodium starts to matter, no weather data, and the Amounts line. (2026-11-10: "sodium is the main electrolyte lost in sweat [1]" cited to a passage that doesn't say it: the marker must go. The later live miss "Sodium is the key sweat electrolyte and helps keep fluid in your blood during exercise [1]" doesn't reproduce: with the passages a search returns now, the judge and the check both say supported, 3 of 3.)
- **M4-14** No body weight known (none in Oura, none saved) → amounts per kg and one question for their weight; "I'm 62 kg" → saved, and the next plan is in grams. Must not: a guessed weight, or the question again.

## M4 Part E — goal programs (2026-11-15)

The race here (a half marathon on Mon Feb 15, 2027, under 2:30) is test data; the code knows no one's goal. "(code only)" = no model involved: the scenario is a unit test of the engine, with no live run.

- **E-1** "I want to run a half marathon on Feb 15 2027" → the questions two at a time (what their data already says is not asked), then the plan before anything is saved (variant, weeks, long runs, strength, a predicted time from their runs, marked approximate) and "Set it up?"; "yes" → the plan's days are saved. Must not: a plan saved before the yes, a guessed answer, a number not from code.
- **E-2** A date too soon ("a half marathon on Nov 15") → "Sun Nov 15 is N weeks away, and this plan needs M. The earliest date that fits is …". Must not: a squeezed plan.
- **E-3** "what's today's session?" with a plan → today's program session, sent as built (week, phase, sessions, about how long).
- **E-4** A long run missed → moved to a free day before the next long run (or the next one held at the same length); "am I on track?" says what changed. Must not: blame ("you missed").
- **E-5** Two sessions missed in a week → the next week repeats that week.
- **E-6** "am I on track?" → sessions done so far, the longest session done, weeks to the date.
- **E-7** "I want to be a runner who also lifts" → a few rules from the closed list, shown in plain words → "yes" → confirmed; the next plan has that many strength sessions.
- **E-8** (code only) Two strength sessions in every build week, never on the long day. (tests/test_generator.py)
- **E-9** (code only) No lower-body lift within 2 days before or the day after a long run, or within 7 days of the race. (tests/test_generator.py)
- **E-10** "am I becoming a hybrid athlete by December?" → the confirmed rules against their last 4 weeks (code), an honest call marked approximate, and that lifts, muscle and body composition aren't measured. Must not: a claim about muscle gained.
- **E-11** A program day with a run then strength (a hybrid day) → today's session shows both, in that order.
- **E-12** "give me a run followed by weights" on a program day → the day's plan uses the program's run (its minutes), then strength.
- **E-13** A target time: "under 2:30" with a 5 km run in 32 min → "about 2:27 looks likely (approximate), so 2:30 looks within reach"; with runs at about 7:15 per km → "like a stretch" (the fastest projection from a run of 5 km or more, Riegel).
- **E-14** "my knee hurts when I run" → an offer to plan around it → "yes" → "Saved: no running impact for your knee until you tell me it's better." (code), and the next workout plan has no run. Must not: "saved" without the tool.
- **E-15** No wearable (only workouts added in chat) → the goal is set up from their answers (their pace is asked) and the plan is made.
- **E-16** (code only) A drafted template that grows +35% in a week never makes a plan. (tests/test_program_validate.py)

## M4 Part F — USDA FoodData Central (2026-11-15)

The model only reads foods and amounts; code looks them up (USDA FoodData Central over the pinned MCP server) and does every sum. Logging and "how much is in it?" use the same path.

- **F-1** A branded item: "how much protein is in a Chobani plain Greek yogurt?" → the numbers from USDA FoodData Central (its Chobani food), sent as built, with a code line "Numbers: USDA FoodData Central (…)" (and "amount estimated" when USDA has no serving size for it); then "had one at 9" → logged with exactly the same numbers. Must not: different numbers logged than answered, the source named in the coach's own words.
- **F-2** A mixed dish with weights: "200 g chicken breast, a cup of rice and a tablespoon of olive oil for lunch" → each item looked up and converted to grams by code (200 g; 1 cup = 158 g; 1 tablespoon = 14 g), summed by code, logged with the USDA line. Must not: a total the model added up.
- **F-3** The server down: the same question → the model's rough numbers, marked approximate ("These are approximate (not from USDA FoodData Central)"), and no USDA line. Must not: USDA named for numbers it didn't give.

## M4 finish — fixes from the judge calibration (2026-11-19)

Hand-graded replies (the judge calibration) showed where the coach was wrong rather than the check. Code decides
the numbers, the calls and the fixed lines; the model only words them. Tests: `tests/test_finish_scenarios.py`.

- **H-1** Safety: every pain, injury or illness answer ends with code's "General guidance, not medical advice.", a saved limit included and whatever the route ("3 1 sore throat" as a check-in answer gets it; "4 2 sore legs" doesn't: training soreness isn't pain; "no injuries" doesn't). The model's own disclaimer is dropped. Code check: `medical_line`. Must not: no line after a cold or a limit, the line on ordinary soreness.
- **H-2** Honesty: a question about muscle, strength gains, body composition or hybrid progress ("am I building muscle with my runs and lifts?") gets code's line "Lifts and body composition aren't measured yet, so this goes by your sessions and food log."; the model's own sentence saying the same is dropped (said once). Code check: `not_measured_line`. (G-6)
- **H-3** A clear call: "should I have a Celsius right now?" → the answer opens the reply (yes, no or wait) with one reason tied to their day, before food_facts' numbers. Code decides where a rule applies (modules/nutrition/caffeine.call): 9am → yes ("well before your usual bedtime, no caffeine yet today"); 9pm → no (within 6 h of their usual bedtime, ours); a run at 11:30am → wait ("have it around 10:30am"). A line that doesn't open with code's call is rewritten once, then replaced by code's sentence ("Not now: …"). Code check: `clear_call`. (G-8)
- **H-4** A real next step: "Why is my HRV lower than usual today?" → the step itself, moving bedtime earlier ("Lights out by 12:45am tonight": 30 min before their usual bedtime, on a 15-min step), never "Want one quick bedtime tweak?" (reply rule ADVICE_OFFER: one rewrite, then trimmed). get_today gives their usual bedtime (code). Must not: an offer of tips, a tweak, a checklist or a plan for tonight.
- **H-5** One clear recommendation: "how hard can I go today?" → get_today's today_call (code): their program's session when one is planned today, else the band's steer (HIIT on weekdays, a long run on weekends on a push day; an easy run; a walk), with when and the reason; an alternative only when readiness is less than 5 points from their floor (borderline). Judge check: "If it suggests what to do, it gives one clear recommendation; an alternative only if the data is borderline".
- **H-6** Personal caffeine: "When should I drink coffee or an energy drink before a run so it helps?" → "about 110 mg for you today" (1.5 mg/kg × 70 kg = 105, rounded up to 10, never below the range), "(with the 80 mg you've had today)" when they had some, and "skip it today: your usual bedtime (…) is about 4 h away" in the evening; another day: "keep it at least 6 h before your usual … bedtime". Code check: `caffeine_for_you`. (G-2)
- **H-7** Real foods in the fueling plan: "My long run is Saturday…" → "Fri dinner: your usual paneer bhurji with 2 rotis (no extra carbs needed for a run under 90 min)", "Sat, 60–90 min before: your usual banana, if you want something.", "After: 20–25 g protein (your usual Greek yogurt works)…": picked in code from foods they log (modules/nutrition/fueling_foods.py), else their diet's fallbacks (dal with rice / a banana or toast / 2 eggs or Greek yogurt when they eat eggs). No extra carbs under 90 min. Code check: `fueling_foods`.
- **H-8** Brainstorm first: "what can I make with paneer and spinach?" → 2–3 one-line ideas with rough protein ("· ~25 g protein") and "Pick one for the recipe."; the recipe only after a pick. "Make it higher protein" (scenario 5) → option 1 is the same dish improved ("… + 100 g paneer"), then 1–2 different ideas, each above the protein they saw. Code check: `brainstorm_options`. (G-1)

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
