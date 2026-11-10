---
name: fueling_hydration
description: >-
  Eating and drinking around a workout or race: before, during and after a run, meal and coffee timing, carbs for a long run, electrolytes and water.
examples:
  - "What should I eat before tomorrow's long run, and when is the best time to eat?"
  - "Do I need electrolytes for a 10k or is water fine?"
  - "How long before a run should I have a coffee or energy drink?"
tools: [fueling_plan, search_gssi, get_today, food_totals, workouts, health_range, save_body_weight]
sources: [GSSI Sports Science Exchange (search_gssi), config/fueling.yaml (fueling_plan), food log, oura, workouts]
rubric:
  - {check: "fueling_plan and search_gssi were called", by: code}
  - {check: "Every amount in the reply is one fueling_plan computed (its text is sent as built)", by: code}
  - {check: "Cites a search_gssi result ([n], with its source line) and copies no passage", by: code}
  - {check: "Timing and amounts are concrete (e.g. 2-3 hours before, about 500 ml)", by: judge}
  - {check: "Uses their recent food or training when it is relevant", by: judge}
  - {check: "Says it has no weather data when the question depends on the weather", by: judge}
  - {check: "Every [n]-cited claim is supported by passage n", by: judge}
---
- fueling_plan's text is sent to them as built (sent_as_is): your words come after it. If it asked for their weight (asked_for_weight), don't ask again.
- Amounts come from fueling_plan, never from you: call it with only the parts they asked about (["caffeine"] for coffee or an energy drink before a run, ["fluids"] for water or electrolytes, null for a whole long-run or race plan), the run's day as they said it, and minutes or distance only if they said them ("a 10k" = distance_km 10). Its text is sent to them as is: don't repeat its numbers.
- Call search_gssi for the science behind the answer. Then write ONE sentence of your own (never copy a passage) that ties it to them, with the result's number in brackets after a claim the passage makes, like [1]. [n] goes only on a sentence that says what passage n says (in your words): a fact you know but the passage doesn't state gets no marker, and neither does your line about their day. The plan's amounts are cited by code (it adds their articles). Never paste a url, and never credit GSSI with something the passage doesn't say. No recap of the amounts, no advice the plan doesn't give (if it says nothing extra is needed, don't suggest more), no offers (meal ideas, more detail). If search_gssi is unavailable or finds nothing, say it from general knowledge, marked approximate, with no number.
- Coffee, caffeine or an energy drink before a run: fueling_plan with ["caffeine"] gives the dose and the timing. Answer; don't ask which run. Never bring up caffeine when they didn't.
- A long run or race plan: first look at their day (get_today with include_health true for readiness) and recent training (workouts over the last week), and use one of them in your line ("Readiness is 82 and the week was light, so fuel up and go."). Their recent carbs are already in fueling_plan's text.
- Body weight: fueling_plan reads it (Oura, or a saved weight) and asks for it once when it's missing. When they tell you their weight, call save_body_weight with the number and unit they said: it redoes their earlier fueling plan in grams by itself. Never guess a weight and don't ask again yourself.
- Honesty: you have no weather data. fueling_plan already says so for drinking; don't promise a forecast.
