"""Citations for knowledge answers, built by code.

search_gssi numbers its results across the turn ([1], [2], ...). The coach writes the marker after the claim it
supports ("A low dose works well [1]."); markers are for what GSSI says in words. Amounts are cited by code: a
code-built plan (fueling_plan) names the article behind each of its ranges. `finish` then makes the reply
consistent: a pasted source url becomes its marker, a marker no result has is dropped, one source line per cited
result is appended, then one per article behind the code's amounts (the top result only when neither exists):

    Source: GSSI – Caffeine and Exercise Performance: An Update <https://www.gssiweb.org/en/...>
    Amounts: GSSI – Dietary Carbohydrate And Endurance Athletes <https://www.gssiweb.org/en/...>

That plain form is what logs, tests and the terminal see. `to_html` is the Telegram form: every character is
escaped, and only a source line whose url is in docs/gssi_sources.txt becomes a link
(`Source: <a href="...">GSSI – Title</a>`); Telegram sends it in HTML mode with link previews off.
"""
import html
import re
from functools import lru_cache

from core.config import ROOT

MARKER = re.compile(r"\s?\[(\d{1,2})\]")
SOURCE_LINE = re.compile(r"^(Source(?: \[\d{1,2}\])?|Amounts): GSSI – (.+) <(https://\S+)>$")


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


def has_marker(text: str, sources: list[dict]) -> bool:
    return any(int(n) in by_number(sources) for n in MARKER.findall(text))


def mark_urls(text: str, sources: list[dict]) -> str:
    """A pasted source url becomes its marker ("... start. https://..." -> "... start [3].")."""
    for s in sources:
        text = re.sub(rf"\s*[(<]?{re.escape(s['url'])}/?[)>]?", f" [{s['n']}]", text)
    return re.sub(r"([.!?;:])((?: \[\d{1,2}\])+)", r"\2\1", text)


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
    if not cited and not code_sources:
        cited = [sources[0]["n"]]
    numbered = len(cited) > 1 or bool(code_sources)
    lines = [f"Source{f' [{n}]' if numbered else ''}: GSSI – {numbers[n]['title']} <{numbers[n]['url']}>"
             for n in cited]
    shown = {numbers[n]["url"] for n in cited}
    lines += [f"Amounts: GSSI – {s['title']} <{s['url']}>" for s in code_sources if s["url"] not in shown]
    return text.rstrip() + "\n" + "\n".join(lines)


def to_html(text: str) -> str:
    """Telegram HTML: everything escaped; a source line with a known url becomes a link."""
    out = []
    for line in text.split("\n"):
        found = SOURCE_LINE.match(line)
        if found and found.group(3) in known_urls():
            label, title, url = found.groups()
            out.append(f'{html.escape(label)}: <a href="{html.escape(url, quote=True)}">GSSI – '
                       f"{html.escape(title)}</a>")
        else:
            out.append(html.escape(line, quote=False))
    return "\n".join(out)
