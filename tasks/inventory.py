"""Inventory domain: move stock between warehouses without breaching stock limits."""

from __future__ import annotations

import copy
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

_WAREHOUSES = [
    {"warehouse_id": "BOS", "city": "Boston"},
    {"warehouse_id": "CHI", "city": "Chicago"},
    {"warehouse_id": "DAL", "city": "Dallas"},
    {"warehouse_id": "PHX", "city": "Phoenix"},
    {"warehouse_id": "SEA", "city": "Seattle"},
]

# (sku, warehouse_id) -> levels. Available = on_hand - reserved. For WID-204 only DAL can
# cover BOS's shortfall of 28 while staying at or above its safety stock: CHI, PHX and SEA
# all have enough on hand but not enough available above safety stock.
_STOCK = {
    ("WID-204", "BOS"): {"on_hand": 20, "reserved": 8, "safety_stock": 5},
    ("WID-204", "CHI"): {"on_hand": 40, "reserved": 5, "safety_stock": 20},
    ("WID-204", "DAL"): {"on_hand": 60, "reserved": 10, "safety_stock": 15},
    ("WID-204", "PHX"): {"on_hand": 80, "reserved": 55, "safety_stock": 10},
    ("WID-204", "SEA"): {"on_hand": 90, "reserved": 70, "safety_stock": 10},
    ("WID-310", "BOS"): {"on_hand": 14, "reserved": 0, "safety_stock": 5},
    ("WID-310", "DAL"): {"on_hand": 120, "reserved": 30, "safety_stock": 25},
}

SKU = "WID-204"
TARGET = "BOS"
TARGET_AVAILABLE = 40


class InventoryBackend:
    # Subclasses swap in their own stock data.
    WAREHOUSES = _WAREHOUSES
    STOCK = _STOCK

    def __init__(self) -> None:
        self.warehouses = copy.deepcopy(self.WAREHOUSES)
        self.stock = copy.deepcopy(self.STOCK)
        self.initial_total = {sku: self._total(sku) for sku, _ in self.stock}
        self.transfers: list[dict[str, Any]] = []

    def _total(self, sku: str) -> int:
        return sum(v["on_hand"] for (s, _), v in self.stock.items() if s == sku)

    def _levels(self, sku: str, warehouse_id: str) -> dict[str, int]:
        if not any(w["warehouse_id"] == warehouse_id for w in self.warehouses):
            raise ToolError(f"No warehouse with ID {warehouse_id!r}.")
        if (sku, warehouse_id) not in self.stock:
            if not any(s == sku for s, _ in self.stock):
                raise ToolError(f"Unknown SKU {sku!r}.")
            self.stock[(sku, warehouse_id)] = {"on_hand": 0, "reserved": 0, "safety_stock": 0}
        return self.stock[(sku, warehouse_id)]

    def list_warehouses(self) -> dict[str, Any]:
        return {"warehouses": copy.deepcopy(self.warehouses)}

    def get_stock(self, sku: str, warehouse_id: str) -> dict[str, Any]:
        levels = self._levels(sku, warehouse_id)
        return {"sku": sku, "warehouse_id": warehouse_id, **levels}

    def get_sku_stock(self, sku: str) -> dict[str, Any]:
        rows = [(w, v) for (s, w), v in sorted(self.stock.items()) if s == sku]
        if not rows:
            raise ToolError(f"Unknown SKU {sku!r}.")
        return {"sku": sku, "warehouses": [{"warehouse_id": w, **v} for w, v in rows]}

    def get_warehouse_stock(self, warehouse_id: str) -> dict[str, Any]:
        if not any(w["warehouse_id"] == warehouse_id for w in self.warehouses):
            raise ToolError(f"No warehouse with ID {warehouse_id!r}.")
        items = [{"sku": s, **v} for (s, w), v in sorted(self.stock.items()) if w == warehouse_id]
        return {"warehouse_id": warehouse_id, "items": items}

    def transfer_stock(self, sku: str, from_warehouse_id: str, to_warehouse_id: str, quantity: int) -> dict[str, Any]:
        if from_warehouse_id == to_warehouse_id:
            raise ToolError("Source and destination warehouses must differ.")
        if quantity <= 0:
            raise ToolError("Quantity must be a positive integer.")
        src = self._levels(sku, from_warehouse_id)
        dst = self._levels(sku, to_warehouse_id)
        movable = src["on_hand"] - src["reserved"]
        if quantity > movable:
            raise ToolError(
                f"Cannot move {quantity} units: {from_warehouse_id} has only {movable} unreserved units of {sku}."
            )
        src["on_hand"] -= quantity
        dst["on_hand"] += quantity
        transfer = {
            "transfer_id": f"TR-{len(self.transfers) + 1:04d}",
            "sku": sku,
            "from_warehouse_id": from_warehouse_id,
            "to_warehouse_id": to_warehouse_id,
            "quantity": quantity,
        }
        self.transfers.append(transfer)
        return dict(transfer)

    def snapshot(self) -> dict[str, Any]:
        return {
            "transfers": copy.deepcopy(self.transfers),
            "stock": {f"{s}@{w}": dict(v) for (s, w), v in sorted(self.stock.items())},
        }


