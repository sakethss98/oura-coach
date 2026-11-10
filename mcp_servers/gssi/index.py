"""The GSSI index: article text split into chunks, embedded, and stored in Chroma (data/gssi_index/, gitignored).

scripts/ingest_gssi.py writes it (through mcp_servers/gssi/ingest.py); mcp_servers/gssi/server.py searches it.
Nothing here imports core/, so the MCP server process needs only OPENAI_API_KEY in its environment.

Chunks: 500 tokens with 60 overlapping (cl100k_base, the tokenizer of text-embedding-3-small). Each chunk keeps
the heading it falls under, the article's title, url and publication year. Embeddings are ours (job
`embeddings` in config/models.yaml); Chroma's own embedding model is never used.
"""
import hashlib
import math
import os
import re
from pathlib import Path

import tiktoken
import yaml

ROOT = Path(__file__).resolve().parents[2]
INDEX_DIR = Path(os.getenv("GSSI_INDEX_DIR") or ROOT / "data" / "gssi_index")
COLLECTION = "gssi"
CHUNK_TOKENS = 500
OVERLAP_TOKENS = 60
PASSAGE_CHARS = 700
MAX_K = 8
ENCODING = tiktoken.get_encoding("cl100k_base")
# A short line in capitals ("KEY POINTS", "INTRODUCTION") is a heading in PDF text.
CAPS_HEADING = re.compile(r"^[A-Z][A-Z0-9 ,&:/()'-]{2,60}$")
# Everything from the reference list on is left out: it matches every query and answers none.
REFERENCES = re.compile(r"^(#+\s*)?(references|bibliography|literature cited)\s*$", re.I)


# --- embeddings ---------------------------------------------------------------

def embedding_model() -> str:
    return yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["jobs"]["embeddings"]["model"]


def hash_embed(texts: list[str]) -> list[list[float]]:
    """A deterministic bag-of-words vector, for tests only (GSSI_EMBEDDER=hash): no network, no cost."""
    vectors = []
    for text in texts:
        v = [0.0] * 256
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 256] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        vectors.append([x / norm for x in v])
    return vectors


def embedder():
    """texts -> vectors. OpenAI (the `embeddings` job), or the test hash with GSSI_EMBEDDER=hash."""
    if os.getenv("GSSI_EMBEDDER") == "hash":
        return hash_embed
    from langchain_openai import OpenAIEmbeddings
    return OpenAIEmbeddings(model=embedding_model()).embed_documents


# --- chunks -------------------------------------------------------------------

def paragraphs(text: str) -> list[tuple[str, str]]:
    """(heading, paragraph) for each paragraph, with the heading it falls under ("" before the first).
    Headings are markdown lines ("## Key Points") or short lines in capitals (PDF text). Stops at the references."""
    heading, found = "", []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        first = block.splitlines()[0].strip()
        if REFERENCES.match(first):
            break
        title = re.match(r"^#{1,6}\s+(.+)$", first)
        if title or CAPS_HEADING.match(first):
            heading = (title.group(1) if title else first.title()).strip()
        found.append((heading, block))
    return found


def chunk(paras: list[tuple[str, str]]) -> list[dict]:
    """Windows of CHUNK_TOKENS tokens, each starting OVERLAP_TOKENS before the previous one ended.
    A chunk's section is the heading of its first new token."""
    tokens, owner = [], []
    for heading, text in paras:
        ids = ENCODING.encode(text + "\n\n")
        tokens += ids
        owner += [heading] * len(ids)
    chunks = []
    for start in range(0, max(len(tokens) - OVERLAP_TOKENS, 1), CHUNK_TOKENS - OVERLAP_TOKENS):
        window = tokens[start:start + CHUNK_TOKENS]
        first_new = min(start + (OVERLAP_TOKENS if start else 0), len(owner) - 1)
        chunks.append({"text": ENCODING.decode(window).strip(), "section": owner[first_new] if owner else "",
                       "tokens": len(window)})
    return [c for c in chunks if c["text"]]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def chunk_id(url: str, i: int) -> str:
    return hashlib.sha1(f"{url}#{i}".encode()).hexdigest()


# --- the Chroma collection ----------------------------------------------------

def open_collection(path: Path | None = None):
    import chromadb
    from chromadb.config import Settings
    client = chromadb.PersistentClient(path=str(path or INDEX_DIR), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(COLLECTION, embedding_function=None,
                                           configuration={"hnsw": {"space": "cosine"}})


def stored(collection, url: str) -> dict | None:
    """{"doc_hash", "chunks"} of an article already in the index, or None."""
    rows = collection.get(where={"url": url}, include=["metadatas"])
    if not rows["ids"]:
        return None
    return {"doc_hash": rows["metadatas"][0]["doc_hash"], "chunks": len(rows["ids"])}


def replace_article(collection, embed, article: dict, chunks: list[dict]) -> int:
    """Store an article's chunks (its old chunks removed first). article: url, title, year, doc_hash."""
    collection.delete(where={"url": article["url"]})
    vectors = embed([c["text"] for c in chunks])
    collection.add(
        ids=[chunk_id(article["url"], i) for i in range(len(chunks))],
        embeddings=vectors,
        documents=[c["text"] for c in chunks],
        # Chroma metadata can't be None: an unknown year is stored as 0.
        metadatas=[{"url": article["url"], "title": article["title"], "section": c["section"],
                    "year": article["year"] or 0, "chunk": i, "doc_hash": article["doc_hash"]}
                   for i, c in enumerate(chunks)],
    )
    return len(chunks)


def articles(collection) -> dict[str, dict]:
    """{url: {"title", "year", "chunks"}} of everything in the index."""
    found = {}
    for meta in collection.get(include=["metadatas"])["metadatas"]:
        row = found.setdefault(meta["url"], {"title": meta["title"], "year": meta["year"] or None, "chunks": 0})
        row["chunks"] += 1
    return found


def passage(text: str, limit: int = PASSAGE_CHARS) -> str:
    """At most `limit` characters, cut at a word."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0] + "…"


def search(query: str, k: int = 4, collection=None, embed=None) -> list[dict]:
    """The k chunks closest to the query: [{title, url, section, year, passage}] (passage <= 700 characters)."""
    k = max(1, min(int(k), MAX_K))
    collection = collection or open_collection()
    if collection.count() == 0:
        return []
    found = collection.query(query_embeddings=(embed or embedder())([query]), n_results=k,
                             include=["documents", "metadatas"])
    return [{"title": meta["title"], "url": meta["url"], "section": meta["section"], "year": meta["year"] or None,
             "passage": passage(doc)}
            for doc, meta in zip(found["documents"][0], found["metadatas"][0])]
