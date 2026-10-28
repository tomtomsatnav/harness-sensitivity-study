"""Price the full grid from pilot token usage.

    python -m analysis.cost results/pilot.jsonl

Prices are USD per million tokens. Check them against the current pricing page before
setting the budget; cache writes assume the 5-minute TTL rate of 1.25x input.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

import numpy as np

from analysis.stats import load_results
from runner.schemas import EpisodeResult, Usage

PRICES = {
    #                    input  output  cache_read  cache_write
    "claude-haiku-5-5":  (0.10, 0.50, 0.01, 0.125),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5-5":   (4.00, 20.00, 0.20, 5.00),
}

# Episodes per model in the full grid (spec: 25 tasks x 6 conditions x runs).
FULL_GRID = {
    "claude-haiku-5-5": 25 * 6 * 10,
    "claude-sonnet-5-5": 25 * 6 * 10,
    "claude-opus-5-5": 25 * 6 * 5,
}


def episode_cost(model: str, usage: Usage) -> float:
    p_in, p_out, p_read, p_write = PRICES[model]
    return (
        usage.input_tokens * p_in
        + usage.output_tokens * p_out
        + usage.cache_read_input_tokens * p_read
        + usage.cache_creation_input_tokens * p_write
    ) / 1e6


def project(results: list[EpisodeResult], grid: dict[str, int] = FULL_GRID) -> list[dict]:
    by_model: dict[str, list[EpisodeResult]] = defaultdict(list)
    for r in results:
        by_model[r.model].append(r)
    rows = []
    for model, rs in sorted(by_model.items()):
        costs = [episode_cost(model, r.usage) for r in rs]
        mean = float(np.mean(costs))
        rows.append({
            "model": model,
            "pilot_episodes": len(rs),
            "mean_turns": float(np.mean([r.turns for r in rs])),
            "mean_input": float(np.mean([r.usage.input_tokens for r in rs])),
            "mean_cache_read": float(np.mean([r.usage.cache_read_input_tokens for r in rs])),
            "mean_cache_write": float(np.mean([r.usage.cache_creation_input_tokens for r in rs])),
            "mean_output": float(np.mean([r.usage.output_tokens for r in rs])),
            "cost_per_episode": mean,
            "p90_cost_per_episode": float(np.percentile(costs, 90)),
            "grid_episodes": grid.get(model, 0),
            "projected_cost": mean * grid.get(model, 0),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results")
    args = p.parse_args(argv)

    rows = project(load_results(args.results))
    print(f"{'model':<20}{'eps':>5}{'turns':>7}{'in':>8}{'c_read':>9}{'c_write':>9}{'out':>8}"
          f"{'$/ep':>10}{'p90 $/ep':>10}{'grid':>7}{'projected':>12}")
    for r in rows:
        print(f"{r['model']:<20}{r['pilot_episodes']:>5}{r['mean_turns']:>7.1f}{r['mean_input']:>8.0f}"
              f"{r['mean_cache_read']:>9.0f}{r['mean_cache_write']:>9.0f}{r['mean_output']:>8.0f}"
              f"{r['cost_per_episode']:>10.4f}{r['p90_cost_per_episode']:>10.4f}{r['grid_episodes']:>7}"
              f"{r['projected_cost']:>12.2f}")
    print(f"\nprojected total for the full grid: ${sum(r['projected_cost'] for r in rows):,.2f}")
    print("Pilot tasks may be shorter or longer than the remaining 23; treat this as a first estimate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
