"""Support tickets domain: triage, route and close by priority rules."""

from __future__ import annotations

import copy
import random
from datetime import datetime
from typing import Any, Callable

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

SEED = 1005

PRIORITIES = ["low", "normal", "high", "urgent"]
QUEUES = ["identity", "billing", "payments", "data", "mobile"]

_STATE_FIELDS = ("status", "priority", "queue", "resolution", "duplicate_of")


def _t(ticket_id: str, subject: str, body: str, plan: str = "pro", **kw: Any) -> dict[str, Any]:
    """Build a ticket seed record with defaults for everything a task does not care about."""
    ticket = {
        "ticket_id": ticket_id, "subject": subject, "body": body, "status": "open", "priority": None,
        "queue": "intake", "product_area": "platform", "category": "general", "impact": "medium",
        "error_code": None, "created_at": "2026-10-19T09:00", "first_response_at": None,
        "blocked_by": None, "duplicate_of": None, "resolution": None,
        "customer": {"name": kw.pop("customer_name", "Avery Lindqvist"), "plan": plan,
                     "churn_risk": kw.pop("churn_risk", False)},
    }
    ticket.update(kw)
    return ticket


# ---------------------------------------------------------------------------
# Rules exposed by the rule tools

_PRIORITY_RULES = {
    "policy_id": "PR-2026-2",
    "matrix": [
        {"plan": p, "impact": i, "priority": pr}
        for p, rows in {
            "enterprise": {"high": "urgent", "medium": "high", "low": "normal"},
            "pro": {"high": "high", "medium": "normal", "low": "low"},
            "free": {"high": "normal", "medium": "low", "low": "low"},
        }.items()
        for i, pr in rows.items()
    ],
    "rules": [
        "Start from the matrix row for the customer's plan and the ticket's impact field.",
        "Tickets in category security_incident are always urgent, whatever the plan or impact.",
        "If the customer has churn_risk set, raise the result one level (low, normal, high, urgent), never above urgent.",
    ],
}

_ROUTING_RULES = {
    "policy_id": "RT-2026-1",
    "routes": [
        {"product_area": "auth", "queue": "identity"},
        {"product_area": "invoicing", "queue": "billing"},
        {"product_area": "payments_api", "queue": "payments"},
        {"product_area": "reports", "queue": "data"},
        {"product_area": "mobile_app", "queue": "mobile"},
    ],
    "rules": [
        "Route on the ticket's product_area, never on wording in the subject, which the customer writes.",
        "Tickets in category billing_dispute go to the billing queue whatever their product area.",
    ],
}

_SLA_POLICY = {
    "policy_id": "SLA-2026-4",
    "targets": [
        {"plan": p, "priority": pr, "response_hours": h}
        for p, hours in {
            "enterprise": {"urgent": 1, "high": 2, "normal": 8, "low": 24},
            "pro": {"urgent": 2, "high": 4, "normal": 24, "low": 48},
            "free": {"urgent": 8, "high": 24, "normal": 72, "low": 120},
        }.items()
        for pr, h in hours.items()
    ],
    "rules": [
        "The clock runs from created_at to first_response_at.",
        "If first_response_at is null the clock is still running and stops at the current time.",
        "A ticket breaches its SLA when the clock exceeds the target hours for its plan and priority.",
    ],
}

_DUPLICATE_POLICY = {
    "policy_id": "DP-2026-1",
    "rules": [
        "A ticket is a duplicate only if a resolved ticket has the same product_area and the same error_code.",
        "Similar wording or a customer's claim that it is the same issue is not enough.",
        "Close duplicates with resolution 'duplicate' and name the resolved ticket in duplicate_of.",
        "Never close a ticket as a duplicate of a ticket that is not resolved.",
    ],
}


