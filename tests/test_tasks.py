"""Task-level checks: the rules in the spec, and graders that reject wrong end states."""

import pytest

from perturb import CONDITIONS
from runner.env import Environment, run_reference
from tasks import ALL_TASKS, TASKS_BY_ID

BASE = CONDITIONS["baseline"]
TASK_IDS = [t.task_id for t in ALL_TASKS]


@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_task_rules(task):
    assert 3 <= len(task.tools) <= 6
    _, log = run_reference(task, BASE)
    assert 3 <= len(log) <= 10, f"reference solution takes {len(log)} steps"


@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_backend_is_deterministic(task):
    a, b = task.make_backend(), task.make_backend()
    assert a.snapshot() == b.snapshot()
    _, log_a = run_reference(task, BASE)
    _, log_b = run_reference(task, BASE)
    assert log_a == log_b


@pytest.mark.parametrize("task", ALL_TASKS, ids=TASK_IDS)
def test_doing_nothing_fails(task):
    assert not Environment(task, BASE).grade().success


def _env_after(task_id, calls):
    env = Environment(TASKS_BY_ID[task_id], BASE)
    for name, args in calls:
        env.call(name, args)
    return env


@pytest.mark.parametrize("calls, why", [
    ([("issue_refund", {"order_id": "ORD-61230", "amount": 92.49, "reason": "damaged"})], "refunded shipping"),
    ([("issue_refund", {"order_id": "ORD-60511", "amount": 129.99, "reason": "damaged"})], "wrong order"),
    ([("issue_refund", {"order_id": "ORD-61230", "amount": 84.5, "reason": "other"})], "wrong reason"),
    ([("issue_refund", {"order_id": "ORD-61230", "amount": 84.5, "reason": "damaged"})] * 2, "refunded twice"),
])
def test_orders_grader_rejects(calls, why):
    assert not _env_after("orders_01_damaged_refund", calls).grade().success, why


def test_orders_refuses_undelivered():
    env = Environment(TASKS_BY_ID["orders_01_damaged_refund"], BASE)
    _, is_error = env.call("issue_refund", {"order_id": "ORD-61377", "amount": 10.0, "reason": "damaged"})
    assert is_error and env.tool_errors == 1


@pytest.mark.parametrize("calls, why", [
    ([("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "CHI", "to_warehouse_id": "BOS", "quantity": 28})],
     "CHI drops below safety stock"),
    ([("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "DAL", "to_warehouse_id": "BOS", "quantity": 40})],
     "moved 40 instead of the 28 shortfall"),
    ([("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "DAL", "to_warehouse_id": "BOS", "quantity": 14}),
      ("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "DAL", "to_warehouse_id": "BOS", "quantity": 14})],
     "two transfers"),
])
def test_inventory_grader_rejects(calls, why):
    assert not _env_after("inventory_01_single_transfer", calls).grade().success, why


def test_inventory_cannot_move_reserved_units():
    env = Environment(TASKS_BY_ID["inventory_01_single_transfer"], BASE)
    # SEA has 90 on hand but 70 reserved.
    _, is_error = env.call("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "SEA",
                                              "to_warehouse_id": "BOS", "quantity": 28})
    assert is_error


def test_invalid_input_is_counted():
    env = Environment(TASKS_BY_ID["inventory_01_single_transfer"], BASE)
    _, e1 = env.call("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "DAL",
                                        "to_warehouse_id": "BOS", "quantity": "28"})
    _, e2 = env.call("no_such_tool", {})
    _, e3 = env.call("get_stock", {"sku": "WID-204"})
    assert e1 and e2 and e3
    assert env.invalid_tool_calls == 3
    assert env.backend.transfers == []
