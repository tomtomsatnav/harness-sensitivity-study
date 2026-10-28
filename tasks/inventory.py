"""Inventory domain: move stock between warehouses without breaching stock limits."""

from __future__ import annotations

import copy
import random
from typing import Any

from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

_WAREHOUSES = [
    {"warehouse_id": "BOS", "city": "Boston", "region": "northeast", "timezone": "America/New_York",
     "street_address": "1200 Harbor Way"},
    {"warehouse_id": "CHI", "city": "Chicago", "region": "midwest", "timezone": "America/Chicago",
     "street_address": "4410 Industrial Drive"},
    {"warehouse_id": "DAL", "city": "Dallas", "region": "south", "timezone": "America/Chicago",
     "street_address": "901 Commerce Parkway"},
    {"warehouse_id": "PHX", "city": "Phoenix", "region": "southwest", "timezone": "America/Phoenix",
     "street_address": "3300 Saguaro Boulevard"},
    {"warehouse_id": "SEA", "city": "Seattle", "region": "northwest", "timezone": "America/Los_Angeles",
     "street_address": "77 Rainier Avenue"},
    {"warehouse_id": "ATL", "city": "Atlanta", "region": "southeast", "timezone": "America/New_York",
     "street_address": "560 Fulton Court"},
    {"warehouse_id": "DEN", "city": "Denver", "region": "mountain", "timezone": "America/Denver",
     "street_address": "18 Front Range Road"},
    {"warehouse_id": "MIA", "city": "Miami", "region": "southeast", "timezone": "America/New_York",
     "street_address": "2450 Biscayne Trail"},
    {"warehouse_id": "MSP", "city": "Minneapolis", "region": "midwest", "timezone": "America/Chicago",
     "street_address": "710 Lakeside Loop"},
    {"warehouse_id": "NJE", "city": "Newark", "region": "northeast", "timezone": "America/New_York",
     "street_address": "95 Port Street"},
    {"warehouse_id": "LAX", "city": "Los Angeles", "region": "west", "timezone": "America/Los_Angeles",
     "street_address": "6100 Alameda Avenue"},
    {"warehouse_id": "PDX", "city": "Portland", "region": "northwest", "timezone": "America/Los_Angeles",
     "street_address": "2 Willamette Row"},
    {"warehouse_id": "SLC", "city": "Salt Lake City", "region": "mountain", "timezone": "America/Denver",
     "street_address": "1480 Wasatch Lane"},
    {"warehouse_id": "CLT", "city": "Charlotte", "region": "southeast", "timezone": "America/New_York",
     "street_address": "330 Piedmont Way"},
]

_SKUS = ["WID-204", "WID-310", "WID-410", "GAD-550", "WID-118", "WID-275", "GAD-120", "GAD-731",
         "BRK-055", "BRK-402", "CAB-900", "CAB-915", "FLT-230", "FLT-611", "HNG-077", "PMP-340"]


def _lv(on_hand: int, reserved: int, safety_stock: int) -> dict[str, int]:
    return {"on_hand": on_hand, "reserved": reserved, "safety_stock": safety_stock}


