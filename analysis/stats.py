"""Success rates and paired bootstrap confidence intervals.

    python -m analysis.stats results/episodes.jsonl

The headline number is, per model and condition, the change in success rate from
baseline. It is paired by task: for each task, the condition's success rate minus the
baseline's, averaged over tasks. The 95% CI resamples tasks (not episodes), so one
fragile task cannot fake an effect.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from pydantic import BaseModel

from runner.schemas import EpisodeResult


def load_results(path: str | Path) -> list[EpisodeResult]:
    """Load episodes, dropping api_error ones (they are retried, not counted)."""
    with Path(path).open() as f:
        results = [EpisodeResult.model_validate_json(line) for line in f if line.strip()]
    return [r for r in results if r.status != "api_error"]


def task_rates(results: list[EpisodeResult], model: str, condition: str) -> dict[str, float]:
    outcomes: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        if r.model == model and r.condition == condition:
            outcomes[r.task_id].append(bool(r.success))
    return {t: float(np.mean(v)) for t, v in outcomes.items()}


class DeltaCI(BaseModel):
    model: str
    condition: str
    n_tasks: int
    baseline_rate: float
    condition_rate: float
    delta: float
    ci_low: float
    ci_high: float


def delta_ci(
    results: list[EpisodeResult],
    model: str,
    condition: str,
    baseline: str = "baseline",
    n_boot: int = 10_000,
    seed: int = 0,
) -> DeltaCI:
    base = task_rates(results, model, baseline)
    cond = task_rates(results, model, condition)
    tasks = sorted(set(base) & set(cond))
    if not tasks:
        raise ValueError(f"no tasks with both {baseline} and {condition} results for {model}")
    b = np.array([base[t] for t in tasks])
    c = np.array([cond[t] for t in tasks])
    d = c - b
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(tasks), size=(n_boot, len(tasks)))
    boot = d[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return DeltaCI(
        model=model, condition=condition, n_tasks=len(tasks),
        baseline_rate=float(b.mean()), condition_rate=float(c.mean()),
        delta=float(d.mean()), ci_low=float(lo), ci_high=float(hi),
    )


def cell_summary(results: list[EpisodeResult]) -> list[dict]:
    """Primary and secondary metrics per model x condition."""
    cells: dict[tuple[str, str], list[EpisodeResult]] = defaultdict(list)
    for r in results:
        cells[(r.model, r.condition)].append(r)
    rows = []
    for (model, condition), rs in sorted(cells.items()):
        completed = [r for r in rs if r.success]
        rows.append({
            "model": model,
            "condition": condition,
            "episodes": len(rs),
            "success_rate": float(np.mean([bool(r.success) for r in rs])),
            "turns_to_completion": float(np.mean([r.turns for r in completed])) if completed else float("nan"),
            "invalid_tool_calls_per_ep": float(np.mean([r.invalid_tool_calls for r in rs])),
            "turn_cap_rate": float(np.mean([r.status == "turn_cap" for r in rs])),
        })
    return rows


def all_deltas(results: list[EpisodeResult], **kw) -> list[DeltaCI]:
    pairs = sorted({(r.model, r.condition) for r in results if r.condition != "baseline"})
    return [delta_ci(results, m, c, **kw) for m, c in pairs]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results")
    p.add_argument("--out", default=None, help="CSV of deltas (default: <results>.deltas.csv)")
    args = p.parse_args(argv)

    results = load_results(args.results)
    deltas = all_deltas(results)
    out = Path(args.out or Path(args.results).with_suffix(".deltas.csv"))
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(DeltaCI.model_fields))
        w.writeheader()
        w.writerows(d.model_dump() for d in deltas)

    print(f"{'model':<20}{'condition':<26}{'tasks':>6}{'base':>8}{'cond':>8}{'delta':>8}   95% CI")
    for d in deltas:
        print(f"{d.model:<20}{d.condition:<26}{d.n_tasks:>6}{d.baseline_rate:>8.1%}{d.condition_rate:>8.1%}"
              f"{d.delta:>+8.1%}   [{d.ci_low:+.1%}, {d.ci_high:+.1%}]")
    print()
    print(f"{'model':<20}{'condition':<26}{'eps':>5}{'success':>9}{'turns':>7}{'invalid/ep':>12}{'capped':>8}")
    for row in cell_summary(results):
        print(f"{row['model']:<20}{row['condition']:<26}{row['episodes']:>5}{row['success_rate']:>9.1%}"
              f"{row['turns_to_completion']:>7.1f}{row['invalid_tool_calls_per_ep']:>12.2f}{row['turn_cap_rate']:>8.1%}")
    print(f"\ndeltas written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
