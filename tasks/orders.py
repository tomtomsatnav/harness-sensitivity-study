"""Orders domain: find an order, check its status, issue a refund under a policy rule."""

from __future__ import annotations

import copy
import random
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
    def __init__(self) -> None:
        self.customers = copy.deepcopy(_CUSTOMERS)
        self.orders = copy.deepcopy(_ORDERS)
        # Seeded shuffle so the most recent order is not simply first or last in listings.
        random.Random(SEED).shuffle(self.orders)
        self.refunds: list[dict[str, Any]] = []

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
        return copy.deepcopy(_POLICY)

    def issue_refund(self, order_id: str, amount: float, reason: str) -> dict[str, Any]:
        o = self._order(order_id)
        if o["status"] != "delivered":
            raise ToolError(f"Order {order_id} has status {o['status']!r}; only delivered orders can be refunded.")
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

    def snapshot(self) -> dict[str, Any]:
        return {"refunds": copy.deepcopy(self.refunds)}


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
