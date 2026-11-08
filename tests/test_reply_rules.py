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
