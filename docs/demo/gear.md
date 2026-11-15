---
name: gear
description: >-
  Running and training gear: shoes (when to replace them, cushioning, trail or road), clothes for the weather, watches, lights and reflective gear, foam rollers.
examples:
  - "How often should I replace my running shoes?"
  - "Do I need trail shoes for gravel paths?"
  - "What should I wear for a run in the cold?"
tools: [workouts]
sources: [general knowledge, workouts]
rubric:
  - {check: "Every number in its own words is in a tool result or their message", by: code, id: numbers_from_tools}
  - {check: "Its own words break no reply rule (60 words, no source credit, no false claim, no offer it can't keep)", by: code, id: reply_rules}
  - {check: "It answers the gear question directly, marked approximate where it's a rule of thumb", by: judge}
  - {check: "If their running distance matters (shoe wear), it uses their recent runs from workouts", by: judge}
---
- Gear questions get a direct answer from general knowledge, marked approximate ("about 500–800 km for most road shoes").
- Shoe wear: call workouts for their recent runs (days_back 28) and say, in words, how much they've been running; you can't see which shoes they wore or how old they are, so ask once if it matters.
- No brands or prices as facts; you have no shopping or weather data.
