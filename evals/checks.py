"""The rubric checks code can answer (`by: code` in a skill's rubric, named by `id`), on one graded turn.

Each check returns (answer, reason): answer is "yes", "no" or "n/a" (the check's condition doesn't apply, e.g. no
[n] marker to check). Checks about wording look only at the coach's own words: the reply minus the text tools
built and code sent as is, and minus the code's credit lines (Source / Amounts / Numbers).
"""
import json
import re
from dataclasses import dataclass, field

from core import reply_rules

CODE_LINE = re.compile(r"^(Source \[\d+\]:|Source:|Amounts:|Numbers:|Weight saved:|General guidance, not medical "
                       r"advice\.$|Lifts and body composition aren't measured yet)")
YES = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|sounds good|do it|set it up|let'?s do it|go ahead)\b", re.I)
NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Turn:
    """One graded message: what they wrote, what the coach did and said."""
    case: dict
    message: str                       # the graded message
    earlier: list[tuple[str, str]]     # (who, text) before it in this case: ("you" | "coach", text)
    reply: str
    tool_results: list[dict]           # this turn's tool messages: {name, content, artifact}
    categories: list[str] = field(default_factory=list)   # what the classifier picked
    decline: str | None = None         # the off_topic skill's fixed line

    @property
    def tools(self) -> list[str]:
        return [r["name"] for r in self.tool_results]

    def built_texts(self) -> list[str]:
        """Text tools built and code sent as is (verbatim), plus the code's extra lines (USDA source)."""
        out = []
        for r in self.tool_results:
            art = r.get("artifact")
            if isinstance(art, dict):
                out += [art[k] for k in ("verbatim", "source_line") if isinstance(art.get(k), str)]
        return out

    def own_words(self) -> str:
        text = self.reply
        for built in sorted(self.built_texts(), key=len, reverse=True):
            text = text.replace(built, "\n")
        lines = [line for line in text.splitlines() if line.strip() and not CODE_LINE.match(line.strip())]
        return "\n".join(lines).strip()

    def tool_text(self) -> str:
        return json.dumps([[r.get("content"), r.get("artifact")] for r in self.tool_results], default=str)

    def sources(self) -> list[dict]:
        """search_gssi results this turn, numbered as the reply cites them."""
        return [s for r in self.tool_results if r["name"] == "search_gssi" and isinstance(r.get("artifact"), dict)
                for s in r["artifact"].get("sources", [])]


def numbers(text: str) -> list[float]:
    text = re.sub(r"\[\d+\]", " ", text)                     # citation markers aren't numbers
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)            # 2,218 = 2218
    return [float(n) for n in NUMBER.findall(text)]


def _known(x: float, allowed: set[float]) -> bool:
    if x in allowed or (x <= 12 and x + 12 in allowed):      # 1pm = 13:00
        return True
    return any(round(a) == x or round(a, 1) == x for a in allowed)


# --- the checks ---------------------------------------------------------------------------------------------

def unknown_numbers(t: Turn) -> list[float]:
    """Numbers in its own words that are in no tool result and not in their messages."""
    their = " ".join([t.message] + [text for who, text in t.earlier if who == "you"])
    allowed = set(numbers(t.tool_text() + " " + their))
    return sorted({x for x in numbers(t.own_words()) if not _known(x, allowed)})


def numbers_from_tools(t: Turn, **_) -> tuple[str, str]:
    """Code finds the numbers no tool gave; runner.grade then asks the judge only whether those are general advice
    (a typical amount, a duration to try) rather than claims about their data."""
    own = numbers(t.own_words())
    if not own:
        return "n/a", "no number in its own words"
    unknown = unknown_numbers(t)
    if unknown:
        return "no", "not in any tool result or their message: " + ", ".join(f"{x:g}" for x in unknown)
    return "yes", f"{len(own)} number(s), all from tools or their message"


def tools_called(t: Turn, all: list | None = None, any: list | None = None, none: bool = False, **_):
    ran = set(t.tools)
    if none:
        return ("yes", "no tool ran") if not ran else ("no", f"ran {sorted(ran)}")
    if all is not None:
        missing = [name for name in all if name not in ran]
        return ("no", f"not called: {missing}") if missing else ("yes", f"called {all}")
    hit = [name for name in any or [] if name in ran]
    return ("yes", f"called {hit}") if hit else ("no", f"none of {any} ran (ran {sorted(ran)})")