def _expand(base: dict[tuple[str, str], dict[str, int]], seed: int, fixed: tuple[str, ...],
            skip: tuple[str, ...] = ()) -> dict[tuple[str, str], dict[str, int]]:
    """Add seeded filler rows for SKUs a task does not mention.

    Rows for the task's own SKUs (`fixed`) are always hand-written so their levels can be reasoned
    about; warehouses in `skip` get no filler at all.
    """
    rng = random.Random(seed)
    stock = dict(base)
    for w in _WAREHOUSES:
        wid = w["warehouse_id"]
        if wid in skip:
            continue
        for sku in _SKUS:
            if sku in fixed or (sku, wid) in stock or rng.random() < 0.45:
                continue
            on_hand = rng.randint(8, 180)
            stock[(sku, wid)] = _lv(on_hand, rng.randint(0, on_hand * 2 // 3), rng.randint(4, 30))
    return stock


# (sku, warehouse_id) -> levels. Available = on_hand - reserved. For WID-204 only DAL can
# cover BOS's shortfall of 28 while staying at or above its safety stock: CHI, PHX, SEA, ATL and
# DEN all have enough on hand but not enough available above safety stock.
_STOCK = _expand({
    ("WID-204", "BOS"): _lv(20, 8, 5),
    ("WID-204", "CHI"): _lv(40, 5, 20),
    ("WID-204", "DAL"): _lv(60, 10, 15),
    ("WID-204", "PHX"): _lv(80, 55, 10),
    ("WID-204", "SEA"): _lv(90, 70, 10),
    ("WID-204", "ATL"): _lv(36, 12, 10),   # 24 available: short of the 28 needed
    ("WID-204", "DEN"): _lv(55, 20, 15),   # 35 available, but only 20 above safety stock
    ("WID-310", "BOS"): _lv(14, 0, 5),
    ("WID-310", "DAL"): _lv(120, 30, 25),
}, seed=101, fixed=("WID-204", "WID-310"))

SKU = "WID-204"
TARGET = "BOS"
TARGET_AVAILABLE = 40


# ---------------------------------------------------------------------------
# Presentation: realistic but irrelevant fields, derived deterministically from each entity's ID.

def _rng(*parts: str) -> random.Random:
    return random.Random("inventory:" + ":".join(parts))


def _warehouse_view(w: dict[str, Any], stocked: list[str]) -> dict[str, Any]:
    rng = _rng("site", w["warehouse_id"])
    first = rng.choice(["Maria", "Devon", "Priya", "Jonah", "Elena", "Kofi", "Hannah", "Luis"])
    last = rng.choice(["Okafor", "Lindqvist", "Castillo", "Nakamura", "Brennan", "Haddad", "Petrov"])
    return {
        "warehouse_id": w["warehouse_id"],
        "city": w["city"],
        "region": w["region"],
        "status": "active",
        "timezone": w["timezone"],
        "street_address": w["street_address"],
        "dock_doors": rng.choice([4, 6, 8, 12, 16]),
        "site_manager": f"{first} {last}",
        "commissioned_at": f"{rng.randint(2009, 2024)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        "cutoff_time": rng.choice(["14:00", "15:00", "16:30", "17:00"]),
        "stocked_skus": stocked,
    }


def _row_view(sku: str, warehouse_id: str, lv: dict[str, int]) -> dict[str, Any]:
    rng = _rng("row", sku, warehouse_id)
    return {
        "sku": sku,
        "warehouse_id": warehouse_id,
        "bin_location": f"{rng.choice('ABCDEF')}-{rng.randint(1, 40):02d}-{rng.randint(1, 8):02d}",
        "on_hand": lv["on_hand"],
        "inbound_units": rng.choice([0, 0, 0, 12, 24, 36, 48]),
        "reserved": lv["reserved"],
        "unit_cost": rng.choice([4.2, 7.85, 12.4, 19.99, 23.5, 41.0]),
        "safety_stock": lv["safety_stock"],
        "unit_of_measure": "each",
        "supplier": rng.choice(["Northwind Components", "Acme Fabrication", "Keystone Supply", "Harbor Metals"]),
        "lead_time_days": rng.choice([5, 7, 10, 14, 21]),
        "last_counted_at": f"2026-{rng.randint(8, 10):02d}-{rng.randint(1, 18):02d}",
    }


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
        return {"warehouses": [
            _warehouse_view(w, sorted(s for (s, wid) in self.stock if wid == w["warehouse_id"]))
            for w in self.warehouses
        ]}

    def get_stock(self, sku: str, warehouse_id: str) -> dict[str, Any]:
        return _row_view(sku, warehouse_id, self._levels(sku, warehouse_id))

    def get_sku_stock(self, sku: str) -> dict[str, Any]:
        rows = [(w, v) for (s, w), v in sorted(self.stock.items()) if s == sku]
        if not rows:
            raise ToolError(f"Unknown SKU {sku!r}.")
        return {"sku": sku, "warehouses": [
            {k: v for k, v in _row_view(sku, w, lv).items() if k != "sku"} for w, lv in rows
        ]}

    def get_warehouse_stock(self, warehouse_id: str) -> dict[str, Any]:
        if not any(w["warehouse_id"] == warehouse_id for w in self.warehouses):
            raise ToolError(f"No warehouse with ID {warehouse_id!r}.")
        rows = [(s, v) for (s, w), v in sorted(self.stock.items()) if w == warehouse_id]
        return {"warehouse_id": warehouse_id, "items": [
            {k: v for k, v in _row_view(s, warehouse_id, lv).items() if k != "warehouse_id"} for s, lv in rows
        ]}

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
        return {**transfer, "status": "completed", "carrier": "internal_fleet",
                "requested_at": f"2026-10-20T09:{len(self.transfers):02d}"}

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
    sites = call("list_warehouses")["warehouses"]
    holders = [w["warehouse_id"] for w in sites if SKU in w["stocked_skus"]]
    levels = {w: call("get_stock", sku=SKU, warehouse_id=w) for w in holders}
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
        description="List all warehouses in the network with their IDs, cities and the SKUs each one stocks.",
        paraphrase="Return every warehouse in the network, along with its ID, city and the SKUs it holds.",
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
# CHI has the most on hand but most of it is reserved, so it is a late choice by surplus. Surpluses:
# DAL 40, PHX 35, ATL 30, PDX 25, LAX 20, CHI 15, DEN 10, SEA 2, MIA 0.
class SplitBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-204", "BOS"): _lv(12, 2, 5),
        ("WID-204", "CHI"): _lv(100, 75, 10),
        ("WID-204", "DAL"): _lv(70, 10, 20),
        ("WID-204", "PHX"): _lv(50, 5, 10),
        ("WID-204", "SEA"): _lv(30, 0, 28),
        ("WID-204", "ATL"): _lv(48, 10, 8),
        ("WID-204", "DEN"): _lv(90, 70, 10),
        ("WID-204", "MIA"): _lv(26, 6, 20),
        ("WID-204", "LAX"): _lv(140, 100, 20),
        ("WID-204", "PDX"): _lv(33, 3, 5),
    }, seed=102, fixed=("WID-204",))


