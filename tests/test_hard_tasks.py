"""The hard task set: each trap has a grader-rejection test, and each answer is shown to be unique.

The generic rules (tool count, step count, determinism, doing nothing fails) and the perturbation
guards cover the hard tasks through tests/test_tasks.py and tests/test_perturb.py.
"""

from datetime import date, datetime

import pytest

from perturb import CONDITIONS
from runner.env import Environment, run_reference
from tasks import HARD_TASKS, TASKS_BY_ID

BASE = CONDITIONS["baseline"]
HARD_IDS = [t.task_id for t in HARD_TASKS]


def _env_after(task_id, calls):
    env = Environment(TASKS_BY_ID[task_id], BASE)
    for name, args in calls:
        env.call(name, args)
    return env


def _refund(oid, amount, reason):
    return ("issue_refund", {"order_id": oid, "amount": amount, "reason": reason})


def _credit(cid, oid, amount):
    return ("issue_store_credit", {"customer_id": cid, "order_id": oid, "amount": amount})


def _xfer(sku, src, dst, qty):
    return ("transfer_stock", {"sku": sku, "from_warehouse_id": src, "to_warehouse_id": dst, "quantity": qty})


def _upd(rid, field, value):
    return ("update_record", {"record_id": rid, "field": field, "value": value})


def _arch(rid, into):
    return ("archive_record", {"record_id": rid, "merged_into": into})


D = "2026-11-03T"


def _book(room, start, end, people, subject="x"):
    return ("book_meeting", {"subject": subject, "room_id": room, "start": D + start, "end": D + end,
                             "attendee_ids": people})


def _cancel(bid):
    return ("cancel_booking", {"booking_id": bid})


def _prio(tid, p):
    return ("set_priority", {"ticket_id": tid, "priority": p})


def _esc(tid):
    return ("escalate_ticket", {"ticket_id": tid})


def _route(tid, queue):
    return ("route_ticket", {"ticket_id": tid, "queue": queue})


@pytest.mark.parametrize("task", HARD_TASKS, ids=HARD_IDS)
def test_hard_observations_fit_the_context(task):
    _, log = run_reference(task, BASE)
    assert max(len(BASE.encode(obs)) / 3.6 for _, _, obs in log) <= 2500


# --- correct end states, reached without the reference solution -----------------------------------

_REVIEW, _SYNC = ["P-ANA", "P-BEN", "P-CHL"], ["P-ANA", "P-BEN", "P-TOM"]
_DEBRIEF = ["P-ANA", "P-BEN", "P-DEV"]

CORRECT = {
    "orders_h1_refund_cap": [_refund("ORD-80231", 65.0, "damaged"), _credit("C-4410", "ORD-80231", 63.5)],
    "orders_h2_return_fee": [_refund("ORD-81455", 105.4, "other")],
    "inventory_h1_region_first": [_xfer("WID-204", "NJE", "BOS", 20), _xfer("WID-204", "DAL", "SEA", 30)],
    "inventory_h2_lane_routing": [_xfer("WID-204", "DAL", "LAX", 18), _xfer("WID-204", "LAX", "SEA", 30)],
    "records_h1_three_way_merge": [
        _upd("NH-533", "phone", "+44 7700 900410"), _upd("NH-533", "address", "17 Bell Lane, Reading"),
        _upd("NH-533", "marketing_consent", "opted_out"), _arch("NH-410", "NH-533"), _arch("NH-472", "NH-533")],
    "records_h2_history_overrides_ranking": [
        _upd("CUS-9210", "phone", "+33 6 55 01 02 77"), _upd("CUS-9210", "address", "3 Allee des Tilleuls, Lyon"),
        _arch("CUS-9277", "CUS-9210")],
    "scheduling_h1_back_to_back": [_book("R-FERN", "10:30", "11:30", _REVIEW), _book("R-FERN", "11:30", "12:00", _SYNC)],
    "scheduling_h2_cancel_then_earliest": [_cancel("BK-9701"), _book("R-HAVEN", "10:00", "11:00", _DEBRIEF)],
    "support_h1_triage_then_sla": [_prio("T-7101", "urgent"), _prio("T-7102", "urgent"), _prio("T-7103", "normal"),
                                   _esc("T-7101"), _esc("T-7103")],
    "support_h2_queue_capacity": [_route("T-9104", "identity"), _route("T-9101", "payments"),
                                  _route("T-9102", "billing"), _route("T-9103", "billing")],
}


