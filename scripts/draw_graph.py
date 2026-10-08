"""Draw the coach graph: docs/graph.mmd (Mermaid text) and docs/graph.png.

Usage: python scripts/draw_graph.py
The PNG is rendered by the mermaid.ink web service: only the node names are sent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ROOT  # noqa: E402
from graph import build_graph  # noqa: E402

DOCS = ROOT / "docs"


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    drawable = build_graph(None).get_graph()
    (DOCS / "graph.mmd").write_text(drawable.draw_mermaid())
    print(f"Wrote {DOCS / 'graph.mmd'}")
    (DOCS / "graph.png").write_bytes(drawable.draw_mermaid_png())
    print(f"Wrote {DOCS / 'graph.png'}")


if __name__ == "__main__":
    main()