def grade_split(backend: InventoryBackend) -> Grade:
    # BOS needs 45 more: DAL's whole surplus (40), then 5 from PHX.
    return _grade_on_hand(backend, {("WID-204", "DAL"): -40, ("WID-204", "PHX"): -5, ("WID-204", "BOS"): 45})


def reference_split(call: CallFn) -> None:
    call("list_warehouses")
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


# inventory_03: three SKUs; each short SKU has exactly one source that stays above safety stock
# (WID-204 needs 18: only DAL's surplus of 25 covers it; WID-310 needs 26: only CHI's 35), and
# GAD-550 needs nothing.
class MultiSkuBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-204", "BOS"): _lv(20, 8, 5),
        ("WID-204", "CHI"): _lv(50, 10, 30),
        ("WID-204", "DAL"): _lv(40, 0, 15),
        ("WID-204", "PHX"): _lv(90, 70, 5),
        ("WID-204", "ATL"): _lv(30, 10, 5),
        ("WID-204", "SEA"): _lv(44, 20, 10),
        ("WID-204", "LAX"): _lv(25, 5, 10),
        ("WID-310", "BOS"): _lv(9, 5, 3),
        ("WID-310", "CHI"): _lv(80, 20, 25),
        ("WID-310", "DAL"): _lv(30, 0, 10),
        ("WID-310", "PHX"): _lv(120, 100, 0),
        ("WID-310", "ATL"): _lv(60, 30, 10),
        ("WID-310", "SEA"): _lv(50, 25, 5),
        ("WID-310", "MIA"): _lv(28, 0, 10),
        ("GAD-550", "BOS"): _lv(34, 4, 6),
        ("GAD-550", "CHI"): _lv(60, 5, 10),
        ("GAD-550", "DAL"): _lv(45, 0, 10),
        ("GAD-550", "SEA"): _lv(40, 10, 10),
        ("GAD-550", "PHX"): _lv(55, 50, 5),
        ("GAD-550", "ATL"): _lv(12, 2, 4),
    }, seed=103, fixed=("WID-204", "WID-310", "GAD-550"))


def grade_multi(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {
        ("WID-204", "DAL"): -18, ("WID-204", "BOS"): 18,
        ("WID-310", "CHI"): -26, ("WID-310", "BOS"): 26,
    })


