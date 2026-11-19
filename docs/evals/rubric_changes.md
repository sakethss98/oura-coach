# Rubric changes

A check changes only when it contradicts the designed behavior, never to raise the pass rate (decided
2026-11-15). Each change: the skill, the check before and after, and why. Check texts only, no case text.

## 2026-11-15: before the pilot

**nutrition**
- Before: "Food it suggests fits their saved diet (vegetarian, eats eggs)"
- After: "If it suggests food, the food fits their saved diet (vegetarian, eats eggs)"
- Why: the check has a condition. A reply that suggests no food was judged "yes" instead of "doesn't apply", which counted a check that never applied.

## 2026-11-15: after the pilot (20 replies, before any full run)

**memory**
- Before: "A memory tool ran (remember, forget, list_memory, save_body_weight)" (code)
- After: removed. Cases that should save, forget or list now name the tool in `expect_tools`.
- Why: the designed answer to a fact with no readable days is to save nothing and ask which days, with no tool call. The check failed that correct reply.

**training**
- Before: "It answers what they asked about training"
- After: "It answers what they asked about training, or asks the check-in question first (the designed first step for a workout request)"
- Why: a workout request gets the check-in question by design. The judge failed it for not giving a plan.

**training**
- Before: "It offers one suggestion or at most two options"
- After: "If it suggests workouts, it offers one suggestion or at most two options"
- Why: the check-in question offers no options by design. The check now applies only when workouts are suggested.

**trends_patterns**
- Before: "It names one pattern or takeaway, not just a list of numbers"
- After: "When they ask how a period went or about a pattern, it names one takeaway, not just a list of numbers (n/a for a direct question like which day or how much)"
- Why: a direct question ("which day…") is answered with the day. Adding a takeaway isn't the designed behavior, and the judge failed a correct direct answer.

**goal_program**
- Before: "It gives an honest call on feasibility or progress, with a reason"
- After: "When it judges a goal or their progress, the call is honest and has a reason (n/a while it is still asking the setup questions)"
- Why: a new goal starts with the setup questions by design, with no call on feasibility yet.

**Every numbers check (all skills that have it)**
- Before: "Every number in its own words is in a tool result or their message" (code only)
- After: the same text, answered code first. Numbers in no tool result and not in their messages go to the judge with one question: general advice or an example (a typical amount, a duration to try), or a claim about their data?
- Why: general advice may carry numbers by design ("add a 20–30 g protein food"). The guard is about facts of their own data, so advice numbers failed a correct reply.

## 2026-11-15: designed behavior changed (before the full run)

**pain_injury**
- Before: "It says its exercise guidance is general, not medical advice" (judge)
- After: "The reply ends with the code's \"General guidance, not medical advice.\" line when it gives guidance" (code)
- Why: the line is now added by code to every pain, injury or illness answer that has guidance (core/agent.MEDICAL), so the check tests code's line.

**fueling_hydration**
- Before: "Each [n] left in the reply has its Source line with a link"
- After: "Next to a code-built plan its own line has no [n] marker; any [n] left has its Source line with a link"
- Why: G-4 fix. The own line next to a code-built plan carries no [n] marker, because the plan's Amounts line credits the source; markers stay only in answers without a plan.

## Check code fixed (no text change)

**source_lines**
- What changed: it now accepts "Source: GSSI – …", the unnumbered line citations.finish writes for a single source with no Amounts line.
- Why: before the fix, every correct single-source reply would have failed.

## 2026-11-15: after the first full run (228 replies)

The published table (docs/evals/2026-11-15.md) was graded with the checks as they were before these changes.

**training**
- Before: "A workout it gives with minutes came from a plan tool, not the model's own words" (code, `plan_by_code`)
- After: "If they ask for a workout to do now, it asks the check-in question or sends a plan from a tool, rather than writing the workout itself (a suggestion while pushing back on their idea is fine)" (judge)
- Why: it applied 8 times and failed all 8, every one a designed reply. When they propose something that works against them, the coach offers one better option or two (scenarios M4-8..10), and general advice may name a duration. Code can't tell a pushback option from a plan written in place of the check-in; the check's intent (a workout request goes through the check-in or a plan tool) is kept.

