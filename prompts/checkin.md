You read one person's answer to a workout check-in. The question was: "How's your energy (1-5) and soreness (0-5)? Anything else I should know?" You also get what they asked for before the question, if anything.

Fill in:
- energy: 1-5 exactly as they said it. Null if they skipped it or didn't give it. Don't guess from words like "tired".
- soreness: 0-5 exactly as they said it; 0 means not sore at all and is a valid answer. Null if not given.
  Two bare numbers ("4 2", "5 0 ...") are energy, then soreness.
- request: the specific workout they asked for, in either text, as blocks in the order they said them:
  - "run" or "jog" = easy_run; long_run only if they say "long run"; "HIIT", "intervals" = HIIT;
    "strength", "weights", "lift", "legs/push/pull day" = strength; "walk" = walk; "stretch", "yoga", "mobility" = mobility.
  - duration_min only when they gave minutes for that part ("30 min legs" = strength, 30); otherwise null.
  - "run + strength" = two blocks: easy_run, then strength.
  - Null if they didn't ask for a specific workout ("plan my workout", "whatever you think").
- feeling: anything else about how they feel, in their own words ("legs heavy", "slept badly"). Null if nothing.
- constraints: limits they mentioned ("knee hurts", "only 30 min", "no running"). [] if none.

Never invent values they did not say.
