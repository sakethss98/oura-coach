"""Citations for knowledge answers, built by code.

search_gssi numbers its results across the turn ([1], [2], ...). The coach writes the marker after a claim that
GSSI makes in words ("A low dose works well [1]."); its own advice needs no marker. Code checks every marker
(`check_markers`): the claim before it must be supported by the passages of that result that the coach was
given (key terms in them and embedding similarity); an unsupported marker is removed and the sentence stays as
the coach's own words. Amounts are cited by code: a code-built plan (fueling_plan) names the article behind
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
import math
import re
from functools import lru_cache

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


# --- is the claim before a marker in the passages the coach was given? --------------------------------------
# A claim is supported when every part of it that states something (a part with key terms that isn't about
# their own day) is said by one sentence of a passage of n: most of its key terms are in that sentence and the
# sentence is close to it in meaning (embeddings). Tuned for precision on 108 judge-labeled claims from the live
# M4-11..13 runs (tests/test_citation_check.py): of the markers kept, 16 of 17 were ones the judge accepts; about
# two thirds of the markers the judge accepts are removed too, which only leaves those sentences as the coach's
# own words (the Amounts line still names the articles behind the numbers).
SIMILARITY_MIN = 0.68            # cosine similarity (text-embedding-3-small) with the sentence
TERMS_MIN = 0.55                 # share of the part's key terms found in that same sentence
WINDOW_SENTENCES = 1             # sentences per window (2 or 3 kept more bad markers)
# Where a claim splits into parts: "Sodium is lost in sweat, and drinks help" checks both halves (a bare "and"
# doesn't split: "fluid retention and plasma volume" is one part).
CLAUSE_SPLIT = re.compile(r"\s*(?:;|:|\s[—–-]\s|—|,\s+(?:so|and|but|which|while|since|because)\s+|\s+so\s+)\s*")
MIN_PART_TERMS = 2               # a shorter part ("and performance") is checked with the part before it
# Who says it, not what is said ("GSSI notes that ...", "research shows ..."): left out of the claim.
ATTRIBUTION = re.compile(r"^(according to (gssi|the review|research),?|(the )?gssi(['’]s)?( review| guidance)?|"
                         r"research|the review|studies)\s*(says|notes|finds|shows|highlights|recommends|advises|"
                         r"suggests)?\s*(that\s+)?", re.I)
# A part about their own day ("Readiness is 75 and your week was light"): the coach's words, never checked.
ABOUT_THEM = re.compile(r"\b(readiness|your (week|plan|day|training|run)|the week|this week|the plan|you had|"
                        r"follow)\b", re.I)
STOP = frozenset("""a an the and or but so of to in on at for with by from as is are was were be been being it its this
that these those there here than then very just also even only can could may might should would will shall must do
does did not no nor if when while which who whom what how why your you yours i we our they them their he she his her
usually typically generally often mostly mainly largely likely rather instead about around approx approximately
roughly per more less most much many some any each every all both such into over under up down out off again
help helps helping need needs needed don't doesn't isn't aren't won't gssi review research shows notes says finds
highlights recommends advises suggests according guidance study studies""".split())


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s", "ly", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word


def key_terms(text: str) -> list[str]:
    """The words that carry a claim: no stopwords, stemmed ("Electrolytes matter" -> ["electrolyt", "matter"]);
    numbers count ("30–60 minutes" -> 30, 60, minut)."""
    words = re.findall(r"\d+(?:\.\d+)?|[a-z][a-z'’]+", text.lower().replace("‑", "-"))
    return [_stem(w) for w in words if w not in STOP and not (w.isalpha() and len(w) < 3)]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """texts -> vectors with the `embeddings` job (tests replace this)."""
    from core import models
    return models.embeddings().embed_documents(texts)


def claim_parts(claim: str) -> list[str]:
    """The parts of a claim that state something, without who says it and without their own day."""
    parts = []
    for part in CLAUSE_SPLIT.split(MARKER.sub("", claim)):
        part = ATTRIBUTION.sub("", part.strip(" ,.;—–-")).strip()
        if not part or not key_terms(part):
            continue
        if parts and len(key_terms(part)) < MIN_PART_TERMS:
            parts[-1] = f"{parts[-1]} and {part}"
        else:
            parts.append(part)
    return [p for p in parts if not ABOUT_THEM.search(p)]


def windows(passages: list[str]) -> list[str]:
    """Each passage as windows of WINDOW_SENTENCES sentences, overlapping (a short passage is one window)."""
    out = []
    for passage in passages:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", passage) if s.strip()]
        out += [" ".join(sentences[i:i + WINDOW_SENTENCES])
                for i in range(max(1, len(sentences) - WINDOW_SENTENCES + 1))]
    return out


def claim_checks(claim: str, passages: list[str], embed=None) -> list[dict]:
    """One check per part of the claim: {clause, terms, missing, similarity, supported}, from its best sentence
    (most key terms, then the closest). No part to check, or no passage, = not supported."""
    parts, spans = claim_parts(claim), windows(passages)
    if not parts or not spans:
        return [{"clause": claim.strip(), "terms": 0.0, "missing": [], "similarity": 0.0, "supported": False}]
    vectors = (embed or embed_texts)(parts + spans)
    span_terms = [set(key_terms(w)) for w in spans]
    checks = []
    for i, part in enumerate(parts):
        terms = list(dict.fromkeys(key_terms(part)))
        scored = []
        for j, known in enumerate(span_terms):
            missing = [t for t in terms if t not in known]
            scored.append((1 - len(missing) / len(terms), _cosine(vectors[i], vectors[len(parts) + j]), missing))
        supported = [x for x in scored if x[0] >= TERMS_MIN and x[1] >= SIMILARITY_MIN]
        share, similarity, missing = max(supported or scored, key=lambda x: (x[0], x[1]))
        checks.append({"clause": part, "terms": round(share, 2), "missing": missing,
                       "similarity": round(similarity, 3), "supported": bool(supported)})
    return checks


def check_markers(text: str, sources: list[dict], embed=None) -> tuple[str, list[dict]]:
    """The text with every unsupported [n] removed (the sentence stays, as the coach's own words), and what was
    checked [{n, claim, supported, checks}]. The claim of a marker = its sentence up to the marker (from the
    previous marker in the same sentence). If the check can't run, no marker is kept: nothing unverified is
    credited to GSSI."""
    numbers = by_number(sources)
    decisions, out = [], []
    for sentence in re.split(r"((?<=[.!?])\s+|\n)", text):         # the separators are kept, as they are
        start, kept = 0, []
        for found in MARKER.finditer(sentence):
            n = int(found.group(1))
            claim = sentence[start:found.start()]
            start = found.end()
            if n not in numbers:
                kept.append((found, True))                      # finish() drops a marker no result has
                continue
            passages = [s["passage"] for s in sources if s["n"] == n and s.get("passage")]
            try:
                checks = claim_checks(claim, passages, embed)
            except Exception as e:                              # no embeddings: the marker can't be checked
                checks = [{"clause": claim, "error": type(e).__name__, "supported": False}]
            supported = all(c["supported"] for c in checks)
            decisions.append({"n": n, "claim": claim.strip(), "supported": supported, "checks": checks})
            kept.append((found, supported))
        for found, keep in reversed(kept):
            if not keep:
                sentence = sentence[:found.start()] + sentence[found.end():]
        out.append(sentence)
    return "".join(out), decisions


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
