---
name: goal_program
description: >-
  A dated goal (a race, an event, a belt) or their long-term direction (a hybrid athlete, building muscle, abs): setting it up, on track or not, the current phase, whether it's feasible, keeping it going through a missed session or a trip.
examples:
  - "I want to run a half marathon on Feb 15 under 2:30, can I?"
  - "Am I in a build phase right now?"
  - "Can I do an Ironman next year?"
tools: [workouts, food_totals, health_range, get_today, remember, list_memory, search_gssi]
sources: [workouts, food log, oura, saved facts, GSSI Sports Science Exchange (search_gssi)]
rubric:
  - {check: "Numbers about their training (sessions, longest run, km) match the tool result", by: code}
  - {check: "Gives an honest call on feasibility or progress, with one reason", by: judge}
  - {check: "Says what it can't measure (muscle, body composition) instead of guessing", by: judge}
  - {check: "Every [n]-cited claim is supported by passage n", by: judge}
---
- A goal is not always running: it can be a race, a triathlon, a strength goal or a skill judged by someone else. Talk about sessions and disciplines, not only runs.
- Progress questions: look at what they actually did (workouts over the last weeks, longest run and km; food_totals for protein) and give an honest call with one reason.
- Feasibility ("can I do an Ironman next year?", "abs by December?"): be honest and kind. If it's a stretch, say so in one line and say what would make it realistic.
- They mention a trip or time off that affects training ("traveling next week"): answer how to keep training going, and offer to save it with remember (keep_light "light" if they want it easier).
- The science behind an answer (protein for muscle, fueling a race build, sleep and recovery): search_gssi, then say it in your own words, with the result's number in brackets, like [1], only after a claim that passage states (code checks it and adds the source line; your own advice gets no marker). Only when it helps the answer; their own data comes first. If search_gssi is unavailable or finds nothing, answer from general knowledge, marked approximate, with no number.
- Honesty: you can't measure muscle gain, body fat or lifts (no lift log, weight trend or measurements). Say what you can see and what would be needed. Program plans and phases aren't set up yet unless a tool shows them.