def reference_multi(call: CallFn) -> None:
    call("list_warehouses")
    for sku in ("WID-204", "WID-310", "GAD-550"):
        rows = call("get_sku_stock", sku=sku)["warehouses"]
        bos = next(r for r in rows if r["warehouse_id"] == "BOS")
        need = 30 - _avail(bos)
        if need <= 0:
            continue
        source = next(r for r in rows if r["warehouse_id"] != "BOS" and _surplus(r) >= need)
        call("transfer_stock", sku=sku, from_warehouse_id=source["warehouse_id"], to_warehouse_id="BOS", quantity=need)


# inventory_04: two warehouses to top up from one common donor; only DAL (surplus 50) can cover
# both shortfalls (45 in total). ATL's surplus of 40 is the nearest miss.
class RebalanceBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-410", "BOS"): _lv(30, 5, 5),
        ("WID-410", "CHI"): _lv(100, 40, 25),
        ("WID-410", "DAL"): _lv(95, 25, 20),
        ("WID-410", "PHX"): _lv(220, 170, 30),
        ("WID-410", "SEA"): _lv(14, 4, 5),
        ("WID-410", "ATL"): _lv(80, 30, 10),
        ("WID-410", "MIA"): _lv(60, 20, 5),
        ("WID-410", "LAX"): _lv(150, 120, 10),
        ("WID-410", "DEN"): _lv(40, 10, 5),
        ("WID-410", "PDX"): _lv(20, 10, 5),
    }, seed=104, fixed=("WID-410",))


def grade_rebalance(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {("WID-410", "DAL"): -45, ("WID-410", "BOS"): 15, ("WID-410", "SEA"): 30})


def reference_rebalance(call: CallFn) -> None:
    call("list_warehouses")
    rows = call("get_sku_stock", sku="WID-410")["warehouses"]
    targets = {r["warehouse_id"]: 40 - _avail(r) for r in rows if r["warehouse_id"] in ("BOS", "SEA")}
    total = sum(targets.values())
    donor = next(r for r in rows if r["warehouse_id"] not in targets and _surplus(r) >= total)
    for wh, qty in targets.items():
        if qty > 0:
            call("transfer_stock", sku="WID-410", from_warehouse_id=donor["warehouse_id"],
                 to_warehouse_id=wh, quantity=qty)


# inventory_05: closing PHX; only unreserved units move. Besides the two SKUs with unreserved units,
# PHX holds several that are fully reserved or out of stock, so there is nothing to move for them.
class ClosureBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-204", "BOS"): _lv(20, 8, 5),
        ("WID-204", "DAL"): _lv(40, 10, 15),
        ("WID-204", "PHX"): _lv(60, 35, 10),
        ("WID-310", "CHI"): _lv(30, 5, 10),
        ("WID-310", "DAL"): _lv(50, 10, 15),
        ("WID-310", "PHX"): _lv(25, 25, 5),
        ("GAD-550", "BOS"): _lv(34, 4, 6),
        ("GAD-550", "DAL"): _lv(22, 2, 8),
        ("GAD-550", "PHX"): _lv(48, 12, 8),
        ("WID-410", "PHX"): _lv(40, 40, 10),
        ("GAD-120", "PHX"): _lv(18, 18, 5),
        ("FLT-230", "PHX"): _lv(12, 12, 2),
        ("PMP-340", "PHX"): _lv(9, 9, 3),
        ("BRK-055", "PHX"): _lv(0, 0, 4),
        ("CAB-900", "PHX"): _lv(0, 0, 6),
        ("HNG-077", "PHX"): _lv(0, 0, 3),
    }, seed=105, fixed=("WID-204", "WID-310", "GAD-550"), skip=("PHX",))


def grade_closure(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {
        ("WID-204", "PHX"): -25, ("WID-204", "DAL"): 25,
        ("GAD-550", "PHX"): -36, ("GAD-550", "DAL"): 36,
    })


def reference_closure(call: CallFn) -> None:
    call("list_warehouses")
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


# --- hard tasks ---------------------------------------------------------------------------------

