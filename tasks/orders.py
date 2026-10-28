"""Orders domain: find an order, check its status, issue a refund under a policy rule."""

from __future__ import annotations

import copy
import random
from datetime import date
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

SEED = 1001

_CUSTOMERS = [
    {"customer_id": "C-1042", "name": "Dana Whitfield", "email": "dana.w@example.com", "tier": "silver"},
    {"customer_id": "C-1187", "name": "Dana Whitley", "email": "dwhitley@example.com", "tier": "gold"},
    {"customer_id": "C-0931", "name": "Marcus Oyelaran", "email": "m.oyelaran@example.com", "tier": "bronze"},
]

# Line items per order; subtotal, shipping and total are derived from these.
_ORDERS = [
    {
        "order_id": "ORD-55902", "customer_id": "C-1042", "placed_at": "2026-07-14",
        "status": "delivered", "delivered_at": "2026-07-18", "shipping": 4.99,
        "items": [{"sku": "TEA-GRN-50", "description": "Green tea, 50 bags", "quantity": 1, "unit_price": 18.25}],
    },
    {
        "order_id": "ORD-58817", "customer_id": "C-1042", "placed_at": "2026-09-02",
        "status": "delivered", "delivered_at": "2026-09-06", "shipping": 5.99,
        "items": [{"sku": "MUG-STN-2", "description": "Stoneware mugs (2)", "quantity": 2, "unit_price": 21.0}],
    },
    {
        "order_id": "ORD-60511", "customer_id": "C-1042", "placed_at": "2026-09-21",
        "status": "delivered", "delivered_at": "2026-09-25", "shipping": 0.0,
        "items": [{"sku": "KTL-ELC-1", "description": "Electric kettle", "quantity": 1, "unit_price": 129.99}],
    },
    {
        "order_id": "ORD-61230", "customer_id": "C-1042", "placed_at": "2026-10-06",
        "status": "delivered", "delivered_at": "2026-10-11", "shipping": 7.99,
        "items": [
            {"sku": "CER-BWL-4", "description": "Ceramic bowl set (4)", "quantity": 1, "unit_price": 54.5},
            {"sku": "LIN-NAP-6", "description": "Linen napkins (6)", "quantity": 2, "unit_price": 15.0},
        ],
    },
    {
        "order_id": "ORD-61377", "customer_id": "C-1187", "placed_at": "2026-10-08",
        "status": "shipped", "delivered_at": None, "shipping": 0.0,
        "items": [{"sku": "CER-BWL-4", "description": "Ceramic bowl set (4)", "quantity": 1, "unit_price": 54.5}],
    },
]

_POLICY = {
    "policy_id": "RP-2026-3",
    "rules": [
        "Items that arrived damaged are refunded at the full order subtotal.",
        "Shipping charges are refunded only for customers on the gold tier.",
        "Refunds must be requested within 30 days of the delivery date.",
        "Refunds can only be issued for orders with status 'delivered'.",
    ],
}

TARGET_ORDER = "ORD-61230"


def _subtotal(order: dict[str, Any]) -> float:
    return round(sum(i["quantity"] * i["unit_price"] for i in order["items"]), 2)


