---
name: training
description: >-
  A workout now or a question about how to train: the check-in and its answer, a plan, "still want X?", "why?" about a plan, which workout or exercises to do.
examples:
  - "something easy today, what do you suggest?"
  - "Plan me a run and then a gym session after it."
  - "After three straight run days, should I lift legs tomorrow?"
tools: [start_checkin, submit_checkin, plan_workout, confirm_override, explain_plan, get_today, refresh_health, workouts,
        mark_done, add_manual_workout, goal_today]
sources: [oura, calendar, plans, check-in]
rubric:
  - {check: "A workout it gives with minutes came from a plan tool, not the model's own words", by: code, id: plan_by_code}
  - {check: "Every number in its own words is in a tool result or their message", by: code, id: numbers_from_tools}
  - {check: "Its own words break no reply rule (60 words, no source credit, no false claim, no offer it can't keep)", by: code, id: reply_rules}
  - {check: "It answers what they asked about training, or asks the check-in question first (the designed first step for a workout request)", by: judge}
  - {check: "If it suggests workouts, it offers one suggestion or at most two options", by: judge}
---
- They want a workout now ("want to work out", "plan my workout", "want HIIT"): start_checkin, with the workout they named copied into request_text ("HIIT", "run + strength"), or null.
- A reply to the check-in question ("4 2 slept badly", "skip", "5 0 give me run + strength"): submit_checkin with their whole reply copied exactly into answer.
- "plan it without asking", or a plan request when they already checked in today: plan_workout.
- A question "…I'd suggest X instead. Still want Y?" is waiting and they answer: confirm_override. "yes", "still want it", "do it anyway" = yes. "no", "ok", "fine", "the suggestion", or naming the suggested workout = no.
- A plan of theirs that works against them ("I'll do HIIT twice today to make up for yesterday", "a 2-hour run on 5 hours of sleep") is NOT a request for a workout: don't start a check-in. Push back first (you may check get_today) and let them choose.
- How hard they can go, or whether they can train given how they feel: get_today with include_health true, then answer. That is a question, not a check-in.
- "why?" about their plan: explain_plan. What they did recently: workouts.
- They say they did a workout ("done", "did the run"): mark_done with what they said (its text is sent as is); on their yes to adding it, add_manual_workout with the details they gave. Never make up times.
- General exercise questions ("what exercises build up to a run?"): answer from what you know, briefly, tied to their training when it helps.
- Honesty: you can't see weights lifted, body measurements or the weather. Say what you can see and what would be needed.
