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


# --- new orders and inventory tasks ----------------------------------------------------------------

def _refund(oid, amount, reason):
    return ("issue_refund", {"order_id": oid, "amount": amount, "reason": reason})


def _credit(cid, oid, amount):
    return ("issue_store_credit", {"customer_id": cid, "order_id": oid, "amount": amount})


def _xfer(sku, src, dst, qty):
    return ("transfer_stock", {"sku": sku, "from_warehouse_id": src, "to_warehouse_id": dst, "quantity": qty})


@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-70162", 85.0, "not_received")], "refunded without shipping"),
    ([_refund("ORD-70115", 52.5, "not_received")], "refunded a delivered order"),
    ([_refund("ORD-70162", 91.25, "other")], "wrong reason"),
    ([_refund("ORD-70162", 91.25, "not_received")] * 2, "refunded twice"),
    ([_refund("ORD-70162", 91.25, "not_received"), _refund("ORD-70115", 52.5, "not_received")], "extra refund"),
])
def test_orders_02_grader_rejects(calls, why):
    assert not _env_after("orders_02_lost_in_transit", calls).grade().success, why


def test_orders_02_refuses_still_shipped():
    env = Environment(TASKS_BY_ID["orders_02_lost_in_transit"], BASE)
    _, is_error = env.call("issue_refund", {"order_id": "ORD-70240", "amount": 24.0, "reason": "not_received"})
    assert is_error and env.tool_errors == 1


@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-71004", 36.0, "damaged")], "cash refund outside the window"),
    ([_credit("C-2310", "ORD-71004", 36.0)], "credit on the carafe line only"),
    ([_credit("C-2310", "ORD-71004", 36.5)], "credit counted shipping"),
    ([_credit("C-2310", "ORD-71190", 8.75)], "credit on the newer order"),
    ([_credit("C-2310", "ORD-71004", 34.0), _credit("C-2310", "ORD-71004", 34.0)], "credited twice"),
    ([_credit("C-2310", "ORD-71004", 34.0), _refund("ORD-71004", 34.0, "damaged")], "credit plus refund"),
])
def test_orders_03_grader_rejects(calls, why):
    assert not _env_after("orders_03_outside_window_credit", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-72144", 76.0, "wrong_item")], "gold shipping not refunded"),
    ([_refund("ORD-72144", 106.75, "wrong_item")], "refunded the whole order"),
    ([_refund("ORD-72031", 44.5, "wrong_item")], "older order outside the window"),
    ([_refund("ORD-72144", 38.0 + 8.75, "wrong_item")], "only one board refunded"),
    ([_refund("ORD-72144", 84.75, "damaged")], "wrong reason"),
])
def test_orders_04_grader_rejects(calls, why):
    assert not _env_after("orders_04_wrong_item_line", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-73010", 84.0, "damaged")], "silver Priya Nair's order"),
    ([_refund("ORD-73077", 84.5, "damaged")], "Priya Nayar's order"),
    ([_refund("ORD-73055", 103.0, "damaged")], "gold shipping left out"),
    ([_refund("ORD-73055", 84.5, "damaged")], "refunded only the duvet line"),
    ([_refund("ORD-73055", 108.5, "damaged"), _refund("ORD-73010", 84.0, "damaged")], "extra refund to wrong Priya"),
])
def test_orders_05_grader_rejects(calls, why):
    assert not _env_after("orders_05_similar_names", calls).grade().success, why


@pytest.mark.parametrize("task_id, calls", [
    ("orders_02_lost_in_transit", [_refund("ORD-70162", 91.25, "not_received")]),
    ("orders_03_outside_window_credit", [_credit("C-2310", "ORD-71004", 34.0)]),
    ("orders_04_wrong_item_line", [_refund("ORD-72144", 84.75, "wrong_item")]),
    ("orders_05_similar_names", [_refund("ORD-73055", 108.5, "damaged")]),
    ("inventory_02_split_sources", [_xfer("WID-204", "DAL", "BOS", 40), _xfer("WID-204", "PHX", "BOS", 5)]),
    ("inventory_03_three_skus", [_xfer("WID-204", "DAL", "BOS", 18), _xfer("WID-310", "CHI", "BOS", 26)]),
    ("inventory_04_rebalance_two_sites", [_xfer("WID-410", "DAL", "BOS", 15), _xfer("WID-410", "DAL", "SEA", 30)]),
    ("inventory_05_warehouse_closure", [_xfer("WID-204", "PHX", "DAL", 25), _xfer("GAD-550", "PHX", "DAL", 36)]),
])
def test_new_tasks_correct_end_state_passes(task_id, calls):
    assert _env_after(task_id, calls).grade().success


