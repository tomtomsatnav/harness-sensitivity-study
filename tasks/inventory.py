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
    def __init__(self) -> None:
        self.warehouses = copy.deepcopy(_WAREHOUSES)
        self.stock = copy.deepcopy(_STOCK)
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
