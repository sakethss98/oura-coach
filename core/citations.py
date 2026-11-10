"""Citations for knowledge answers, built by code.

search_gssi numbers its results across the turn ([1], [2], ...). The coach writes the marker after a claim that
GSSI makes in words ("A low dose works well [1]."); its own advice needs no marker. Code checks every marker
(`check_markers`): one yes/no question per marker to the classifier model, "does the passage state this
claim?", about the passages of that result the coach was given; an unsupported marker is removed and the
sentence stays as the coach's own words. The coach never names GSSI in its own prose
(reply_rules.without_credit): the credit is only in the lines code builds. Amounts are cited by code: a code-built plan (fueling_plan) names the article behind
each of its ranges. `finish` then makes the reply consistent: a pasted source url becomes its marker, a marker no
result has is dropped, one source line per cited result is appended, then one line with the articles behind
the code's amounts (no line at all when neither exists):

    Source: GSSI – Caffeine and Exercise Performance: An Update <https://www.gssiweb.org/en/...>
    Amounts: GSSI – Dietary Carbohydrate And Endurance Athletes <https://...> · Fluid Intake Strategies <https://...>

That plain form is what logs, tests and the terminal see. `to_html` is the Telegram form: every character is
escaped, and only a url in docs/gssi_sources.txt becomes a link (`Source: <a href="...">GSSI – Title</a>`,
`Amounts: GSSI – <a href="...">A</a> · <a href="...">B</a>`); Telegram sends it in HTML mode with link
previews off.
"""
import html
import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from pydantic import BaseModel, Field

from core.config import ROOT

MARKER = re.compile(r"\s?\[(\d{1,2})\]")
SOURCE_LINE = re.compile(r"^(Source(?: \[\d{1,2}\])?): GSSI – (.+) <(https://\S+)>$")
AMOUNTS = "Amounts: GSSI – "
AMOUNTS_JOIN = " · "
LINKED = re.compile(r"^(.+) <(https://\S+)>$")          # "Title <url>"


@lru_cache(maxsize=1)
def known_urls() -> frozenset[str]:
    """The canonical article urls (docs/gssi_sources.txt, normalized): the only links ever made clickable."""
    from mcp_servers.gssi.ingest import normalize, read_sources
    return frozenset(normalize(u) for u in read_sources(ROOT / "docs" / "gssi_sources.txt"))


def numbered(earlier: list[dict], found: list[dict]) -> list[dict]:
    """This search's results with turn-wide numbers: an article already numbered this turn keeps its number."""
    numbers = {s["url"]: s["n"] for s in earlier}
    out = []
    for source in found:
        if source["url"] not in numbers:
            numbers[source["url"]] = max(numbers.values(), default=0) + 1
        out.append({"n": numbers[source["url"]], **source})
    return out


def by_number(sources: list[dict]) -> dict[int, dict]:
    return {s["n"]: s for s in sources}


def mark_urls(text: str, sources: list[dict]) -> str:
    """A pasted source url becomes its marker ("... start. https://..." -> "... start [3].")."""
    for s in sources:
        text = re.sub(rf"\s*[(<]?{re.escape(s['url'])}/?[)>]?", f" [{s['n']}]", text)
    return re.sub(r"([.!?;:])((?: \[\d{1,2}\])+)", r"\2\1", text)


# --- does the passage state the claim before a marker? -----------------------------------------------------
# One yes/no question per marker (job citation_check: the classifier's model with low reasoning,
# prompts/citation_check.md), all markers of a reply at once. Tuned on 108 claims from the live M4-11..13 runs,
# labeled by the gpt-5 citation judge (3 votes, majority): of the markers kept, 91% are ones the judge accepts,
# and 81-83% of the markers the judge accepts are kept (3 runs). It adds one round of calls, about 3.5 s.
CHECK_WORKERS = 8


class Statement(BaseModel):
    states: bool = Field(description="true if the passages state the claim (or it clearly follows from them)")
    reason: str = Field(description="one short sentence")


def passage_states(claim: str, passages: list[str], user_id: int | None = None) -> dict:
    """{"states", "reason"}: do the passages state this claim? One call (job citation_check), recorded in `runs`.
    Tests replace this."""
    from core.llm import load_prompt, structured_call
    text = "\n\n".join(f"Passage {i}: {p}" for i, p in enumerate(passages, 1))
    answer = structured_call("citation_check", Statement, load_prompt("citation_check"),
                             f"{text}\n\nThe claim: {claim.strip()}", user_id, job="citation_check")
    return answer.model_dump()


