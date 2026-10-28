"""Support tickets domain: triage, route and close by priority rules."""

from __future__ import annotations

import copy
import random
from datetime import datetime, timedelta
from typing import Any, Callable

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

SEED = 1005

PRIORITIES = ["low", "normal", "high", "urgent"]
QUEUES = ["identity", "billing", "payments", "data", "mobile"]
PLANS = ["enterprise", "pro", "free"]

_STATE_FIELDS = ("status", "priority", "queue", "resolution", "duplicate_of")
_CLOCK_STOP = datetime(2026, 10, 20, 11, 55)  # derived timestamps never run past "now"


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
# Filler tickets: realistic reports that no task acts on. Every error code here is unique, and
# none of the bodies asks for the ticket to be closed.

_POOL = [
    ("Two-factor codes arrive late", "SMS codes take several minutes to arrive, so the login window has usually "
     "expired by the time we type them in. It happens on every carrier we have tried.", "auth", "general", "medium", "AUTH-207"),
    ("Invoice PDF shows an old address", "The billing address on our October invoice is the one we changed in "
     "August. The portal itself shows the new address correctly.", "invoicing", "general", "low", None),
    ("Charge endpoint rejects a valid currency", "POST /charges returns 400 for currency ISK although the "
     "documentation lists it as supported. Other Nordic currencies work.", "payments_api", "general", "medium", "PAY-118"),
    ("Scheduled report never emails", "The weekly usage report is marked as sent in the UI but nobody on the "
     "distribution list receives it. Manual sends do arrive.", "reports", "general", "medium", "RPT-220"),
    ("Android widget stuck on loading", "The home-screen widget spins forever after the latest update. Opening "
     "the app itself works fine.", "mobile_app", "general", "low", "MOB-33"),
    ("Export missing custom fields", "CSV exports no longer include the custom fields we added last quarter. "
     "They still show in the web table.", "reports", "general", "medium", "RPT-305"),
    ("Session expires every few minutes", "We get logged out roughly every ten minutes even with remember-me "
     "ticked. Started on Monday for the whole team.", "auth", "general", "high", "AUTH-412"),
    ("Refund pending for a week", "A refund issued through the API has shown as pending for seven days and "
     "the cardholder is chasing us for it.", "payments_api", "general", "medium", "PAY-240"),
    ("Tax line doubled on invoice", "Invoice INV-20931 lists the VAT line twice, so the total is wrong. We "
     "will not pay until it is corrected.", "invoicing", "billing_dispute", "medium", None),
    ("Push notifications silent on iOS", "Notifications arrive but never make a sound or show a banner since "
     "the last OS update. Settings look right on the phone.", "mobile_app", "general", "low", "MOB-71"),
    ("Dashboard shows stale numbers", "The overview dashboard is about a day behind the detailed reports, "
     "which are current.", "reports", "general", "low", None),
    ("Webhook signature mismatch", "Signatures on webhook deliveries stopped validating after we rotated our "
     "signing secret in the console.", "webhooks", "general", "high", "WH-311"),
    ("Cannot add a second admin", "Inviting a second administrator fails with a permissions error even though "
     "I am the account owner.", "auth", "general", "medium", "AUTH-150"),
    ("Proration looks wrong after upgrade", "We upgraded mid-cycle and the prorated charge is higher than the "
     "calculator in the pricing page predicted.", "invoicing", "billing_dispute", "low", None),
    ("Dark mode contrast too low", "Grey text on the dark background is hard to read in the mobile app, "
     "especially in the settings screens.", "mobile_app", "general", "low", None),
    ("Report filters reset on refresh", "Any filter applied to a report is lost when the page reloads, which "
     "makes sharing links pointless.", "reports", "general", "low", "RPT-118"),
    ("Retry storm from our listener", "Our listener briefly returned 500s and we then received hundreds of "
     "retries within a minute.", "webhooks", "general", "medium", "WH-520"),
    ("SSO group mapping ignored", "Users in our Okta Finance group are not getting the finance role when they "
     "sign in. Other group mappings work.", "auth", "general", "medium", "SSO-230"),
    ("Statement download times out", "Downloading the annual statement spins for about a minute and then "
     "fails with a generic error.", "invoicing", "general", "low", None),
    ("Rate limit lower than documented", "We are being throttled at around 200 requests per minute while the "
     "docs say 600 for our plan.", "payments_api", "general", "medium", "PAY-429"),
    ("Offline edits are discarded", "Edits made while the phone is offline are thrown away when the app "
     "reconnects instead of syncing.", "mobile_app", "general", "high", "MOB-140"),
    ("Audit log export truncated", "The audit log export stops at 10,000 rows with no warning that it is "
     "incomplete.", "reports", "general", "medium", "RPT-410"),
]
_ROUTE = {"auth": "identity", "invoicing": "billing", "payments_api": "payments", "reports": "data",
          "mobile_app": "mobile", "webhooks": "payments"}
