"""Orders domain: find an order, check its status, issue a refund under a policy rule."""

from __future__ import annotations

import copy
import random
from datetime import date, timedelta
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

SEED = 1001
TODAY = date(2026, 10, 20)

_CUSTOMERS = [
    {"customer_id": "C-1042", "name": "Dana Whitfield", "email": "dana.w@example.com", "tier": "silver"},
    {"customer_id": "C-1187", "name": "Dana Whitley", "email": "dwhitley@example.com", "tier": "gold"},
    {"customer_id": "C-0931", "name": "Marcus Oyelaran", "email": "m.oyelaran@example.com", "tier": "bronze"},
]


def _item(sku: str, description: str, quantity: int, unit_price: float) -> dict[str, Any]:
    return {"sku": sku, "description": description, "quantity": quantity, "unit_price": unit_price}


def _order_row(order_id: str, customer_id: str, placed: str, status: str, delivered: str | None,
               shipping: float, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"order_id": order_id, "customer_id": customer_id, "placed_at": placed, "status": status,
            "delivered_at": delivered, "shipping": shipping, "items": items}


# Catalogue the filler orders draw on. It deliberately contains no carafe, cutting board or duvet:
# those are the products the task instructions name, so only hand-written orders carry them.
_CATALOG = [
    ("TEA-GRN-50", "Green tea, 50 bags", 18.25), ("TEA-EGY-40", "Earl Grey, 40 bags", 16.0),
    ("MUG-STN-2", "Stoneware mugs (2)", 21.0), ("KTL-ELC-1", "Electric kettle", 129.99),
    ("CER-BWL-4", "Ceramic bowl set (4)", 54.5), ("LIN-NAP-6", "Linen napkins (6)", 15.0),
    ("TWL-TEA-3", "Tea towels (3)", 17.5), ("CND-SET-3", "Candle set", 11.5),
    ("PLT-HRB-1", "Herb planter", 24.0), ("OIL-OLV-1", "Olive oil tin", 22.0),
    ("WRP-BWX-3", "Beeswax wrap set", 14.0), ("SOC-WOL-3", "Wool socks (3 pairs)", 27.0),
    ("PIL-LIN-2", "Pillowcase pair", 24.0), ("TRY-OAK-1", "Oak serving tray", 42.0),
    ("JAR-GLS-6", "Glass storage jars (6)", 29.0), ("TUM-GLS-4", "Glass tumblers (4)", 19.5),
    ("PCH-STN-1", "Stoneware pitcher", 34.0), ("APR-CAN-1", "Canvas apron", 26.0),
    ("SPN-WDN-4", "Wooden spoons (4)", 12.75), ("BSK-WVN-1", "Woven storage basket", 31.0),
    ("CST-IRN-1", "Cast-iron casserole", 89.0), ("GRT-BOX-1", "Box grater", 15.5),
    ("SLT-CER-1", "Ceramic salt cellar", 13.0), ("PLC-COT-4", "Cotton placemats (4)", 18.0),
    ("BLK-WOL-1", "Wool blanket", 74.0), ("CSH-VLV-2", "Velvet cushion covers (2)", 28.0),
    ("DIF-REE-1", "Reed diffuser", 23.5), ("SCS-KIT-1", "Kitchen scissors", 11.0),
]

_FILLER_STATUSES = ("delivered",) * 6 + ("cancelled", "returned")


def _extra_orders(customer_id: str, count: int, before: str, base_id: int, seed: int,
                  statuses: tuple[str, ...] = _FILLER_STATUSES) -> list[dict[str, Any]]:
    """Seeded filler orders for one customer, all placed strictly before `before`, oldest first."""
    rng = random.Random(seed)
    end = date.fromisoformat(before)
    rows, next_id = [], base_id
    for offset in sorted(rng.sample(range(10, 560), count), reverse=True):
        placed = end - timedelta(days=offset)
        status = rng.choice(statuses)
        delivered = placed + timedelta(days=rng.randint(3, 7)) if status in ("delivered", "returned") else None
        lines = [_item(sku, desc, rng.choice([1, 1, 1, 2, 2, 3]), price)
                 for sku, desc, price in rng.sample(_CATALOG, rng.choice([1, 1, 2, 2, 3]))]
        next_id += rng.randint(35, 400)
        rows.append(_order_row(f"ORD-{next_id}", customer_id, placed.isoformat(), status,
                               delivered.isoformat() if delivered else None,
                               rng.choice([0.0, 4.99, 5.99, 7.99, 9.5]), lines))
    return rows


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
    # Filler: older orders for Dana Whitfield (all placed before ORD-61230) and for Dana Whitley.
    *_extra_orders("C-1042", 20, "2026-10-06", 41000, 11),
    *_extra_orders("C-1187", 6, "2026-10-08", 46000, 12),
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


