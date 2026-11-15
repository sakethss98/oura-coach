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