@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-204", "DAL", "BOS", 45)], "DAL pushed below safety stock"),
    ([_xfer("WID-204", "CHI", "BOS", 15), _xfer("WID-204", "DAL", "BOS", 30)], "CHI first because it has most on hand"),
    ([_xfer("WID-204", "DAL", "BOS", 25), _xfer("WID-204", "PHX", "BOS", 20)], "even-ish split, not largest surplus first"),
    ([_xfer("WID-204", "DAL", "BOS", 40)], "shortfall of 5 left"),
    ([_xfer("WID-204", "DAL", "BOS", 40), _xfer("WID-204", "SEA", "BOS", 5)], "SEA below safety stock"),
])
def test_inventory_02_grader_rejects(calls, why):
    assert not _env_after("inventory_02_split_sources", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-204", "DAL", "BOS", 18)], "WID-310 never moved"),
    ([_xfer("WID-204", "DAL", "BOS", 18), _xfer("WID-310", "DAL", "BOS", 26)], "DAL cannot cover WID-310 above safety"),
    ([_xfer("WID-204", "PHX", "BOS", 18), _xfer("WID-310", "CHI", "BOS", 26)], "PHX below safety stock"),
    ([_xfer("WID-204", "DAL", "BOS", 18), _xfer("WID-310", "CHI", "BOS", 26), _xfer("GAD-550", "DAL", "BOS", 5)],
     "moved GAD-550 that already had enough"),
    ([_xfer("WID-204", "DAL", "BOS", 18), _xfer("WID-310", "CHI", "BOS", 30)], "overshot WID-310"),
])
def test_inventory_03_grader_rejects(calls, why):
    assert not _env_after("inventory_03_three_skus", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-410", "DAL", "BOS", 15)], "SEA never topped up"),
    ([_xfer("WID-410", "CHI", "SEA", 30), _xfer("WID-410", "DAL", "BOS", 15)], "two donors"),
    ([_xfer("WID-410", "CHI", "BOS", 15), _xfer("WID-410", "CHI", "SEA", 30)], "CHI below safety stock"),
    ([_xfer("WID-410", "DAL", "BOS", 15), _xfer("WID-410", "DAL", "SEA", 35)], "SEA overshot"),
    ([_xfer("WID-410", "DAL", "SEA", 45)], "all to SEA"),
])
def test_inventory_04_grader_rejects(calls, why):
    assert not _env_after("inventory_04_rebalance_two_sites", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-204", "PHX", "DAL", 25)], "GAD-550 left behind"),
    ([_xfer("WID-204", "PHX", "DAL", 25), _xfer("GAD-550", "PHX", "DAL", 36), _xfer("WID-204", "BOS", "DAL", 5)],
     "touched another warehouse"),
    ([_xfer("WID-204", "PHX", "BOS", 25), _xfer("GAD-550", "PHX", "DAL", 36)], "sent WID-204 to BOS"),
    ([_xfer("WID-204", "PHX", "DAL", 20), _xfer("GAD-550", "PHX", "DAL", 36)], "WID-204 left 5 unreserved units"),
])
def test_inventory_05_grader_rejects(calls, why):
    assert not _env_after("inventory_05_warehouse_closure", calls).grade().success, why


def test_inventory_05_cannot_move_reserved_units():
    env = Environment(TASKS_BY_ID["inventory_05_warehouse_closure"], BASE)
    _, is_error = env.call("transfer_stock", {"sku": "WID-310", "from_warehouse_id": "PHX",
                                              "to_warehouse_id": "DAL", "quantity": 25})
    assert is_error and env.tool_errors == 1


# --- records ---------------------------------------------------------------------------------------

def _upd(rid, field, value):
    return ("update_record", {"record_id": rid, "field": field, "value": value})


def _arch(rid, into):
    return ("archive_record", {"record_id": rid, "merged_into": into})


