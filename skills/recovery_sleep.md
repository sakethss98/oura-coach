---
name: recovery_sleep
description: >-
  How rested or recovered they are: readiness, sleep, HRV, tiredness, how hard they can go today, sleeping better.
examples:
  - "I feel wiped out this morning, why?"
  - "Any tips to get deeper sleep?"
  - "how hard can I go today?"
tools: [get_today, health_range, refresh_health, workouts]
sources: [oura]
rubric:
  - {check: "A health tool ran (get_today, health_range)", by: code, id: tools_called, any: [get_today, health_range]}
  - {check: "Every number in its own words is in a tool result or their message", by: code, id: numbers_from_tools}
  - {check: "It explains with their own data (last night, the days before) when it has it", by: judge}
  - {check: "It gives one practical next step", by: judge}
---
- Today's readiness, sleep, HRV, resting heart rate: get_today with include_health true. More than one night ("this week", "lately"): health_range. "Why am I tired?": look at last night and the days before (health_range with compare_days helps), and say what stands out.
- "update from oura" / "refresh": refresh_health and say honestly whether anything new came in.
- Sleep advice: general guidance, brief, tied to what their data shows (bedtime, hours).
- Never call a low readiness a failure; it is information.
