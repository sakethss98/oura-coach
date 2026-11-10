"""The reply rules (core/reply_rules.py): limits, internal terms, first-person claims."""
from core.reply_rules import MAX_WORDS, enforce, violations


def test_a_short_plain_reply_passes():
    assert violations("About 1838 kcal and 101 g protein left today.", set()) == []


def test_too_many_lines_or_words():
    assert any("lines" in v for v in violations("a\nb\nc\nd", set()))
    assert any("words" in v for v in violations("word " * 61, set()))


def test_internal_terms():
    for text in ("Today is a PUSH day.", "Your band is maintain.", "Keep it at intensity 3.",
                 "Your remaining_today is 500.", "Free window until 7pm.", "Feeling: null"):
        assert violations(text, set()), text
    assert violations("Your body needs to recover today.", set()) == []   # the plain verb is fine


def test_first_person_claims_need_the_tool():
    assert violations("I synced your Oura data.", set())
    assert violations("I synced your Oura data.", {"refresh_health"}) == []
    assert violations("I've updated the entry.", {"log_food"})            # no edit tool ran
    assert violations("I'll resend it tonight.", {"log_food"})            # never allowed
    assert violations("I logged your poha.", {"log_food"}) == []
    assert violations("I'll remember that you eat eggs.", {"remember"}) == []
    assert violations("I'll remember that you eat eggs.", set())


def test_bare_words_are_not_claims():
    assert violations("Logged poha: about 300 kcal.", set()) == []         # no "I"
    assert violations("You logged 3 entries today.", set()) == []
    assert violations("Oura hasn't synced your run yet.", set()) == []


def test_enforce_drops_bad_sentences_and_trims():
    text = "Your readiness is 82. I synced your Oura data. Today is a PUSH day.\nGo for a run."
    assert enforce(text, set()) == "Your readiness is 82.\nGo for a run."
    long = enforce(" ".join(["word"] * 100), set())
    assert len(long.split()) <= MAX_WORDS
    assert enforce("a\nb\nc\nd\ne", set()) == "a\nb\nc"


def test_a_tacked_on_anything_else_is_a_violation():
    assert violations("Logged 2 eggs. Anything else?", {"log_food"}) == ["uses an extra offer ('anything else')"]
    assert enforce("Logged 2 eggs. Anything else?", {"log_food"}) == "Logged 2 eggs."


def test_offers_no_tool_can_keep_are_never_allowed():
    for text in ("Want me to look it up?", "I can search online for the exact number.", "I'll check online.",
                 "Let me look that up for you.", "Would you like me to find the label?", "I could look it up."):
        assert any("offers" in v for v in violations(text, {"log_food", "refresh_health"})), text
    for text in ("About 200 mg of caffeine in a 12 oz Celsius (approximate).", "Look at the label for the exact amount.",
                 "You can check the can's label."):
        assert violations(text, set()) == [], text


def test_enforce_drops_an_offer_and_keeps_the_answer():
    assert enforce("About 200 mg of caffeine. Want me to look it up?", set()) == "About 200 mg of caffeine."


def test_guilt_words_are_rewritten():
    for text in ("Not a smart way to make up one missed session.", "You failed to log dinner.",
                 "You should have slept more.", "You're behind on protein."):
        assert any("guilt" in v for v in violations(text, set())), text
    assert violations("Rough night, that happens. An easy run today?", set()) == []


def test_enforce_keeps_whole_sentences_and_a_short_closing_question():
    long_middle = "Two hard sessions add fatigue. " + " ".join(["Option"] * 60) + ". Which one?"
    assert enforce(long_middle, set()) == "Two hard sessions add fatigue. Which one?"


POPPI_REPLY = ("Fine as a low‑cal fizzy treat — if you’re tracking, log ~40 kcal / ~9 g sugar. "
               "Want me to add it to today’s food?")
NUTRITION_TOOLS = {"get_today", "list_food", "food_totals", "suggest_meal", "meal_recipe"}