@pytest.mark.parametrize("task_id", HARD_IDS)
def test_correct_end_state_passes(task_id):
    env = _env_after(task_id, CORRECT[task_id])
    assert env.tool_errors == 0 and env.invalid_tool_calls == 0
    assert env.grade().success, env.grade().detail


# --- orders ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-80231", 128.5, "damaged")], "ignored the cap"),
    ([_refund("ORD-80231", 65.0, "damaged")], "no store credit for the rest"),
    ([_credit("C-4410", "ORD-80231", 128.5)], "counted the June refund, so nothing in cash"),
    ([_refund("ORD-80231", 90.0, "damaged"), _credit("C-4410", "ORD-80231", 38.5)], "missed the September refund"),
    ([_refund("ORD-80231", 65.0, "damaged"), _credit("C-4410", "ORD-80231", 54.0)], "gold shipping left out"),
    ([_refund("ORD-80231", 65.0, "other"), _credit("C-4410", "ORD-80231", 63.5)], "wrong reason"),
    ([_refund("ORD-80231", 65.0, "damaged"), _credit("C-4410", "ORD-79880", 63.5)], "credit on another order"),
    ([_refund("ORD-80231", 65.0, "damaged"), _refund("ORD-80231", 63.5, "damaged")], "rest refunded in cash"),
])
def test_orders_h1_grader_rejects(calls, why):
    assert not _env_after("orders_h1_refund_cap", calls).grade().success, why


def test_orders_h1_window_arithmetic():
    from tasks.orders import TODAY, RefundCapBackend
    b = RefundCapBackend()
    mine = [o for o in b.orders if o["customer_id"] == "C-4410"]
    assert len(mine) >= 15
    newest = max(o["placed_at"] for o in mine)
    assert [o["order_id"] for o in mine if o["placed_at"] == newest] == ["ORD-80231"]
    opens = TODAY.toordinal() - RefundCapBackend.POLICY["cap_window_days"]
    inside = [r["amount"] for rs in RefundCapBackend.PRIOR_REFUNDS.values() for r in rs
              if date.fromisoformat(r["issued_at"]).toordinal() >= opens]
    assert sorted(inside) == [25.0, 60.0]
    assert round(b.get_order("ORD-80231")["total"], 2) == 128.5


@pytest.mark.parametrize("calls, why", [
    ([_refund("ORD-81455", 105.4, "damaged")], "wrong reason"),
    ([_refund("ORD-81455", 124.0, "other")], "restocking fee skipped"),
    ([_refund("ORD-81455", 124.1, "other")], "olive oil included, fee applied"),
    ([_refund("ORD-81455", 146.0, "other")], "olive oil included, no fee"),
    ([_refund("ORD-81455", 111.9, "other")], "shipping added"),
    ([_refund("ORD-81455", 133.88, "other")], "whole subtotal less fee"),
    ([_refund("ORD-80990", 52.7, "other")], "the older single-throw order"),
    ([_refund("ORD-81455", 105.4, "other"), _refund("ORD-80990", 52.7, "other")], "second refund"),
])
def test_orders_h2_grader_rejects(calls, why):
    assert not _env_after("orders_h2_return_fee", calls).grade().success, why


def test_orders_h2_target_order_is_unique_and_food_is_marked():
    from tasks.orders import TODAY, ReturnFeeBackend
    b = ReturnFeeBackend()
    mine = [o for o in b.orders if o["customer_id"] == "C-5120"]
    assert len(mine) >= 15
    throws = [o for o in mine if any("wool throw" in i["description"].lower() for i in o["items"])]
    assert len(throws) == 2
    hits = [o["order_id"] for o in throws
            if any("wool throw" in i["description"].lower() and i["quantity"] == 2 for i in o["items"])]
    assert hits == ["ORD-81455"]
    assert (TODAY - date.fromisoformat("2026-09-12")).days > 30, "the decoy must be outside the window"
    codes = {i["sku"]: i["tax_code"] for i in b.get_order("ORD-81455")["items"]}
    assert codes == {"THR-WOL-1": "HOME", "OIL-OLV-1": "FOOD", "CND-SET-3": "GEN"} or (
        codes["OIL-OLV-1"] == "FOOD" and codes["THR-WOL-1"] != "FOOD")
    # No order the agent might open shows a non-food item as FOOD.
    for o in mine:
        for i in b.get_order(o["order_id"])["items"]:
            assert (i["tax_code"] == "FOOD") == (i["sku"] in {"TEA-GRN-50", "TEA-EGY-40", "OIL-OLV-1"})