_NAMES = ["Priya Raman", "Tomas Weil", "Lena Fischer", "Diego Alvarez", "Mei Tanaka", "Olu Adeyemi",
          "Sara Lindgren", "Jonas Brekke", "Amira Haddad", "Ruth Okafor", "Felix Moreau", "Hana Novak"]


def _pool_ticket(ticket_id: str, idx: int, plan: str, **kw: Any) -> dict[str, Any]:
    subject, body, area, category, impact, code = _POOL[idx]
    kw.setdefault("queue", "billing" if category == "billing_dispute" else _ROUTE[area])
    return _t(ticket_id, subject, body, plan, product_area=area, category=category, impact=impact,
              error_code=code, **kw)


def _fillers(start: int, seed: int, open_idx: list[int], closed_idx: list[int], **open_kw: Any) -> list[dict[str, Any]]:
    """Seeded filler tickets: `open_idx` pool entries stay open, `closed_idx` ones are already closed."""
    rng = random.Random(seed)
    rows = []
    for k, idx in enumerate(open_idx + closed_idx):
        created = datetime(2026, 10, 20, 11, 0) - timedelta(hours=rng.randint(2, 130), minutes=rng.randint(0, 59))
        kw: dict[str, Any] = {"created_at": created.strftime("%Y-%m-%dT%H:%M"), "customer_name": rng.choice(_NAMES),
                              "priority": rng.choice(PRIORITIES)}
        if k < len(open_idx):
            kw.update(open_kw)
        else:
            kw.update(status="closed", resolution=rng.choice(["resolved", "resolved", "wont_fix"]))
        rows.append(_pool_ticket(f"T-{start + k}", idx, rng.choice(PLANS), **kw))
    return rows


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
        "If the customer is flagged as a churn risk, raise the result one level (low, normal, high, urgent), never above urgent.",
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
        "Route on the ticket's product area, never on wording in the subject, which the customer writes.",
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
        "The clock runs from the ticket's creation time to its first response time.",
        "If there is no first response yet the clock is still running and stops at the current time.",
        "A ticket breaches its SLA when the clock exceeds the target hours for its plan and priority.",
    ],
}

_DUPLICATE_POLICY = {
    "policy_id": "DP-2026-1",
    "rules": [
        "A ticket is a duplicate only if a resolved ticket has the same product area and the same error code.",
        "Similar wording or a customer's claim that it is the same issue is not enough.",
        "Close duplicates with resolution 'duplicate' and name the resolved ticket in duplicate_of.",
        "Never close a ticket as a duplicate of a ticket that is not resolved.",
    ],
}


