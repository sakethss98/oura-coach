from baseline import compare, compute_baseline, metric_status, readiness_band


def day(readiness=None, hrv=None, rhr=None, sleep=None):
    return {"readiness_score": readiness, "hrv_ms": hrv, "resting_hr": rhr, "sleep_score": sleep}


def test_average_skips_missing_days():
    history = [day(readiness=70), day(readiness=None), day(readiness=80)]
    result = compare(day(readiness=75), history)["readiness_score"]
    assert result["avg_7d"] == 75.0
    assert result["days_in_avg"] == 2
    assert result["delta"] == 0
    assert result["status"] == "normal"


def test_point_threshold_boundaries():
    # readiness uses +/-5 points: exactly 5 away is still normal
    assert metric_status(80, 75, "points", 5) == "normal"
    assert metric_status(81, 75, "points", 5) == "above"
    assert metric_status(70, 75, "points", 5) == "normal"
    assert metric_status(69, 75, "points", 5) == "below"


def test_percent_threshold_for_hrv():
    history = [day(hrv=40)] * 7
    assert compare(day(hrv=45), history)["hrv_ms"]["status"] == "above"   # +12.5%
    assert compare(day(hrv=37), history)["hrv_ms"]["status"] == "normal"  # -7.5%
    assert compare(day(hrv=35), history)["hrv_ms"]["status"] == "below"   # -12.5%


def test_resting_hr_threshold():
    history = [day(rhr=55)] * 7
    assert compare(day(rhr=59), history)["resting_hr"]["status"] == "above"
    assert compare(day(rhr=53), history)["resting_hr"]["status"] == "normal"


def test_missing_today_or_history():
    assert compare(day(), [day(sleep=80)])["sleep_score"]["status"] == "missing"
    assert compare(day(sleep=80), [day()])["sleep_score"]["status"] == "missing"


def test_compute_baseline_returns_comparison_and_band():
    result = compute_baseline(day(readiness=80, hrv=50, rhr=55, sleep=80), [day(80, 50, 55, 80)] * 7, 70)
    assert set(result) == {"comparison", "band"}
    assert result["band"]["band"] == "push"


def statuses(readiness="normal", hrv="normal", rhr="normal", sleep="normal", readiness_value=80):
    """A comparison dict with the given statuses (only the fields readiness_band reads)."""
    return {
        "readiness_score": {"today": readiness_value, "status": readiness},
        "hrv_ms": {"today": 50, "status": hrv},
        "resting_hr": {"today": 55, "status": rhr},
        "sleep_score": {"today": 80, "status": sleep},
    }


def test_band_push_when_readiness_and_hrv_ok_and_nothing_worse():
    assert readiness_band(statuses(), 70)["band"] == "push"
    # lower resting HR is good, so "below" still allows push
    assert readiness_band(statuses(hrv="above", rhr="below"), 70)["band"] == "push"


def test_band_maintain_when_mixed():
    assert readiness_band(statuses(sleep="below"), 70)["band"] == "maintain"
    assert readiness_band(statuses(rhr="above"), 70)["band"] == "maintain"


def test_band_maintain_when_readiness_or_hrv_missing():
    assert readiness_band(statuses(hrv="missing"), 70)["band"] == "maintain"


def test_band_recover_when_readiness_below_floor():
    band = readiness_band(statuses(readiness_value=65), 70)
    assert band["band"] == "recover"
    assert "65" in band["reason"]


def test_band_recover_when_two_metrics_worse():
    assert readiness_band(statuses(hrv="below", rhr="above"), 70)["band"] == "recover"
    assert readiness_band(statuses(readiness="below", sleep="below"), 70)["band"] == "recover"


# --- effective band: the check-in lowers (never raises) the Oura band ----------

from baseline import effective_band  # noqa: E402

CHECKIN_RULES = {"recover_if": {"energy_at_most": 2, "soreness_at_least": 4},
                 "down_one_if": {"energy_at_most": 3, "soreness_at_least": 3}}


def oura(level):
    return {"band": level, "reason": "r", "plain": "p"}


def effective(level, energy=None, soreness=None):
    return effective_band(oura(level), {"energy": energy, "soreness": soreness}, CHECKIN_RULES)


def test_low_energy_or_high_soreness_means_recover():
    assert effective("push", energy=2, soreness=1)["band"] == "recover"
    assert effective("push", energy=5, soreness=4)["band"] == "recover"
    assert effective("push", energy=2)["plain"] == "You said energy 2, so today is a recovery day."


def test_middling_checkin_drops_one_level():
    assert effective("push", energy=3, soreness=1)["band"] == "maintain"
    assert effective("maintain", energy=4, soreness=3)["band"] == "recover"
    lowered = effective("push", energy=3, soreness=3)
    assert lowered["changed_by_checkin"] and lowered["plain"] == (
        "You said energy 3 and soreness 3, so no hard session today.")


def test_good_or_skipped_checkin_changes_nothing():
    for result in (effective("push", 4, 2), effective("push"), effective_band(oura("push"), None, CHECKIN_RULES)):
        assert result["band"] == "push" and not result["changed_by_checkin"] and result["plain"] == "p"


def test_checkin_never_raises_the_band():
    assert effective("recover", energy=5, soreness=1)["band"] == "recover"
    assert effective("recover", energy=3)["band"] == "recover"
