---
name: fueling_hydration
description: >-
  Eating and drinking around a workout or race: before, during and after a run, meal and coffee timing, carbs for a long run, electrolytes and water.
examples:
  - "What should I eat before tomorrow's long run, and when is the best time to eat?"
  - "Do I need electrolytes for a 10k or is water fine?"
  - "How long before a run should I have a coffee or energy drink?"
tools: [get_today, food_totals, workouts, health_range]
sources: [food log, oura, workouts]
rubric:
  - {check: "Timing and amounts are concrete (e.g. 2-3 hours before, about 500 ml)", by: judge}
  - {check: "Uses their recent food or training when it is relevant", by: judge}
  - {check: "Says it has no weather data when the question depends on the weather", by: judge}
---
- General fueling and hydration questions: answer from what you know, with concrete timing and amounts, marked approximate.
- Tie it to them when it helps: their recent carbs (food_totals), what they did lately (workouts), today's readiness (get_today).
- Honesty: you have no weather data. If the answer depends on heat or humidity, say so and give the rule for a hot day and a mild day.