def expected_tools(t: Turn, **_):
    expect = t.case.get("expect_tools")
    if not expect:
        return "n/a", "the case expects no particular tool"
    return tools_called(t, **expect)


def reply_rules_check(t: Turn, **_):
    own = t.own_words()
    if not own:
        return "n/a", "no words of its own"
    broken = reply_rules.violations(own, set(t.tools), None, t.sources())
    return ("no", "; ".join(broken)[:300]) if broken else ("yes", "no rule broken")


PLAN_TOOLS_WITH_AMOUNTS = {"fueling_plan", "save_body_weight"}      # core/agent.AMOUNT_TOOLS


def source_lines(t: Turn, **_):
    """Next to a code-built plan, no [n] at all (G-4); otherwise each [n] has its Source line, and a USDA lookup
    its Numbers line."""
    body = "\n".join(line for line in t.reply.splitlines() if not CODE_LINE.match(line.strip()))
    markers = sorted(set(re.findall(r"\[(\d+)\]", body)))
    beside_plan = any(r["name"] in PLAN_TOOLS_WITH_AMOUNTS and isinstance(r.get("artifact"), dict)
                      and r["artifact"].get("verbatim") for r in t.tool_results)
    if beside_plan and markers:
        return "no", f"[n] marker(s) {markers} next to a code-built plan"
    usda = any(isinstance(r.get("artifact"), dict) and r["artifact"].get("source_line") for r in t.tool_results) \
        or '"from_usda": true' in t.tool_text()
    if not markers and not usda:
        return "n/a", "no [n] marker and nothing from USDA"
    # citations.finish numbers the lines ("Source [2]: …") when there are several or an Amounts line; else "Source: …"
    missing = [n for n in markers if not re.search(rf"^Source(?: \[{n}\])?: .*https?://", t.reply, re.M)]
    if usda and "Numbers: USDA FoodData Central" not in t.reply:
        missing.append("the USDA FoodData Central line")
    return ("no", f"missing: {missing}") if missing else ("yes", "every marker and lookup has its line")


def no_ids(t: Turn, **_):
    found = re.search(r"\bids?\b\s*[:#]?\s*\d|#\d+|\(id\b", t.reply, re.I)
    return ("no", f"shows {found.group(0)!r}") if found else ("yes", "no ids")


def fixed_decline(t: Turn, **_):
    if t.decline is None:
        return "n/a", "no decline line"
    return ("yes", "the fixed line") if t.reply.strip() == t.decline.strip() else ("no", "not the fixed line")


def confirm_after_yes(t: Turn, **_):
    if "confirm_goal" not in t.tools:
        return "n/a", "no goal was set up this turn"
    asked = bool(t.earlier) and t.earlier[-1][0] == "coach" and "Set it up?" in t.earlier[-1][1]
    if asked and YES.match(t.message):
        return "yes", "set up after the plan and their yes"
    return "no", "set up without the plan shown and a yes right after it"


def medical_line(t: Turn, **_):
    """core/agent.MEDICAL closes every pain, injury or illness answer: routed to pain_injury, or their message names
    an illness, pain or injury (H-1)."""
    from core.agent import MEDICAL, MEDICAL_SKILL, mentions_illness
    if MEDICAL_SKILL not in t.categories and not mentions_illness(t.message):
        return "n/a", "not about pain, an injury or an illness"
    return ("yes", "the code's line is there") if MEDICAL in t.reply.splitlines() else ("no", "the line is missing")


def not_measured_line(t: Turn, **_):
    """A question about muscle, body composition or hybrid progress gets code's not-measured line, once (H-2)."""
    from core.agent import MEASURE_ASK, NOT_MEASURED
    if not MEASURE_ASK.search(t.message):
        return "n/a", "not a question about muscle, body composition or hybrid progress"
    if NOT_MEASURED not in t.reply:
        return "no", "the code's not-measured line is missing"
    return "yes", "the code's line is there"