def check_markers(text: str, sources: list[dict], user_id: int | None = None) -> tuple[str, list[dict]]:
    """The text with every unsupported [n] removed (the sentence stays, as the coach's own words), and what was
    checked [{n, claim, supported, reason}]. The claim of a marker = its sentence up to the marker (from the
    previous marker in the same sentence). All checks run at once. If a check fails, its marker goes: nothing
    unverified is credited to GSSI."""
    numbers = by_number(sources)
    pieces = re.split(r"((?<=[.!?])\s+|\n)", text)               # sentences and their separators, as they are
    todo = []                                                       # (piece, marker match, n, claim)
    for i, sentence in enumerate(pieces):
        start = 0
        for found in MARKER.finditer(sentence):
            n = int(found.group(1))
            if n in numbers:                                        # finish() drops a marker no result has
                todo.append((i, found, n, sentence[start:found.start()]))
            start = found.end()

    def check(item):
        _, _, n, claim = item
        passages = [s["passage"] for s in sources if s["n"] == n and s.get("passage")]
        if not passages or not claim.strip():
            return {"states": False, "reason": "nothing to check against"}
        try:
            return passage_states(claim, passages, user_id)
        except Exception as e:                                      # no answer: the marker can't be checked
            return {"states": False, "reason": f"check failed ({type(e).__name__})"}

    with ThreadPoolExecutor(max_workers=min(CHECK_WORKERS, len(todo) or 1)) as pool:
        verdicts = list(pool.map(check, todo))
    decisions = [{"n": n, "claim": claim.strip(), "supported": v["states"], "reason": v["reason"]}
                 for (_, _, n, claim), v in zip(todo, verdicts)]
    for (i, found, _, _), verdict in reversed(list(zip(todo, verdicts))):   # from the end: offsets stay right
        if not verdict["states"]:
            pieces[i] = pieces[i][:found.start()] + pieces[i][found.end():]
    return "".join(pieces), decisions


def finish(text: str, sources: list[dict], code_sources: list[dict] = ()) -> str:
    """The reply with clean markers and its source lines (see the module doc). Unchanged without any source.
    sources: search_gssi results ({n, title, url}); code_sources: the articles behind code-built amounts."""
    if not sources and not code_sources:
        return text
    numbers = by_number(sources)
    text = mark_urls(text, sources)
    text = MARKER.sub(lambda m: m.group(0) if int(m.group(1)) in numbers else "", text)
    text = re.sub(r"(\[\d{1,2}\])(?:\s*\1)+", r"\1", text)          # [1] [1] -> [1]
    text = re.sub(r"([.!?;:])((?: \[\d{1,2}\])+)", r"\2\1", text)       # "start. [3]" -> "start [3]."
    cited = list(dict.fromkeys(int(n) for n in MARKER.findall(text)))
    numbered = len(cited) > 1 or bool(code_sources)
    lines = [f"Source{f' [{n}]' if numbered else ''}: GSSI – {numbers[n]['title']} <{numbers[n]['url']}>"
             for n in cited]
    shown = {numbers[n]["url"] for n in cited}
    amounts = [s for s in code_sources if s["url"] not in shown]
    if amounts:
        lines.append(AMOUNTS + AMOUNTS_JOIN.join(f"{s['title']} <{s['url']}>" for s in amounts))
    return text.rstrip() + "".join(f"\n{line}" for line in lines)


def _link(title: str, url: str) -> str:
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(title)}</a>'


def to_html(text: str) -> str:
    """Telegram HTML: everything escaped; a source line with known urls becomes links."""
    out = []
    for line in text.split("\n"):
        found = SOURCE_LINE.match(line)
        items = [LINKED.match(item) for item in line[len(AMOUNTS):].split(AMOUNTS_JOIN)] \
            if line.startswith(AMOUNTS) else []
        if found and found.group(3) in known_urls():
            label, title, url = found.groups()
            out.append(f"{html.escape(label)}: {_link(f'GSSI – {title}', url)}")
        elif items and all(item and item.group(2) in known_urls() for item in items):
            out.append(f"{html.escape(AMOUNTS)}{AMOUNTS_JOIN.join(_link(*item.groups()) for item in items)}")
        else:
            out.append(html.escape(line, quote=False))
    return "\n".join(out)