# ---------------------------------------------------------------------------
# Presentation: realistic but irrelevant fields, derived deterministically from each entity's ID.

_CHANNELS = ["web", "ios_app", "android_app", "phone"]
_CARRIERS = ["UPS", "FedEx", "USPS", "DHL"]
_PAYMENTS = ["card_visa", "card_mastercard", "card_amex", "paypal", "apple_pay"]
_ADDRESSES = [
    ("14 Beacon Street", "Boston", "MA"), ("880 Lake Shore Drive", "Chicago", "IL"),
    ("2301 Elm Street", "Dallas", "TX"), ("45 Camelback Road", "Phoenix", "AZ"),
    ("1900 Pike Place", "Seattle", "WA"), ("77 Peachtree Lane", "Atlanta", "GA"),
]
_NOTES = [None, None, "Prefers email contact.", "Asked about bulk pricing in March.",
          "Delivery instructions: leave with concierge.", "Gift registry opened 2025."]


def _rng(*parts: str) -> random.Random:
    return random.Random("orders:" + ":".join(parts))


def _stamp(d: date, rng: random.Random) -> str:
    return f"{d.isoformat()}T{rng.randint(7, 20):02d}:{rng.randint(0, 59):02d}"


def _customer_view(c: dict[str, Any]) -> dict[str, Any]:
    rng = _rng("customer", c["customer_id"])
    return {
        "customer_id": c["customer_id"],
        "name": c["name"],
        "email": c["email"],
        "phone": f"+1 {rng.choice([617, 312, 214, 602, 206])} 555 {rng.randint(100, 999)}{rng.randint(0, 9)}",
        "tier": c["tier"],
        "region": rng.choice(["us_northeast", "us_midwest", "us_south", "us_west"]),
        "locale": "en-US",
        "created_at": (TODAY - timedelta(days=rng.randint(200, 1500))).isoformat(),
        "last_login_at": _stamp(TODAY - timedelta(days=rng.randint(0, 12)), rng),
        "marketing_opt_in": rng.random() < 0.6,
        "tags": rng.sample(["newsletter", "repeat_buyer", "gift_registry", "app_user", "wholesale_inquiry"],
                           rng.randint(1, 3)),
        "account_notes": rng.choice(_NOTES),
    }


def _order_extras(o: dict[str, Any]) -> dict[str, Any]:
    rng = _rng("order", o["order_id"])
    shipped = o["status"] != "cancelled"
    last = date.fromisoformat(o["delivered_at"] or o["placed_at"]) + timedelta(days=0 if o["delivered_at"] else 1)
    return {
        "channel": rng.choice(_CHANNELS),
        "carrier": rng.choice(_CARRIERS) if shipped else None,
        "tracking_ref": f"TRK-{rng.randint(10**8, 10**9 - 1)}" if shipped else None,
        "payment_method": rng.choice(_PAYMENTS),
        "promo_code": rng.choice([None, None, None, "WELCOME10", "AUTUMN15", "FREESHIP"]),
        "internal_ref": f"OMS-{rng.randint(100000, 999999)}",
        "updated_at": _stamp(last, rng),
    }


def _address(customer_id: str) -> dict[str, Any]:
    line, city, state = _rng("address", customer_id).choice(_ADDRESSES)
    return {"line_one": line, "city": city, "region_code": state, "country": "US"}


