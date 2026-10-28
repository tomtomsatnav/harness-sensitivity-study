"""Analysis tests on synthetic results."""

import json

import pytest

from analysis import chart, cost, flips, stats
from runner.schemas import EpisodeResult, Usage


def ep(model, task, cond, run, success, **usage):
    return EpisodeResult(
        episode_id=f"{model}|{task}|{cond}|{run}", model=model, task_id=task, domain="d", condition=cond,
        run_idx=run, status="completed" if success else "turn_cap", success=success, state_correct=success,
        turns=4, tool_calls=3, invalid_tool_calls=0, tool_errors=0, usage=Usage(**usage),
        started_at="2026-10-28T00:00:00+00:00", duration_s=1.0,
    )


def synthetic():
    # Baseline: every task succeeds. Perturbed: task t0 always fails, others always succeed.
    rs = []
    for t in range(4):
        for run in range(5):
            rs.append(ep("claude-haiku-5-5", f"t{t}", "baseline", run, True))
            rs.append(ep("claude-haiku-5-5", f"t{t}", "format_xml", run, t != 0))
    return rs


def test_delta_is_paired_by_task():
    d = stats.delta_ci(synthetic(), "claude-haiku-5-5", "format_xml")
    assert d.n_tasks == 4
    assert d.baseline_rate == 1.0 and d.condition_rate == 0.75
    assert d.delta == pytest.approx(-0.25)
    # Resampling tasks: the upper bound is "fragile task never drawn", the lower bound well below the mean.
    assert d.ci_low <= -0.5
    assert d.ci_high == pytest.approx(0.0)


def test_api_errors_are_dropped(tmp_path):
    path = tmp_path / "r.jsonl"
    bad = ep("m", "t", "baseline", 0, True).model_copy(update={"status": "api_error", "success": None})
    path.write_text(ep("m", "t", "baseline", 1, True).model_dump_json() + "\n" + bad.model_dump_json() + "\n")
    assert len(stats.load_results(path)) == 1


def test_cost_projection():
    rs = [ep("claude-sonnet-5-5", "t", "baseline", i, True, input_tokens=1_000_000, output_tokens=100_000)
          for i in range(2)]
    [row] = cost.project(rs)
    assert row["cost_per_episode"] == pytest.approx(2.0 + 1.0)
    assert row["projected_cost"] == pytest.approx(3.0 * 1500)


def test_flips_and_chart(tmp_path):
    rs = synthetic()
    flipped = flips.flipped_episodes(rs)
    assert len(flipped) == 5 and {r.task_id for r in flipped} == {"t0"}

    path = tmp_path / "r.jsonl"
    path.write_text("".join(r.model_dump_json() + "\n" for r in rs))
    out = tmp_path / "sample.jsonl"
    assert flips.main([str(path), "--n", "3", "--out", str(out)]) == 0
    lines = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(lines) == 3 and all(l["label"] == "" for l in lines)

    png = chart.plot(stats.all_deltas(rs), tmp_path / "h.png")
    assert png.stat().st_size > 0
