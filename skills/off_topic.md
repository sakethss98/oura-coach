---
name: off_topic
description: >-
  Nothing to do with training, food, sleep, health or their goals (poems, sports scores, taxes, coding).
examples:
  - "write me a poem"
  - "who won the game last night?"
  - "help me with my taxes"
tools: []
sources: []
decline: >-
  I'm your training and food coach, so I'll stay out of that one. Ask me about workouts, food, sleep or your goals.
rubric:
  - {check: "The reply is the fixed decline line", by: code, id: fixed_decline}
  - {check: "No tool ran", by: code, id: tools_called, none: true}
  - {check: "Its own words break no reply rule (60 words, no source credit, no false claim, no offer it can't keep)", by: code, id: reply_rules}
---
Sent as a fixed line by code; the agent is not called.