@pytest.mark.parametrize("calls, why", [
    ([_upd("CRM-2001", "email", "priya.r.raman@example.org"), _upd("CRM-2001", "address", "9 Kestrel Court, York"),
      _upd("CRM-2001", "phone", "+44 7700 900456"), _arch("CRM-2044", "CRM-2001")], "took phone from the older-verified record"),
    ([_arch("CRM-2044", "CRM-2001")], "archived without merging newer fields"),
    ([_upd("CRM-2001", "email", "priya.r.raman@example.org"), _upd("CRM-2001", "address", "9 Kestrel Court, York")],
     "never archived the duplicate"),
    ([_upd("CRM-2044", "phone", "+44 7700 900123"), _arch("CRM-2001", "CRM-2044")], "wrong survivor"),
    ([_upd("CRM-2001", "email", "priya.r.raman@example.org"), _upd("CRM-2001", "address", "9 Kestrel Court, York"),
      _arch("CRM-2310", "CRM-2001")], "archived the similar-name decoy"),
])
def test_records_01_grader_rejects(calls, why):
    assert not _env_after("records_01_latest_verified", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_upd("CUS-4410", "email", "tomas.becker@example.org"), _upd("CUS-4410", "address", "Karl-Marx-Allee 90, Berlin"),
      _upd("CUS-4410", "phone", "+49 30 5550 9999"), _arch("CUS-4472", "CUS-4410")], "invented phone"),
    ([_upd("CUS-4410", "email", "tomas.becker@example.org"), _arch("CUS-4472", "CUS-4410")], "address not merged"),
    ([_upd("CUS-4410", "email", "tomas.becker@example.org"), _upd("CUS-4410", "address", "Karl-Marx-Allee 90, Berlin"),
      _upd("CUS-4410", "phone", "n/a")], "phone overwritten and not archived"),
    ([_upd("CUS-4410", "address", "Karl-Marx-Allee 90, Berlin"), _arch("CUS-4472", "CUS-4410")], "email still crm"),
])
def test_records_02_grader_rejects(calls, why):
    assert not _env_after("records_02_source_precedence", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_arch("LEE-301", "LEE-377")], "address not filled"),
    ([_upd("LEE-377", "address", "55 Dock Street, Bristol"), _upd("LEE-377", "email", "jlee@northwind.example"),
      _arch("LEE-301", "LEE-377")], "overwrote survivor's email"),
    ([_upd("LEE-377", "address", "55 Dock Street, Bristol"), _arch("LEE-352", "LEE-377")], "archived the decoy"),
    ([_upd("LEE-301", "phone", "+44 7700 900777"), _arch("LEE-377", "LEE-301")], "older record survived"),
    ([_upd("LEE-377", "address", "3 Hill Road, Bath"), _arch("LEE-301", "LEE-377")], "filled from the decoy"),
])
def test_records_03_grader_rejects(calls, why):
    assert not _env_after("records_03_same_name_decoy", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_upd("CUS-7120", "address", "21 Awolowo Road, Ikoyi, Lagos"), _arch("CUS-7185", "CUS-7120")],
     "kept staff-set phone"),
    ([_upd("CUS-7120", "phone", "+234 803 555 0199"), _arch("CUS-7185", "CUS-7120")], "address not updated"),
    ([_upd("CUS-7120", "phone", "+234 803 555 0199"), _upd("CUS-7120", "address", "21 Awolowo Road, Ikoyi, Lagos"),
      _upd("CUS-7120", "email", "amara.o@example.net"), _arch("CUS-7185", "CUS-7120")], "overwrote customer-set email"),
    ([_upd("CUS-7120", "phone", "+234 803 555 0199"), _upd("CUS-7120", "address", "21 Awolowo Road, Ikoyi, Lagos")],
     "never archived"),
])
def test_records_04_grader_rejects(calls, why):
    assert not _env_after("records_04_customer_entered", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_upd("MAR-188", "phone", "+39 333 555 0101"), _arch("MAR-100", "MAR-188")], "consent still opted_in"),
    ([_upd("MAR-188", "marketing_consent", "opted_out"), _arch("MAR-100", "MAR-188")], "phone not merged"),
    ([_upd("MAR-188", "phone", "+39 333 555 0101"), _upd("MAR-188", "marketing_consent", "opted_out"),
      _upd("MAR-188", "email", "e.marchetti@example.com"), _arch("MAR-100", "MAR-188")], "replaced login email"),
    ([_upd("MAR-100", "marketing_consent", "opted_out"), _arch("MAR-188", "MAR-100")], "wrong survivor"),
    ([_upd("MAR-188", "phone", "+39 333 555 0101"), _upd("MAR-188", "marketing_consent", "opted_out")],
     "never archived"),
])
def test_records_05_grader_rejects(calls, why):
    assert not _env_after("records_05_active_subscription", calls).grade().success, why


