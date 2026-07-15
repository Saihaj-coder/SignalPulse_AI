"""Run the practical eval question set through the agentic RAG chatbot.

Usage:
    python run_eval.py              # 15 practical + 2 refusal checks
    python run_eval.py --skip-refusal
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from signalpulse.agent import ask, get_agent
from signalpulse.config import PROCESSED_DIR
from signalpulse.eval_questions import EVAL_QUESTIONS, REFUSAL_QUESTIONS, EvalQuestion
from signalpulse import graph


def _score(eq: EvalQuestion, answer: str, refused: bool) -> str:
    """Coarse human-oriented label for the summary table."""
    a = (answer or "").strip()
    low = a.lower()
    has_url = "http://" in low or "https://" in low
    thin = len(a) < 80

    if eq.expect == "refuse":
        return "PASS_REFUSE" if refused else "FAIL_SHOULD_REFUSE"

    if refused:
        return "NEEDS_MORE_DATA"  # corpus/tools didn't support a grounded answer
    if not has_url:
        return "WEAK_NO_CITATION"
    if thin:
        return "WEAK_THIN"
    return "OK"


def run_one(eq: EvalQuestion) -> dict:
    t0 = time.time()
    reply = ask(eq.question)
    elapsed = time.time() - t0
    label = _score(eq, reply.answer, reply.refused)
    return {
        "id": eq.id,
        "category": eq.category,
        "question": eq.question,
        "expect": eq.expect,
        "label": label,
        "refused": reply.refused,
        "tools": reply.tool_calls,
        "answer": reply.answer,
        "seconds": round(elapsed, 1),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run SignalPulse practical eval questions.")
    p.add_argument("--skip-refusal", action="store_true")
    p.add_argument(
        "--out",
        default=str(PROCESSED_DIR / "eval_results.json"),
        help="Where to write JSON results",
    )
    args = p.parse_args(argv)

    graph.verify_connectivity()
    get_agent.cache_clear()

    questions = list(EVAL_QUESTIONS)
    if not args.skip_refusal:
        questions.extend(REFUSAL_QUESTIONS)

    results: list[dict] = []
    print(f"Running {len(questions)} questions...\n")
    for i, eq in enumerate(questions, 1):
        print(f"[{i}/{len(questions)}] {eq.id} ({eq.category})")
        print(f"  Q: {eq.question}")
        try:
            row = run_one(eq)
        except Exception as exc:  # noqa: BLE001
            row = {
                "id": eq.id,
                "category": eq.category,
                "question": eq.question,
                "expect": eq.expect,
                "label": "ERROR",
                "refused": False,
                "tools": [],
                "answer": f"{type(exc).__name__}: {exc}",
                "seconds": 0.0,
            }
        results.append(row)
        tools = ", ".join(t["name"] for t in row.get("tools") or []) or "(none)"
        print(f"  -> {row['label']} | tools: {tools} | {row['seconds']}s")
        preview = (row.get("answer") or "").replace("\n", " ")[:160]
        print(f"  A: {preview}...\n")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # Summary counts
    from collections import Counter

    counts = Counter(r["label"] for r in results)
    print("=" * 60)
    print("SUMMARY")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
