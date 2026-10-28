"""Find tasks that flip between success and failure, and sample flipped episodes to label.

    python -m analysis.flips results/episodes.jsonl --n 20 --out results/flipped_sample.jsonl

A flipped episode is a failure under a perturbation on a (model, task) whose baseline
success rate is at least --min-baseline. The sample file has a blank `label` field for
hand-labelling (e.g. misread_field, wrong_tool, gave_up).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from analysis.stats import load_results, task_rates
from runner.schemas import EpisodeResult


def flipped_episodes(results: list[EpisodeResult], min_baseline: float = 0.8) -> list[EpisodeResult]:
    flipped = []
    for model in sorted({r.model for r in results}):
        base = task_rates(results, model, "baseline")
        for r in results:
            if (r.model == model and r.condition != "baseline" and not r.success
                    and base.get(r.task_id, 0.0) >= min_baseline):
                flipped.append(r)
    return flipped


def flip_table(results: list[EpisodeResult]) -> list[dict]:
    """Per model x task x condition: baseline rate, condition rate, and the change."""
    rows = []
    for model in sorted({r.model for r in results}):
        base = task_rates(results, model, "baseline")
        for cond in sorted({r.condition for r in results} - {"baseline"}):
            for task, rate in sorted(task_rates(results, model, cond).items()):
                if task in base:
                    rows.append({"model": model, "condition": cond, "task_id": task,
                                 "baseline_rate": base[task], "condition_rate": rate,
                                 "change": rate - base[task]})
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results")
    p.add_argument("--n", type=int, default=20)
    p.add_argument("--min-baseline", type=float, default=0.8)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", default="results/flipped_sample.jsonl")
    args = p.parse_args(argv)

    results = load_results(args.results)
    rows = sorted(flip_table(results), key=lambda r: r["change"])
    print(f"{'model':<20}{'condition':<26}{'task':<32}{'base':>7}{'cond':>7}{'change':>8}")
    for r in rows:
        if r["change"] != 0:
            print(f"{r['model']:<20}{r['condition']:<26}{r['task_id']:<32}"
                  f"{r['baseline_rate']:>7.0%}{r['condition_rate']:>7.0%}{r['change']:>+8.0%}")

    flipped = flipped_episodes(results, args.min_baseline)
    sample = random.Random(args.seed).sample(flipped, min(args.n, len(flipped)))
    with Path(args.out).open("w") as f:
        for r in sample:
            f.write(json.dumps({"label": "", "notes": "", **r.model_dump(mode="json")}) + "\n")
    print(f"\n{len(flipped)} flipped episodes; wrote a sample of {len(sample)} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
