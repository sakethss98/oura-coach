"""The GSSI index and ingest, on made-up text (never article text): URLs, chunks, saved files, robots.txt,
listing pages, re-runs. No network: a fake HTTP client and the test hash embedder."""
from pathlib import Path

import pytest

from mcp_servers.gssi import index, ingest

SOURCES = Path(__file__).resolve().parent.parent / "docs" / "gssi_sources.txt"
GUT = "https://www.gssiweb.org/en/sports-science-exchange/article/training-the-gut-for-athletes"
SLEEP = "https://www.gssiweb.org/en/sports-science-exchange/article/a-practitioners-guide-to-improving-sleep-in-athletes"
# Fetch tests use another host: gssiweb.org itself is manual only (never fetched).
OTHER = "https://example.org/en/sports-science-exchange/article/"


def made_up(words: int, topic: str = "carbohydrate") -> str:
    return " ".join(f"{topic}{i % 7} sentence part {i}." for i in range(words // 4))


def page(url: str | None, title: str, body: str, heading: str = "Key Points") -> str:
    link = f'<link rel="canonical" href="{url}"/>' if url else ""
    return (f"<html><head><title>{title} | Gatorade Sports Science Institute</title>{link}</head><body>"
            f"<article><h1>{title}</h1><p>Sports Science Exchange (2019) Vol. 1</p><h2>{heading}</h2>"
            f"<p>{body}</p><h2>References</h2><p>Someone 2010. Other 2012.</p></article></body></html>")


@pytest.fixture
def collection(tmp_path):
    return index.open_collection(tmp_path / "index")


class FakeResponse:
    def __init__(self, status, text="", url="", content_type="text/html"):
        self.status_code, self.text, self.url = status, text, url
        self.content, self.headers = text.encode(), {"content-type": content_type}


class FakeClient:
    """robots.txt and pages by URL; records every request."""

    def __init__(self, pages: dict[str, FakeResponse]):
        self.pages, self.requests = pages, []

    def get(self, url):
        self.requests.append(url)
        return self.pages.get(url, FakeResponse(404, url=url))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch):
    monkeypatch.setattr(ingest, "PAUSE_S", 0)


# --- URLs ------------------------------------------------------------------------

def test_the_two_odd_urls_normalize_to_the_en_article_form():
    assert ingest.normalize("https://www.gssiweb.org/sports-science-exchange/Article/the-athlete-gut--x") == \
        "https://www.gssiweb.org/en/sports-science-exchange/article/the-athlete-gut--x"
    assert ingest.normalize("https://www.gssiweb.org/sports-science-exchange/article/caffeine-and-exercise/") == \
        "https://www.gssiweb.org/en/sports-science-exchange/article/caffeine-and-exercise"
    assert ingest.normalize(GUT) == GUT


def test_the_source_list_has_22_distinct_articles():
    urls = [ingest.normalize(u) for u in ingest.read_sources(SOURCES)]
    assert len(urls) == len(set(urls)) == 22 and all(ingest.is_article_url(u) for u in urls)


# --- chunks ----------------------------------------------------------------------

def test_paragraphs_keep_their_heading_and_stop_at_the_references():
    text = "# Title\n\nIntro text.\n\n## Key Points\n\nPoint one.\n\nKEY FINDINGS\n\nFinding.\n\n## References\n\nA 2010."
    assert index.paragraphs(text) == [("Title", "# Title"), ("Title", "Intro text."),
                                      ("Key Points", "## Key Points"), ("Key Points", "Point one."),
                                      ("Key Findings", "KEY FINDINGS"), ("Key Findings", "Finding.")]


def test_chunks_are_500_tokens_overlapping_by_60():
    chunks = index.chunk([("A", made_up(900)), ("B", made_up(900, "fluid"))])
    assert all(c["tokens"] <= 500 for c in chunks) and len(chunks) >= 3
    assert chunks[1]["text"][:120] in chunks[0]["text"]                   # the next chunk starts inside the last
    assert chunks[0]["section"] == "A" and chunks[-1]["section"] == "B"


def test_a_passage_is_at_most_700_characters_cut_at_a_word():
    p = index.passage("word " * 400)
    assert len(p) <= 700 and p.endswith("…") and not p.endswith(" …")


def test_search_returns_title_url_section_and_a_short_passage(collection):
    article = {"url": GUT, "title": "Gut", "year": 2017, "doc_hash": "h"}
    index.replace_article(collection, index.hash_embed, article,
                          index.chunk([("Fluids", made_up(300, "water")), ("Carbs", made_up(300, "pasta"))]))
    [hit] = index.search("pasta0 pasta1 pasta2", 1, collection, index.hash_embed)
    assert (hit["url"], hit["title"], hit["year"]) == (GUT, "Gut", 2017) and len(hit["passage"]) <= 700
    assert len(index.search("x", 99, collection, index.hash_embed)) == collection.count()   # k capped, never an error


# --- saved files, robots.txt, listing pages, re-runs -----------------------------

def saved(tmp_path: Path, name: str, html: str) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    (raw / name).write_text(html)
    return raw


def test_a_saved_page_is_matched_by_its_canonical_link_and_indexed_once(tmp_path, collection):
    raw = saved(tmp_path, "whatever.html", page(GUT, "Training the Gut for Athletes", made_up(800)))
    [row] = ingest.run([GUT], raw, collection, index.hash_embed, None)
    assert (row["status"], row["year"], row["title"]) == ("indexed", 2019, "Training the Gut for Athletes")
    assert row["chunks"] == collection.count() >= 2
    [again] = ingest.run([GUT], raw, collection, index.hash_embed, None)
    assert (again["status"], again["chunks"]) == ("unchanged", row["chunks"])


def test_a_saved_page_without_a_link_is_matched_by_its_name_words(tmp_path, collection):
    raw = saved(tmp_path, "Training the Gut for Athletes _ Gatorade Sports Science Institute.html",
                page(None, "Training the Gut for Athletes", made_up(800)))
    rows = ingest.run([GUT, SLEEP], raw, collection, index.hash_embed, None)
    assert [(r["url"], r["status"]) for r in rows] == [(GUT, "indexed"), (SLEEP, "missing")]


def test_a_changed_page_replaces_its_chunks_and_a_dropped_url_leaves_the_index(tmp_path, collection):
    raw = saved(tmp_path, "gut.html", page(GUT, "Gut", made_up(800)))
    ingest.run([GUT], raw, collection, index.hash_embed, None)
    saved(tmp_path, "gut.html", page(GUT, "Gut", made_up(2000, "fluid")))
    [row] = ingest.run([GUT], raw, collection, index.hash_embed, None)
    assert row["status"] == "indexed" and collection.count() == row["chunks"]
    rows = ingest.run([SLEEP], tmp_path / "empty", collection, index.hash_embed, None)
    assert {r["status"] for r in rows} == {"missing", "removed"} and collection.count() == 0


def test_a_listing_page_is_reported_not_indexed(tmp_path, collection):
    listing = "https://www.gssiweb.org/en/sports-science-exchange"
    raw = saved(tmp_path, "gut.html", page(listing, "Training the Gut for Athletes", made_up(800)))
    raw = saved(tmp_path, "short.html", page(SLEEP, "Sleep", "Read more. Next page."))
    rows = {r["url"]: r for r in ingest.run([GUT, SLEEP], raw, collection, index.hash_embed, None)}
    assert rows[SLEEP]["status"] == "listing" and "words" in rows[SLEEP]["note"]
    assert collection.count() == 0


def test_gssiweb_is_manual_only_and_never_requested(tmp_path, collection):
    client = FakeClient({})
    rows = ingest.run([GUT, SLEEP], tmp_path / "raw", collection, index.hash_embed, ingest.Fetcher(client))
    assert {r["status"] for r in rows} == {"blocked"} and ingest.MANUAL_ONLY in rows[0]["note"]
    assert client.requests == []


def test_robots_txt_that_answers_403_means_nothing_is_fetched(tmp_path, collection):
    client = FakeClient({"https://example.org/robots.txt": FakeResponse(403, "<html>Request unsuccessful. Incapsula incident ID: 1</html>")})
    rows = ingest.run([OTHER + "a", OTHER + "b"], tmp_path / "raw", collection, index.hash_embed,
                      ingest.Fetcher(client))
    assert {r["status"] for r in rows} == {"blocked"} and "403" in rows[0]["note"]
    assert client.requests == ["https://example.org/robots.txt"]           # read once, no article requested


def test_a_bot_wall_403_stops_every_further_request_to_the_host(tmp_path, collection):
    robots = FakeResponse(200, "User-agent: *\nDisallow:\n")
    client = FakeClient({"https://example.org/robots.txt": robots,
                         OTHER + "a": FakeResponse(403, "<html>Request unsuccessful. Incapsula incident ID: 1</html>", url=OTHER + "a")})
    rows = ingest.run([OTHER + "a", OTHER + "b", OTHER + "c"], tmp_path / "raw", collection, index.hash_embed,
                      ingest.Fetcher(client))
    assert [r["status"] for r in rows] == ["blocked"] * 3 and "earlier this run" in rows[2]["note"]
    assert client.requests == ["https://example.org/robots.txt", OTHER + "a"]


def test_a_fetch_follows_robots_txt(tmp_path, collection):
    robots = FakeResponse(200, "User-agent: *\nDisallow: /en/private/\n")
    url = OTHER + "training-the-gut-for-athletes"
    client = FakeClient({"https://example.org/robots.txt": robots,
                         url: FakeResponse(200, page(url, "Gut", made_up(800)), url=url)})
    rows = ingest.run([url, "https://example.org/en/private/x"], tmp_path / "raw", collection, index.hash_embed,
                      ingest.Fetcher(client))
    assert [r["status"] for r in rows] == ["indexed", "blocked"] and "disallowed" in rows[1]["note"]
    assert (tmp_path / "raw" / "fetched" / "training-the-gut-for-athletes.html").exists()


def test_a_robots_txt_that_is_an_html_page_means_no_rules(tmp_path, collection):
    soft_404 = FakeResponse(200, "<!DOCTYPE html><html><title>404 - Gatorade Sports Science Institute</title></html>")
    fetcher = ingest.Fetcher(FakeClient({"https://example.org/robots.txt": soft_404}))
    assert fetcher.allowed(OTHER + "a") == (True, "no robots.txt (answered with an HTML page): no rules")


def test_a_real_page_with_the_monitoring_script_is_not_a_bot_check():
    real = page(GUT, "Gut", made_up(800)) + '<script src="/_Incapsula_Resource?SWJIYLWA=1" async></script>'
    assert not ingest.BOT_WALL.search(real)
    assert ingest.BOT_WALL.search("<iframe>Request unsuccessful. Incapsula incident ID: 100</iframe>")
