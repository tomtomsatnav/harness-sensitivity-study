"""Guard tests: every perturbation must be semantics-preserving.

If a perturbation breaks the reference solution, loses information on the way to the
agent, or changes what the tools are, it is not presentation-only and the study is invalid.
"""

import pytest

from perturb import CONDITIONS, STUDY_CONDITIONS
from perturb.synonyms import SYNONYMS, UNMAPPED
from runner.env import run_reference
from tasks import ALL_TASKS

TASK_IDS = [t.task_id for t in ALL_TASKS]
COND_NAMES = list(CONDITIONS)


def _keys(data, acc=None):
    acc = set() if acc is None else acc
    if isinstance(data, dict):
        for k, v in data.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(data, list):
        for v in data:
            _keys(v, acc)
    return acc


@pytest.mark.parametrize("cond_name", COND_NAMES)
@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_reference_solution_passes(task, cond_name):
    env, _ = run_reference(task, CONDITIONS[cond_name])
    grade = env.grade()
    assert grade.success, f"{task.task_id} under {cond_name}: {grade.detail}"


@pytest.mark.parametrize("cond_name", COND_NAMES)
@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_observations_round_trip(task, cond_name):
    """decode(encode(obs)) == obs for every observation the reference solution sees, plus errors."""
    cond = CONDITIONS[cond_name]
    _, log = run_reference(task, CONDITIONS["baseline"])
    observations = [obs for _, _, obs in log] + [{"error": "No order with ID 'X-1'."}]
    for obs in observations:
        assert cond.decode(cond.encode(obs)) == obs


@pytest.mark.parametrize("cond_name", COND_NAMES)
@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_tool_contract_unchanged(task, cond_name):
    """Same tool names and input schemas in every condition; only order and description wording may change."""
    base = {t["name"]: t["input_schema"] for t in CONDITIONS["baseline"].tools(task.tools)}
    pert = {t["name"]: t["input_schema"] for t in CONDITIONS[cond_name].tools(task.tools)}
    assert pert == base


@pytest.mark.parametrize("cond_name", [c for c in COND_NAMES if c != "baseline"])
def test_condition_changes_something(cond_name):
    """Each perturbation must actually differ from baseline on what the agent sees."""
    cond, base = CONDITIONS[cond_name], CONDITIONS["baseline"]
    for task in ALL_TASKS:
        if cond.applies_to == "tools":
            assert cond.tools(task.tools) != base.tools(task.tools), task.task_id
            assert cond.encode({"a_b": 1, "c": 2}) == base.encode({"a_b": 1, "c": 2})
        else:
            _, log = run_reference(task, base)
            assert any(cond.encode(obs) != base.encode(obs) for _, _, obs in log), task.task_id
            assert cond.tools(task.tools) == base.tools(task.tools)


def test_description_paraphrase_length():
    for task in ALL_TASKS:
        for spec in task.tools:
            ratio = len(spec.paraphrase) / len(spec.description)
            assert 0.9 <= ratio <= 1.1, (task.task_id, spec.name, ratio)


@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_synonyms_cover_every_key(task):
    _, log = run_reference(task, CONDITIONS["baseline"])
    keys = set().union(*(_keys(obs) for _, _, obs in log))
    missing = keys - set(SYNONYMS) - UNMAPPED
    assert not missing, f"add synonyms for {sorted(missing)} in perturb/synonyms.py"


def test_encoding_is_deterministic():
    obs = {"order_id": "ORD-1", "items": [{"sku": "A", "quantity": 2}], "status": "delivered"}
    for cond in CONDITIONS.values():
        assert cond.encode(obs) == cond.encode(dict(obs))


def test_study_conditions_exist():
    assert STUDY_CONDITIONS[0] == "baseline"
    assert len(STUDY_CONDITIONS) == 6
    assert all(c in CONDITIONS for c in STUDY_CONDITIONS)