# --- inventory ------------------------------------------------------------------------------------

@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-204", "NJE", "BOS", 20), _xfer("WID-204", "NJE", "SEA", 30)], "largest surplus read before the first transfer"),
    ([_xfer("WID-204", "DAL", "BOS", 20), _xfer("WID-204", "NJE", "SEA", 30)], "same-region rule ignored for BOS"),
    ([_xfer("WID-204", "DAL", "BOS", 20), _xfer("WID-204", "DAL", "SEA", 30)], "largest surplus used for both"),
    ([_xfer("WID-204", "NJE", "BOS", 20), _xfer("WID-204", "CHI", "SEA", 30)], "most available, not most surplus"),
    ([_xfer("WID-204", "NJE", "BOS", 20)], "SEA never topped up"),
    ([_xfer("WID-204", "NJE", "BOS", 20), _xfer("WID-204", "PDX", "SEA", 10), _xfer("WID-204", "DAL", "SEA", 20)],
     "SEA topped up from two sources"),
    ([_xfer("WID-204", "NJE", "BOS", 20), _xfer("WID-204", "DAL", "SEA", 35)], "SEA overshot"),
])
def test_inventory_h1_grader_rejects(calls, why):
    assert not _env_after("inventory_h1_region_first", calls).grade().success, why


def test_inventory_h1_surplus_order_flips_after_first_transfer():
    from tasks.inventory import _WAREHOUSES, RegionBackend, _surplus
    region = {w["warehouse_id"]: w["region"] for w in _WAREHOUSES}
    rows = {w: v for (s, w), v in RegionBackend.STOCK.items() if s == "WID-204"}
    assert len(rows) >= 10
    assert [w for w in rows if w != "BOS" and region[w] == region["BOS"]] == ["NJE"]
    assert [w for w in rows if w != "SEA" and region[w] == region["SEA"]] == ["PDX"]
    assert _surplus(rows["PDX"]) < 30 < _surplus(rows["NJE"]) == 45
    ranked = sorted((_surplus(v), w) for w, v in rows.items() if w not in ("BOS", "SEA"))
    assert ranked[-1] == (45, "NJE") and ranked[-2] == (40, "DAL")
    assert 45 - 20 < 40, "after BOS's 20 units leave NJE, DAL has the largest surplus"


@pytest.mark.parametrize("calls, why", [
    ([_xfer("WID-204", "DAL", "LAX", 30), _xfer("WID-204", "LAX", "SEA", 30)], "LAX took in the whole shortfall"),
    ([_xfer("WID-204", "LAX", "SEA", 12)], "only LAX's own surplus moved"),
    ([_xfer("WID-204", "DAL", "LAX", 18)], "stock never reached SEA"),
    ([_xfer("WID-204", "CHI", "DAL", 18), _xfer("WID-204", "DAL", "LAX", 18), _xfer("WID-204", "LAX", "SEA", 30)],
     "needless third hop"),
    ([_xfer("WID-204", "DAL", "LAX", 14), _xfer("WID-204", "PDX", "SEA", 4), _xfer("WID-204", "LAX", "SEA", 26)],
     "two transfers into SEA"),
    ([_xfer("WID-204", "DAL", "LAX", 20), _xfer("WID-204", "LAX", "SEA", 30)], "LAX took in two spare units"),
])
def test_inventory_h2_grader_rejects(calls, why):
    assert not _env_after("inventory_h2_lane_routing", calls).grade().success, why


def test_inventory_h2_lanes_are_enforced_and_no_direct_source_suffices():
    from tasks.inventory import LaneBackend, _surplus
    env = Environment(TASKS_BY_ID["inventory_h2_lane_routing"], BASE)
    _, is_error = env.call("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "DAL",
                                              "to_warehouse_id": "SEA", "quantity": 30})
    assert is_error and env.tool_errors == 1
    _, is_error = env.call("transfer_stock", {"sku": "WID-204", "from_warehouse_id": "LAX",
                                              "to_warehouse_id": "SEA", "quantity": 30})
    assert is_error, "LAX cannot send 30 before it is topped up"
    rows = {w: v for (s, w), v in LaneBackend.STOCK.items() if s == "WID-204"}
    into_sea = [a for a, b in LaneBackend.LANES if b == "SEA"]
    assert sorted(into_sea) == ["LAX", "PDX"]
    assert all(_surplus(rows[w]) < 30 for w in into_sea)
    assert [a for a, b in LaneBackend.LANES if b == "PDX"] == []
    feeders = {a for a, b in LaneBackend.LANES if b == "LAX" and _surplus(rows.get(a, {"on_hand": 0, "reserved": 0, "safety_stock": 0})) >= 18}
    assert feeders == {"DAL"}


