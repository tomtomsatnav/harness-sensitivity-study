"""Headline chart: change in success rate from baseline, per condition, one panel per model.

    python -m analysis.chart results/episodes.jsonl --out results/headline.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from analysis.stats import DeltaCI, all_deltas, load_results  # noqa: E402
from perturb import STUDY_CONDITIONS  # noqa: E402

MODEL_ORDER = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
MODEL_LABELS = {"claude-haiku-5-5": "Haiku 5.5", "claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5"}

MARK = "#2a78d6"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"


def plot(deltas: list[DeltaCI], out: str | Path) -> Path:
    models = [m for m in MODEL_ORDER if any(d.model == m for d in deltas)]
    models += sorted({d.model for d in deltas} - set(models))
    # Spec order (observation perturbations, then tool-definition ones); unknown conditions last.
    order = {c: i for i, c in enumerate(STUDY_CONDITIONS)}
    conditions = sorted({d.condition for d in deltas}, key=lambda c: (order.get(c, len(order)), c))

    fig, axes = plt.subplots(
        1, len(models), figsize=(3.6 * len(models) + 1.6, 0.5 * len(conditions) + 1.6),
        sharex=True, sharey=True, squeeze=False, facecolor=SURFACE,
    )
    for ax, model in zip(axes[0], models):
        ax.set_facecolor(SURFACE)
        rows = {d.condition: d for d in deltas if d.model == model}
        for y, cond in enumerate(conditions):
            d = rows.get(cond)
            if d is None:
                continue
            ax.errorbar(
                d.delta * 100, y,
                xerr=[[(d.delta - d.ci_low) * 100], [(d.ci_high - d.delta) * 100]],
                fmt="o", color=MARK, ecolor=MARK, elinewidth=2, capsize=0, markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2,
            )
        ax.axvline(0, color=INK_2, linewidth=1)
        for x in (-5, 5):  # the H1 threshold
            ax.axvline(x, color=AXIS, linewidth=1, linestyle=(0, (3, 3)))
        ax.set_title(MODEL_LABELS.get(model, model), color=INK, fontsize=11, loc="left")
        ax.set_xlabel("Δ success vs baseline (pp)", color=INK_2, fontsize=9)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, labelsize=9, length=0)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
    axes[0][0].set_yticks(range(len(conditions)), [c.replace("_", " ") for c in conditions], color=INK_2)
    axes[0][0].invert_yaxis()
    fig.suptitle(
        "Change in task success from baseline (95% CI, bootstrapped over tasks; dashed lines at ±5 pp)",
        x=0.01, ha="left", color=INK, fontsize=10,
    )
    fig.tight_layout()
    out = Path(out)
    fig.savefig(out, dpi=200, facecolor=SURFACE)
    plt.close(fig)
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("results")
    p.add_argument("--out", default="results/headline.png")
    args = p.parse_args(argv)
    print(f"wrote {plot(all_deltas(load_results(args.results)), args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
