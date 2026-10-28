# Harness Sensitivity Study — Spec

Oct 28, 2026 · @Tom Mitchell

## Research question

Does Claude's success on agentic tool-use tasks change when the harness presents the same information differently, with the optimal action unchanged?

**Hypotheses**

1. **H1:** at least one semantics-preserving perturbation changes success rate by 5 percentage points or more on some model.
2. **H2:** sensitivity shrinks with model size (Haiku > Sonnet > Opus).
3. **H3:** perturbations to observations (what tools return) matter more than perturbations to tool definitions (what tools are offered).

A null result is still a result. "Claude is robust to these perturbations, unlike the models in the HKUST paper" is worth writing up.

## Background

An HKUST paper (late Oct 2026) argues that trained agent policies encode their training harness's presentation. Reversing JSON field order in MiniWoB++ left every optimal decision unchanged but cut task success from 85.0% to 19.2%. On Terminal-Bench Lite, one frozen 9B open model scored 47.9% to 64.1% depending on the harness ([reported by AI Weekly](https://aiweekly.co/ai-news-today)).

This matters because agent benchmark scores may partly measure harness fit rather than capability. Open question: read the paper itself, and check whether anyone has already run the equivalent test on current Claude models.

## Task suite

25 text-only tasks, each with a single verifiable correct end state, run against a mock backend in plain Python. No browser, no network.

**Domains (5 tasks each)**

- **Orders:** find an order, check its status, issue a refund under a policy rule
- **Records:** reconcile two customer records and merge the correct fields
- **Scheduling:** book a slot that satisfies three constraints
- **Inventory:** move stock between warehouses without going negative
- **Support tickets:** triage, route and close by priority rules

**Rules for every task**

- 3 to 6 tools, 3 to 10 steps to solve
- Success is checked by a deterministic grader on the backend's final state, never by an LLM judge
- Each task has a reference solution that passes in every perturbation condition, which proves the perturbation is semantics-preserving
- Seeded data, so every run of a condition sees identical content

## Perturbations

Six conditions: one baseline and five perturbations, each applied on its own. Every perturbation changes presentation only, never content or the optimal action.

| Condition | Applies to | What changes |
| --- | --- | --- |
| Baseline | — | Natural field order, snake\_case keys, JSON, tools in logical order |
| Field order | Observations | JSON keys reversed or shuffled (seeded) |
| Key naming | Observations | snake\_case to camelCase, or neutral synonyms (`qty` for `quantity`) |
| Format | Observations | Same data as YAML or XML instead of JSON |
| Tool order | Tool definitions | Tool list reversed or shuffled |
| Description wording | Tool definitions | Tool descriptions paraphrased, same meaning and length (±10%) |

If time allows, a seventh condition stacks all five to find the worst case.

## Models, runs and budget

The full grid is 25 tasks × 6 conditions × 10 runs = 1,500 episodes per model, at default sampling settings so run-to-run variance is real.

| Model | Runs per task-condition | Episodes |
| --- | --- | --- |
| Haiku 5.5 | 10 | 1,500 |
| Sonnet 5.5 | 10 | 1,500 |
| Opus 5.5 | 5 | 750 |

- Cap each episode at 15 turns; hitting the cap counts as a failure
- Run a pilot first (2 tasks × 6 conditions × 2 runs per model), log tokens per episode, then price the full grid before committing
- Use prompt caching on the system prompt and tool definitions to cut cost; batch processing is a poor fit, since each episode is a multi-turn loop
- Open question: total budget ceiling, to be set from the pilot's numbers

## Metrics and analysis

The headline number is the change in success rate from baseline for each perturbation and model, with a 95% confidence interval.

- **Primary:** success rate per model × condition
- **Statistics:** paired comparison against baseline on the same task and seed; bootstrap confidence intervals resampled over tasks, not episodes, so one fragile task can't fake an effect
- **Secondary:** turns to completion, invalid tool calls, and which tasks flip between success and failure
- **Failure analysis:** read 20 flipped episodes by hand and label why they failed (misread field, wrong tool, gave up)

**Headline chart:** for each model, the change from baseline per condition as a dot with an error bar, zero line marked.

## Build plan for Claude Code

Python, the Anthropic SDK, pytest, Pydantic for task and result schemas. Results go to JSONL, one line per episode.

```
harness-sensitivity/
  tasks/           # one module per domain: backend, tools, seed data, grader, reference solution
  perturb/         # one function per condition: (tools, observation) -> (tools, observation)
  runner/          # agent loop, retries, turn cap, JSONL logging
  analysis/        # bootstrap CIs, charts, flipped-episode sampler
  tests/           # reference solution passes every task in every condition
  results/
  SPEC.md          # this document
```

**Build order**

1. Task framework and 2 tasks, with reference solutions passing under pytest
2. Perturbation functions, each tested to keep the reference solution passing
3. Agent loop for one model, then the pilot
4. Remaining 23 tasks (good work to hand to parallel agents under AOS)
5. Full runs, then analysis

The test in step 2 is the guard that makes the study valid: if a perturbation breaks the reference solution, it isn't semantics-preserving.

## One-week timeline

The deliverable by next Wednesday is a public repo plus a short write-up with the headline chart.

- [ ] **Wed–Thu:** task framework, first 2 tasks, perturbations with guard tests
- [ ] **Fri:** agent loop and pilot on all three models; set the budget
- [ ] **Sat–Sun:** remaining 23 tasks; start Haiku and Sonnet runs
- [ ] **Mon:** Opus runs; analysis and charts
- [ ] **Tue:** failure analysis on flipped episodes; draft the write-up
- [ ] **Wed:** publish the repo and write-up; LinkedIn post

If the week slips, cut to 15 tasks and drop Opus before cutting the guard tests or the failure analysis.
