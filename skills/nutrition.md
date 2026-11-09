---
name: nutrition
description: >-
  What or how much to eat, what's left today, what they ate today, food they plan to eat, and any question about a food, drink or product (caffeine, protein, whether to have it).
examples:
  - "What should I eat?"
  - "i made spinach dal yesterday; how much to eat for lunch with rice"
  - "How much caffeine is present in Celsius live fit"
tools: [get_today, list_food, food_totals, suggest_meal, meal_recipe]
sources: [food log, targets, saved facts]
rubric:
  - {check: "Numbers about their day (eaten, left, targets) match the tool result", by: code}
  - {check: "Meal ideas come from suggest_meal or meal_recipe, not the model", by: code}
  - {check: "A general food question gets a direct answer, marked approximate", by: judge}
  - {check: "Respects their saved diet (vegetarian, eats eggs)", by: judge}
---
- What's left today, targets, today's food: get_today. A past day: list_food with the day exactly as they said it. Habits over time: food_totals.
- Meal ideas ("dinner ideas", "what should I eat now?", "I have dal, rice and spinach", "how much to eat for lunch"): suggest_meal (its text is sent as is). "The second one" / "the recipe": meal_recipe with that dish's name from the ideas. "Make it higher protein": meal_recipe with the same dish and the change. Never write meal ideas yourself.
- A question about a food ("should I have a Celsius?", "how much caffeine is in…") is NOT a log: answer it, approximate. Food they'll eat later ("I'm going to have pizza later") is not a log either.
- Whether to skip, cut or delay a meal ("should I skip dinner to hit my deficit?") is a question, not a request for meal ideas: answer it in your own words first. Their calorie deficit is already built into their daily targets, so skipping a meal doesn't help them reach it; a lighter, protein-first meal does. Offer ideas only if they ask (don't call suggest_meal for this).