class OrdersBackend:
    # Subclasses swap in their own data and the statuses that can be refunded.
    CUSTOMERS = _CUSTOMERS
    ORDERS = _ORDERS
    POLICY = _POLICY
    REFUNDABLE = ("delivered",)

    def __init__(self) -> None:
        self.customers = copy.deepcopy(self.CUSTOMERS)
        self.orders = copy.deepcopy(self.ORDERS)
        # Seeded shuffle so the most recent order is not simply first or last in listings.
        random.Random(SEED).shuffle(self.orders)
        self.refunds: list[dict[str, Any]] = []
        self.store_credits: list[dict[str, Any]] = []

    def _order(self, order_id: str) -> dict[str, Any]:
        for o in self.orders:
            if o["order_id"] == order_id:
                return o
        raise ToolError(f"No order with ID {order_id!r}.")

    def find_customer(self, email: str) -> dict[str, Any]:
        for c in self.customers:
            if c["email"].lower() == email.strip().lower():
                return dict(c)
        raise ToolError(f"No customer with email {email!r}.")

    def search_customers(self, customer_name: str) -> dict[str, Any]:
        needle = customer_name.strip().lower()
        return {
            "query": customer_name,
            "matches": [dict(c) for c in self.customers if needle and needle in c["name"].lower()],
        }

    def list_orders(self, customer_id: str) -> dict[str, Any]:
        if not any(c["customer_id"] == customer_id for c in self.customers):
            raise ToolError(f"No customer with ID {customer_id!r}.")
        return {
            "customer_id": customer_id,
            "orders": [
                {
                    "order_id": o["order_id"],
                    "placed_at": o["placed_at"],
                    "status": o["status"],
                    "total": round(_subtotal(o) + o["shipping"], 2),
                }
                for o in self.orders
                if o["customer_id"] == customer_id
            ],
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        o = self._order(order_id)
        subtotal = _subtotal(o)
        return {
            "order_id": o["order_id"],
            "customer_id": o["customer_id"],
            "placed_at": o["placed_at"],
            "status": o["status"],
            "delivered_at": o["delivered_at"],
            "items": copy.deepcopy(o["items"]),
            "subtotal": subtotal,
            "shipping": o["shipping"],
            "total": round(subtotal + o["shipping"], 2),
        }

    def get_refund_policy(self) -> dict[str, Any]:
        return copy.deepcopy(self.POLICY)

    def issue_refund(self, order_id: str, amount: float, reason: str) -> dict[str, Any]:
        o = self._order(order_id)
        if o["status"] not in self.REFUNDABLE:
            allowed = " or ".join(self.REFUNDABLE)
            raise ToolError(f"Order {order_id} has status {o['status']!r}; only {allowed} orders can be refunded.")
        total = round(_subtotal(o) + o["shipping"], 2)
        if not 0 < amount <= total:
            raise ToolError(f"Refund amount must be greater than 0 and at most the order total ({total}).")
        refund = {
            "refund_id": f"RF-{len(self.refunds) + 1:04d}",
            "order_id": order_id,
            "amount": round(amount, 2),
            "reason": reason,
            "status": "issued",
        }
        self.refunds.append(refund)
        return dict(refund)

    def issue_store_credit(self, customer_id: str, order_id: str, amount: float) -> dict[str, Any]:
        o = self._order(order_id)
        if o["customer_id"] != customer_id:
            raise ToolError(f"Order {order_id} does not belong to customer {customer_id}.")
        total = round(_subtotal(o) + o["shipping"], 2)
        if not 0 < amount <= total:
            raise ToolError(f"Credit amount must be greater than 0 and at most the order total ({total}).")
        credit = {
            "credit_id": f"SC-{len(self.store_credits) + 1:04d}",
            "customer_id": customer_id,
            "order_id": order_id,
            "amount": round(amount, 2),
            "status": "issued",
        }
        self.store_credits.append(credit)
        return dict(credit)

    def snapshot(self) -> dict[str, Any]:
        return {"refunds": copy.deepcopy(self.refunds), "store_credits": copy.deepcopy(self.store_credits)}


def grade(backend: OrdersBackend) -> Grade:
    refunds = backend.refunds
    if len(refunds) != 1:
        return Grade(success=False, detail=f"expected exactly 1 refund, found {len(refunds)}")
    r = refunds[0]
    if r["order_id"] != TARGET_ORDER:
        return Grade(success=False, detail=f"refunded {r['order_id']}, expected {TARGET_ORDER}")
    if abs(r["amount"] - 84.5) > 0.005:
        return Grade(success=False, detail=f"refunded {r['amount']}, expected 84.50 (subtotal, no shipping)")
    if r["reason"] != "damaged":
        return Grade(success=False, detail=f"reason {r['reason']!r}, expected 'damaged'")
    return Grade(success=True, detail="correct refund")


def reference_solution(call: CallFn) -> None:
    customer = call("find_customer", email="dana.w@example.com")
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    latest = max(orders, key=lambda o: o["placed_at"])
    order = call("get_order", order_id=latest["order_id"])
    call("get_refund_policy")
    amount = order["subtotal"] + (order["shipping"] if customer["tier"] == "gold" else 0.0)
    call("issue_refund", order_id=order["order_id"], amount=round(amount, 2), reason="damaged")


TOOLS = (
    ToolSpec(
        name="find_customer",
        description="Look up a customer account by email address. Returns the customer's ID, name, email and loyalty tier.",
        paraphrase="Find a customer account using its email address. Gives back the customer's ID, name, email and loyalty tier.",
        input_schema={
            "type": "object",
            "properties": {"email": {"type": "string", "description": "The customer's email address."}},
            "required": ["email"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="list_orders",
        description="List every order placed by a customer, with each order's ID, placement date, status and total.",
        paraphrase="Return all orders a customer has placed, including each order's ID, date placed, status and total.",
        input_schema={
            "type": "object",
            "properties": {"customer_id": {"type": "string", "description": "The customer's ID."}},
            "required": ["customer_id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_order",
        description="Get the full details of one order: line items, subtotal, shipping charge, total, status and delivery date.",
        paraphrase="Fetch complete information for a single order: its items, subtotal, shipping fee, total, status and delivery date.",
        input_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "The order's ID."}},
            "required": ["order_id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="get_refund_policy",
        description="Return the current refund policy rules that every refund must follow.",
        paraphrase="Get the refund policy rules now in force, which all refunds must obey.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="issue_refund",
        description="Issue a refund against an order. The amount is in US dollars and the refund is final once issued.",
        paraphrase="Refund money on an order. The amount is given in US dollars, and a refund can't be undone once issued.",
        input_schema={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "The order to refund."},
                "amount": {"type": "number", "description": "Refund amount in US dollars."},
                "reason": {
                    "type": "string",
                    "enum": ["damaged", "not_received", "wrong_item", "other"],
                    "description": "Why the refund is being issued.",
                },
            },
            "required": ["order_id", "amount", "reason"],
            "additionalProperties": False,
        },
    ),
)

TASKS = [
    Task(
        task_id="orders_01_damaged_refund",
        domain="orders",
        instruction=(
            "Today is 2026-10-20. The customer with email dana.w@example.com reports that her most recent "
            "order arrived damaged. Issue the refund she is owed under the refund policy."
        ),
        tools=TOOLS,
        make_backend=OrdersBackend,
        grade=grade,
        reference_solution=reference_solution,
    ),
]

# --- orders_02..05 ------------------------------------------------------------------------------

def _item(sku: str, description: str, quantity: int, unit_price: float) -> dict[str, Any]:
    return {"sku": sku, "description": description, "quantity": quantity, "unit_price": unit_price}


def _order_row(order_id: str, customer_id: str, placed: str, status: str, delivered: str | None,
               shipping: float, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"order_id": order_id, "customer_id": customer_id, "placed_at": placed, "status": status,
            "delivered_at": delivered, "shipping": shipping, "items": items}


def _grade_one_refund(backend: OrdersBackend, order_id: str, amount: float, reason: str) -> Grade:
    if backend.store_credits:
        return Grade(success=False, detail="store credit issued, expected a refund")
    if len(backend.refunds) != 1:
        return Grade(success=False, detail=f"expected exactly 1 refund, found {len(backend.refunds)}")
    r = backend.refunds[0]
    if r["order_id"] != order_id:
        return Grade(success=False, detail=f"refunded {r['order_id']}, expected {order_id}")
    if abs(r["amount"] - amount) > 0.005:
        return Grade(success=False, detail=f"refunded {r['amount']}, expected {amount:.2f}")
    if r["reason"] != reason:
        return Grade(success=False, detail=f"reason {r['reason']!r}, expected {reason!r}")
    return Grade(success=True, detail="correct refund")


# orders_02: a not_received claim; only the lost_in_transit order qualifies (the newest is still in transit).
class LostBackend(OrdersBackend):
    CUSTOMERS = [
        {"customer_id": "C-2201", "name": "Helen Brandt", "email": "helen.brandt@example.com", "tier": "silver"},
        {"customer_id": "C-2202", "name": "Omar Haddad", "email": "omar.haddad@example.com", "tier": "bronze"},
    ]
    ORDERS = [
        _order_row("ORD-70115", "C-2201", "2026-09-28", "delivered", "2026-10-02", 4.5,
                   [_item("SKT-CST-1", "Cast-iron skillet", 1, 48.0)]),
        _order_row("ORD-70162", "C-2201", "2026-10-05", "lost_in_transit", None, 6.25,
                   [_item("THR-WOL-1", "Wool throw blanket", 1, 62.0), _item("CND-SET-3", "Candle set", 2, 11.5)]),
        _order_row("ORD-70240", "C-2201", "2026-10-16", "shipped", None, 0.0,
                   [_item("PLT-HRB-1", "Herb planter", 1, 24.0)]),
        _order_row("ORD-70199", "C-2202", "2026-10-10", "lost_in_transit", None, 3.0,
                   [_item("KNF-CHF-1", "Chef's knife", 1, 55.0)]),
    ]
    POLICY = {
        "policy_id": "RP-2026-4",
        "rules": [
            "A not_received refund is only available for orders with status 'lost_in_transit'.",
            "Orders with status 'shipped' are still in transit and cannot be refunded.",
            "Orders with status 'delivered' are not eligible for a not_received refund.",
            "A lost order is refunded in full: the whole order total, shipping included.",
        ],
    }
    REFUNDABLE = ("delivered", "lost_in_transit")


def grade_lost(backend: OrdersBackend) -> Grade:
    return _grade_one_refund(backend, "ORD-70162", 91.25, "not_received")


def reference_lost(call: CallFn) -> None:
    customer = call("find_customer", email="helen.brandt@example.com")
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    lost = [o for o in orders if o["status"] == "lost_in_transit"]
    order = call("get_order", order_id=lost[0]["order_id"])
    call("get_refund_policy")
    call("issue_refund", order_id=order["order_id"], amount=order["total"], reason="not_received")


# orders_03: outside the 30-day window, so no refund; the policy's alternative is store credit.
class WindowBackend(OrdersBackend):
    CUSTOMERS = [
        {"customer_id": "C-2310", "name": "Noor Rahimi", "email": "noor.rahimi@example.com", "tier": "bronze"},
    ]
    ORDERS = [
        _order_row("ORD-71004", "C-2310", "2026-09-12", "delivered", "2026-09-17", 5.99,
                   [_item("CRF-GLS-1", "Glass carafe", 1, 36.0), _item("MUG-STN-4", "Stoneware mugs (4)", 1, 32.0)]),
        _order_row("ORD-71190", "C-2310", "2026-10-09", "delivered", "2026-10-14", 4.99,
                   [_item("TWL-TEA-3", "Tea towels (3)", 1, 17.5)]),
    ]
    POLICY = {
        "policy_id": "RP-2026-5",
        "refund_window_days": 30,
        "store_credit_percent": 50,
        "rules": [
            "Cash refunds must be requested within refund_window_days of the delivery date.",
            "For damaged orders outside that window, no refund is issued; instead issue store credit "
            "of store_credit_percent of the order subtotal, shipping excluded.",
            "Store credit is issued with issue_store_credit and is final once issued.",
        ],
    }


def grade_window(backend: OrdersBackend) -> Grade:
    if backend.refunds:
        return Grade(success=False, detail="a cash refund was issued outside the refund window")
    if len(backend.store_credits) != 1:
        return Grade(success=False, detail=f"expected exactly 1 store credit, found {len(backend.store_credits)}")
    c = backend.store_credits[0]
    if c["order_id"] != "ORD-71004" or c["customer_id"] != "C-2310":
        return Grade(success=False, detail=f"credit on {c['order_id']} for {c['customer_id']}, expected ORD-71004/C-2310")
    if abs(c["amount"] - 34.0) > 0.005:
        return Grade(success=False, detail=f"credit {c['amount']}, expected 34.00 (50% of the 68.00 subtotal)")
    return Grade(success=True, detail="correct store credit")


def reference_window(call: CallFn) -> None:
    customer = call("find_customer", email="noor.rahimi@example.com")
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    details = [call("get_order", order_id=o["order_id"]) for o in orders]
    order = next(d for d in details if any("carafe" in i["description"].lower() for i in d["items"]))
    policy = call("get_refund_policy")
    age = (date(2026, 10, 20) - date.fromisoformat(order["delivered_at"])).days
    if age <= policy["refund_window_days"]:
        raise AssertionError("order is inside the refund window")
    credit = round(order["subtotal"] * policy["store_credit_percent"] / 100, 2)
    call("issue_store_credit", customer_id=customer["customer_id"], order_id=order["order_id"], amount=credit)


# orders_04: wrong_item on one line of an order; the same product is also in an older, out-of-window order.
class WrongItemBackend(OrdersBackend):
    CUSTOMERS = [
        {"customer_id": "C-1187", "name": "Dana Whitley", "email": "dwhitley@example.com", "tier": "gold"},
        {"customer_id": "C-1042", "name": "Dana Whitfield", "email": "dana.w@example.com", "tier": "silver"},
    ]
    ORDERS = [
        _order_row("ORD-72031", "C-1187", "2026-08-18", "delivered", "2026-08-24", 6.5,
                   [_item("BRD-WLN-1", "Walnut cutting board", 1, 38.0), _item("WRP-BWX-3", "Beeswax wrap set", 1, 14.0)]),
        _order_row("ORD-72144", "C-1187", "2026-10-04", "delivered", "2026-10-09", 8.75,
                   [_item("BRD-WLN-1", "Walnut cutting board", 2, 38.0), _item("OIL-OLV-1", "Olive oil tin", 1, 22.0)]),
        _order_row("ORD-72150", "C-1042", "2026-10-06", "delivered", "2026-10-11", 4.0,
                   [_item("BRD-WLN-1", "Walnut cutting board", 1, 38.0)]),
    ]
    POLICY = {
        "policy_id": "RP-2026-6",
        "rules": [
            "A wrong_item refund covers only the affected line items: quantity times unit price for each.",
            "Shipping is refunded in full on a wrong_item refund for customers on the gold tier, and not otherwise.",
            "Refunds must be requested within 30 days of the delivery date.",
            "Refunds can only be issued for orders with status 'delivered'.",
        ],
    }


def grade_wrong_item(backend: OrdersBackend) -> Grade:
    return _grade_one_refund(backend, "ORD-72144", 84.75, "wrong_item")


def reference_wrong_item(call: CallFn) -> None:
    customer = call("find_customer", email="dwhitley@example.com")
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    details = [call("get_order", order_id=o["order_id"]) for o in orders]
    recent = [d for d in details if (date(2026, 10, 20) - date.fromisoformat(d["delivered_at"])).days <= 30]
    order = next(d for d in recent if any("cutting board" in i["description"].lower() for i in d["items"]))
    call("get_refund_policy")
    line = next(i for i in order["items"] if "cutting board" in i["description"].lower())
    amount = line["quantity"] * line["unit_price"] + (order["shipping"] if customer["tier"] == "gold" else 0.0)
    call("issue_refund", order_id=order["order_id"], amount=round(amount, 2), reason="wrong_item")


# orders_05: several similar names; the registered email domain and tier pick the right account.
class SimilarNamesBackend(OrdersBackend):
    CUSTOMERS = [
        {"customer_id": "C-3301", "name": "Priya Nair", "email": "priya.nair@mailbox.example", "tier": "silver"},
        {"customer_id": "C-3302", "name": "Priya Nair", "email": "p.nair@fernhill.example", "tier": "gold"},
        {"customer_id": "C-3303", "name": "Priya Nayar", "email": "priya.nayar@fernhill.example", "tier": "gold"},
        {"customer_id": "C-3304", "name": "Priyanka Nair", "email": "pnair@fernhill.example", "tier": "bronze"},
    ]
    ORDERS = [
        _order_row("ORD-73010", "C-3301", "2026-10-06", "delivered", "2026-10-12", 5.0,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0)]),
        _order_row("ORD-73055", "C-3302", "2026-10-03", "delivered", "2026-10-10", 5.5,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0), _item("PIL-LIN-2", "Pillowcase pair", 1, 24.0)]),
        _order_row("ORD-73101", "C-3302", "2026-10-10", "delivered", "2026-10-15", 0.0,
                   [_item("SOC-WOL-3", "Wool socks (3 pairs)", 1, 27.0)]),
        _order_row("ORD-73077", "C-3303", "2026-10-05", "delivered", "2026-10-11", 5.5,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0)]),
        _order_row("ORD-73090", "C-3304", "2026-10-07", "delivered", "2026-10-13", 5.5,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0)]),
    ]


