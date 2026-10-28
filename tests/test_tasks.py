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
