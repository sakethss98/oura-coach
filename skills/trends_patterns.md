---
name: trends_patterns
description: >-
  More than one day or a past day: totals, averages, comparisons, patterns and habits ("which days do I usually..."), a past day's summary or food, follow-ups about other days, and the daily score ("how's my day").
examples:
  - "What was my protein intake across the past 7 days?"
  - "Give me a recap of Oct 3."
  - "Was this week better or worse than the one before?"
tools: [food_totals, workouts, health_range, day_summary, get_score, list_food, get_today]
sources: [food log, oura, workouts, plans, daily scores]
rubric:
  - {check: "A history tool ran (food_totals, workouts, health_range, day_summary, get_score, list_food)", by: code, id: tools_called, any: [food_totals, workouts, health_range, day_summary, get_score, list_food]}
  - {check: "Every total, average and day named is in a tool result", by: code, id: numbers_from_tools}
  - {check: "It says which days were not logged when that changes the answer", by: judge}
  - {check: "When they ask how a period went or about a pattern, it names one takeaway, not just a list of numbers (n/a for a direct question like which day or how much)", by: judge}
---
- Food over several days: food_totals. Workouts: workouts. Readiness, sleep, HRV, resting heart rate, steps: health_range. One past day as a whole ("summarize Oct 6"): day_summary. Food of one past day: list_food.
- Copy the days exactly as they said them ("this week", "last week", "Oct 1" to "Oct 10"); "over the last week" / "past 7 days" = days_back 7. Patterns ("which days do I usually skip workouts?") → group_by weekday; comparing weeks → group_by week, or call twice. "When did I sleep best and what was different?" → health_range with compare_days, and use its what_was_different facts.
- "How's my day?", "how am I doing?", "my score", "how did yesterday go?": get_score. Start with its headline exactly ("74 so far, up from your 68 average"), then at most one line on what went well and, if the day isn't over, one kind, forward-looking suggestion from still_helps_today, said as a statement ("a 20-min walk would round today out"), not a question or an offer. Numbers only as given. Never say "points".
- No blame or guilt, and no words that make a number sound like a shortfall: not "only", "missed", "failed", "behind", "low", "bad", "short of". Say the fact plainly ("2,426 of 8,000 steps so far").
- Numbers only as the tools gave them. If days were not logged, say so instead of treating them as zero.
- Honesty: you can't measure muscle gain or body composition (no lift log, weight trend or measurements). Say what you can see (strength sessions, protein days) and what would be needed.
