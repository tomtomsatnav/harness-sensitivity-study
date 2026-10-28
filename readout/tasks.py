"""Three MiniWoB++-style task families as deterministic simulations, plus seeded instances.

These are original reimplementations of the task ideas, not copies of benchmark code.

A simulation exposes the *semantic* side of the interface: the canonical observation fields
(instruction, page, controller) and the ordered list of available actions as plain dicts.
Presentation (field order, code labels) is the job of `readout.conditions`.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

MAX_DECISIONS = 10
INSTANCES_PER_FAMILY = 40

CHECKBOXES = "click-checkboxes"
CHECKBOXES_SOFT = "click-checkboxes-soft"
ASCENDING = "ascending-numbers"
FAMILIES = (CHECKBOXES, CHECKBOXES_SOFT, ASCENDING)

# Fixed synonym vocabulary for click-checkboxes-soft: label -> the word used in the instruction.
# Labels and synonyms are disjoint, and each synonym has exactly one sensible match among
# the labels (tests/test_readout.py checks the disjointness and uniqueness).
SYNONYMS: dict[str, str] = {
    "car": "automobile", "house": "home", "big": "large", "happy": "glad", "quick": "fast",
    "cold": "chilly", "dog": "hound", "street": "road", "shop": "store", "start": "begin",
    "talk": "speak", "smart": "clever", "small": "tiny", "sofa": "couch", "bag": "sack",
    "stone": "rock", "ocean": "sea", "forest": "woods", "cup": "mug", "gift": "present",
    "error": "mistake", "photo": "picture", "child": "kid", "money": "cash",
}
LABELS = tuple(SYNONYMS)
SYNONYMS_INVERSE = {v: k for k, v in SYNONYMS.items()}

SUBMIT = {"click": "button", "label": "Submit"}


def _join(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


@dataclass(frozen=True)
class Instance:
    family: str
    task_id: str
    seed: int
    # click-checkboxes(-soft): labels in page order and the labels that must be selected.
    labels: tuple[str, ...] = ()
    targets: frozenset[str] = frozenset()
    # ascending-numbers: tiles in page (reading) order as (id, x, y, number).
    tiles: tuple[tuple[str, int, int, int], ...] = ()

    @property
    def instruction(self) -> str:
        if self.family == ASCENDING:
            return "Click the tiles in ascending numeric order, lowest number first."
        wanted = [l for l in self.labels if l in self.targets]
        if self.family == CHECKBOXES:
            return "Select " + _join(['"%s"' % l for l in wanted]) + " and click Submit."
        return "Select words similar to " + _join(['"%s"' % SYNONYMS[l] for l in wanted]) + " and click Submit."


# ---------------------------------------------------------------------------
# Simulations


class CheckboxesSim:
    def __init__(self, inst: Instance) -> None:
        self.inst = inst
        self.checked: set[str] = set()
        self.clicks = 0
        self.done = False

    @property
    def instruction(self) -> str:
        return self.inst.instruction

    def page(self) -> dict[str, Any]:
        return {
            "checkboxes": [{"id": f"c{i + 1}", "label": l, "checked": l in self.checked}
                           for i, l in enumerate(self.inst.labels)],
            "buttons": ["Submit"],
        }

    def controller(self) -> dict[str, Any]:
        return {"clicks": self.clicks}

    def actions(self) -> list[dict[str, Any]]:
        return [{"click": "checkbox", "id": f"c{i + 1}", "label": l} for i, l in enumerate(self.inst.labels)] + [SUBMIT]

    def step(self, action: dict[str, Any]) -> None:
        assert action in self.actions(), action
        self.clicks += 1
        if action == SUBMIT:
            self.done = True
        else:
            self.checked ^= {action["label"]}

    def success(self) -> bool:
        return self.done and self.checked == set(self.inst.targets)

    def detail(self) -> str:
        return f"submitted={self.done} checked={sorted(self.checked)} target={sorted(self.inst.targets)}"


class AscendingSim:
    def __init__(self, inst: Instance) -> None:
        self.inst = inst
        self.order = sorted(inst.tiles, key=lambda t: t[3])
        self.clicked: list[str] = []
        self.failed = False
        self.done = False

    @property
    def instruction(self) -> str:
        return self.inst.instruction

    def _remaining(self) -> list[tuple[str, int, int, int]]:
        return [t for t in self.inst.tiles if t[0] not in self.clicked]

    def page(self) -> dict[str, Any]:
        # Numbers are visible only before the first click.
        if not self.clicked:
            return {"tiles": [{"id": i, "x": x, "y": y, "number": n} for i, x, y, n in self.inst.tiles]}
        return {"tiles": [{"id": i, "x": x, "y": y} for i, x, y, _ in self._remaining()]}

    def controller(self) -> dict[str, Any]:
        return {"completed_clicks": len(self.clicked)}

    def actions(self) -> list[dict[str, Any]]:
        return [{"click": "tile", "id": t[0]} for t in self._remaining()]

    def step(self, action: dict[str, Any]) -> None:
        assert action in self.actions(), action
        expected = self.order[len(self.clicked)][0]
        if action["id"] == expected:
            self.clicked.append(action["id"])
            self.done = len(self.clicked) == len(self.inst.tiles)
        else:
            self.failed = self.done = True  # a wrong click ends the episode, as in MiniWoB++

    def success(self) -> bool:
        return self.done and not self.failed

    def detail(self) -> str:
        return f"clicked={self.clicked} failed={self.failed} order={[t[0] for t in self.order]}"


Sim = CheckboxesSim | AscendingSim


def make_sim(inst: Instance) -> Sim:
    return AscendingSim(inst) if inst.family == ASCENDING else CheckboxesSim(inst)


def observation(sim: Sim, decisions_remaining: int) -> dict[str, Any]:
    """The canonical observation, without the action menu (which the condition renders)."""
    return {
        "instruction": sim.instruction,
        "page": sim.page(),
        "decisions_remaining": decisions_remaining,
        "controller": sim.controller(),
    }


# ---------------------------------------------------------------------------
# Instance generation


def _checkbox_instance(family: str, i: int) -> Instance:
    rng = random.Random(f"{family}:{i}")
    n = rng.choice([2, 3, 4])  # fewer than five boxes
    labels = tuple(rng.sample(LABELS, n))
    k = rng.randint(1, n)
    targets = frozenset(rng.sample(labels, k))
    prefix = "soft" if family == CHECKBOXES_SOFT else "checkboxes"
    return Instance(family=family, task_id=f"{prefix}_{i:02d}", seed=i, labels=labels, targets=targets)


def _ascending_instance(i: int) -> Instance:
    rng = random.Random(f"{ASCENDING}:{i}")
    while True:
        cells = rng.sample([(r, c) for r in range(3) for c in range(3)], 5)
        cells.sort()  # reading order: top to bottom, then left to right
        numbers = rng.sample(range(1, 21), 5)
        # The first required number lies in one of the first three spatial positions.
        if numbers.index(min(numbers)) < 3:
            break
    tiles = tuple((f"t{j + 1}", 20 + c * 50, 20 + r * 50, n) for j, ((r, c), n) in enumerate(zip(cells, numbers)))
    return Instance(family=ASCENDING, task_id=f"ascending_{i:02d}", seed=i, tiles=tiles)


def make_instances(family: str, n: int = INSTANCES_PER_FAMILY) -> list[Instance]:
    if family == ASCENDING:
        return [_ascending_instance(i) for i in range(n)]
    return [_checkbox_instance(family, i) for i in range(n)]


ALL_INSTANCES: list[Instance] = [inst for fam in FAMILIES for inst in make_instances(fam)]
INSTANCES_BY_ID: dict[str, Instance] = {inst.task_id: inst for inst in ALL_INSTANCES}


# ---------------------------------------------------------------------------
# Scripted optimal solver. It reads only canonical observations (never codes) and returns a
# semantic action, so the guard tests can show that every condition is solvable.


class Solver:
    def __init__(self, family: str) -> None:
        self.family = family
        self.number_of: dict[str, int] = {}  # ascending-numbers: remembered from the first observation

    def act(self, obs: dict[str, Any]) -> dict[str, Any]:
        page = obs["page"]
        if self.family == ASCENDING:
            if obs["controller"]["completed_clicks"] == 0:
                self.number_of = {t["id"]: t["number"] for t in page["tiles"]}
            remaining = [t["id"] for t in page["tiles"]]
            return {"click": "tile", "id": min(remaining, key=self.number_of.__getitem__)}
        words = re.findall(r'"([^"]+)"', obs["instruction"])
        wanted = {SYNONYMS_INVERSE[w] for w in words} if self.family == CHECKBOXES_SOFT else set(words)
        for box in page["checkboxes"]:
            if (box["label"] in wanted) != box["checked"]:
                return {"click": "checkbox", "id": box["id"], "label": box["label"]}
        return dict(SUBMIT)
