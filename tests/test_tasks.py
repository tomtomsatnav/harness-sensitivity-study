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


# --- support ---------------------------------------------------------------

def _prio(tid, p):
    return ("set_priority", {"ticket_id": tid, "priority": p})


def _dup(tid, of):
    return ("close_ticket", {"ticket_id": tid, "resolution": "duplicate", "duplicate_of": of})


def _route(tid, queue):
    return ("route_ticket", {"ticket_id": tid, "queue": queue})


def _close(tid, resolution="resolved"):
    return ("close_ticket", {"ticket_id": tid, "resolution": resolution})


def _esc(tid):
    return ("escalate_ticket", {"ticket_id": tid})


@pytest.mark.parametrize("calls, why", [
    ([_prio("T-2101", "high"), _prio("T-2102", "low"), _prio("T-2103", "urgent")], "ignored security override"),
    ([_prio("T-2101", "high"), _prio("T-2102", "urgent"), _prio("T-2103", "high")], "ignored churn_risk raise"),
    ([_prio("T-2101", "urgent"), _prio("T-2102", "urgent"), _prio("T-2103", "urgent")], "wrong matrix cell"),
    ([_prio("T-2101", "high"), _prio("T-2102", "urgent"), _prio("T-2103", "urgent"), _prio("T-2104", "low")],
     "touched a closed ticket"),
    ([_prio("T-2101", "high"), _prio("T-2102", "urgent"), _prio("T-2103", "urgent"), _prio("T-2100", "high")],
     "changed an already prioritised ticket"),
])
def test_support_priority_grader_rejects(calls, why):
    assert not _env_after("support_01_priority_rules", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_route("T-3101", "payments"), _route("T-3102", "billing"), _route("T-3103", "payments")],
     "routed by subject wording"),
    ([_route("T-3101", "identity"), _route("T-3102", "data"), _route("T-3103", "payments")],
     "ignored billing_dispute override"),
    ([_route("T-3101", "identity"), _route("T-3102", "billing")], "left a ticket in intake"),
    ([_route("T-3101", "identity"), _route("T-3102", "billing"), _route("T-3103", "payments"),
      _route("T-3100", "data")], "re-routed a ticket already placed"),
])
def test_support_routing_grader_rejects(calls, why):
    assert not _env_after("support_02_route_by_area", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_dup("T-4101", "T-4100"), _dup("T-4103", "T-4090"), _dup("T-4102", "T-4100")], "closed the near-duplicate"),
    ([_dup("T-4101", "T-4100")], "missed a duplicate"),
    ([_dup("T-4101", "T-4090"), _dup("T-4103", "T-4090")], "named the wrong original"),
    ([_dup("T-4101", "T-4100"), _dup("T-4103", "T-4090"), _close("T-4102", "wont_fix")],
     "closed T-4102 another way"),
])
def test_support_duplicates_grader_rejects(calls, why):
    assert not _env_after("support_03_close_duplicates", calls).grade().success, why


def test_support_duplicate_must_reference_resolved_ticket():
    env = Environment(TASKS_BY_ID["support_03_close_duplicates"], BASE)
    _, is_error = env.call("close_ticket", {"ticket_id": "T-4102", "resolution": "duplicate",
                                            "duplicate_of": "T-4101"})
    assert is_error


@pytest.mark.parametrize("calls, why", [
    ([_esc("T-5102")], "escalated the oldest unanswered ticket"),
    ([_esc("T-5103")], "escalated an already answered ticket"),
    ([_esc("T-5101"), _esc("T-5105")], "escalated an extra ticket"),
    ([_esc("T-5104")], "escalated a ticket still inside its SLA"),
])
def test_support_sla_grader_rejects(calls, why):
    assert not _env_after("support_04_sla_escalation", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_close("T-6103"), _close("T-6101")], "missed T-6105"),
    ([_close("T-6103"), _close("T-6101"), _close("T-6105"), _close("T-6102")], "closed a relapsed ticket"),
    ([_close("T-6103"), _close("T-6101"), _close("T-6105"), _close("T-6104")], "closed a ticket told to stay open"),
    ([_close("T-6103", "wont_fix"), _close("T-6101"), _close("T-6105")], "wrong resolution"),
])
def test_support_close_grader_rejects(calls, why):
    assert not _env_after("support_05_close_in_order", calls).grade().success, why


def test_support_cannot_close_before_parent():
    env = Environment(TASKS_BY_ID["support_05_close_in_order"], BASE)
    _, is_error = env.call("close_ticket", {"ticket_id": "T-6101", "resolution": "resolved"})
    assert is_error