# --- records --------------------------------------------------------------------------------------

_H1_UPDATES = CORRECT["records_h1_three_way_merge"][:3]


@pytest.mark.parametrize("calls, why", [
    ([_upd("NH-410", "address", "17 Bell Lane, Reading"), _upd("NH-410", "marketing_consent", "opted_out"),
      _arch("NH-472", "NH-410")], "earliest-created record kept as survivor"),
    ([*_H1_UPDATES, _upd("NH-533", "email", "n.haddad@example.org"), _arch("NH-410", "NH-533"), _arch("NH-472", "NH-533")],
     "login email replaced by the most recently verified one"),
    ([_H1_UPDATES[0], _H1_UPDATES[1], _arch("NH-410", "NH-533"), _arch("NH-472", "NH-533")],
     "consent left opted_in because the survivor's is newer"),
    ([_H1_UPDATES[1], _H1_UPDATES[2], _arch("NH-410", "NH-533"), _arch("NH-472", "NH-533")],
     "phone kept from the survivor, not the latest verified"),
    ([*_H1_UPDATES, _arch("NH-410", "NH-533")], "only one duplicate archived"),
    ([*_H1_UPDATES, _arch("NH-410", "NH-533"), _arch("NH-472", "NH-533"), _arch("NH-601", "NH-533")],
     "archived the record with another birth date"),
])
def test_records_h1_grader_rejects(calls, why):
    assert not _env_after("records_h1_three_way_merge", calls).grade().success, why


def test_records_h1_cannot_archive_the_subscriber():
    env = Environment(TASKS_BY_ID["records_h1_three_way_merge"], BASE)
    _, is_error = env.call("archive_record", {"record_id": "NH-533", "merged_into": "NH-410"})
    assert is_error and env.tool_errors == 1


@pytest.mark.parametrize("calls, why", [
    ([_upd("CUS-9210", "phone", "+33 6 55 01 02 77"), _upd("CUS-9210", "address", "3 Allee des Tilleuls, Lyon"),
      _upd("CUS-9210", "email", "f.moreau@example.org"), _arch("CUS-9277", "CUS-9210")],
     "email taken from billing although the customer set the survivor's"),
    ([_upd("CUS-9210", "address", "3 Allee des Tilleuls, Lyon"), _arch("CUS-9277", "CUS-9210")],
     "phone kept because of an older customer-made change"),
    ([_upd("CUS-9210", "phone", "+33 6 55 01 02 77"), _arch("CUS-9277", "CUS-9210")],
     "address kept on the billing ranking"),
    ([_upd("CUS-9210", "phone", "+33 6 55 01 02 09"), _upd("CUS-9210", "address", "3 Allee des Tilleuls, Lyon"),
      _arch("CUS-9277", "CUS-9210")], "phone set to the historical customer value"),
    ([_upd("CUS-9210", "phone", "+33 6 55 01 02 77"), _upd("CUS-9210", "address", "3 Allee des Tilleuls, Lyon")],
     "never archived"),
    ([_upd("CUS-9277", "email", "felix.moreau@example.com"), _arch("CUS-9210", "CUS-9277")], "wrong survivor"),
])
def test_records_h2_grader_rejects(calls, why):
    assert not _env_after("records_h2_history_overrides_ranking", calls).grade().success, why


def test_records_h2_each_field_exercises_a_different_rule():
    from tasks.records import _SCENARIOS
    sc = _SCENARIOS["h2"]
    last = {}
    for rid, rows in sc["history"].items():
        for field in ("email", "phone", "address"):
            last[(rid, field)] = max((r for r in rows if r["field"] == field), key=lambda r: r["changed_at"])
    assert last[("CUS-9210", "email")]["changed_by"] == "customer" and last[("CUS-9277", "email")]["changed_by"] == "staff"
    assert last[("CUS-9210", "phone")]["changed_by"] == "staff" and last[("CUS-9277", "phone")]["changed_by"] == "staff"
    assert last[("CUS-9210", "address")]["changed_by"] == "customer" and last[("CUS-9277", "address")]["changed_by"] == "customer"
    assert last[("CUS-9277", "address")]["changed_at"] > last[("CUS-9210", "address")]["changed_at"]
    # Each record's current value is the value of its most recent change.
    for rec in sc["records"]:
        for field, entry in rec["fields"].items():
            assert entry["value"] == last[(rec["record_id"], field)]["value"]