def grade(backend: InventoryBackend) -> Grade:
    if len(backend.transfers) != 1:
        return Grade(success=False, detail=f"expected exactly 1 transfer, found {len(backend.transfers)}")
    for sku, total in backend.initial_total.items():
        if backend._total(sku) != total:
            return Grade(success=False, detail=f"total stock of {sku} changed")
    for (sku, wh), v in backend.stock.items():
        if sku == SKU and v["on_hand"] - v["reserved"] < v["safety_stock"]:
            return Grade(success=False, detail=f"{wh} is below safety stock for {sku}")
    bos = backend.stock[(SKU, TARGET)]
    available = bos["on_hand"] - bos["reserved"]
    if available != TARGET_AVAILABLE:
        return Grade(success=False, detail=f"{TARGET} has {available} available, expected {TARGET_AVAILABLE}")
    return Grade(success=True, detail="correct transfer")


def reference_solution(call: CallFn) -> None:
    warehouses = [w["warehouse_id"] for w in call("list_warehouses")["warehouses"]]
    levels = {w: call("get_stock", sku=SKU, warehouse_id=w) for w in warehouses}
    target = levels[TARGET]
    shortfall = TARGET_AVAILABLE - (target["on_hand"] - target["reserved"])
    for w, lv in levels.items():
        if w != TARGET and lv["on_hand"] - lv["reserved"] - shortfall >= lv["safety_stock"]:
            call("transfer_stock", sku=SKU, from_warehouse_id=w, to_warehouse_id=TARGET, quantity=shortfall)
            return
    raise AssertionError("no single source can cover the shortfall")


TOOLS = (
    ToolSpec(
        name="list_warehouses",
        description="List all warehouses in the network with their IDs and cities.",
        paraphrase="Return every warehouse in the network, along with its ID and city.",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    ToolSpec(
        name="get_stock",
        description=(
            "Get stock levels for one SKU at one warehouse: units on hand, units reserved for open orders, "
            "and the safety stock level."
        ),
        paraphrase=(
            "Return one SKU's stock at one warehouse: on-hand units, units held for open orders, "
            "and the warehouse's safety stock."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "The product SKU."},
                "warehouse_id": {"type": "string", "description": "The warehouse ID."},
            },
            "required": ["sku", "warehouse_id"],
            "additionalProperties": False,
        },
    ),
    ToolSpec(
        name="transfer_stock",
        description="Move on-hand units of a SKU from one warehouse to another. Reserved units cannot be moved.",
        paraphrase="Transfer on-hand units of a SKU between two warehouses. Units that are reserved can't be moved.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string", "description": "The product SKU."},
                "from_warehouse_id": {"type": "string", "description": "Warehouse to move units out of."},
                "to_warehouse_id": {"type": "string", "description": "Warehouse to move units into."},
                "quantity": {"type": "integer", "description": "Number of units to move."},
            },
            "required": ["sku", "from_warehouse_id", "to_warehouse_id", "quantity"],
            "additionalProperties": False,
        },
    ),
)

