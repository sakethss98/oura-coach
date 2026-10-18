"""Reading the check-in answer: code for "4 2" / "skip", one LLM call for anything else (stubbed)."""
import pytest

from modules.training.checkin import RANGE_ERROR, CheckinError, parse
from tests.conftest import parsed_checkin


@pytest.mark.parametrize("answer, expected", [("4 2", (4, 2)), ("5,0", (5, 0)), ("3/1", (3, 1)), (" 2 5 ", (2, 5)),
                                              ("skip", (None, None)), ("Skip!", (None, None)), ("", (None, None))])
def test_bare_numbers_and_skip_are_read_by_code(fake_llm, answer, expected):
    result = parse(answer)
    assert (result["energy"], result["soreness"]) == expected
    assert result["request"] is None
    assert fake_llm.calls == []


def test_soreness_zero_is_valid(fake_llm):
    assert parse("5 0")["soreness"] == 0


def test_out_of_range_numbers_ask_again(fake_llm):
    with pytest.raises(CheckinError, match=RANGE_ERROR):
        parse("6 2")
    fake_llm.checkin = parsed_checkin(4, 7)
    with pytest.raises(CheckinError):
        parse("4 7 legs ok")


def test_words_go_to_one_llm_call_with_the_request_in_order(fake_llm):
    fake_llm.checkin = parsed_checkin(5, 0, blocks=[("easy_run", None), ("strength", 30)], feeling="fresh",
                                      constraints=["only 60 min"])
    result = parse("5 0 give me run + strength")
    assert fake_llm.kinds() == ["checkin"]
    assert result == {"energy": 5, "soreness": 0, "note": "fresh", "constraints": ["only 60 min"],
                      "request": [{"workout_type": "easy_run", "duration_min": None},
                                  {"workout_type": "strength", "duration_min": 30}]}


def test_a_saved_request_is_read_even_with_a_bare_answer(fake_llm):
    fake_llm.checkin = parsed_checkin(4, 2, blocks=[("HIIT", None)])
    result = parse("4 2", request_text="want HIIT")
    assert result["request"] == [{"workout_type": "HIIT", "duration_min": None}]
    assert "want HIIT" in fake_llm.calls[-1][1] and "4 2" in fake_llm.calls[-1][1]
