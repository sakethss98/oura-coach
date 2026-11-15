"""States a conversation starts from, for evals and scenario tests: run history in the Oura fixtures, an active
half-marathon goal, confirmed direction rules, food already logged, a second user. Fixtures' today is Wed Oct 7, 2026.
The race here (Mon Feb 15, 2027) is example data, the same as in the scenario tests."""
from datetime import date, datetime, time, timedelta

from core import db, users
from modules.goals import service
from today import fixtures_now

RACE = date(2027, 2, 15)
ANSWERS = {"days_per_week": 5, "minutes_per_session": 90, "longest_session_min": 150, "limits": "none",
           "equipment": "gym", "long_session_day": "sunday", "target_time": 150}


def at(hour: int, minute: int = 0) -> datetime:
    return fixtures_now(time(hour, minute))


def history(raw, today: date, runs: list[tuple[int, float, int]]) -> None:
    """Runs on past days, as Oura records them: [(days back, km, minutes < 60)]."""
    template = raw["workout"]["data"][0]
    for n, (back, km, minutes) in enumerate(runs):
        d = today - timedelta(days=back)
        raw["workout"]["data"].append({**template, "id": f"history-{n}", "activity": "running", "day": d.isoformat(),
                                       "intensity": "moderate", "start_datetime": f"{d}T07:00:00.000-05:00",
                                       "end_datetime": f"{d}T07:{minutes:02d}:00.000-05:00", "distance": km * 1000})


def novice_runs(minutes: tuple[int, int, int] = (39, 32, 33)) -> list[tuple[int, float, int]]:
    """Six weeks of three runs (6, 5 and 5 km in these minutes): about 16 km a week, the longest 6 km."""
    six, five, other = minutes
    return [r for w in range(6) for r in ((7 * w + 2, 6, six), (7 * w + 4, 5, five), (7 * w + 6, 5, other))]


def set_up(user, day: date, answers=ANSWERS, event=RACE) -> dict:
    """An active half-marathon plan made `day` (it starts the day after), without the chat."""
    goal = db.add_goal(user.id, "half marathon", "half_marathon", event, "setting_up")
    for key, value in answers.items():
        db.set_goal_answer(goal["id"], key, value, "user")
    now = at(9) - timedelta(days=(at(9).date() - day).days)
    service.refresh_data_answers(goal, service.library.load("half_marathon"),
                                 service.signals_for(user, True, day - timedelta(days=42), day, now), day)
    service.proposal(user, db.get_goal(goal["id"]), day, now, True)
    service.activate(user, db.get_goal(goal["id"]), day, now, True)
    assert db.get_goal(goal["id"])["status"] == "active"
    return db.get_goal(goal["id"])


def _food(user, text, hour, minute, protein, carbs, calories, fiber=4, kind="meal", day_back=0):
    when = at(hour, minute) - timedelta(days=day_back)
    db.add_food(user.id, text, {"entry_type": kind, "est_protein_g": protein, "est_carbs_g": carbs,
                                "est_calories": calories, "est_fiber_g": fiber, "est_caffeine_mg": 0,
                                "alcohol_drinks": 0, "confidence": "med"}, when)


def food_today(user, until: time) -> None:
    """Breakfast at 8:00, and lunch at 12:30 when the case is later than that."""
    _food(user, "2 eggs, toast and chai", 8, 0, 18, 40, 380)
    if until >= time(12, 30):
        _food(user, "dal, rice and spinach", 12, 30, 16, 70, 480, fiber=9)


WEEK = [  # (protein of the day's three entries) for the 6 days before today: one low day, one day not logged
    (20, 18, 25), (15, 20, 30), None, (8, 10, 12), (22, 25, 28), (18, 16, 30)]


def food_week(user) -> None:
    for back, day in enumerate(WEEK, start=1):
        if day is None:
            continue
        for (hour, text), protein in zip(((8, "poha and chai"), (13, "rajma chawal"), (20, "paneer roti sabzi")), day):
            _food(user, text, hour, 0, protein, 60, 420, day_back=back)


def apply(names: list[str], raw: dict, until: time):
    """Apply the named setups (in order) and return the user the case runs as."""
    user = users.default()
    today = at(9).date()
    for name in names:
        if name == "run_history":
            history(raw, today, novice_runs())
        elif name == "half_goal":
            history(raw, today, novice_runs())
            set_up(user, today)
        elif name == "direction":
            d = db.add_direction(user.id, "a runner who lifts", {"strength_sessions_per_week": 2})
            db.confirm_direction(d["id"], d["rules"])
        elif name == "food_today":
            food_today(user, until)
        elif name == "food_week":
            food_week(user)
        elif name.startswith("readiness_"):
            day = today.isoformat()
            next(r for r in raw["daily_readiness"]["data"] if r["day"] == day)["score"] = int(name.split("_")[1])
        elif name == "user2":
            user = users.get(db.add_user("200", "Sam"))
        else:
            raise ValueError(f"unknown setup {name!r}")
    return user


SETUPS = ("run_history", "half_goal", "direction", "food_today", "food_week", "readiness_<n>", "user2")