TASKS = [
    Task(
        task_id="inventory_01_single_transfer",
        domain="inventory",
        instruction=(
            "Warehouse BOS needs exactly 40 available units of SKU WID-204 for an outbound order. "
            "Bring it to exactly 40 with a single transfer from one other warehouse. A warehouse's available "
            "units are its on-hand units minus its reserved units, and the transfer must not leave the source "
            "warehouse with fewer available units than its safety stock."
        ),
        tools=TOOLS,
        make_backend=InventoryBackend,
        grade=grade,
        reference_solution=reference_solution,
    ),
]


# --- inventory_02..05 ---------------------------------------------------------------------------

def _lv(on_hand: int, reserved: int, safety_stock: int) -> dict[str, int]:
    return {"on_hand": on_hand, "reserved": reserved, "safety_stock": safety_stock}


def _grade_on_hand(backend: InventoryBackend, moves: dict[tuple[str, str], int]) -> Grade:
    """Whole-state check: on-hand units everywhere must equal the start plus `moves`, nothing else touched."""
    base = backend.STOCK
    expected = {k: v["on_hand"] + moves.get(k, 0) for k, v in base.items()}
    actual = {k: v["on_hand"] for k, v in backend.stock.items() if k in base or v["on_hand"]}
    for k in sorted(set(expected) | set(actual)):
        if actual.get(k, 0) != expected.get(k, 0):
            return Grade(success=False,
                         detail=f"{k[0]}@{k[1]} has {actual.get(k, 0)} on hand, expected {expected.get(k, 0)}")
    for k, v in backend.stock.items():
        if k in base and (v["reserved"], v["safety_stock"]) != (base[k]["reserved"], base[k]["safety_stock"]):
            return Grade(success=False, detail=f"reserved or safety levels changed for {k[0]}@{k[1]}")
    return Grade(success=True, detail="correct end state")


def _avail(lv: dict[str, int]) -> int:
    return lv["on_hand"] - lv["reserved"]


def _surplus(lv: dict[str, int]) -> int:
    return lv["on_hand"] - lv["reserved"] - lv["safety_stock"]


# inventory_02: no single source can cover the shortfall; sources are used by largest surplus first.
# CHI has the most on hand but most of it is reserved, so it is the last choice by surplus.
class SplitBackend(InventoryBackend):
    STOCK = {
        ("WID-204", "BOS"): _lv(12, 2, 5),
        ("WID-204", "CHI"): _lv(100, 75, 10),
        ("WID-204", "DAL"): _lv(70, 10, 20),
        ("WID-204", "PHX"): _lv(50, 5, 10),
        ("WID-204", "SEA"): _lv(30, 0, 28),
    }


def grade_split(backend: InventoryBackend) -> Grade:
    # BOS needs 45 more: DAL's whole surplus (40), then 5 from PHX.
    return _grade_on_hand(backend, {("WID-204", "DAL"): -40, ("WID-204", "PHX"): -5, ("WID-204", "BOS"): 45})


def reference_split(call: CallFn) -> None:
    rows = call("get_sku_stock", sku="WID-204")["warehouses"]
    bos = next(r for r in rows if r["warehouse_id"] == "BOS")
    need = 55 - _avail(bos)
    donors = sorted((r for r in rows if r["warehouse_id"] != "BOS"), key=_surplus, reverse=True)
    for r in donors:
        if need <= 0:
            break
        qty = min(_surplus(r), need)
        if qty > 0:
            call("transfer_stock", sku="WID-204", from_warehouse_id=r["warehouse_id"],
                 to_warehouse_id="BOS", quantity=qty)
            need -= qty