def test_an_offer_to_add_needs_a_tool_that_can_add():
    assert any("no tool for that is available" in v for v in violations(POPPI_REPLY, set(), NUTRITION_TOOLS))
    assert violations(POPPI_REPLY, set(), NUTRITION_TOOLS | {"log_food"}) == []           # logging offered: fine
    assert violations(POPPI_REPLY, set()) == []                                          # offers not checked
    assert enforce(POPPI_REPLY, set(), NUTRITION_TOOLS) == ("Fine as a low‑cal fizzy treat — if you’re tracking, "
                                                            "log ~40 kcal / ~9 g sugar.")


def test_offers_to_save_or_mark_need_their_tools():
    assert violations("Want me to save that?", set(), {"get_today"})
    assert violations("Want me to save that?", set(), {"remember"}) == []
    assert violations("I can mark it done if you like.", set(), {"log_food"})
    assert violations("I can mark it done if you like.", set(), {"mark_done"}) == []
    assert violations("I'll add it once you tell me the time.", set(), {"add_manual_workout"}) == []


# --- knowledge search: cite a link, never copy a passage (M4 Part D) ---------------------------------

SOURCE = {"n": 1, "title": "Caffeine and Exercise Performance: An Update",
          "url": "https://www.gssiweb.org/en/sports-science-exchange/article/caffeine-and-exercise-performance-an-update",
          "passage": "Test passage says that a modest dose taken about an hour before the run helps many people "
                     "feel sharper without upsetting the stomach."}


def test_a_reply_after_a_search_must_cite_a_result():
    without = "Have it 30-60 min before the run; a smaller dose is easier on sleep."
    assert any("cites no source" in p for p in violations(without, {"search_gssi"}, sources=[SOURCE]))
    assert not violations(without.rstrip(".") + " [1].", {"search_gssi"}, sources=[SOURCE])
    assert not violations(without + " " + SOURCE["url"], {"search_gssi"}, sources=[SOURCE])   # code makes it [1]
    assert any("cites no source" in p for p in violations(without + " [4]", {"search_gssi"}, sources=[SOURCE]))
    assert not violations(without, set(), sources=[])                     # nothing found, nothing to cite


def test_twelve_words_in_a_row_from_a_passage_is_a_copy_eleven_is_not():
    copy = "Good news: a modest dose taken about an hour before the run helps many people [1]."
    assert any("copies a passage" in p for p in violations(copy, {"search_gssi"}, sources=[SOURCE]))
    near = "Good news: a modest dose taken about an hour before the run helps [1]."
    assert not violations(near, {"search_gssi"}, sources=[SOURCE])


def test_the_trim_drops_a_copied_sentence():
    text = "A modest dose taken about an hour before the run helps many people feel sharper. Keep it small."
    assert enforce(text, {"search_gssi"}, sources=[SOURCE]) == "Keep it small."


def test_the_line_next_to_a_fueling_plan_never_restates_an_amount():
    from core.reply_rules import without_amounts
    line = ("Readiness is 75, so go for it. Before: 60–250 g carbs, during none. "
            "Have it about 30-60 min before.\nhttps://www.gssiweb.org/en/x/a-1-4-g-kg")
    assert without_amounts(line) == ("Readiness is 75, so go for it. Have it about 30-60 min before.\n"
                                     "https://www.gssiweb.org/en/x/a-1-4-g-kg")
    assert without_amounts("Aim for 100 mg.") == "" and without_amounts("Try 1.5–3 mg/kg of it.") == ""
    assert without_amounts("Restore glycogen with about 1.0–1.2 g/kg/h for 4 hours [1].") == ""


def test_a_promise_to_recalculate_later_is_never_allowed():
    assert any("claims" in p for p in violations("I’ll recalc the exact gram amounts and send the plan.", set()))


