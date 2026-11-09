---
name: logging
description: >-
  Reporting something that already happened: food or drinks they had (a food named with no question, e.g. "2 eggs and chai"), a correction or removal of a food entry, a workout they did ("done", "I did 49 min HIIT"), or pulling their workouts from Oura ("update from oura"). Not a question.
examples:
  - "had poha and chai at 8"
  - "correction, it's 30 cal"
  - "I did 49 min HIIT at 10:37"
tools: [log_food, edit_food, remove_food, list_food, mark_done, add_manual_workout, refresh_health, get_today]
sources: [food log, oura, plans]
rubric:
  - {check: "The right write tool ran (log_food, edit_food, remove_food, mark_done or add_manual_workout)", by: code}
  - {check: "A correction changed the same entry, never a new one", by: code}
  - {check: "No invented times for a workout", by: code}
---
- Food, drinks, coffee, alcohol, supplements they already had: log_food. One call per thing eaten at a different time ("coffee at 8 and a banana at 10:30" = two calls); things eaten together are one call ("2 eggs, toast and coffee for breakfast"). Copy a clock time exactly as written ("at 1", "8am") into time_text. Numbers they state ("30 cal", "9g carbs") go into the number fields.
- A correction to food already logged ("correction, it's 3g fiber, 30cal", "it was cheese pizza with olives, edit that") is NEVER a new log_food call: edit_food on that entry, with the full corrected description in new_text if the food changed (keep the amount: "5 slices of pizza" + "it was cheese pizza with olives" = "5 slices of cheese pizza with olives"), and only the numbers they stated. "I don't think it's 60g protein" is not a number they stated: pass it as feedback.
- Finding an entry: use the ids from log_food / list_food results in this conversation, or call list_food. If more than one entry could match, ask which one and change nothing. "the last two" = the two newest entries. remove_food and edit_food only take ids you have seen.
- They did a workout, planned or not ("done", "did the run", "I did 49 min HIIT at 10:37"): mark_done, with what they said in workout_type, minutes and start_time_text (its text is sent as is; if Oura doesn't have it, it says Oura only shares ring-recorded workouts and offers to add it). If they also asked something ("done, what should I eat now?"), answer only that, in your own line. When they say yes to adding it, or give the time: add_manual_workout with the details they gave. Never make up times.
- "update from oura" / "get it from oura": refresh_health and say honestly whether anything new came in.