def clear_call(t: Turn, **_):
    """"Should I have X now?": the reply opens with yes, no or wait, and with code's call when code made one (H-3)."""
    from core.agent import OPENS, SHOULD_HAVE
    if not SHOULD_HAVE.search(t.message):
        return "n/a", "not a should-I-have-it question"
    verdict = next((r["artifact"]["call"] for r in t.tool_results if r["name"] == "food_facts"
                    and isinstance(r.get("artifact"), dict) and r["artifact"].get("call")), None)
    first = t.reply.strip()
    opened = [call for call, pattern in OPENS.items() if pattern.match(first)]
    if not opened:
        return "no", f"opens with {first[:40]!r}, not yes, no or wait"
    if verdict and verdict["call"] not in opened:
        return "no", f"opens with {opened[0]}, code's call was {verdict['call']}"
    return "yes", f"opens with {opened[0]}" + (" (code's call)" if verdict else "")


def caffeine_for_you(t: Turn, **_):
    """A fueling plan with caffeine says the amount for them today (or why to skip it), not only the range (H-6)."""
    texts = [r["artifact"]["verbatim"] for r in t.tool_results if r["name"] in PLAN_TOOLS_WITH_AMOUNTS
             and isinstance(r.get("artifact"), dict) and "Caffeine" in (r["artifact"].get("verbatim") or "")]
    if not texts:
        return "n/a", "no caffeine in a fueling plan"
    if re.search(r"about \d+ mg for you|skip it today|skip more today", texts[-1]) and texts[-1] in t.reply:
        return "yes", "the amount for them, by code"
    return "no", "the caffeine line has only the range"


def fueling_foods(t: Turn, **_):
    """A long-run or race plan names real foods with their timing (the dinner before, a snack before) (H-7)."""
    texts = [r["artifact"]["verbatim"] for r in t.tool_results if r["name"] in PLAN_TOOLS_WITH_AMOUNTS
             and isinstance(r.get("artifact"), dict) and "🏃" in (r["artifact"].get("verbatim") or "")]
    if not texts or not re.search(r"^(\w{3}|The day before)( dinner)?:|before:", texts[-1], re.M):
        return "n/a", "no plan for the day before or before the run"
    named = re.search(r"dinner: \S|at dinner, \S|min before: \S|\(.+ works\)", texts[-1])
    return ("yes", "real foods with their timing") if named else ("no", "no food named in the plan")


def brainstorm_options(t: Turn, **_):
    """Meal ideas are 2-3 one-line options with rough protein, then the pick; no recipe before a pick (H-8)."""
    from modules.nutrition.messages import PICK_ONE
    texts = [r["artifact"]["verbatim"] for r in t.tool_results if r["name"] == "suggest_meal"
             and isinstance(r.get("artifact"), dict) and r["artifact"].get("verbatim")]
    if not texts:
        return "n/a", "no meal ideas this turn"
    lines = texts[-1].splitlines()
    options = [line for line in lines if re.match(r"^\d\. ", line)]
    ok = 2 <= len(options) <= 3 and all("g protein" in line for line in options) and lines[-1] == PICK_ONE \
        and not re.search(r" \d+ g \(", texts[-1])
    return ("yes", f"{len(options)} one-line options, then the pick") if ok else ("no", f"{len(options)} options, "
                                                                                     "or portions before a pick")


CHECKS = {"numbers_from_tools": numbers_from_tools, "tools_called": tools_called, "expected_tools": expected_tools,
          "reply_rules": reply_rules_check, "source_lines": source_lines, "no_ids": no_ids,
          "fixed_decline": fixed_decline, "confirm_after_yes": confirm_after_yes,
          "medical_line": medical_line, "not_measured_line": not_measured_line, "clear_call": clear_call,
          "caffeine_for_you": caffeine_for_you, "fueling_foods": fueling_foods, "brainstorm_options": brainstorm_options}
ARGS = ("all", "any", "none")


def run(check: dict, t: Turn) -> tuple[str, str]:
    return CHECKS[check["id"]](t, **{k: check[k] for k in ARGS if k in check})
