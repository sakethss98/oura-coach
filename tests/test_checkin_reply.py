from chat import parse_checkin_reply


def test_two_numbers():
    assert parse_checkin_reply("4 2") == {"energy": 4, "soreness": 2, "note": None}


def test_numbers_with_note():
    assert parse_checkin_reply("4 2 slept badly") == {"energy": 4, "soreness": 2, "note": "slept badly"}


def test_labeled_values_keep_the_note():
    assert parse_checkin_reply("energy 4, soreness 2, legs heavy") == {
        "energy": 4, "soreness": 2, "note": "legs heavy"}


def test_out_of_five_format():
    assert parse_checkin_reply("4/5, 2/5") == {"energy": 4, "soreness": 2, "note": None}
    assert parse_checkin_reply("4/5 2/5") == {"energy": 4, "soreness": 2, "note": None}


def test_skip():
    assert parse_checkin_reply("skip") == {"energy": None, "soreness": None, "note": None}


def test_food_with_numbers_is_not_a_checkin():
    assert parse_checkin_reply("had 3 eggs and 2 toast") is None
    assert parse_checkin_reply("3 eggs and toast") is None


def test_out_of_range_is_not_a_checkin():
    assert parse_checkin_reply("7 2") is None
