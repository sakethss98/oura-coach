"""Print a pip requirements file from a uv.lock: every package the project needs, at its locked version, with the
lock's sha256 hashes (pip --require-hashes then installs exactly these files) and its environment marker.

Used by scripts/install_usda_mcp.sh when uv isn't installed. A package's marker comes from the paths that reach it
from the project: each step's marker joined with "and" along a path, the paths joined with "or"; a package that
some path needs everywhere gets none (colorama only on Windows, typing-extensions only before 3.13).

    python scripts/uv_lock_requirements.py vendor/usda-mcp/uv.lock > requirements.lock.txt
"""
import sys
import tomllib
from pathlib import Path


def both(a: str | None, b: str | None) -> str | None:
    if a and b:
        return f"({a}) and ({b})"
    return a or b


def markers(packages: dict[str, dict], root: str) -> dict[str, set[str] | None]:
    """Package name -> the markers of the paths that reach it (None = some path has no marker: always needed)."""
    reached: dict[str, set[str] | None] = {}
    stack = [(root, None)]
    while stack:
        name, marker = stack.pop()
        for dep in packages[name].get("dependencies", []):
            child, edge = dep["name"], both(marker, dep.get("marker"))
            known = reached.get(child, set())
            if known is None or (edge is not None and edge in known):
                continue                                     # nothing new on this path
            reached[child] = None if edge is None else known | {edge}
            stack.append((child, edge))
    return reached


def requirements(lock_path: Path) -> str:
    lock = tomllib.loads(lock_path.read_text())
    packages = {p["name"]: p for p in lock["package"]}
    [root] = [p["name"] for p in lock["package"] if "virtual" in p.get("source", {})
              or "editable" in p.get("source", {})]
    lines = [f"# Generated from {lock_path.name} by scripts/uv_lock_requirements.py; do not edit."]
    for name, paths in sorted(markers(packages, root).items()):
        p = packages[name]
        if "registry" not in p.get("source", {}):
            raise SystemExit(f"{name}: not from a package index ({p.get('source')}); this script handles only those")
        hashes = [f["hash"] for f in [p.get("sdist")] + p.get("wheels", []) if f]
        marker = "" if paths is None else " ; " + " or ".join(f"({m})" for m in sorted(paths))
        lines.append(f"{name}=={p['version']}{marker} \\")
        lines += [f"    --hash={h} \\" for h in hashes[:-1]] + [f"    --hash={hashes[-1]}"]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(requirements(Path(sys.argv[1])), end="")