# inventory_h1: two destinations, handled in order, with a same-region-first rule and a fallback to
# the largest surplus at the time of the transfer. NJE (northeast, surplus 45) covers BOS's 20; that
# drops NJE to 25, so SEA's fallback is DAL (40), not NJE. PDX is SEA's region but has only 10. CHI
# has the most on hand and the most available but almost no surplus.
class RegionBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-204", "BOS"): _lv(25, 5, 5),      # 20 available: needs 20
        ("WID-204", "SEA"): _lv(30, 20, 8),     # 10 available: needs 30
        ("WID-204", "NJE"): _lv(70, 15, 10),    # surplus 45
        ("WID-204", "DAL"): _lv(80, 25, 15),    # surplus 40
        ("WID-204", "PDX"): _lv(22, 4, 8),      # surplus 10
        ("WID-204", "CHI"): _lv(120, 60, 55),   # surplus 5
        ("WID-204", "PHX"): _lv(60, 45, 5),     # surplus 10
        ("WID-204", "ATL"): _lv(44, 10, 20),    # surplus 14
        ("WID-204", "MIA"): _lv(30, 2, 25),     # surplus 3
        ("WID-204", "DEN"): _lv(50, 30, 10),    # surplus 10
        ("WID-204", "LAX"): _lv(90, 70, 10),    # surplus 10
    }, seed=106, fixed=("WID-204",))


def grade_region(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {
        ("WID-204", "NJE"): -20, ("WID-204", "BOS"): 20,
        ("WID-204", "DAL"): -30, ("WID-204", "SEA"): 30,
    })


def reference_region(call: CallFn) -> None:
    region = {w["warehouse_id"]: w["region"] for w in call("list_warehouses")["warehouses"]}
    for dest in ("BOS", "SEA"):
        rows = call("get_sku_stock", sku="WID-204")["warehouses"]  # re-read: the first transfer changes surpluses
        need = 40 - _avail(next(r for r in rows if r["warehouse_id"] == dest))
        donors = [r for r in rows if r["warehouse_id"] not in ("BOS", "SEA")]
        local = [r for r in donors if region[r["warehouse_id"]] == region[dest] and _surplus(r) >= need]
        source = local[0] if local else max(donors, key=_surplus)
        call("transfer_stock", sku="WID-204", from_warehouse_id=source["warehouse_id"],
             to_warehouse_id=dest, quantity=need)


# inventory_h2: stock may only move along lanes. Both lanes into SEA (LAX, PDX) lack the surplus to
# cover the shortfall of 30, so it is routed DAL -> LAX -> SEA. LAX contributes its own surplus of 12
# and takes in only the 18 it lacks. PHX also feeds LAX but is fully reserved; CHI has a large surplus
# but no useful lane.
_LANES = (
    ("DAL", "LAX"), ("PHX", "LAX"), ("LAX", "SEA"), ("PDX", "SEA"), ("CHI", "DAL"), ("DAL", "BOS"),
    ("NJE", "BOS"), ("BOS", "NJE"), ("ATL", "MIA"), ("MIA", "CLT"), ("DEN", "SLC"), ("SLC", "DEN"),
    ("CHI", "MSP"), ("DAL", "PHX"),
)


class LaneBackend(InventoryBackend):
    STOCK = _expand({
        ("WID-204", "SEA"): _lv(18, 8, 5),      # 10 available: needs 30
        ("WID-204", "LAX"): _lv(40, 18, 10),    # surplus 12
        ("WID-204", "PDX"): _lv(14, 4, 6),      # surplus 4
        ("WID-204", "DAL"): _lv(90, 20, 30),    # surplus 40
        ("WID-204", "PHX"): _lv(50, 50, 5),     # fully reserved
        ("WID-204", "CHI"): _lv(100, 20, 20),   # surplus 60, no lane toward SEA
        ("WID-204", "BOS"): _lv(30, 10, 5),
        ("WID-204", "NJE"): _lv(45, 5, 10),
    }, seed=107, fixed=("WID-204",))
    LANES = _LANES

    def list_lanes(self) -> dict[str, Any]:
        return {"lanes": [
            {"from_warehouse_id": a, "to_warehouse_id": b, "transit_days": _rng("lane", a, b).choice([1, 2, 3, 4])}
            for a, b in self.LANES
        ]}

    def transfer_stock(self, sku: str, from_warehouse_id: str, to_warehouse_id: str, quantity: int) -> dict[str, Any]:
        if (from_warehouse_id, to_warehouse_id) not in self.LANES:
            raise ToolError(f"No shipping lane from {from_warehouse_id} to {to_warehouse_id}.")
        return super().transfer_stock(sku, from_warehouse_id, to_warehouse_id, quantity)