def test_records_cannot_archive_active_subscription():
    env = Environment(TASKS_BY_ID["records_05_active_subscription"], BASE)
    _, is_error = env.call("archive_record", {"record_id": "MAR-188", "merged_into": "MAR-100"})
    assert is_error and env.tool_errors == 1


# ---- scheduling

D = "2026-11-03T"


def _book(room, start, end, people, subject="x"):
    return ("book_meeting", {"subject": subject, "room_id": room, "start": D + start, "end": D + end,
                             "attendee_ids": people})


_FIVE = ["P-ANA", "P-BEN", "P-CHL", "P-DEV", "P-ELI"]
_FOUR = ["P-ANA", "P-BEN", "P-CHL", "P-DEV"]
_THREE = ["P-PRI", "P-TOM", "P-SVE"]
_SCHEDULING_IDS = [t.task_id for t in ALL_TASKS if t.domain == "scheduling"]


def test_scheduling_has_five_tasks_with_unique_answers():
    from tasks import scheduling
    assert len(_SCHEDULING_IDS) == 5
    for sc in (scheduling._S01, scheduling._S02, scheduling._S03, scheduling._S04, scheduling._S05):
        assert len(scheduling.valid_options(sc)) == 1


@pytest.mark.parametrize("task_id, calls", [
    ("scheduling_01_vc_room_for_five", [_book("R-EMBER", "13:30", "14:30", _FIVE)]),
    ("scheduling_02_three_time_zones", [_book("R-EMBER", "10:00", "11:00", _THREE)]),
    ("scheduling_03_workshop_room_buffer", [_book("R-CEDAR", "10:30", "12:00", _FOUR)]),
    ("scheduling_04_client_demo_policy", [_book("R-EMBER", "13:30", "14:30", ["P-ANA", "P-BEN"])]),
    ("scheduling_05_reschedule_planning", [("cancel_booking", {"booking_id": "BK-7731"}),
                                           _book("R-DUNE", "15:00", "16:30", _FIVE)]),
])
def test_scheduling_correct_end_state_passes(task_id, calls):
    assert _env_after(task_id, calls).grade().success


@pytest.mark.parametrize("calls, why", [
    ([_book("R-CEDAR", "09:30", "10:30", _FIVE)], "earliest common slot in a big room without video conferencing"),
    ([_book("R-BIRCH", "09:30", "10:30", _FIVE)], "video-conference room that seats only four"),
    ([_book("R-DUNE", "13:30", "14:30", _FIVE)], "right time, room without video conferencing"),
    ([_book("R-EMBER", "13:30", "14:30", _FIVE[:4])], "Eli left out"),
    ([_book("R-EMBER", "13:30", "14:30", _FIVE), _book("R-CEDAR", "09:30", "10:30", _FIVE)], "two bookings"),
])
def test_scheduling_01_grader_rejects(calls, why):
    assert not _env_after("scheduling_01_vc_room_for_five", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_book("R-ATLAS", "16:00", "17:00", _THREE)], "all free on the clock but outside London and Berlin working hours"),
    ([_book("R-CEDAR", "10:00", "11:00", _THREE)], "right time, room without video conferencing"),
    ([_book("R-EMBER", "10:00", "11:00", _THREE[:2])], "Sven left out"),
    ([_book("R-EMBER", "10:00", "10:45", _THREE)], "wrong length"),
])
def test_scheduling_02_grader_rejects(calls, why):
    assert not _env_after("scheduling_02_three_time_zones", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_book("R-CEDAR", "10:00", "11:30", _FOUR)], "back to back with the 09:00-10:00 Cedar booking"),
    ([_book("R-BIRCH", "10:30", "12:00", _FOUR)], "ends back to back with the 12:00 Birch booking"),
    ([_book("R-EMBER", "10:30", "12:00", _FOUR)], "no whiteboard"),
    ([_book("R-CEDAR", "10:30", "12:00", _FOUR[:3])], "Dev left out"),
])
def test_scheduling_03_grader_rejects(calls, why):
    assert not _env_after("scheduling_03_workshop_room_buffer", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_book("R-ATLAS", "10:00", "11:00", ["P-ANA", "P-BEN"])], "earliest slot but not on the ground floor"),
    ([_book("R-EMBER", "12:00", "13:00", ["P-ANA", "P-BEN"])], "inside the midday blackout"),
    ([_book("R-NOOK", "10:00", "11:00", ["P-ANA", "P-BEN"])], "ground floor but seats two"),
])
def test_scheduling_04_grader_rejects(calls, why):
    assert not _env_after("scheduling_04_client_demo_policy", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_book("R-DUNE", "15:00", "16:30", _FIVE)], "booked the new slot but never cancelled the old booking"),
    ([("cancel_booking", {"booking_id": "BK-7731"})], "cancelled without rebooking"),
    ([("cancel_booking", {"booking_id": "BK-7731"}), _book("R-ATLAS", "15:00", "16:30", _FIVE)], "no whiteboard"),
    ([("cancel_booking", {"booking_id": "BK-7731"}), _book("R-BIRCH", "15:00", "16:30", _FIVE)], "room seats four"),
    ([("cancel_booking", {"booking_id": "BK-7731"}), _book("R-BIRCH", "10:00", "11:30", _FOUR)], "Eli left out, same slot"),
    ([("cancel_booking", {"booking_id": "BK-9501"}), _book("R-DUNE", "15:00", "16:30", _FIVE)], "cancelled the wrong booking"),
])
def test_scheduling_05_grader_rejects(calls, why):
    assert not _env_after("scheduling_05_reschedule_planning", calls).grade().success, why


