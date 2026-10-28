"""Run a grid of episodes and append results to a JSONL file.

    python -m runner.run --pilot
    python -m runner.run --models haiku sonnet --runs 10 --out results/full.jsonl
    python -m runner.run --models opus --runs 5 --out results/full.jsonl

Re-running the same command resumes: episodes already in the output file are skipped,
except api_error episodes, which are retried.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

from perturb import CONDITIONS, STUDY_CONDITIONS
from runner.agent import MAX_TURNS, run_episode
from runner.schemas import EpisodeResult
from tasks import ALL_TASKS, TASKS_BY_ID

MODELS = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}

PILOT = {"models": list(MODELS), "tasks": ["orders_01_damaged_refund", "inventory_01_single_transfer"], "runs": 2, "out": "results/pilot.jsonl"}


def load_done(path: Path) -> set[tuple[str, str, str, int]]:
    if not path.exists():
        return set()
    done = set()
    with path.open() as f:
        for line in f:
            r = EpisodeResult.model_validate_json(line)
            if r.status != "api_error":
                done.add(r.key)
    return done


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pilot", action="store_true", help="2 tasks x study conditions x 2 runs on all models")
    p.add_argument("--models", nargs="+", choices=list(MODELS), default=["haiku"])
    p.add_argument("--tasks", nargs="+", default=["all"], help="task IDs, or 'all'")
    p.add_argument("--conditions", nargs="+", default=["study"], help="condition names, 'study' or 'all'")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help="omit to use each model's default, as the spec requires")
    p.add_argument("--max-turns", type=int, default=MAX_TURNS)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--out", default="results/episodes.jsonl")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = p.parse_args(argv)

    if args.pilot:
        args.models, args.runs, args.out = PILOT["models"], PILOT["runs"], PILOT["out"]
        tasks = [TASKS_BY_ID[t] for t in PILOT["tasks"]]
    else:
        tasks = ALL_TASKS if args.tasks == ["all"] else [TASKS_BY_ID[t] for t in args.tasks]

    if args.conditions == ["study"]:
        conditions = [CONDITIONS[c] for c in STUDY_CONDITIONS]
    elif args.conditions == ["all"]:
        conditions = list(CONDITIONS.values())
    else:
        conditions = [CONDITIONS[c] for c in args.conditions]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out)
    plan = [
        (MODELS[m], t, c, r)
        for m in args.models
        for t in tasks
        for c in conditions
        for r in range(args.runs)
        if (MODELS[m], t.task_id, c.name, r) not in done
    ]
    total = len(args.models) * len(tasks) * len(conditions) * args.runs
    print(f"{total} episodes in grid, {total - len(plan)} already done, {len(plan)} to run -> {out}")
    if args.dry_run or not plan:
        return 0

    client = anthropic.Anthropic(max_retries=6)
    lock = threading.Lock()
    finished = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool, out.open("a") as f:
        futures = [
            pool.submit(run_episode, client, model, task, cond, run, effort=args.effort, max_turns=args.max_turns)
            for model, task, cond, run in plan
        ]
        for fut in as_completed(futures):
            try:
                result = fut.result()
            except Exception:
                # A harness bug (e.g. a 400). Results already written are kept; resume after fixing.
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            with lock:
                f.write(result.model_dump_json() + "\n")
                f.flush()
                finished += 1
            u = result.usage
            print(
                f"[{finished}/{len(plan)}] {result.model} {result.task_id} {result.condition} "
                f"#{result.run_idx}: {result.status} success={result.success} turns={result.turns} "
                f"in={u.input_tokens} cache_read={u.cache_read_input_tokens} out={u.output_tokens}",
                flush=True,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