def _policy_view(p: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random("support:policy:" + p["policy_id"])
    rest = {k: v for k, v in p.items() if k != "policy_id"}
    return {
        "policy_id": p["policy_id"],
        "version": rng.randint(2, 6),
        "effective_from": f"2026-{rng.randint(1, 6):02d}-01",
        "owner": "Support Operations",
        **copy.deepcopy(rest),
        "last_reviewed_at": f"2026-{rng.randint(7, 9):02d}-{rng.randint(1, 28):02d}",
    }


# ---------------------------------------------------------------------------
# Presentation: realistic but irrelevant fields, derived deterministically from the ticket ID.

_AGENTS = ["Noah Kim", "Ines Duarte", "Samir Qureshi", "Beatrix Lang"]
_COMPANIES = ["Harbor Logistics", "Fernhill Press", "Bluefin Analytics", "Oakridge Clinics", "Kestrel Games",
              "Northwind Traders", "Lumen Energy", "Tidewater Legal"]
_TAGS = ["needs_repro", "customer_waiting", "regression", "docs", "enterprise_watch", "mobile", "q4_review"]


def _iso(d: datetime) -> str:
    return min(d, _CLOCK_STOP).strftime("%Y-%m-%dT%H:%M")


def _decorate(t: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random("support:ticket:" + t["ticket_id"])
    created = datetime.fromisoformat(t["created_at"])
    assignee = None if t["queue"] == "intake" else rng.choice(_AGENTS)
    events = [
        {"occurred_at": _iso(created), "actor": "system", "event_type": "created"},
        {"occurred_at": _iso(created + timedelta(minutes=rng.randint(5, 60))),
         "actor": assignee or "triage_bot", "event_type": "assigned" if assignee else "tag_added"},
    ]
    if rng.random() < 0.5:
        events.append({"occurred_at": _iso(created + timedelta(minutes=rng.randint(61, 180))),
                       "actor": rng.choice(_AGENTS), "event_type": "internal_note"})
    c = t["customer"]
    return {
        "ticket_id": t["ticket_id"],
        "subject": t["subject"],
        "channel": rng.choice(["email", "chat", "web_form", "phone", "api"]),
        "status": t["status"],
        "priority": t["priority"],
        "queue": t["queue"],
        "product_area": t["product_area"],
        "category": t["category"],
        "impact": t["impact"],
        "error_code": t["error_code"],
        "internal_ref": f"ZD-{rng.randint(40000, 99999)}",
        "created_at": t["created_at"],
        "first_response_at": t["first_response_at"],
        "updated_at": _iso(created + timedelta(minutes=rng.randint(10, 240))),
        "assignee": assignee,
        "tags": rng.sample(_TAGS, rng.randint(1, 3)),
        "region": rng.choice(["emea", "amer", "apac"]),
        "language": rng.choice(["en", "en", "en", "de", "es", "ja"]),
        "attachment_count": rng.choice([0, 0, 1, 2, 3]),
        "blocked_by": t["blocked_by"],
        "duplicate_of": t["duplicate_of"],
        "resolution": t["resolution"],
        "customer": {
            "account_id": f"ACC-{rng.randint(10000, 99999)}",
            "name": c["name"],
            "company": rng.choice(_COMPANIES),
            "plan": c["plan"],
            "seat_count": rng.choice([1, 3, 8, 25, 60, 140, 400]),
            "customer_since": f"{rng.randint(2019, 2026)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "churn_risk": c["churn_risk"],
            "timezone": rng.choice(["Europe/London", "Europe/Berlin", "America/New_York", "Asia/Tokyo"]),
        },
        "body": t["body"],
        "events": events,
    }


_SUMMARY_FIELDS = ("ticket_id", "subject", "status", "priority", "queue", "channel", "created_at",
                   "first_response_at", "error_code", "updated_at")
_ACTION_STAMP = {"updated_at": "2026-10-20T12:00", "updated_by": "support_agent"}


class SupportBackend:
    def __init__(self, seed_tickets: list[dict[str, Any]]) -> None:
        self.tickets = [_decorate(t) for t in seed_tickets]
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
                {k: t[k] for k in _SUMMARY_FIELDS}
                for t in self.tickets
                if status is None or t["status"] == status
            ]
        }

    def get_ticket(self, ticket_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._ticket(ticket_id))

    def get_priority_rules(self) -> dict[str, Any]:
        return _policy_view(_PRIORITY_RULES)

    def get_routing_rules(self) -> dict[str, Any]:
        return _policy_view(_ROUTING_RULES)

    def get_sla_policy(self) -> dict[str, Any]:
        return _policy_view(_SLA_POLICY)

    def get_duplicate_policy(self) -> dict[str, Any]:
        return _policy_view(_DUPLICATE_POLICY)

    def set_priority(self, ticket_id: str, priority: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if priority not in PRIORITIES:
            raise ToolError(f"Priority must be one of {PRIORITIES}.")
        t["priority"] = priority
        return {"ticket_id": ticket_id, "priority": priority, **_ACTION_STAMP}

    def route_ticket(self, ticket_id: str, queue: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if queue not in QUEUES:
            raise ToolError(f"Unknown queue {queue!r}; queues are {QUEUES}.")
        t["queue"] = queue
        return {"ticket_id": ticket_id, "queue": queue, **_ACTION_STAMP}

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
        return {"ticket_id": ticket_id, "status": "closed", "resolution": resolution, "duplicate_of": duplicate_of,
                **_ACTION_STAMP}

    def escalate_ticket(self, ticket_id: str) -> dict[str, Any]:
        t = self._ticket(ticket_id)
        if t["status"] != "open":
            raise ToolError(f"Only open tickets can be escalated; {ticket_id} is {t['status']}.")
        t["status"] = "escalated"
        return {"ticket_id": ticket_id, "status": "escalated", **_ACTION_STAMP}

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
    # Filler: open tickets that already carry a priority, plus closed ones.
    *_fillers(2105, 201, open_idx=[0, 2, 3, 5, 6, 7, 9, 11, 13, 16, 19], closed_idx=[1, 4, 10]),
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
    # Filler: open tickets already sitting in their routed queues, plus closed ones.
    *_fillers(3105, 202, open_idx=[0, 1, 3, 5, 7, 8, 10, 12, 14, 17, 20], closed_idx=[2, 9, 15]),
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
    # Same error code as the resolved SAML ticket, but a different product area: not a duplicate.
    _t("T-4104", "Mobile app shows SSO-114 after update", "Since the last app update, signing in on Android "
       "fails with code SSO-114. The web app works for the same users.", "pro", product_area="mobile_app",
       error_code="SSO-114", queue="mobile"),
    # Filler: open tickets with other error codes, and closed tickets whose codes nothing else shares.
    *_fillers(4105, 203, open_idx=[0, 2, 4, 6, 7, 11, 13, 16, 20], closed_idx=[3, 9, 17, 21]),
]
_DUPLICATE_EXPECTED = {
    "T-4101": {"status": "closed", "resolution": "duplicate", "duplicate_of": "T-4100"},
    "T-4103": {"status": "closed", "resolution": "duplicate", "duplicate_of": "T-4090"},
}


def duplicates_reference(call: CallFn) -> None:
    call("get_duplicate_policy")
    summaries = call("list_tickets")["tickets"]
    resolved: dict[tuple[str, str], str] = {}
    for s in summaries:
        if s["status"] == "resolved":
            t = call("get_ticket", ticket_id=s["ticket_id"])
            resolved[(t["product_area"], t["error_code"])] = t["ticket_id"]
    resolved_codes = {code for _, code in resolved}
    for s in summaries:
        if s["status"] != "open" or s["error_code"] not in resolved_codes:
            continue
        t = call("get_ticket", ticket_id=s["ticket_id"])
        original = resolved.get((t["product_area"], t["error_code"]))
        if original is not None:
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
    # Filler: every one of these is inside its target (clock in hours shown in the comments).
    _pool_ticket("T-5106", 0, "pro", priority="high", created_at="2026-10-20T09:30"),                  # 2.5 of 4
    _pool_ticket("T-5107", 2, "pro", priority="normal", created_at="2026-10-20T01:00"),                # 11 of 24
    _pool_ticket("T-5108", 4, "free", priority="urgent", created_at="2026-10-20T10:30"),               # 1.5 of 8
    _pool_ticket("T-5109", 6, "enterprise", priority="high", created_at="2026-10-20T11:15"),           # 0.75 of 2
    _pool_ticket("T-5110", 8, "enterprise", priority="low", created_at="2026-10-19T20:00"),            # 16 of 24
    _pool_ticket("T-5111", 10, "free", priority="normal", created_at="2026-10-18T14:00",
                 first_response_at="2026-10-18T16:30"),                                                # 2.5 of 72
    _pool_ticket("T-5112", 12, "pro", priority="urgent", created_at="2026-10-20T11:40"),               # 0.33 of 2
    _pool_ticket("T-5113", 14, "enterprise", priority="normal", created_at="2026-10-20T07:00",
                 first_response_at="2026-10-20T07:45"),                                                # 0.75 of 8
    _pool_ticket("T-5114", 16, "pro", priority="low", created_at="2026-10-19T13:00"),                  # 23 of 48
    _pool_ticket("T-5115", 18, "free", priority="high", created_at="2026-10-20T10:30",
                 first_response_at="2026-10-20T11:45"),                                                # 1.25 of 24
    _pool_ticket("T-5116", 20, "pro", priority="high", created_at="2026-10-19T15:00",
                 first_response_at="2026-10-19T16:30"),                                                # 1.5 of 4
    _pool_ticket("T-5117", 21, "enterprise", priority="urgent", created_at="2026-10-20T11:30",
                 first_response_at="2026-10-20T11:50"),                                                # 0.33 of 1
    _pool_ticket("T-5118", 3, "free", priority="low", created_at="2026-10-19T14:00"),                  # 22 of 120
    *_fillers(5119, 204, open_idx=[], closed_idx=[1, 9, 15]),
]