# inventory_03: three SKUs; each has exactly one source that stays above safety stock, and one needs nothing.
class MultiSkuBackend(InventoryBackend):
    STOCK = {
        ("WID-204", "BOS"): _lv(20, 8, 5),
        ("WID-204", "CHI"): _lv(50, 10, 30),
        ("WID-204", "DAL"): _lv(40, 0, 15),
        ("WID-204", "PHX"): _lv(90, 70, 5),
        ("WID-310", "BOS"): _lv(9, 5, 3),
        ("WID-310", "CHI"): _lv(80, 20, 25),
        ("WID-310", "DAL"): _lv(30, 0, 10),
        ("WID-310", "PHX"): _lv(120, 100, 0),
        ("GAD-550", "BOS"): _lv(34, 4, 6),
        ("GAD-550", "CHI"): _lv(60, 5, 10),
        ("GAD-550", "DAL"): _lv(45, 0, 10),
    }


def grade_multi(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {
        ("WID-204", "DAL"): -18, ("WID-204", "BOS"): 18,
        ("WID-310", "CHI"): -26, ("WID-310", "BOS"): 26,
    })


def reference_multi(call: CallFn) -> None:
    for sku in ("WID-204", "WID-310", "GAD-550"):
        rows = call("get_sku_stock", sku=sku)["warehouses"]
        bos = next(r for r in rows if r["warehouse_id"] == "BOS")
        need = 30 - _avail(bos)
        if need <= 0:
            continue
        source = next(r for r in rows if r["warehouse_id"] != "BOS" and _surplus(r) >= need)
        call("transfer_stock", sku=sku, from_warehouse_id=source["warehouse_id"], to_warehouse_id="BOS", quantity=need)


# inventory_04: two warehouses to top up from one common donor; only DAL can cover both shortfalls.
class RebalanceBackend(InventoryBackend):
    STOCK = {
        ("WID-410", "BOS"): _lv(30, 5, 5),
        ("WID-410", "CHI"): _lv(100, 40, 25),
        ("WID-410", "DAL"): _lv(95, 25, 20),
        ("WID-410", "PHX"): _lv(220, 170, 30),
        ("WID-410", "SEA"): _lv(14, 4, 5),
    }


def grade_rebalance(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {("WID-410", "DAL"): -45, ("WID-410", "BOS"): 15, ("WID-410", "SEA"): 30})


def reference_rebalance(call: CallFn) -> None:
    rows = call("get_sku_stock", sku="WID-410")["warehouses"]
    targets = {r["warehouse_id"]: 40 - _avail(r) for r in rows if r["warehouse_id"] in ("BOS", "SEA")}
    total = sum(targets.values())
    donor = next(r for r in rows if r["warehouse_id"] not in targets and _surplus(r) >= total)
    for wh, qty in targets.items():
        if qty > 0:
            call("transfer_stock", sku="WID-410", from_warehouse_id=donor["warehouse_id"],
                 to_warehouse_id=wh, quantity=qty)


# inventory_05: closing PHX; only unreserved units move, and one SKU is fully reserved.
class ClosureBackend(InventoryBackend):
    STOCK = {
        ("WID-204", "BOS"): _lv(20, 8, 5),
        ("WID-204", "DAL"): _lv(40, 10, 15),
        ("WID-204", "PHX"): _lv(60, 35, 10),
        ("WID-310", "CHI"): _lv(30, 5, 10),
        ("WID-310", "DAL"): _lv(50, 10, 15),
        ("WID-310", "PHX"): _lv(25, 25, 5),
        ("GAD-550", "BOS"): _lv(34, 4, 6),
        ("GAD-550", "DAL"): _lv(22, 2, 8),
        ("GAD-550", "PHX"): _lv(48, 12, 8),
    }


def grade_closure(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {
        ("WID-204", "PHX"): -25, ("WID-204", "DAL"): 25,
        ("GAD-550", "PHX"): -36, ("GAD-550", "DAL"): 36,
    })