def _item_view(order_id: str, i: dict[str, Any]) -> dict[str, Any]:
    rng = _rng("item", order_id, i["sku"])
    return {
        "sku": i["sku"],
        "description": i["description"],
        "quantity": i["quantity"],
        "tax_code": rng.choice(["GEN", "HOME", "FOOD"]),
        "unit_price": i["unit_price"],
        "weight_kg": rng.choice([0.2, 0.35, 0.5, 0.8, 1.2, 1.9, 2.4]),
        "fulfilled_from": rng.choice(["NJE", "DAL", "PHX", "CHI"]),
        "gift_wrap": rng.random() < 0.15,
    }


def _order_summary(o: dict[str, Any]) -> dict[str, Any]:
    x = _order_extras(o)
    return {
        "order_id": o["order_id"],
        "placed_at": o["placed_at"],
        "channel": x["channel"],
        "status": o["status"],
        "item_count": sum(i["quantity"] for i in o["items"]),
        "item_summary": "; ".join(i["description"] for i in o["items"]),
        "currency": "USD",
        "total": round(_subtotal(o) + o["shipping"], 2),
        "carrier": x["carrier"],
        "updated_at": x["updated_at"],
    }


def _order_detail(o: dict[str, Any]) -> dict[str, Any]:
    x = _order_extras(o)
    subtotal = _subtotal(o)
    return {
        "order_id": o["order_id"],
        "customer_id": o["customer_id"],
        "placed_at": o["placed_at"],
        "channel": x["channel"],
        "status": o["status"],
        "delivered_at": o["delivered_at"],
        "carrier": x["carrier"],
        "tracking_ref": x["tracking_ref"],
        "payment_method": x["payment_method"],
        "currency": "USD",
        "shipping_address": _address(o["customer_id"]),
        "billing_address": _address(o["customer_id"]),
        "items": [_item_view(o["order_id"], i) for i in o["items"]],
        "subtotal": subtotal,
        "shipping": o["shipping"],
        "total": round(subtotal + o["shipping"], 2),
        "promo_code": x["promo_code"],
        "internal_ref": x["internal_ref"],
        "updated_at": x["updated_at"],
    }


def _policy_view(p: dict[str, Any]) -> dict[str, Any]:
    rng = _rng("policy", p["policy_id"])
    params = {k: v for k, v in p.items() if k not in ("policy_id", "rules")}
    return {
        "policy_id": p["policy_id"],
        "version": rng.randint(2, 7),
        "effective_from": f"2026-{rng.randint(1, 6):02d}-01",
        "owner": "Customer Operations",
        **params,
        "rules": list(p["rules"]),
        "jurisdiction": "US",
        "last_reviewed_at": f"2026-{rng.randint(7, 9):02d}-{rng.randint(1, 28):02d}",
        "policy_notes": "Refunds settle to the original payment method within five working days.",
    }


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
                return _customer_view(c)
        raise ToolError(f"No customer with email {email!r}.")

    def search_customers(self, customer_name: str) -> dict[str, Any]:
        needle = customer_name.strip().lower()
        return {
            "query": customer_name,
            "matches": [_customer_view(c) for c in self.customers if needle and needle in c["name"].lower()],
        }

    def list_orders(self, customer_id: str) -> dict[str, Any]:
        if not any(c["customer_id"] == customer_id for c in self.customers):
            raise ToolError(f"No customer with ID {customer_id!r}.")
        return {
            "customer_id": customer_id,
            "orders": [_order_summary(o) for o in self.orders if o["customer_id"] == customer_id],
        }

    def get_order(self, order_id: str) -> dict[str, Any]:
        return _order_detail(self._order(order_id))

    def get_refund_policy(self) -> dict[str, Any]:
        return _policy_view(self.POLICY)

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
        return {**refund, "currency": "USD", "settlement_method": "original_payment_method",
                "issued_at": f"{TODAY.isoformat()}T10:{len(self.refunds):02d}"}

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
        return {**credit, "currency": "USD", "issued_at": f"{TODAY.isoformat()}T10:{len(self.store_credits):02d}"}

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
        description="List every order placed by a customer, with each order's ID, placement date, status, item summary and total.",
        paraphrase="Return all orders a customer has placed, including each order's ID, date placed, status, item summary and total.",
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
        # Filler: every one of these arrived (or came back), so none can be the lost order.
        *_extra_orders("C-2201", 19, "2026-09-28", 62000, 21, statuses=("delivered",) * 5 + ("returned",)),
        *_extra_orders("C-2202", 5, "2026-10-10", 66000, 22, statuses=("delivered",) * 5 + ("returned",)),
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
        # Filler: no other order of hers contains a carafe (the catalogue has none).
        *_extra_orders("C-2310", 20, "2026-10-09", 63000, 31),
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
    candidates = [o for o in orders if "carafe" in o["item_summary"].lower()]
    order = call("get_order", order_id=candidates[0]["order_id"])
    policy = call("get_refund_policy")
    age = (TODAY - date.fromisoformat(order["delivered_at"])).days
    if age <= policy["refund_window_days"]:
        raise AssertionError("order is inside the refund window")
    credit = round(order["subtotal"] * policy["store_credit_percent"] / 100, 2)
    call("issue_store_credit", customer_id=customer["customer_id"], order_id=order["order_id"], amount=credit)


