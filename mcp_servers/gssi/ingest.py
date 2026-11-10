"""Turn the GSSI article list (docs/gssi_sources.txt) into the index: find each article's text, extract it,
chunk it and store it (mcp_servers/gssi/index.py). The CLI is scripts/ingest_gssi.py.

Where the text comes from, per URL:
1. A page or PDF the person saved from their browser into data/gssi_raw/ (gitignored). gssiweb.org sits
   behind a bot wall (robots.txt and articles answer 403), so this is the normal path. A saved file is
   matched to its URL by the page's canonical link, else by the article's name words in the file name/title.
2. Otherwise a polite fetch, except from a manual-only host (gssiweb.org: decided 2026-11-09, after its bot
   wall blocked curl): robots.txt checked first (403 or a bot-check page = not allowed), our own User-Agent,
   one URL at a time with a 3 s pause, redirects followed. A 401/403 from a host stops every further request to
   it for the run (the rest are reported as blocked). A fetched page is kept in data/gssi_raw/fetched/; the 22
   GSSI pages fetched on 2026-11-09 are kept there and reused.

URLs are normalized (lowercase path, /en/ prefix, no trailing slash), so /sports-science-exchange/Article/...
and /en/sports-science-exchange/article/... are the same article. A page that turns out to be a listing or
index page instead of an article is reported and not indexed. Re-runs skip articles whose text hasn't changed.
"""
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from mcp_servers.gssi import index

USER_AGENT = "oura-coach-ingest/1.0 (personal coaching project; github.com/sakethss98/oura-coach)"
# Hosts we never fetch from: their pages are saved by hand into data/gssi_raw/.
MANUAL_ONLY_HOSTS = {"www.gssiweb.org", "gssiweb.org"}
MANUAL_ONLY = "manual only: save the page from your browser into data/gssi_raw/"
# A bot-check page (what curl got from gssiweb.org: "Request unsuccessful. Incapsula incident ID ..."). Not the
# word Incapsula alone: every real gssiweb.org page loads an Incapsula monitoring script.
BOT_WALL = re.compile(r"Request unsuccessful|Incapsula incident|captcha|cf-chl|Attention Required", re.I)
PAUSE_S = 3
FETCH_TIMEOUT_S = 30
MIN_ARTICLE_WORDS = 500          # fewer extracted words = a listing page, a cookie wall or an error page
MATCH_SHARE = 0.6                # share of the article's name words a saved file must contain
STOPWORDS = {"the", "and", "of", "a", "an", "for", "to", "in", "on", "it", "is", "vs", "how", "does", "what",
             "with", "sse", "gssi", "en"}
CANONICAL = re.compile(r"""<link[^>]+rel=["']canonical["'][^>]*href=["']([^"']+)["']"""
                       r"""|<meta[^>]+property=["']og:url["'][^>]*content=["']([^"']+)["']""", re.I)
SSE_YEAR = re.compile(r"Sports Science Exchange\s*\((\d{4})\)")
TITLE_SUFFIX = re.compile(r"\s*[|–-]\s*(Gatorade Sports Science Institute|GSSI).*$", re.I)


# --- URLs ---------------------------------------------------------------------

