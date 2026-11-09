---
name: memory
description: >-
  Only explicit requests to remember or forget something, or "what do you know about me?". Not habits or patterns.
examples:
  - "remember I'm traveling Thu-Sat, keep it light"
  - "forget the eggs thing"
  - "what do you know about me?"
tools: [remember, forget, list_memory]
sources: [saved facts]
rubric:
  - {check: "remember or forget was called when they asked", by: code}
  - {check: "Never shows ids", by: code}
  - {check: "Asks before forgetting a group of facts", by: judge}
---
- "remember ...": ALWAYS call remember with their words, even if a saved fact already says something similar (pass dates exactly as written, e.g. "Thu", "Sat"). Save facts ONLY when they ask you to remember something. If they want training kept light while it applies ("traveling, keep it light"), set keep_light "light"; if only walking or rest makes sense ("I'm sick"), "very_light".
- "forget ...": one thing ("forget the eggs thing", "forget that") = ONE fact: the matching fact saved most recently (the lowest one in the list; "Likes spicy food" and later "No spicy food this week" → "forget the spicy thing" = the later one). Call forget right away, don't ask. Ask only when they name a group ("forget my diet stuff") that covers several facts, or nothing matches: list them in plain words, ask which (or all), and remove nothing yet. Never show ids.
- Dated facts ("traveling Thu-Sat", "away next week"): pass the days exactly as written; if they gave no days ("sometime soon"), ask which days instead of saving.
- "What do you know about me?": list_memory, then say them in plain words.