# --- scheduling -----------------------------------------------------------------------------------

def test_scheduling_hard_answers_are_unique():
    from tasks import scheduling
    assert scheduling.valid_pair_options(scheduling._SH1) == [(scheduling._m("10:30"), "R-FERN")]
    assert scheduling.expected_cascade(scheduling._SH2) == (scheduling._m("10:00"), "R-HAVEN")
    # The traps exist: reading Tom's hours as HQ time opens a 13:00 block, and leaving the vendor demo
    # in place pushes the debrief to 16:00.
    from dataclasses import replace
    naive_tom = {**scheduling._SH1.people, "P-TOM": {**scheduling._SH1.people["P-TOM"], "utc_offset_hours": -5}}
    naive = scheduling.valid_pair_options(replace(scheduling._SH1, people=naive_tom))
    assert (scheduling._m("13:00"), "R-CEDAR") in naive
    stale = replace(scheduling._SH2, cancel_subject="nothing")
    assert scheduling.expected_cascade(stale)[0] == scheduling._m("16:00")


@pytest.mark.parametrize("calls, why", [
    ([_book("R-CEDAR", "13:00", "14:00", _REVIEW), _book("R-CEDAR", "14:00", "14:30", _SYNC)], "Tom outside London hours"),
    ([_book("R-FERN", "10:30", "11:30", _REVIEW), _book("R-LARK", "11:30", "12:00", _SYNC)], "different rooms"),
    ([_book("R-FERN", "10:30", "11:30", _REVIEW)], "second meeting never booked"),
    ([_book("R-FERN", "09:30", "10:30", _REVIEW), _book("R-FERN", "10:30", "11:00", _SYNC)], "Chloe busy at 10:00"),
    ([_book("R-FERN", "10:30", "11:30", _REVIEW), _book("R-FERN", "11:30", "12:00", _REVIEW)], "Tom left out of the sync"),
    ([_book("R-FERN", "10:30", "11:30", _REVIEW), _book("R-FERN", "12:00", "12:30", _SYNC)], "gap between the meetings"),
    ([_book("R-FERN", "10:30", "11:30", _SYNC), _book("R-FERN", "11:30", "12:00", _REVIEW)], "meetings swapped"),
])
def test_scheduling_h1_grader_rejects(calls, why):
    assert not _env_after("scheduling_h1_back_to_back", calls).grade().success, why


@pytest.mark.parametrize("calls, why", [
    ([_cancel("BK-9701"), _book("R-ATLAS", "10:00", "11:00", _DEBRIEF)], "bigger of the two free rooms"),
    ([_cancel("BK-9701"), _book("R-HAVEN", "16:00", "17:00", _DEBRIEF)], "slot computed before the cancellation"),
    ([_book("R-HAVEN", "16:00", "17:00", _DEBRIEF)], "demo never cancelled"),
    ([_cancel("BK-9702"), _book("R-HAVEN", "16:00", "17:00", _DEBRIEF)], "cancelled the prep instead of the demo"),
    ([_cancel("BK-9701"), _book("R-DUNE", "10:00", "11:00", _DEBRIEF)], "freed room has no video conferencing"),
    ([_cancel("BK-9701")], "cancelled without booking"),
    ([_cancel("BK-9701"), _book("R-HAVEN", "10:00", "11:00", _DEBRIEF[:2])], "Dev left out"),
])
def test_scheduling_h2_grader_rejects(calls, why):
    assert not _env_after("scheduling_h2_cancel_then_earliest", calls).grade().success, why


def test_scheduling_h2_slot_is_blocked_until_the_demo_is_cancelled():
    env = Environment(TASKS_BY_ID["scheduling_h2_cancel_then_earliest"], BASE)
    name, args = _book("R-HAVEN", "10:00", "11:00", _DEBRIEF)
    _, is_error = env.call(name, args)
    assert is_error and env.tool_errors == 1


# --- support --------------------------------------------------------------------------------------

