"""Run a grid of readout episodes and append results to a JSONL file.

    python -m readout.run --models haiku --conditions all --runs 3 --out results/readout.jsonl
    python -m readout.run --models haiku --families ascending-numbers --conditions R0_canonical R1_reversed_fields
    python -m readout.run --models qwen2b --runs 3 --out results/readout_qwen.jsonl   # local Ollama model

Mirrors runner/run.py: re-running the same command resumes (episodes already in the output
file are skipped, api_error episodes are retried), the API key comes from ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import anthropic

from readout.conditions import CONDITIONS
from readout.episode import THINKING, run_episode
from readout.ollama import OllamaClient
from readout.tasks import ALL_INSTANCES, FAMILIES, INSTANCES_BY_ID, MAX_DECISIONS
from runner.run import MODELS as CLAUDE_MODELS
from runner.run import load_done

OLLAMA_MODELS = {"qwen2b": "qwen3.5:2b-bf16"}  # served locally by Ollama, zero cost
MODELS = {**CLAUDE_MODELS, **OLLAMA_MODELS}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", choices=list(MODELS), default=["haiku"])
    p.add_argument("--families", nargs="+", choices=list(FAMILIES), default=list(FAMILIES))
    p.add_argument("--tasks", nargs="+", default=None, help="instance IDs (default: all in --families)")
    p.add_argument("--conditions", nargs="+", default=["all"], help="condition names or 'all'")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--out", default="results/readout.jsonl")
    p.add_argument("--constrained", action="store_true", help="restrict replies to the legal codes via structured outputs")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = p.parse_args(argv)

    for m in args.models:
        if m not in OLLAMA_MODELS and MODELS[m] not in THINKING:
            sys.exit(f"{MODELS[m]} cannot turn thinking off, which the readout protocol requires")
    instances = [INSTANCES_BY_ID[t] for t in args.tasks] if args.tasks else \
        [i for i in ALL_INSTANCES if i.family in args.families]
    conditions = list(CONDITIONS.values()) if args.conditions == ["all"] else [CONDITIONS[c] for c in args.conditions]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done, settings = load_done(out)
    if settings - {(None, MAX_DECISIONS)}:
        sys.exit(f"{out} holds episodes with other settings {sorted(settings, key=str)}; use a different --out.")
    plan = [
        (MODELS[m], inst, c, r)
        for m in args.models
        for inst in instances
        for c in conditions
        for r in range(args.runs)
        if (MODELS[m], inst.task_id, c.name, r) not in done
    ]
    total = len(args.models) * len(instances) * len(conditions) * args.runs
    print(f"{total} episodes in grid, {total - len(plan)} already done, {len(plan)} to run -> {out}")
    if args.dry_run or not plan:
        return 0

    # One client per backend. The Anthropic client is only built when a Claude model is in the plan.
    ollama = OllamaClient()
    clients = {model: ollama for model in OLLAMA_MODELS.values()}
    if any(model in CLAUDE_MODELS.values() for model, *_ in plan):
        claude = anthropic.Anthropic(max_retries=6)
        clients.update({model: claude for model in CLAUDE_MODELS.values()})
    lock = threading.Lock()
    finished = 0
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool, out.open("a") as f:
        futures = [pool.submit(run_episode, clients[model], model, inst, cond, run, constrained=args.constrained) for model, inst, cond, run in plan]
        try:
            for fut in as_completed(futures):
                result = fut.result()
                with lock:
                    f.write(result.model_dump_json() + "\n")
                    f.flush()
                    finished += 1
                u = result.usage
                print(
                    f"[{finished}/{len(plan)}] {result.model} {result.task_id} {result.condition} "
                    f"#{result.run_idx}: {result.status} success={result.success} turns={result.turns} "
                    f"invalid={result.invalid_replies} in={u.input_tokens} cache_read={u.cache_read_input_tokens} "
                    f"out={u.output_tokens}",
                    flush=True,
                )
        except BaseException:
            # A harness bug (e.g. a 400) or Ctrl-C: don't start queued episodes. Results already
            # written are kept; resume after fixing.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