def read_sources(path: Path) -> list[str]:
    lines = [line.strip() for line in path.read_text().splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def normalize(url: str) -> str:
    """https://www.gssiweb.org/en/sports-science-exchange/article/<slug>: lowercase path, /en/ prefix,
    no trailing slash, no query or fragment."""
    parts = urlsplit(url.strip())
    path = parts.path.lower().rstrip("/")
    if parts.netloc.lower().endswith("gssiweb.org") and not path.startswith("/en/"):
        path = "/en" + path
    return f"https://{parts.netloc.lower()}{path}"


def slug(url: str) -> str:
    return urlsplit(normalize(url)).path.rsplit("/", 1)[-1].strip("-.")


def name_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def is_article_url(url: str) -> bool:
    return "/sports-science-exchange/article/" in normalize(url)


# --- extracting text ----------------------------------------------------------

def _title_from_slug(url: str) -> str:
    return re.sub(r"^sse \d+ ", "", slug(url).replace("-", " ")).capitalize()


def extract_html(html: str) -> dict:
    """{"title", "text" (markdown, with headings), "year", "canonical"} of a saved or fetched page."""
    import trafilatura
    meta = trafilatura.extract_metadata(html)
    text = trafilatura.extract(html, output_format="markdown", include_tables=True) or ""
    link = CANONICAL.search(html)
    canonical = (link.group(1) or link.group(2)) if link else (meta.url if meta else None)
    year = SSE_YEAR.search(text)
    title = TITLE_SUFFIX.sub("", meta.title).strip() if meta and meta.title else None
    return {"title": title, "text": text, "canonical": canonical,
            "year": int(year.group(1)) if year else (int(meta.date[:4]) if meta and meta.date else None)}


def extract_pdf(path: Path) -> dict:
    """The same fields from a PDF (an SSE article is often a PDF). No canonical link inside a PDF."""
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    # pypdf gives one line per printed line: rejoin lines inside a paragraph, keep the blank lines and headings.
    text = re.sub(r"(?<![.:!?\n])\n(?!\n|[A-Z][A-Z ]{2,}\n)", " ", text)
    year = SSE_YEAR.search(text)
    title = (reader.metadata.title if reader.metadata and reader.metadata.title else None)
    return {"title": title, "text": text, "canonical": None, "year": int(year.group(1)) if year else None}


def extract(path: Path) -> dict:
    if path.suffix.lower() == ".pdf":
        return extract_pdf(path)
    return extract_html(path.read_text(errors="replace"))


def looks_like_listing(doc: dict) -> str | None:
    """Why this isn't an article (a listing or index page, an error page), or None when it is one."""
    words = len(doc["text"].split())
    if doc.get("canonical") and not is_article_url(doc["canonical"]):
        return f"a page at {normalize(doc['canonical'])} ({doc.get('title') or 'no title'}), not an article"
    if words < MIN_ARTICLE_WORDS:
        return f"only {words} words of text ({doc.get('title') or 'no title'}): a listing or error page, not an article"
    return None


# --- saved files --------------------------------------------------------------

def match_saved(files: list[Path], urls: list[str]) -> tuple[dict[str, Path], list[Path]]:
    """({normalized url: file}, files that match no URL). The canonical link decides; else the file name and
    title must contain most of the article's name words (the best-scoring URL wins)."""
    matched, unmatched = {}, []
    by_slug = {slug(u): normalize(u) for u in urls}
    for path in sorted(files):
        try:
            doc = extract(path)
        except Exception:
            unmatched.append(path)
            continue
        if doc["canonical"] and slug(doc["canonical"]) in by_slug:
            matched[by_slug[slug(doc["canonical"])]] = path
            continue
        have = name_words(f"{path.stem} {doc['title'] or ''} {doc['text'][:400]}")
        scores = {u: len(name_words(s.replace("-", " ")) & have) / max(len(name_words(s.replace("-", " "))), 1)
                  for s, u in by_slug.items()}
        best = max(scores, key=scores.get)
        if scores[best] >= MATCH_SHARE and best not in matched:
            matched[best] = path
        else:
            unmatched.append(path)
    return matched, unmatched


# --- fetching -----------------------------------------------------------------

class Fetcher:
    """One host's robots.txt, read once; then one URL at a time, PAUSE_S apart, with our User-Agent."""

    def __init__(self, client):
        self.client = client
        self.robots: dict[str, tuple[RobotFileParser | None, str]] = {}
        self.blocked: dict[str, str] = {}        # host -> why every further request to it is skipped this run
        self.last = 0.0

    def _wait(self) -> None:
        delay = PAUSE_S - (time.monotonic() - self.last)
        if delay > 0:
            time.sleep(delay)
        self.last = time.monotonic()

    def allowed(self, url: str) -> tuple[bool, str]:
        host = urlsplit(url).netloc
        if host in MANUAL_ONLY_HOSTS:
            return False, MANUAL_ONLY                          # no request at all
        if host in self.blocked:
            return False, self.blocked[host]
        if host not in self.robots:
            self._wait()
            try:
                resp = self.client.get(f"https://{host}/robots.txt")
            except Exception as e:
                self.robots[host] = (None, f"robots.txt could not be read ({type(e).__name__})")
            else:
                if resp.status_code in (401, 403) or BOT_WALL.search(resp.text[:5000]):
                    # The robots.txt standard: an unreadable robots.txt (forbidden) means nothing may be fetched.
                    # A bot-check page in its place says the same.
                    self.robots[host] = (None, f"robots.txt answered {resp.status_code}"
                                               f"{' with a bot check' if BOT_WALL.search(resp.text[:5000]) else ''}, "
                                               "so fetching is not allowed")
                elif resp.status_code >= 400 or resp.text.lstrip()[:1] == "<":
                    # None (404), or an HTML page in its place (gssiweb.org answers 200 with its "404" page):
                    # no rules, so everything may be fetched.
                    parser = RobotFileParser()
                    parser.parse([])
                    what = "answered with an HTML page" if resp.status_code < 400 else f"answered {resp.status_code}"
                    self.robots[host] = (parser, f"no robots.txt ({what}): no rules")
                else:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                    self.robots[host] = (parser, "robots.txt read")
        parser, note = self.robots[host]
        if parser is None:
            return False, note
        return (True, note) if parser.can_fetch(USER_AGENT, url) else (False, "disallowed by robots.txt")

    def fetch(self, url: str, save_dir: Path) -> tuple[Path | None, str]:
        """(saved file, note). Redirects are followed; the final URL is reported when it differs."""
        self._wait()
        try:
            resp = self.client.get(url)
        except Exception as e:
            return None, f"fetch failed ({type(e).__name__})"
        final = normalize(str(resp.url))
        moved = f", redirected to {final}" if final != normalize(url) else ""
        wall = bool(BOT_WALL.search(resp.text[:5000]))
        if resp.status_code in (401, 403) or wall:
            host = urlsplit(url).netloc
            self.blocked[host] = f"{host} answered {resp.status_code}{' (bot wall)' if wall else ''} earlier this run"
            return None, f"answered {resp.status_code}{' (bot wall)' if wall else ''}{moved}"
        if resp.status_code != 200:
            return None, f"answered {resp.status_code}{moved}"
        is_pdf = "pdf" in resp.headers.get("content-type", "")
        save_dir.mkdir(parents=True, exist_ok=True)
        path = save_dir / f"{slug(final)}.{'pdf' if is_pdf else 'html'}"
        path.write_bytes(resp.content)
        return path, f"fetched{moved}"


# --- the whole run ------------------------------------------------------------

def run(sources: list[str], raw_dir: Path, collection, embed, fetcher: Fetcher | None) -> list[dict]:
    """Index every source. Returns one report row per URL: url, status, chunks, title, year, note.
    status: indexed | unchanged | listing | blocked | failed | missing (no saved file, fetching off)."""
    urls = list(dict.fromkeys(normalize(u) for u in sources))
    saved = [p for p in raw_dir.glob("*") if p.suffix.lower() in {".html", ".htm", ".pdf"}] if raw_dir.exists() else []
    matched, unmatched = match_saved(saved, urls)
    report = []
    for url in urls:
        path, note = matched.get(url), "saved file"
        if path is None:
            fetched = raw_dir / "fetched"
            earlier = [p for p in fetched.glob(f"{slug(url)}.*")] if fetched.exists() else []
            if earlier:
                path, note = earlier[0], "fetched earlier"
            elif fetcher is None:
                report.append({"url": url, "status": "missing", "note": "no saved file in data/gssi_raw/"})
                continue
            else:
                ok, why = fetcher.allowed(url)
                if not ok:
                    report.append({"url": url, "status": "blocked", "note": f"{why}; no saved file in data/gssi_raw/"})
                    continue
                path, note = fetcher.fetch(url, fetched)
                if path is None:
                    status = "blocked" if urlsplit(url).netloc in fetcher.blocked else "failed"
                    report.append({"url": url, "status": status, "note": note})
                    continue
        doc = extract(path)
        listing = looks_like_listing(doc)
        if listing:
            report.append({"url": url, "status": "listing", "note": f"{path.name}: {listing}"})
            continue
        if doc["canonical"] and is_article_url(doc["canonical"]) and normalize(doc["canonical"]) != url:
            note += f"; the page's own address is {normalize(doc['canonical'])}"
        article = {"url": url, "title": doc["title"] or _title_from_slug(url), "year": doc["year"],
                   "doc_hash": index.text_hash(doc["text"])}
        before = index.stored(collection, url)
        if before and before["doc_hash"] == article["doc_hash"]:
            report.append({"url": url, "status": "unchanged", "chunks": before["chunks"], "title": article["title"],
                           "year": article["year"], "note": f"{note} ({path.name})"})
            continue
        chunks = index.chunk(index.paragraphs(doc["text"]))
        count = index.replace_article(collection, embed, article, chunks)
        report.append({"url": url, "status": "indexed", "chunks": count, "title": article["title"],
                       "year": article["year"], "tokens": sum(c["tokens"] for c in chunks),
                       "note": f"{note} ({path.name})"})
    for path in unmatched:
        report.append({"url": None, "status": "unmatched", "note": f"{path.name} matches no URL in the list"})
    for url in set(index.articles(collection)) - set(urls):      # taken off the list: out of the index too
        collection.delete(where={"url": url})
        report.append({"url": url, "status": "removed", "note": "no longer in the source list"})
    return report