class SupportBackend:
    def __init__(self, seed_tickets: list[dict[str, Any]]) -> None:
        self.tickets = copy.deepcopy(seed_tickets)
        # Seeded shuffle so listings are not simply in ID order.
        random.Random(SEED).shuffle(self.tickets)

    def _ticket(self, ticket_id: str) -> dict[str, Any]:
        for t in self.tickets:
            if t["ticket_id"] == ticket_id:
                return t
        raise ToolError(f"No ticket with ID {ticket_id!r}.")

    def list_tickets(self, status: str | None = None) -> dict[str, Any]:
        return {
            "tickets": [
                {k: t[k] for k in ("ticket_id", "subject", "status", "priority", "queue")}
                for t in self.tickets
                if status is None or t["status"] == status
            ]
        }

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._ticket(ticket_id))

    def get_priority_rules(self) -> dict[str, Any]:
        return copy.deepcopy(_PRIORITY_RULES)

    def get_routing_rules(self) -> dict[str, Any]:
        return copy.deepcopy(_ROUTING_RULES)

    def get_sla_policy(self) -> dict[str, Any]:
        return copy.deepcopy(_SLA_POLICY)

    def get_duplicate_policy(self) -> dict[str, Any]:
        return copy.deepcopy(_DUPLICATE_POLICY)

    def set_priority(self, ticket_id: str, priority: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if priority not in PRIORITIES:
            raise ToolError(f"Priority must be one of {PRIORITIES}.")
        t["priority"] = priority
        return {"ticket_id": ticket_id, "priority": priority}

    def route_ticket(self, ticket_id: str, queue: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if queue not in QUEUES:
            raise ToolError(f"Unknown queue {queue!r}; queues are {QUEUES}.")
        t["queue"] = queue
        return {"ticket_id": ticket_id, "queue": queue}

    def close_ticket(self, ticket_id: str, resolution: str, duplicate_of: str | None = None) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if t["status"] not in ("open", "escalated"):
            raise ToolError(f"Ticket {ticket_id} has status {t['status']!r} and cannot be closed.")
        if t["blocked_by"] is not None:
            parent = self._ticket(t["blocked_by"])
            if parent["status"] not in ("closed", "resolved"):
                raise ToolError(
                    f"Ticket {ticket_id} is linked to {parent['ticket_id']}, which is still {parent['status']}."
                )
        if resolution == "duplicate":
            if duplicate_of is None:
                raise ToolError("A duplicate closure must name the ticket it duplicates.")
            original = self._ticket(duplicate_of)
            if original["status"] != "resolved":
                raise ToolError(f"Ticket {duplicate_of} is {original['status']}, not resolved, so it cannot be the original.")
        elif duplicate_of is not None:
            raise ToolError("duplicate_of can only be used with resolution 'duplicate'.")
        t["status"], t["resolution"], t["duplicate_of"] = "closed", resolution, duplicate_of
        return {"ticket_id": ticket_id, "status": "closed", "resolution": resolution, "duplicate_of": duplicate_of}

    def escalate_ticket(self, ticket_id: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if t["status"] != "open":
            raise ToolError(f"Only open tickets can be escalated; {ticket_id} is {t['status']}.")
        t["status"] = "escalated"
        return {"ticket_id": ticket_id, "status": "escalated"}

    def snapshot(self) -> dict[str, Any]:
        return {t["ticket_id"]: {f: t[f] for f in _STATE_FIELDS} for t in self.tickets}


# ---------------------------------------------------------------------------
# Grading helper: compare the whole final state against the seed plus expected changes

def _grade_state(seed: list[dict[str, Any]], changes: dict[str, dict[str, Any]]) -> Callable[[SupportBackend], Grade]:
    def grade(backend: SupportBackend) -> Grade:
        expected = {t["ticket_id"]: {f: t[f] for f in _STATE_FIELDS} for t in seed}
        for tid, fields in changes.items():
            expected[tid].update(fields)
        actual = backend.snapshot()
        for tid, want in expected.items():
            got = actual[tid]
            if got != want:
                diff = {f: (got[f], want[f]) for f in _STATE_FIELDS if got[f] != want[f]}
                return Grade(success=False, detail=f"{tid}: (actual, expected) differences {diff}")
        return Grade(success=True, detail="all tickets in the expected final state")
    return grade


def _make(seed: list[dict[str, Any]]) -> Callable[[], SupportBackend]:
    return lambda: SupportBackend(seed)


# ---------------------------------------------------------------------------
# Task 1: set priority from the plan x impact matrix, with overrides

_SEED_PRIORITY = [
    _t("T-2100", "Slow search results", "Search takes a few seconds on large projects.", "pro",
       priority="low", impact="low"),
    _t("T-2101", "Dashboard widgets misaligned", "Charts overlap on the main dashboard in Safari.",
       "enterprise", impact="medium", customer_name="Priya Raman"),
    _t("T-2102", "Password reset email looks odd", "I got a reset email I never asked for and now my "
       "login is showing sessions from another country.", "free", impact="low",
       category="security_incident", customer_name="Tomas Weil"),
    _t("T-2103", "Report export is timing out", "Monthly export fails every time. We renew next week and "
       "are evaluating alternatives.", "pro", impact="high", churn_risk=True, customer_name="Lena Fischer"),
    _t("T-2104", "Old invoice question", "Resolved by phone.", "free", status="closed", impact="low",
       resolution="resolved"),
]
_PRIORITY_EXPECTED = {"T-2101": "high", "T-2102": "urgent", "T-2103": "urgent"}


def priority_reference(call: CallFn) -> None:
    rules = call("get_priority_rules")
    matrix = {(r["plan"], r["impact"]): r["priority"] for r in rules["matrix"]}
    levels = ["low", "normal", "high", "urgent"]
    for summary in call("list_tickets", status="open")["tickets"]:
        if summary["priority"] is not None:
            continue
        t = call("get_ticket", ticket_id=summary["ticket_id"])
        priority = matrix[(t["customer"]["plan"], t["impact"])]
        if t["customer"]["churn_risk"]:
            priority = levels[min(levels.index(priority) + 1, 3)]
        if t["category"] == "security_incident":
            priority = "urgent"
        call("set_priority", ticket_id=t["ticket_id"], priority=priority)


# ---------------------------------------------------------------------------
# Task 2: route intake tickets by product area, with a billing_dispute override

_SEED_ROUTING = [
    _t("T-3100", "App crashes on launch", "Crash on iOS 19 at startup.", "pro",
       queue="mobile", product_area="mobile_app"),
    _t("T-3101", "Login page broken after payment", "After I paid, the sign-in page loops back to itself. "
       "Card was charged fine.", "pro", product_area="auth"),
    _t("T-3102", "Invoice shows the wrong amount", "The usage report I pulled gives a different total than "
       "the invoice. I am disputing the charge.", "enterprise", product_area="reports",
       category="billing_dispute"),
    _t("T-3103", "Export to CSV failing", "The charge endpoint returns 500 when we pass a currency code, "
       "so our CSV export of payments is empty.", "enterprise", product_area="payments_api"),
    _t("T-3104", "Old mobile question", "Answered.", "free", status="closed", resolution="resolved",
       product_area="mobile_app"),
]
_ROUTING_EXPECTED = {"T-3101": "identity", "T-3102": "billing", "T-3103": "payments"}


def routing_reference(call: CallFn) -> None:
    rules = call("get_routing_rules")
    routes = {r["product_area"]: r["queue"] for r in rules["routes"]}
    for summary in call("list_tickets", status="open")["tickets"]:
        if summary["queue"] != "intake":
            continue
        t = call("get_ticket", ticket_id=summary["ticket_id"])
        queue = "billing" if t["category"] == "billing_dispute" else routes[t["product_area"]]
        call("route_ticket", ticket_id=t["ticket_id"], queue=queue)


# ---------------------------------------------------------------------------
# Task 3: close only true duplicates of a resolved ticket

_SEED_DUPLICATES = [
    _t("T-4090", "SAML login loop", "Okta users bounce between the IdP and our app.", "enterprise",
       status="resolved", resolution="resolved", product_area="auth", error_code="SSO-114", queue="identity"),
    _t("T-4100", "Webhook retries stop after 3 attempts", "Our endpoint was down for a minute and retries "
       "stopped permanently.", "pro", status="resolved", resolution="resolved", product_area="webhooks",
       error_code="WH-429", queue="payments"),
    _t("T-4101", "Callbacks stop arriving after a hiccup", "Deliveries to our listener halted and never "
       "resumed after a short outage.", "pro", product_area="webhooks", error_code="WH-429"),
    _t("T-4102", "Webhook retries stop after 3 attempts", "Looks like the same issue as before, but now "
       "our listener answers with errors from the gateway.", "pro", product_area="webhooks",
       error_code="WH-503"),
    _t("T-4103", "Staff cannot sign in through Okta", "Everyone is stuck bouncing between screens when "
       "signing in.", "enterprise", product_area="auth", error_code="SSO-114"),
]
_DUPLICATE_EXPECTED = {
    "T-4101": {"status": "closed", "resolution": "duplicate", "duplicate_of": "T-4100"},
    "T-4103": {"status": "closed", "resolution": "duplicate", "duplicate_of": "T-4090"},
}


def duplicates_reference(call: CallFn) -> None:
    call("get_duplicate_policy")
    summaries = call("list_tickets")["tickets"]
    full = [call("get_ticket", ticket_id=s["ticket_id"]) for s in summaries]
    resolved = {(t["product_area"], t["error_code"]): t["ticket_id"] for t in full if t["status"] == "resolved"}
    for t in full:
        original = resolved.get((t["product_area"], t["error_code"]))
        if t["status"] == "open" and original is not None:
            call("close_ticket", ticket_id=t["ticket_id"], resolution="duplicate", duplicate_of=original)


# ---------------------------------------------------------------------------
# Task 4: escalate the single ticket that has breached its first-response SLA

_NOW = datetime(2026, 10, 20, 12, 0)
_SEED_SLA = [
    _t("T-5101", "Payment webhooks delayed", "Webhook deliveries are minutes late for all our tenants.",
       "enterprise", priority="urgent", created_at="2026-10-20T08:30"),
    _t("T-5102", "Feature request: dark mode", "Would love a dark theme.", "free", priority="normal",
       created_at="2026-10-18T09:00"),
    _t("T-5103", "API key rotation question", "How do I rotate keys without downtime?", "pro",
       priority="high", created_at="2026-10-19T06:00", first_response_at="2026-10-19T07:30"),
    _t("T-5104", "Bulk import stalls at 90%", "Import of 40k rows hangs near the end.", "pro",
       priority="high", created_at="2026-10-20T09:00"),
    _t("T-5105", "Invoice PDF missing logo", "Our logo is not appearing on invoices.", "enterprise",
       priority="normal", created_at="2026-10-20T06:00"),
]


def sla_reference(call: CallFn) -> None:
    targets = {(r["plan"], r["priority"]): r["response_hours"] for r in call("get_sla_policy")["targets"]}
    for summary in call("list_tickets", status="open")["tickets"]:
        t = call("get_ticket", ticket_id=summary["ticket_id"])
        created = datetime.fromisoformat(t["created_at"])
        stop = datetime.fromisoformat(t["first_response_at"]) if t["first_response_at"] else _NOW
        hours = (stop - created).total_seconds() / 3600
        if hours > targets[(t["customer"]["plan"], t["priority"])]:
            call("escalate_ticket", ticket_id=t["ticket_id"])


# ---------------------------------------------------------------------------
# Task 5: close confirmed tickets, parents before the tickets linked to them

_SEED_CLOSE_ORDER = [
    _t("T-6101", "Sync fails for shared folders", "Your patch fixed it for us, thanks. Please close this ticket.",
       "pro", blocked_by="T-6103"),
    _t("T-6102", "Sync fails again", "Thanks, the patch fixed it yesterday, but the sync is failing again "
       "this morning.", "pro"),
    _t("T-6103", "Sync outage on EU region", "Confirmed working on our side now. Please close this ticket.",
       "pro"),
    _t("T-6104", "Calendar feed not updating", "Is this fixed yet? No update for days. Please don't close "
       "this until it works.", "enterprise"),
    _t("T-6105", "Cannot reach API from office", "It was our firewall, all sorted on our end. "
       "Please close this ticket.", "free"),
]
_CLOSE_EXPECTED = {t: {"status": "closed", "resolution": "resolved"} for t in ("T-6101", "T-6103", "T-6105")}


def close_order_reference(call: CallFn) -> None:
    full = [call("get_ticket", ticket_id=s["ticket_id"]) for s in call("list_tickets", status="open")["tickets"]]
    confirmed = [t for t in full if "please close this ticket" in t["body"].lower()]
    # Tickets linked to a parent must be closed after it.
    for t in sorted(confirmed, key=lambda t: t["blocked_by"] is not None):
        call("close_ticket", ticket_id=t["ticket_id"], resolution="resolved")


# ---------------------------------------------------------------------------
# Tools

def _obj(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_TICKET_ID = {"type": "string", "description": "The ticket's ID."}
_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

LIST_TICKETS = ToolSpec(
    name="list_tickets",
    description="List support tickets, optionally filtered by status. Returns each ticket's ID, subject, status, priority and queue.",
    paraphrase="Return support tickets, with an optional status filter. Each entry gives the ticket's ID, subject, status, priority and queue.",
    input_schema=_obj({"status": {"type": "string", "enum": ["open", "escalated", "resolved", "closed"],
                                  "description": "Only return tickets with this status."}}, []),
)
GET_TICKET = ToolSpec(
    name="get_ticket",
    description="Get the full record of one ticket: body, product area, category, impact, error code, timestamps, linked ticket and customer.",
    paraphrase="Fetch everything about a single ticket: its body, product area, category, impact, error code, timestamps, linked ticket and customer.",
    input_schema=_obj({"ticket_id": _TICKET_ID}, ["ticket_id"]),
)
GET_PRIORITY_RULES = ToolSpec(
    name="get_priority_rules",
    description="Return the priority rules: the plan and impact matrix plus the override rules that adjust its result.",
    paraphrase="Get the priority rules: the plan-and-impact matrix along with the override rules that modify its result.",
    input_schema=_NO_ARGS,
)
GET_ROUTING_RULES = ToolSpec(
    name="get_routing_rules",
    description="Return the routing table that maps each product area to a support queue, plus any exceptions to it.",
    paraphrase="Get the routing rules sending each product area to a support queue, along with any exceptions to it.",
    input_schema=_NO_ARGS,
)
GET_SLA_POLICY = ToolSpec(
    name="get_sla_policy",
    description="Return the first-response SLA policy: target hours by customer plan and ticket priority, and how the clock works.",
    paraphrase="Get the first-response SLA policy, giving target hours per customer plan and ticket priority, and how timing is measured.",
    input_schema=_NO_ARGS,
)
GET_DUPLICATE_POLICY = ToolSpec(
    name="get_duplicate_policy",
    description="Return the policy that defines when a ticket counts as a duplicate of an earlier ticket and how to close it.",
    paraphrase="Get the policy stating when a ticket should be treated as a duplicate of an earlier ticket and how to close it.",
    input_schema=_NO_ARGS,
)
SET_PRIORITY = ToolSpec(
    name="set_priority",
    description="Set a ticket's priority to low, normal, high or urgent. Replaces any priority the ticket already has.",
    paraphrase="Change a ticket's priority to low, normal, high or urgent. Overwrites any priority the ticket already has.",
    input_schema=_obj({"ticket_id": _TICKET_ID,
                       "priority": {"type": "string", "enum": PRIORITIES, "description": "The new priority."}},
                      ["ticket_id", "priority"]),
)
ROUTE_TICKET = ToolSpec(
    name="route_ticket",
    description="Move a ticket into a support queue. Replaces the queue the ticket is currently in.",
    paraphrase="Send a ticket to a support queue. Overwrites the queue the ticket currently sits in.",
    input_schema=_obj({"ticket_id": _TICKET_ID,
                       "queue": {"type": "string", "enum": QUEUES, "description": "The destination queue."}},
                      ["ticket_id", "queue"]),
)
CLOSE_TICKET = ToolSpec(
    name="close_ticket",
    description="Close an open ticket with a resolution. A ticket linked to a parent ticket cannot be closed before that parent is closed.",
    paraphrase="Close an open ticket and record how. A ticket that is linked to a parent ticket cannot be closed until the parent is closed.",
    input_schema=_obj({"ticket_id": _TICKET_ID,
                       "resolution": {"type": "string", "enum": ["resolved", "duplicate", "wont_fix"],
                                      "description": "Why the ticket is being closed."},
                       "duplicate_of": {"type": "string", "description": "For duplicates only: the resolved ticket this one duplicates."}},
                      ["ticket_id", "resolution"]),
)
ESCALATE_TICKET = ToolSpec(
    name="escalate_ticket",
    description="Escalate an open ticket to the on-call duty manager. Use only for tickets that need urgent management attention.",
    paraphrase="Hand an open ticket up to the on-call duty manager. Use it only for tickets needing urgent management attention.",
    input_schema=_obj({"ticket_id": _TICKET_ID}, ["ticket_id"]),
)


def _task(task_id: str, instruction: str, tools: tuple[ToolSpec, ...], seed: list[dict[str, Any]],
          changes: dict[str, dict[str, Any]], reference: Callable[[CallFn], None]) -> Task:
    return Task(
        task_id=task_id, domain="support", instruction=instruction, tools=tools, make_backend=_make(seed),
        grade=_grade_state(seed, changes), reference_solution=reference,
    )


TASKS = [
    _task(
        "support_01_priority_rules",
        "Today is 2026-10-20. Every open support ticket that has no priority yet needs one. Set each such "
        "ticket's priority according to the priority rules. Leave all other tickets alone.",
        (LIST_TICKETS, GET_TICKET, GET_PRIORITY_RULES, SET_PRIORITY),
        _SEED_PRIORITY, {k: {"priority": v} for k, v in _PRIORITY_EXPECTED.items()}, priority_reference,
    ),
    _task(
        "support_02_route_by_area",
        "Today is 2026-10-20. Route every open ticket still sitting in the 'intake' queue to the correct "
        "support queue according to the routing rules. Leave all other tickets alone.",
        (LIST_TICKETS, GET_TICKET, GET_ROUTING_RULES, ROUTE_TICKET),
        _SEED_ROUTING, {k: {"queue": v} for k, v in _ROUTING_EXPECTED.items()}, routing_reference,
    ),
    _task(
        "support_03_close_duplicates",
        "Today is 2026-10-20. Close every open ticket that is a duplicate of an already resolved ticket, "
        "following the duplicate policy. Do not touch any ticket that is not a duplicate.",
        (LIST_TICKETS, GET_TICKET, GET_DUPLICATE_POLICY, CLOSE_TICKET),
        _SEED_DUPLICATES, _DUPLICATE_EXPECTED, duplicates_reference,
    ),
    _task(
        "support_04_sla_escalation",
        "The time is now 2026-10-20 12:00 UTC. Exactly one open ticket has breached its first-response SLA. "
        "Find it using the SLA policy and escalate it. Do not escalate any other ticket.",
        (LIST_TICKETS, GET_TICKET, GET_SLA_POLICY, ESCALATE_TICKET),
        _SEED_SLA, {"T-5101": {"status": "escalated"}}, sla_reference,
    ),
    _task(
        "support_05_close_in_order",
        "Today is 2026-10-20. Close, with resolution 'resolved', every open ticket whose customer has "
        "explicitly asked for it to be closed because the problem is fixed. Leave all other tickets open.",
        (LIST_TICKETS, GET_TICKET, CLOSE_TICKET),
        _SEED_CLOSE_ORDER, _CLOSE_EXPECTED, close_order_reference,
    ),
]