# orders_04: wrong_item on one line of an order; the same product is also in older, out-of-window orders.
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
        # A third cutting board order, delivered months before the window opened.
        _order_row("ORD-68420", "C-1187", "2026-05-19", "delivered", "2026-05-24", 6.5,
                   [_item("BRD-WLN-1", "Walnut cutting board", 1, 38.0), _item("APR-CAN-1", "Canvas apron", 1, 26.0)]),
        *_extra_orders("C-1187", 18, "2026-10-04", 64000, 41),
        *_extra_orders("C-1042", 5, "2026-10-06", 67000, 42),
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
    candidates = [o for o in orders if "cutting board" in o["item_summary"].lower()]
    details = [call("get_order", order_id=o["order_id"]) for o in candidates]
    recent = [d for d in details if d["delivered_at"] and (TODAY - date.fromisoformat(d["delivered_at"])).days <= 30]
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
        {"customer_id": "C-3305", "name": "Priya Nair", "email": "priya.nair@example.net", "tier": "bronze"},
        {"customer_id": "C-3306", "name": "Priya Nair", "email": "priya.nair@fernhillpress.example", "tier": "silver"},
        {"customer_id": "C-3307", "name": "Supriya Nair", "email": "supriya.nair@example.com", "tier": "bronze"},
        {"customer_id": "C-3308", "name": "Anil Nair", "email": "anil.nair@fernhill.example", "tier": "silver"},
        {"customer_id": "C-3309", "name": "Priya Natarajan", "email": "priya.n@example.org", "tier": "gold"},
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
        _order_row("ORD-73120", "C-3305", "2026-10-09", "delivered", "2026-10-14", 5.0,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0)]),
        _order_row("ORD-73133", "C-3306", "2026-10-08", "delivered", "2026-10-13", 5.5,
                   [_item("DUV-LIN-1", "Linen duvet cover", 1, 79.0), _item("PIL-LIN-2", "Pillowcase pair", 2, 24.0)]),
        # Filler: the right Priya's other orders, none containing a duvet.
        *_extra_orders("C-3302", 20, "2026-10-03", 65000, 51),
        *_extra_orders("C-3301", 4, "2026-10-06", 68000, 52),
        *_extra_orders("C-3306", 4, "2026-10-08", 68500, 53),
    ]


def grade_similar(backend: OrdersBackend) -> Grade:
    return _grade_one_refund(backend, "ORD-73055", 108.5, "damaged")


def reference_similar(call: CallFn) -> None:
    matches = call("search_customers", customer_name="Priya Nair")["matches"]
    customer = next(c for c in matches if c["name"] == "Priya Nair" and c["email"].endswith("@fernhill.example"))
    orders = call("list_orders", customer_id=customer["customer_id"])["orders"]
    candidates = [o for o in orders if "duvet" in o["item_summary"].lower()]
    order = call("get_order", order_id=candidates[0]["order_id"])
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