def grade_similar(backend: OrdersBackend) -> Grade:
    return _grade_one_refund(backend, "ORD-73055", 108.5, "damaged")


def reference_similar(call: CallFn) -> None:
    matches = call("search_customers", customer_name="Priya Nair")["matches"]
    customer = next(c for c in matches if c["name"] == "Priya Nair" and c["email"].endswith("@fernhill.example"))
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    details = [call("get_order", order_id=o["order_id"]) for o in orders]
    order = next(d for d in details if any("duvet" in i["description"].lower() for i in d["items"]))
    call("get_refund_policy")
    amount = order["subtotal"] + (order["shipping"] if customer["tier"] == "gold" else 0.0)
    call("issue_refund", order_id=order["order_id"], amount=round(amount, 2), reason="damaged")


_STORE_CREDIT_TOOL = ToolSpec(
    name="issue_store_credit",
    description="Issue store credit to a customer against one of their orders. The amount is in US dollars and is final once issued.",
    paraphrase="Grant a customer store credit tied to one of their orders. The amount is in US dollars, and it can't be undone once issued.",
    input_schema={
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "The customer receiving the credit."},
            "order_id": {"type": "string", "description": "The order the credit relates to."},
            "amount": {"type": "number", "description": "Credit amount in US dollars."},
        },
        "required": ["customer_id", "order_id", "amount"],
        "additionalProperties": False,
    },
)

