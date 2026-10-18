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