def sla_reference(call: CallFn) -> None:
    targets = {(r["plan"], r["priority"]): r["response_hours"] for r in call("get_sla_policy")["targets"]}
    # The fastest target any plan has for a priority: a ticket under that cannot have breached.
    floor = {pr: min(h for (_, p), h in targets.items() if p == pr) for pr in PRIORITIES}
    for summary in call("list_tickets", status="open")["tickets"]:
        created = datetime.fromisoformat(summary["created_at"])
        stop = datetime.fromisoformat(summary["first_response_at"]) if summary["first_response_at"] else _NOW
        hours = (stop - created).total_seconds() / 3600
        if hours <= floor[summary["priority"]]:
            continue
        t = call("get_ticket", ticket_id=summary["ticket_id"])
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
    _t("T-6106", "Shared folder permissions reset", "Permissions on our shared folders went back to defaults "
       "overnight. Still broken this morning, any update?", "pro"),
    # Filler: already closed tickets.
    *_fillers(6107, 205, open_idx=[], closed_idx=[0, 2, 3, 5, 6, 8, 10, 11, 13, 15, 18, 20]),
]
_CLOSE_EXPECTED = {t: {"status": "closed", "resolution": "resolved"} for t in ("T-6101", "T-6103", "T-6105")}


def close_order_reference(call: CallFn) -> None:
    summaries = call("list_tickets")["tickets"]
    full = [call("get_ticket", ticket_id=s["ticket_id"]) for s in summaries if s["status"] == "open"]
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
    description="List support tickets, optionally filtered by status. Returns each ticket's ID, subject, status, priority, queue, timestamps and error code.",
    paraphrase="Return support tickets, with an optional status filter. Each entry gives the ticket's ID, subject, status, priority, queue, timestamps and error code.",
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