**logging**
- Before: "A food or workout tool ran (log_food, edit_food, remove_food, list_food, mark_done, add_manual_workout)"
- After: the same list, plus refresh_health
- Why: asking whether a workout was recorded, or asking to pull it from the ring, is answered with refresh_health by design (the logging skill). All 6 failures were that designed reply.

## 2026-11-19: after the judge calibration (20 pilot replies, 37 judge marks)

Agreement with the hand marks was 84% overall (31/37). Each check under 85% was looked at: reworded when its
wording was unclear, kept when the judge read it as designed (the coach is then fixed, not the check). After
rewording, the judge was asked again on the same 20 replies only (`scripts/calibrate_judge.py --rejudge`).

**goal_program**
- Before: "If they ask about muscle, body composition or how they look, it says it can't measure that"
- After: "If they ask about muscle, strength gains, body composition, how they look or hybrid progress, it says lifts and body composition aren't measured"
- Why: the judge read a question about hybrid progress (running and lifting) as not about muscle and answered n/a. The check is meant for any question whose answer needs lifts or body composition.

**recovery_sleep**
- Before: "It gives one practical next step"
- After: "It gives one concrete next step itself (what to do, and when), not a menu of options or an offer to give one"
- Why: the judge counted a menu of three options as "one next step". The designed reply gives the step itself.

**trends_patterns**
- Before: "It says which days were not logged when that changes the answer"
- After: "When a day in the range has no food logged, it names that day (n/a when every day was logged)"
- Why: "when that changes the answer" was unclear to the marker; the new condition can be decided from the tool result.

**training**
- Before: "If it suggests workouts, it offers one suggestion or at most two options"
- After: "If it suggests what to do, it gives one clear recommendation; an alternative only if the data is borderline"
- Why: designed behavior changed (2026-11-19): one clear recommendation, with an alternative only when code marks today borderline (`today_call`).

**recovery_sleep (added)**
- After: "If it suggests what to do, it gives one clear recommendation; an alternative only if the data is borderline"
- Why: the same design change; "how hard can I go?" is answered in this skill.

Kept as they are (the judge read them as designed): "It doesn't diagnose" (pain_injury), "Its own line ties the
answer to their day or recent training…" (fueling_hydration) and "Its own line says something the plan doesn't, tied
to their day" (a case check). The fixes are in the coach (docs/scenarios.md, H-a..H-h). "It says its exercise
guidance is general, not medical advice" had already become the code check `medical_line` (2026-11-15).

## 2026-11-19: designed behavior changed (the fixes from the calibration, H-1..H-8)

**G-1 (case)**
- Before: expect_tools any of [meal_recipe]
- After: expect_tools any of [suggest_meal], plus the code check "Meal ideas are 2-3 one-line options with rough protein, then the pick; no recipe before a pick"
- Why: "make it higher protein" now gets 2-3 higher-protein ideas (option 1 the same dish improved); the recipe comes only after they pick one (H-8).

**pain_injury (code check `medical_line`, no text change)**
- What changed: it applies to every reply routed to pain_injury or whose message names an illness, pain or injury, also when the reply is only code-built text (a saved limit, a plan after "3 1 sore throat").
- Why: the line is now added to every such reply (H-1).

**Code checks added to cases (n/a when they don't apply)**
- G-8 and H-3: "\"Should I have it now?\" opens with yes, no or wait (code's call when code made one)" (`clear_call`, H-3).
- G-2/G-4 and H-6: "The caffeine line gives the amount for them today (or why to skip it), not only the range" (`caffeine_for_you`, H-6).
- G-3 and H-7: "The fueling plan names real foods with their timing (dinner before, a snack before)" (`fueling_foods`, H-7).
- G-6 and H-2: "A question about muscle, body composition or hybrid progress gets code's not-measured line" (`not_measured_line`, H-2).
- H-8: the brainstorm check above (`brainstorm_options`).
