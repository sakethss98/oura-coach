"""The coach is for anyone: one person's goal (a race date, a target time, a direction goal) lives only in tests and
in their own goal_answers rows, never in the code, config, skills or prompts."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("core/", "modules/", "connectors/", "channels/", "programs/", "skills/", "prompts/", "config/",
                "scripts/")
SCANNED_ROOT = ("goals.yaml",)
PERSONAL = re.compile(r"2027-02-15|\bFeb(ruary)?\.? 15\b|\b2:30\b|\b2:29|\b1:59:59|hybrid athlete|abs by", re.I)


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout.splitlines()
    return [f for f in out if (f.startswith(SCANNED_DIRS) or f in SCANNED_ROOT or ("/" not in f and f.endswith(".py")))
            and f.endswith((".py", ".yaml", ".yml", ".md"))]


def test_scans_the_code_and_config():
    files = tracked()
    assert "goals.yaml" in files and "skills/goal_program.md" in files and "programs/schema.py" in files


def test_no_personal_goal_values_outside_tests():
    found = [f"{f}:{i}: {line.strip()}" for f in tracked() if (ROOT / f).exists()
             for i, line in enumerate((ROOT / f).read_text().splitlines(), 1) if PERSONAL.search(line)]
    assert found == []