_SEARCH_TOOL = ToolSpec(
    name="search_customers",
    description="Search customer accounts by name (case-insensitive partial match). Returns each match's ID, name, email and loyalty tier.",
    paraphrase="Look up customer accounts by name, ignoring case and allowing partial matches. Gives each match's ID, name, email and loyalty tier.",
    input_schema={
        "type": "object",
        "properties": {"customer_name": {"type": "string", "description": "Full or partial customer name."}},
        "required": ["customer_name"],
        "additionalProperties": False,
    },
)

_NEW_TASKS = [
    Task(
        task_id="orders_02_lost_in_transit",
        domain="orders",
        instruction=(
            "Today is 2026-10-20. The customer with email helen.brandt@example.com says one of her orders "
            "never arrived. Issue the refund she is owed under the refund policy."
        ),
        tools=TOOLS,
        make_backend=LostBackend,
        grade=grade_lost,
        reference_solution=reference_lost,
    ),
    Task(
        task_id="orders_03_outside_window_credit",
        domain="orders",
        instruction=(
            "Today is 2026-10-20. The customer with email noor.rahimi@example.com says the glass carafe she "
            "ordered arrived cracked. Resolve her claim as the refund policy requires."
        ),
        tools=(*TOOLS, _STORE_CREDIT_TOOL),
        make_backend=WindowBackend,
        grade=grade_window,
        reference_solution=reference_window,
    ),
    Task(
        task_id="orders_04_wrong_item_line",
        domain="orders",
        instruction=(
            "Today is 2026-10-20. The customer with email dwhitley@example.com says she was sent the wrong "
            "item in place of her walnut cutting board(s); the rest of that order was correct. "
            "Issue the refund she is owed under the refund policy."
        ),
        tools=TOOLS,
        make_backend=WrongItemBackend,
        grade=grade_wrong_item,
        reference_solution=reference_wrong_item,
    ),
    Task(
        task_id="orders_05_similar_names",
        domain="orders",
        instruction=(
            "Today is 2026-10-20. Priya Nair, whose account is registered to her work address on the "
            "fernhill.example domain, reports that the linen duvet cover she ordered arrived damaged. "
            "Issue the refund she is owed under the refund policy."
        ),
        tools=(_SEARCH_TOOL, *TOOLS[1:]),
        make_backend=SimilarNamesBackend,
        grade=grade_similar,
        reference_solution=reference_similar,
    ),
]

TASKS.extend(_NEW_TASKS)
