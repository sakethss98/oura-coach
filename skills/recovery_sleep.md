---
name: recovery_sleep
description: >-
  How rested or recovered they are: readiness, sleep, HRV, tiredness, how hard they can go today, sleeping better.
examples:
  - "Why am I so tired today?"
  - "How do I sleep better?"
  - "how hard can I go today?"
tools: [get_today, health_range, refresh_health, workouts]
sources: [oura]
rubric:
  - {check: "Readiness, sleep or HRV numbers match the tool result", by: code}
  - {check: "Explains with their own data (last night, the days before) when it has it", by: judge}
  - {check: "Gives one practical next step", by: judge}
---
- Today's readiness, sleep, HRV, resting heart rate: get_today with include_health true. More than one night ("this week", "lately"): health_range. "Why am I tired?": look at last night and the days before (health_range with compare_days helps), and say what stands out.
- "update from oura" / "refresh": refresh_health and say honestly whether anything new came in.
- Sleep advice: general guidance, brief, tied to what their data shows (bedtime, hours).
- Never call a low readiness a failure; it is information.