def test_next_to_a_plan_at_most_two_sentences_without_recap_amounts_or_questions():
    from core.reply_rules import own_line
    draft = ("Day-before: carb-rich meals. Before: familiar carb meal a few hours out.\n"
             "Readiness 75 and a light week, so you're set [1]. Any tweaks?")
    assert own_line(draft) == "Readiness 75 and a light week, so you're set [1]."
    assert own_line("Light week, so go for it. Aim for 60 g carbs. Want ideas?") == "Light week, so go for it."
    assert own_line("About 30-60 min before (approx.). Low doses work [1]. More is no better.") == (
        "About 30-60 min before (approx.). Low doses work [1].")
    assert own_line("During: nothing. Want ideas?") == ""
    assert own_line("Caffeine guidance sent (see plan). Light week, so go for it [2].") == "Light week, so go for it [2]."
    assert own_line("Plan computed without a saved body weight [2]. Day before: Friday [1].") == ""
    assert own_line("Got your 62 kg. Read the plan and tell me if you want any swaps.") == "Got your 62 kg."
    assert own_line("You likely won't need extra. Tell me your weight (kg or lb) and I'll give amounts.") == (
        "You likely won't need extra.")                       # the plan already asks for it, once



def test_a_fueling_answer_keeps_only_amounts_a_tool_returned():
    from core.reply_rules import amounts_from
    tools = "After: 20–25 g protein, with carbs. Carbs 1–1.2 g/kg an hour → 60–75 g an hour."
    text = "Aim for 20-25 g protein after. That's ~62–74 g/h (~250–300 g total). Go easy."
    assert amounts_from(text, tools) == "Aim for 20-25 g protein after. Go easy."


def test_the_echo_guard_drops_only_instructions_read_back():
    from core.reply_rules import instructions, without_echo
    tool = '{"sent_as_is": true, "note": "Add one sentence of your own tying it to their day and readiness"}'
    orders = instructions(tool)
    assert orders == ["Add one sentence of your own tying it to their day and readiness"]
    echoed = "Add one sentence of your own tying it to their day and readiness. Light week, so go [1]."
    assert without_echo(echoed, orders) == "Light week, so go [1]."
    assert instructions('{"say": "Oura doesn\'t have it.", "reason": "Ask which day."}') == ["Ask which day."]
    assert instructions('{"workouts": [{"status": "today, still going"}], "detail": "6.6 h in bed"}') == []


def test_facts_the_coach_restates_pass_the_echo_guard():
    from core.reply_rules import instructions, without_echo
    from modules.training.tools import RING_ONLY
    results = ['{"saved": true, "entry": {"text": "2 eggs, toast and chai", "time": "8:10am", "kcal": 320}}',
               '"Plan sent to them as is: easy_run, 40 min, 7:20-8:00am."',
               '{"done": [], "say": "Oura doesn\'t have it. ' + RING_ONLY + ' Want me to add 49 min of HIIT at 10:37am?", '
               '"sent_as_is": true}']
    orders = [s for r in results for s in instructions(r)]
    for line in ("Logged 2 eggs, toast and chai at 8:10am.",
                 "Your easy run is 40 min, 7:20-8:00am, so it fits before work.",
                 RING_ONLY):
        assert without_echo(line, orders) == line, line


def test_after_a_log_no_question_or_offer():
    from core.reply_rules import TurnFacts
    logged = TurnFacts(after_log=True)
    offer = ("Logged coffee at 8 and banana at 10:30. Those estimates look high — want me to correct either one now?")
    assert any("after logging" in p for p in violations(offer, {"log_food"}, facts=logged))
    assert enforce(offer, {"log_food"}, facts=logged) == "Logged coffee at 8 and banana at 10:30."
    assert not violations("Logged coffee at 8 and banana at 10:30.", {"log_food"}, facts=logged)
    assert not violations(offer, {"log_food"})                             # without a log, a question is fine


def test_more_carbs_next_to_a_plan_that_says_none_is_a_violation():
    from core.reply_rules import TurnFacts
    drift = ("Readiness is 75 and you had a light week, so boost carbs on Friday and stick to a familiar, "
             "easy-to-digest pre-run meal plus your practiced fluid plan on Saturday to top up glycogen [1].")
    assert any("no extra carbs" in p for p in violations(drift, set(), facts=TurnFacts(carb_load=False)))
    assert not any("no extra carbs" in p for p in violations(drift, set(), facts=TurnFacts(carb_load=True)))
    fine = "Readiness is 75 and the week was light, so your usual meals will do [1]."
    assert not violations(fine, set(), facts=TurnFacts(carb_load=False))