def grade_lanes(backend: InventoryBackend) -> Grade:
    return _grade_on_hand(backend, {("WID-204", "DAL"): -18, ("WID-204", "LAX"): -12, ("WID-204", "SEA"): 30})


def reference_lanes(call: CallFn) -> None:
    call("list_warehouses")
    lanes = call("list_lanes")["lanes"]
    rows = {r["warehouse_id"]: r for r in call("get_sku_stock", sku="WID-204")["warehouses"]}
    need = 40 - _avail(rows["SEA"])
    into_sea = [lane["from_warehouse_id"] for lane in lanes if lane["to_warehouse_id"] == "SEA"]
    direct = [w for w in into_sea if w in rows and _surplus(rows[w]) >= need]
    if direct:
        call("transfer_stock", sku="WID-204", from_warehouse_id=direct[0], to_warehouse_id="SEA", quantity=need)
        return
    for mid in into_sea:
        lack = need - max(0, _surplus(rows[mid])) if mid in rows else need
        upstream = [lane["from_warehouse_id"] for lane in lanes if lane["to_warehouse_id"] == mid]
        source = next((w for w in upstream if w in rows and _surplus(rows[w]) >= lack), None)
        if source is None:
            continue
        call("transfer_stock", sku="WID-204", from_warehouse_id=source, to_warehouse_id=mid, quantity=lack)
        call("transfer_stock", sku="WID-204", from_warehouse_id=mid, to_warehouse_id="SEA", quantity=need)
        return
    raise AssertionError("no two-hop route covers the shortfall")


_LANES_TOOL = ToolSpec(
    name="list_lanes",
    description="List the shipping lanes stock can move along, each as a source warehouse and a destination warehouse.",
    paraphrase="Return every shipping lane that stock may travel on, given as a source warehouse and a destination warehouse.",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)

_LANE_TRANSFER_TOOL = ToolSpec(
    name="transfer_stock",
    description="Move on-hand units of a SKU from one warehouse to another along a shipping lane. Reserved units cannot be moved.",
    paraphrase="Transfer on-hand units of a SKU between two warehouses joined by a shipping lane. Units that are reserved can't be moved.",
    input_schema=_TRANSFER_TOOL.input_schema,
)

HARD_TASKS = [
    Task(
        task_id="inventory_h1_region_first",
        domain="inventory",
        instruction=(
            "Warehouses BOS and SEA must each end with exactly 40 available units of SKU WID-204. Top up BOS "
            "first and then SEA, each with a single transfer from one other warehouse (not BOS or SEA). Choose "
            "the source like this: if a warehouse in the same region as the destination can supply the whole "
            "shortfall without breaking the rules below, use it; otherwise use the warehouse, in any region, "
            "that has the largest surplus (available units minus safety stock) at the time of that transfer. "
            + _RULES
        ),
        tools=(_LIST_TOOL, _SKU_STOCK_TOOL, _GET_TOOL, _TRANSFER_TOOL),
        make_backend=RegionBackend,
        grade=grade_region,
        reference_solution=reference_region,
    ),
    Task(
        task_id="inventory_h2_lane_routing",
        domain="inventory",
        instruction=(
            "Warehouse SEA needs exactly 40 available units of SKU WID-204, delivered in a single transfer into "
            "SEA. Stock can only move along the shipping lanes in the lane table, in the direction shown. If no "
            "warehouse with a lane into SEA can supply the whole shortfall on its own, route the stock through "
            "one that has such a lane: that intermediate warehouse sends SEA the whole shortfall, drawing on its "
            "own surplus (available units minus safety stock) first and taking in, from a warehouse with a lane "
            "into it, only the units it would otherwise lack. " + _RULES
        ),
        tools=(_LIST_TOOL, _LANES_TOOL, _SKU_STOCK_TOOL, _GET_TOOL, _LANE_TRANSFER_TOOL),
        make_backend=LaneBackend,
        grade=grade_lanes,
        reference_solution=reference_lanes,
    ),
]
