"""No GSSI article text in the repo. The index and the saved pages live in data/ (gitignored), and no tracked file,
nor a new file about to be committed, shares 12 words in a row with any indexed chunk (urls left out:
docs/gssi_sources.txt lists them)."""
import re
import subprocess

import pytest

from core.reply_rules import COPY_WORDS
from mcp_servers.gssi import index

ROOT = index.ROOT
URL = re.compile(r"https?://\S+")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", URL.sub(" ", text.lower()))


def runs_of_words(text: str) -> set[tuple]:
    w = words(text)
    return {tuple(w[i:i + COPY_WORDS]) for i in range(len(w) - COPY_WORDS + 1)}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def test_the_index_and_saved_pages_are_gitignored():
    paths = ["data/gssi_index/chroma.sqlite3", "data/gssi_raw/article.html", "data/gssi_raw/fetched/a.pdf"]
    assert git("check-ignore", *paths).split() == paths


@pytest.mark.skipif(not index.INDEX_DIR.exists(), reason="no GSSI index yet (python scripts/ingest_gssi.py)")
def test_no_tracked_file_contains_indexed_text():
    indexed = set()
    for doc in index.open_collection().get(include=["documents"])["documents"]:
        indexed |= runs_of_words(doc)
    assert indexed, "the index is empty"
    found = []
    names = git("ls-files", "-z").split("\0") + git("ls-files", "--others", "--exclude-standard", "-z").split("\0")
    for name in names:
        path = ROOT / name
        if not name or not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:          # images and other binary files
            continue
        if runs_of_words(text) & indexed:
            found.append(name)
    assert found == [], f"files with GSSI text: {found}"
