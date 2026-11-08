"""One tiny call per configured model (config/models.yaml), to prove each works on this API key.

Prints job, model, tokens, cost and latency (never a key) and the price table. Each call is saved to the
`runs` table (kind "check") of the given database, so `scripts/show_runs.py --last 1` shows job + model.

Usage: python scripts/check_models.py [--demo]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage  # noqa: E402

from core import db, models  # noqa: E402
from core.config import DEMO_DB_PATH  # noqa: E402

PROMPT = "Reply with the single word OK."


def check_chat(job: str) -> bool:
    model = models.model_name(job)
    started = time.perf_counter()
    try:
        response = models.chat(job).invoke([HumanMessage(PROMPT)])
    except Exception as e:
        print(f"{job:<15} {model:<24} FAILED ({type(e).__name__})")
        return False
    latency = round((time.perf_counter() - started) * 1000)
    tokens_in, tokens_out = models.usage(response)
    cost = models.cost(model, tokens_in, tokens_out)
    db.log_run("check", model, [{"role": "human", "content": PROMPT}], {"content": response.content}, job=job,
               input_tokens=tokens_in, output_tokens=tokens_out, latency_ms=latency)
    print(f"{job:<15} {model:<24} ok  tokens={tokens_in}+{tokens_out}  "
          + (f"cost=${cost:.6f}" if cost is not None else "cost=n/a") + f"  {latency} ms")
    return True


def check_embeddings() -> bool:
    model = models.model_name("embeddings")
    started = time.perf_counter()
    try:
        vector = models.embeddings().embed_query("carbohydrate before a long run")
    except Exception as e:
        print(f"{'embeddings':<15} {model:<24} FAILED ({type(e).__name__})")
        return False
    print(f"{'embeddings':<15} {model:<24} ok  {len(vector)} dimensions  "
          f"{round((time.perf_counter() - started) * 1000)} ms")
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="log the check runs to demo.db")
    args = parser.parse_args()
    if args.demo:
        db.set_db_path(DEMO_DB_PATH)
    db.init_db()
    jobs = [j for j in models.config()["jobs"] if j != "embeddings"]
    results = [check_chat(job) for job in jobs] + [check_embeddings()]
    print("\nPrices (USD per 1M tokens, input / output), config/models.yaml:")
    for model, price in models.config()["prices"].items():
        print(f"  {model:<24} {price['input']:>6} / {price['output']}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