def test_scheduling_refuses_double_booking():
    env = Environment(TASKS_BY_ID["scheduling_01_vc_room_for_five"], BASE)
    name, args = _book("R-ATLAS", "09:30", "10:30", _FIVE)
    _, is_error = env.call(name, args)
    assert is_error and env.tool_errors == 1


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


def test_scheduling_rejects_off_grid_start():
    # 10:15 in Cedar meets every stated rule except the half-hour grid the grader assumes.
    env = Environment(TASKS_BY_ID["scheduling_03_workshop_room_buffer"], BASE)
    name, args = _book("R-CEDAR", "10:15", "11:45", _FOUR)
    _, is_error = env.call(name, args)
    assert is_error and env.tool_errors == 1


# --- denser observations: records and scheduling ----------------------------------------------------

_DENSE_IDS = [t.task_id for t in ALL_TASKS if t.domain in ("records", "scheduling")]


def _tokens(obs):
    import json
    return len(json.dumps(obs)) / 3.6


@pytest.mark.parametrize("task_id", _DENSE_IDS)
def test_records_scheduling_observation_budget(task_id):
    """The reference solution sees 2,500-5,000 tokens of observations in total, none above 2,500."""
    _, log = run_reference(TASKS_BY_ID[task_id], BASE)
    sizes = [_tokens(obs) for _, _, obs in log]
    assert 2500 <= sum(sizes) <= 5000, f"total {sum(sizes):.0f}"
    assert max(sizes) <= 2500, f"largest observation {max(sizes):.0f}"


def test_records_duplicate_pair_is_unique():
    """Exactly the survivor and loser are active records sharing the target's name and date of birth,
    so the merge rules, which only ever read those two records, yield one field set."""
    from tasks import records
    for key, sc in records._SCENARIOS.items():
        backend = records.RecordsBackend(key)
        target = backend._record(sc["survivor"])
        twins = sorted(r["record_id"] for r in backend.records if r["status"] == "active"
                       and (r["name"], r["date_of_birth"]) == (target["name"], target["date_of_birth"]))
        assert twins == sorted([sc["survivor"], sc["loser"]]), key
        ids = [r["record_id"] for r in backend.records]
        assert len(ids) == len(set(ids)), key


# --- harder observations: size budget and uniqueness of the correct answer -------------------------
# Covers the orders, inventory and support domains. Tokens are estimated as len(json text) / 3.6.

_DENSE_DOMAINS = ("orders", "inventory", "support")
_DENSE_TASKS = [t for t in ALL_TASKS if t.domain in _DENSE_DOMAINS]


