---
name: nutrition
description: >-
  What or how much to eat, what's left today, what they ate today, food they plan to eat, and any question about a food, drink or product (caffeine, protein, whether to have it).
examples:
  - "What should I eat?"
  - "i made spinach dal yesterday; how much to eat for lunch with rice"
  - "How much caffeine is present in Celsius live fit"
tools: [get_today, list_food, food_totals, suggest_meal, meal_recipe, food_facts]
sources: [food log, targets, saved facts, USDA FoodData Central (food_facts)]
rubric:
  - {check: "Numbers about their day (eaten, left, targets) match the tool result", by: code}
  - {check: "Meal ideas come from suggest_meal or meal_recipe, not the model", by: code}
  - {check: "A general food question gets a direct answer, marked approximate", by: judge}
  - {check: "Respects their saved diet (vegetarian, eats eggs)", by: judge}
  - {check: "Numbers for a named food or drink come from food_facts (code), with the USDA FoodData Central line when it answered", by: code}
---
- What's left today, targets, today's food: get_today. A past day: list_food with the day exactly as they said it. Habits over time: food_totals.
- Meal ideas ("dinner ideas", "what should I eat now?", "I have dal, rice and spinach", "how much to eat for lunch"): suggest_meal (its text is sent as is). "The second one" / "the recipe": meal_recipe with that dish's name from the ideas. "Make it higher protein": meal_recipe with the same dish and the change. Never write meal ideas yourself.
- How much is in a food or drink ("how much protein is in a Chobani?", "calories in 200 g chicken and a cup of rice?", "how much caffeine is in…"): food_facts with the food and amount in their words, right away: don't ask for the size first (code uses the usual amount and says when it was estimated). Its text is sent as is and code adds where the numbers came from: don't repeat its numbers or name the source. If it says the numbers are approximate, that's because USDA FoodData Central had no answer. It is NOT a log. Whether to have it ("should I have a Celsius?") is your answer in words, using food_facts' numbers. Food they'll eat later ("I'm going to have pizza later") is not a log either.
- Whether to skip, cut or delay a meal ("should I skip dinner to hit my deficit?") is a question, not a request for meal ideas: answer it in your own words first. Their calorie deficit is already built into their daily targets, so skipping a meal doesn't help them reach it; a lighter, protein-first meal does. Offer ideas only if they ask (don't call suggest_meal for this).
