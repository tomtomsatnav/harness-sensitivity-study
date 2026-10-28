# Harness Sensitivity Study

Does Claude's success on agentic tool-use tasks change when the harness presents the same
information differently, with the optimal action unchanged? See [SPEC.md](SPEC.md) for the
research question, hypotheses and plan.

## Status

Build-plan steps 1–4 are in place: all 25 tasks (5 per domain: orders, inventory, records,
scheduling, support), the perturbation conditions with guard tests, and the agent loop with
pilot and costing tooling. The pilot uses `orders_01` and `inventory_01`. Analysis
(bootstrap CIs, headline chart, flipped-episode sampler) is scaffolded and tested on
synthetic data. The pilot has not been run yet.

## Layout

```
tasks/      one module per domain: seeded backend, tools, grader, reference solution
perturb/    conditions: tool transform + observation encoder/decoder
runner/     episode environment, agent loop, grid runner (JSONL output)
analysis/   stats (paired bootstrap over tasks), cost projection, chart, flipped episodes
tests/      guard tests and unit tests (no API calls)
results/    JSONL episodes and derived outputs
```

## Setup

```
pip install -e '.[dev]'
pytest
export ANTHROPIC_API_KEY=...
```

## Workflow

```
python -m runner.run --pilot                      # 2 tasks x 6 conditions x 2 runs x 3 models = 72 episodes
python -m analysis.cost results/pilot.jsonl       # price the full grid from pilot tokens

python -m runner.run --models haiku sonnet --runs 10 --out results/episodes.jsonl
python -m runner.run --models opus --runs 5 --out results/episodes.jsonl

python -m analysis.stats results/episodes.jsonl   # deltas vs baseline with 95% CIs
python -m analysis.chart results/episodes.jsonl   # results/headline.png
python -m analysis.flips results/episodes.jsonl   # flip table + 20 episodes to hand-label
```

Re-running a `runner.run` command resumes. Episodes already in the output file are skipped,
and `api_error` episodes are retried. Use `--dry-run` to see the plan.

## Conditions

| Condition | Applies to | In main grid |
| --- | --- | --- |
| `baseline` | — | yes |
| `field_order_reverse` / `field_order_shuffle` | observations | reverse |
| `key_naming_synonym` / `key_naming_camel` | observations | synonym |
| `format_xml` / `format_yaml` | observations | xml |
| `tool_order_reverse` / `tool_order_shuffle` | tool definitions | reverse |
| `description_paraphrase` | tool definitions | yes |

The main grid (`STUDY_CONDITIONS` in `perturb/conditions.py`) uses one variant per family to
keep 6 conditions. Every variant is covered by the guard tests and can be run with
`--conditions all` or by name.

## Design decisions

- **Guard tests** (`tests/test_perturb.py`). For every task and every condition:
  - the reference solution, reading only *decoded* perturbed observations, passes the grader
  - every observation round-trips through encode/decode
  - tool names and input schemas are identical to baseline
  - the condition changes what the agent sees
  - every key emitted has a synonym

  Each condition therefore has a `decode`, which the agent never sees. Deliberately lossy
  perturbations (a dropped field, rounded floats, a missing tool) all fail the guard.
- **What stays fixed.** Tool input schemas, parameter descriptions, the system prompt and the
  task instruction are identical across conditions. Only observations or tool-level
  descriptions and order change.
- **Seeds.** Backend data is fixed per task, so every run and condition sees identical
  content. Run-to-run variance comes only from sampling at default settings. Shuffles are
  seeded from content, so they are stable across runs.
- **Success** requires the model to end its turn on its own *and* the grader to pass. Hitting
  the 15-turn cap is a failure even if the state is correct; `state_correct` records the
  grader verdict separately.
- **Model settings.** Model defaults are kept: no `temperature` and no `effort` unless
  `--effort` is passed. Opus 5.5 and Haiku 5.5 default to `medium` effort and Sonnet 5.5 to
  `high`. Thinking stays at the model default, and thinking blocks are passed back unchanged.
  Prompt caching uses automatic `cache_control`, so tools, system prompt and earlier turns
  are read from cache on each turn.
- **Refusal fallbacks are off.** A server-side fallback would swap the model mid-episode and
  contaminate the comparison. Refusals are logged as `status: refusal` and count as failures.
- **API errors.** A 400 stops the run, because it means a harness bug. Other API errors, after
  SDK retries, are logged as `api_error`, excluded from analysis and retried on resume.
- **Statistics.** Deltas are paired by task (condition rate minus baseline rate per task,
  averaged over tasks). The bootstrap resamples tasks, not episodes.

## Adding a task

Add a `Task` to a domain module's `TASKS` list (see `tasks/orders.py`). Every task needs:
- a seeded backend with one method per tool and a `snapshot()`
- 3–6 `ToolSpec`s, each with a paraphrase within ±10% length
- a grader on final state
- a reference solution that computes its answer from observations rather than hard-coding it

Add any new observation keys to `perturb/synonyms.py`. Then run `pytest`: the guard tests
cover the new task automatically.