@pytest.mark.parametrize("task", _DENSE_TASKS, ids=[t.task_id for t in _DENSE_TASKS])
def test_observation_size_budget(task):
    _, log = run_reference(task, BASE)
    sizes = [len(BASE.encode(obs)) / 3.6 for _, _, obs in log]
    assert 2500 <= sum(sizes) <= 5000, f"{task.task_id}: {sum(sizes):.0f} tokens of observations"
    assert max(sizes) <= 2500, f"{task.task_id}: largest observation is {max(sizes):.0f} tokens"


def _orders_data(task_id):
    b = TASKS_BY_ID[task_id].make_backend()
    assert len({o["order_id"] for o in b.orders}) == len(b.orders), "order IDs collide"
    return b


def _mentions(order, word):
    return any(word in i["description"].lower() for i in order["items"])


def test_orders_01_latest_order_is_unique():
    b = _orders_data("orders_01_damaged_refund")
    mine = [o for o in b.orders if o["customer_id"] == "C-1042"]
    assert len(mine) >= 15
    newest = max(o["placed_at"] for o in mine)
    assert [o["order_id"] for o in mine if o["placed_at"] == newest] == ["ORD-61230"]


def test_orders_02_lost_order_is_unique():
    b = _orders_data("orders_02_lost_in_transit")
    mine = [o for o in b.orders if o["customer_id"] == "C-2201"]
    assert len(mine) >= 15
    assert [o["order_id"] for o in mine if o["status"] == "lost_in_transit"] == ["ORD-70162"]


def test_orders_03_carafe_order_is_unique():
    b = _orders_data("orders_03_outside_window_credit")
    mine = [o for o in b.orders if o["customer_id"] == "C-2310"]
    assert len(mine) >= 15
    assert [o["order_id"] for o in mine if _mentions(o, "carafe")] == ["ORD-71004"]


def test_orders_04_in_window_board_order_is_unique():
    from datetime import date
    b = _orders_data("orders_04_wrong_item_line")
    mine = [o for o in b.orders if o["customer_id"] == "C-1187"]
    assert len(mine) >= 15
    boards = [o for o in mine if _mentions(o, "cutting board")]
    assert len(boards) >= 3, "the out-of-window decoys should still be there"
    in_window = [o["order_id"] for o in boards if o["status"] == "delivered"
                 and (date(2026, 10, 20) - date.fromisoformat(o["delivered_at"])).days <= 30]
    assert in_window == ["ORD-72144"]


def test_orders_05_right_priya_and_duvet_order_are_unique():
    b = _orders_data("orders_05_similar_names")
    hits = [c for c in b.customers if "priya nair" in c["name"].lower()]
    assert len(hits) >= 5
    right = [c for c in hits if c["name"] == "Priya Nair" and c["email"].endswith("@fernhill.example")]
    assert [c["customer_id"] for c in right] == ["C-3302"]
    mine = [o for o in b.orders if o["customer_id"] == "C-3302"]
    assert len(mine) >= 15
    assert [o["order_id"] for o in mine if _mentions(o, "duvet")] == ["ORD-73055"]


def _single_transfer_solutions(task_id, sku, to):
    """Every single transfer of `sku` into `to` that the grader accepts: (from, quantity) pairs."""
    task = TASKS_BY_ID[task_id]
    sites = [w["warehouse_id"] for w in task.make_backend().warehouses]
    found = []
    for src in sites:
        if src == to:
            continue
        for qty in range(1, 200):
            env = Environment(task, BASE)
            _, is_error = env.call("transfer_stock", {"sku": sku, "from_warehouse_id": src,
                                                      "to_warehouse_id": to, "quantity": qty})
            if not is_error and env.grade().success:
                found.append((src, qty))
    return found


def test_inventory_01_exactly_one_single_transfer_passes():
    assert _single_transfer_solutions("inventory_01_single_transfer", "WID-204", "BOS") == [("DAL", 28)]


def test_inventory_02_surplus_order_is_strict():
    from tasks.inventory import SplitBackend, _surplus
    rows = {w: _surplus(v) for (s, w), v in SplitBackend.STOCK.items() if s == "WID-204" and w != "BOS"}
    assert len(rows) >= 9
    ranked = sorted(rows.values(), reverse=True)
    assert ranked[0] == 40 < 45, "no single warehouse may cover the shortfall"
    assert ranked[0] > ranked[1] > ranked[2], "the first two donors must be unambiguous"
    assert rows["DAL"] == 40 and rows["PHX"] == 35