@pytest.mark.parametrize("calls, why", [
    ([_prio("T-7101", "high"), _prio("T-7102", "urgent"), _prio("T-7103", "normal"), _esc("T-7103")],
     "churn raise skipped on T-7101, so its breach went unnoticed"),
    ([_prio("T-7101", "urgent"), _prio("T-7102", "urgent"), _prio("T-7103", "normal"), _esc("T-7101"), _esc("T-7102"),
      _esc("T-7103")], "T-7102 judged against the current time, not its first response"),
    ([_prio("T-7101", "urgent"), _prio("T-7102", "urgent"), _prio("T-7103", "low"), _esc("T-7101")],
     "churn raise skipped on T-7103"),
    ([_prio("T-7101", "urgent"), _prio("T-7102", "urgent"), _prio("T-7103", "normal"), _esc("T-7101")],
     "T-7103 breached at normal but not escalated"),
    ([_prio("T-7101", "urgent"), _prio("T-7102", "high"), _prio("T-7103", "normal"), _esc("T-7101"), _esc("T-7103")],
     "security override skipped"),
    ([*CORRECT["support_h1_triage_then_sla"], _esc("T-7104")], "escalated a ticket outside the request"),
])
def test_support_h1_grader_rejects(calls, why):
    assert not _env_after("support_h1_triage_then_sla", calls).grade().success, why


def test_support_h1_breaches_depend_on_the_raise():
    from tasks.support import _NOW, _PRIORITY_RULES, _SLA_POLICY, SupportBackend, _SEED_TRIAGE
    targets = {(r["plan"], r["priority"]): r["response_hours"] for r in _SLA_POLICY["targets"]}
    matrix = {(r["plan"], r["impact"]): r["priority"] for r in _PRIORITY_RULES["matrix"]}
    tickets = {t["ticket_id"]: t for t in SupportBackend(_SEED_TRIAGE).tickets}
    assert len(tickets) >= 15

    def hours(t):
        stop = datetime.fromisoformat(t["first_response_at"]) if t["first_response_at"] else _NOW
        return (stop - datetime.fromisoformat(t["created_at"])).total_seconds() / 3600

    t = tickets["T-7101"]
    assert matrix[("enterprise", "medium")] == "high"
    # 1.5 h: a breach at urgent (1 h), the priority after the churn raise, but not at high (2 h).
    assert targets[("enterprise", "urgent")] < hours(t) <= targets[("enterprise", "high")]
    t = tickets["T-7102"]
    assert hours(t) <= targets[("pro", "urgent")] < (_NOW - datetime.fromisoformat(t["created_at"])).total_seconds() / 3600
    t = tickets["T-7103"]
    assert matrix[("free", "low")] == "low"
    assert targets[("free", "normal")] < hours(t) < targets[("free", "low")]
    assert sorted(tid for tid, t in tickets.items() if t["status"] == "open" and t["priority"] is None) == list(
        ("T-7101", "T-7102", "T-7103"))


@pytest.mark.parametrize("calls, why", [
    ([_route("T-9104", "identity"), _route("T-9103", "payments"), _route("T-9102", "billing"), _route("T-9101", "billing")],
     "newest payments ticket took the last place"),
    ([_route("T-9104", "identity"), _route("T-9101", "billing"), _route("T-9102", "billing"), _route("T-9103", "billing")],
     "closed and resolved tickets counted toward the load"),
    ([_route("T-9104", "identity"), _route("T-9101", "payments"), _route("T-9102", "billing"), _route("T-9103", "payments")],
     "escalated ticket not counted toward the load"),
    ([_route("T-9104", "identity"), _route("T-9101", "payments"), _route("T-9102", "payments"), _route("T-9103", "billing")],
     "billing dispute routed by product area"),
    ([_route("T-9101", "payments"), _route("T-9102", "billing"), _route("T-9103", "billing")], "T-9104 left in intake"),
    ([*CORRECT["support_h2_queue_capacity"], _route("T-9090", "billing")], "moved a ticket already in a queue"),
])
def test_support_h2_grader_rejects(calls, why):
    assert not _env_after("support_h2_queue_capacity", calls).grade().success, why


def test_support_h2_payments_has_exactly_one_place_left():
    from tasks.support import _CAPACITY_ROUTING_RULES, _SEED_CAPACITY, SupportBackend
    tickets = SupportBackend(_SEED_CAPACITY).tickets
    assert len(tickets) >= 15
    load = sum(1 for t in tickets if t["queue"] == "payments" and t["status"] in ("open", "escalated"))
    parked = sum(1 for t in tickets if t["queue"] == "payments")
    limit = next(c["max_open"] for c in _CAPACITY_ROUTING_RULES["capacities"] if c["queue"] == "payments")
    assert load == limit - 1 and parked == limit + 1
    intake = sorted((t["created_at"], t["ticket_id"]) for t in tickets if t["queue"] == "intake" and t["status"] == "open")
    assert [tid for _, tid in intake] == ["T-9104", "T-9101", "T-9102", "T-9103"]