def reference_closure(call: CallFn) -> None:
    for item in call("get_warehouse_stock", warehouse_id="PHX")["items"]:
        movable = _avail(item)
        if movable > 0:
            call("transfer_stock", sku=item["sku"], from_warehouse_id="PHX", to_warehouse_id="DAL", quantity=movable)


_SKU_STOCK_TOOL = ToolSpec(
    name="get_sku_stock",
    description="Get stock levels for one SKU at every warehouse that holds it: on hand, reserved, and safety stock.",
    paraphrase="Return one SKU's stock at each warehouse that carries it: on-hand units, reserved units, and safety stock.",
    input_schema={
        "type": "object",
        "properties": {"sku": {"type": "string", "description": "The product SKU."}},
        "required": ["sku"],
        "additionalProperties": False,
    },
)

_WAREHOUSE_STOCK_TOOL = ToolSpec(
    name="get_warehouse_stock",
    description="Get stock levels for every SKU held at one warehouse: on hand, reserved, and safety stock.",
    paraphrase="Return stock for each SKU one warehouse holds: on-hand, reserved, and safety stock levels.",
    input_schema={
        "type": "object",
        "properties": {"warehouse_id": {"type": "string", "description": "The warehouse ID."}},
        "required": ["warehouse_id"],
        "additionalProperties": False,
    },
)

_LIST_TOOL, _GET_TOOL, _TRANSFER_TOOL = TOOLS

_RULES = (
    "A warehouse's available units are its on-hand units minus its reserved units. A transfer must never "
    "leave a source warehouse with fewer available units than its safety stock, and reserved units cannot move."
)

TASKS.extend([
    Task(
        task_id="inventory_02_split_sources",
        domain="inventory",
        instruction=(
            "Warehouse BOS needs exactly 55 available units of SKU WID-204. No single other warehouse can supply "
            "the shortfall, so split it across sources. " + _RULES + " Draw on the other warehouses in order of "
            "their surplus (available units minus safety stock), largest first, taking each one's entire surplus "
            "before moving on to the next; the last source gives only what is still missing."
        ),
        tools=(_LIST_TOOL, _SKU_STOCK_TOOL, _GET_TOOL, _TRANSFER_TOOL),
        make_backend=SplitBackend,
        grade=grade_split,
        reference_solution=reference_split,
    ),
    Task(
        task_id="inventory_03_three_skus",
        domain="inventory",
        instruction=(
            "Warehouse BOS needs exactly 30 available units of each of SKUs WID-204, WID-310 and GAD-550 for a "
            "consolidated outbound order. For each SKU that is short, make a single transfer from one other "
            "warehouse; leave any SKU that already has enough untouched. " + _RULES
        ),
        tools=(_LIST_TOOL, _SKU_STOCK_TOOL, _GET_TOOL, _TRANSFER_TOOL),
        make_backend=MultiSkuBackend,
        grade=grade_multi,
        reference_solution=reference_multi,
    ),
    Task(
        task_id="inventory_04_rebalance_two_sites",
        domain="inventory",
        instruction=(
            "Warehouses BOS and SEA must each hold exactly 40 available units of SKU WID-410. Bring both up "
            "to 40 using a single donor warehouse for both transfers (not BOS or SEA). " + _RULES
        ),
        tools=(_LIST_TOOL, _SKU_STOCK_TOOL, _GET_TOOL, _TRANSFER_TOOL),
        make_backend=RebalanceBackend,
        grade=grade_rebalance,
        reference_solution=reference_rebalance,
    ),
    Task(
        task_id="inventory_05_warehouse_closure",
        domain="inventory",
        instruction=(
            "Warehouse PHX is closing. Move every unreserved unit of every SKU it holds to warehouse DAL. "
            "Reserved units must stay at PHX to be fulfilled there, and PHX's safety stock levels no longer "
            "apply. Do not touch any other warehouse."
        ),
        tools=(_LIST_TOOL, _WAREHOUSE_STOCK_TOOL, _GET_TOOL, _TRANSFER_TOOL),
        make_backend=ClosureBackend,
        grade=grade_closure,
        reference_solution=reference_closure,
    ),
])
