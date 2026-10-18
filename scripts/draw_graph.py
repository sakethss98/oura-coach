"""Draw the graphs: the agent (docs/graph.mmd/.png) and the training planner (docs/planner.mmd/.png).

Usage: python scripts/draw_graph.py
The PNG is rendered by the mermaid.ink web service: only the node names are sent.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import ROOT  # noqa: E402
from core.agent import build_agent  # noqa: E402
from modules.training.planner import build_graph  # noqa: E402

DOCS = ROOT / "docs"


def draw(app, name: str) -> None:
    drawable = app.get_graph()
    (DOCS / f"{name}.mmd").write_text(drawable.draw_mermaid())
    (DOCS / f"{name}.png").write_bytes(drawable.draw_mermaid_png())
    print(f"Wrote {DOCS / name}.mmd and .png")


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    draw(build_agent(), "graph")
    draw(build_graph(), "planner")


if __name__ == "__main__":
    main()