def test_inventory_03_each_short_sku_has_one_source():
    from tasks.inventory import MultiSkuBackend, _avail, _surplus
    stock = MultiSkuBackend.STOCK
    for sku, need in (("WID-204", 18), ("WID-310", 26)):
        assert 30 - _avail(stock[(sku, "BOS")]) == need
        sources = [w for (s, w), v in stock.items() if s == sku and w != "BOS" and _surplus(v) >= need]
        assert len(sources) == 1, (sku, sources)
        assert sum(1 for (s, _) in stock if s == sku) >= 7
    assert _avail(stock[("GAD-550", "BOS")]) >= 30


def test_inventory_04_exactly_one_donor_covers_both():
    from tasks.inventory import RebalanceBackend, _surplus
    stock = RebalanceBackend.STOCK
    donors = [w for (s, w), v in stock.items()
              if s == "WID-410" and w not in ("BOS", "SEA") and _surplus(v) >= 45]
    assert donors == ["DAL"]
    assert sum(1 for (s, _) in stock if s == "WID-410") >= 10


def test_inventory_05_only_two_skus_are_movable_at_phx():
    from tasks.inventory import ClosureBackend, _avail
    phx = {s: v for (s, w), v in ClosureBackend.STOCK.items() if w == "PHX"}
    assert len(phx) >= 10
    assert sorted(s for s, v in phx.items() if _avail(v) > 0) == ["GAD-550", "WID-204"]


def _support_seed(task_id):
    return TASKS_BY_ID[task_id].make_backend().tickets


def test_support_01_exactly_the_expected_tickets_need_a_priority():
    tickets = _support_seed("support_01_priority_rules")
    assert len(tickets) >= 15
    todo = sorted(t["ticket_id"] for t in tickets if t["status"] == "open" and t["priority"] is None)
    assert todo == ["T-2101", "T-2102", "T-2103"]


def test_support_02_exactly_the_expected_tickets_sit_in_intake():
    tickets = _support_seed("support_02_route_by_area")
    assert len(tickets) >= 15
    todo = sorted(t["ticket_id"] for t in tickets if t["status"] == "open" and t["queue"] == "intake")
    assert todo == ["T-3101", "T-3102", "T-3103"]


def test_support_03_duplicate_pairs_are_exactly_the_expected_ones():
    tickets = _support_seed("support_03_close_duplicates")
    assert len(tickets) >= 15
    resolved = [t for t in tickets if t["status"] == "resolved"]
    assert all(t["error_code"] is not None for t in resolved)
    pairs = {(t["ticket_id"], r["ticket_id"]) for t in tickets for r in resolved
             if t["status"] == "open" and (t["product_area"], t["error_code"]) == (r["product_area"], r["error_code"])}
    assert pairs == {("T-4101", "T-4100"), ("T-4103", "T-4090")}
    # The decoy shares a code but not an area.
    decoy = next(t for t in tickets if t["ticket_id"] == "T-4104")
    assert decoy["error_code"] == "SSO-114" and decoy["product_area"] != "auth"


def test_support_04_exactly_one_open_ticket_breaches():
    from datetime import datetime
    from tasks.support import _NOW, _SLA_POLICY
    targets = {(r["plan"], r["priority"]): r["response_hours"] for r in _SLA_POLICY["targets"]}
    tickets = _support_seed("support_04_sla_escalation")
    assert sum(t["status"] == "open" for t in tickets) >= 15
    breached = []
    for t in tickets:
        if t["status"] != "open":
            continue
        created = datetime.fromisoformat(t["created_at"])
        stop = datetime.fromisoformat(t["first_response_at"]) if t["first_response_at"] else _NOW
        if (stop - created).total_seconds() / 3600 > targets[(t["customer"]["plan"], t["priority"])]:
            breached.append(t["ticket_id"])
    assert breached == ["T-5101"]


def test_support_05_exactly_the_expected_tickets_ask_to_be_closed():
    tickets = _support_seed("support_05_close_in_order")
    assert len(tickets) >= 15
    open_bodies = {t["ticket_id"]: t["body"].lower() for t in tickets if t["status"] == "open"}
    assert len(open_bodies) == 6
    asked = sorted(tid for tid, body in open_bodies.items() if "please close this ticket" in body)
    assert asked == ["T-6101", "T-6103", "T-6105"]
    assert "please don't close" in open_bodies["T-6104"]
