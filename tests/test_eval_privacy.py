"""The repo is public and the eval cases come from the person's own messages: docs/evals/ holds category totals
only. Fails if any file there contains the text of one of their messages (data/my_questions.txt,
data/sample_questions.jsonl, data/evals/eval_set_personal.jsonl; gitignored, so this runs where they are)."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs" / "evals"
DATA = ROOT / "data"


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip(" ?.!")


def their_messages() -> list[str]:
    texts = []
    if (DATA / "my_questions.txt").exists():
        texts += (DATA / "my_questions.txt").read_text().splitlines()
    for path, key in ((DATA / "sample_questions.jsonl", None), (DATA / "evals" / "eval_set_personal.jsonl", "turns")):
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    texts += [t["text"] for t in row["turns"]] if key else [row["text"]]
    return [norm(t) for t in texts if len(t.split()) >= 3]       # "Hi" and "Yes" aren't anyone's own words


def test_docs_evals_has_none_of_their_messages():
    messages = their_messages()
    for path in DOCS.glob("**/*") if DOCS.exists() else []:
        if path.is_file():
            text = norm(path.read_text())
            leaked = [m for m in messages if m in text]
            assert not leaked, f"{path.relative_to(ROOT)} has their messages: {leaked[:3]}"


def test_the_report_prints_totals_and_check_texts_never_a_message():
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_evals
    secret = "a private question about my week of runs"
    r = {"id": "p-training-1", "trial": 1, "categories": ["training"], "message": secret, "reply": "a private reply",
         "routing_hit": True, "passed": False, "latency_s": 3.0, "error": None,
         "checks": [{"check": "It answers what they asked about training", "skill": "training", "id": None,
                     "answer": "no", "reason": f"it ignored {secret}"}],
         "coach": {"input_tokens": 10, "output_tokens": 5, "cost": 0.001, "models": ["agent=gpt-5-mini"]},
         "judge": {"cost": 0.002}}
    text = run_evals.report([r], 1, "Evals test", {})
    assert secret not in text and "a private reply" not in text and "p-training-1" not in text
    assert "It answers what they asked about training" in text


def _whole_phrase(message: str, text: str) -> bool:
    """`message` appears as a whole phrase: not inside a word, and not the start of a longer sentence
    ("what should I eat" in "what should I eat before a run" is a different question)."""
    return re.search(rf"(?<![a-z0-9]){re.escape(message)}(?![a-z0-9]| [a-z0-9])", text) is not None


def test_no_tracked_file_outside_data_and_tests_has_their_messages():
    """Their messages (data/my_questions.txt, data/sample_questions.jsonl) never appear in a tracked file outside
    data/ and tests/: examples in skills and prompts are paraphrases. Lines of 4+ words only ("Hi", "Yes",
    "Last 7 days" are everyone's words)."""
    import subprocess
    messages = [m for m in their_messages() if len(m.split()) >= 4]
    if not messages:
        return                                        # not this person's checkout: nothing to compare
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    found = []
    for name in files:
        if name.startswith(("data/", "tests/")):
            continue
        try:
            text = norm((ROOT / name).read_text())
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        found += [f"{name}: {m}" for m in messages if _whole_phrase(m, text)]
    assert not found, found
