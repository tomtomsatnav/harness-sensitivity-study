"""Presentation conditions R0-R5, matching the paper's readout study.

Every condition leaves the semantic actions, the execution and the optimal policy unchanged.
`render` turns a canonical observation plus the ordered action list into the user-message text
and the decoder (code -> index into the action list) the harness uses to execute the reply.

- R0_canonical: fields in canonical order; actions as {code: action} with codes A, B, C, ...
- R1_reversed_fields: top-level observation fields reversed; contents and the nested action
  dictionary unchanged
- R2_menu_list: the action dictionary rendered as a list of {"code", "action"} records
- R3_shifted_codes: code assignment shifted cyclically by one position
- R4_reversed_codes: code assignment reversed
- R5_permuted_codes: a fixed seeded random permutation of the codes for each menu size

For R3-R5 the order of actions in the menu and their full arguments are unchanged; only which
code labels which action changes.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass
from typing import Any, Callable

ALPHABET = string.ascii_uppercase + string.ascii_lowercase  # A-Z then a-z, case-sensitive

CodeMap = Callable[[int], list[str]]  # menu size -> code for each action position


def _identity(m: int) -> list[str]:
    return list(ALPHABET[:m])


def _shifted(m: int) -> list[str]:
    return [ALPHABET[(i + 1) % m] for i in range(m)]


def _reversed(m: int) -> list[str]:
    return list(ALPHABET[:m][::-1])


def _permuted(m: int) -> list[str]:
    # One fixed permutation per menu size, the same in every episode and run. The seed is
    # chosen so that for menu sizes 3-6 the permutation differs from R0, R3 and R4
    # (tests/test_readout.py checks this); size 2 has only two permutations.
    perm = list(range(m))
    random.Random(f"R5_permuted_codes:0:{m}").shuffle(perm)
    return [ALPHABET[p] for p in perm]


@dataclass(frozen=True)
class Condition:
    name: str
    codes: CodeMap = _identity
    reverse_fields: bool = False
    menu_as_list: bool = False

    def code_list(self, m: int) -> list[str]:
        codes = self.codes(m)
        assert len(set(codes)) == m, "codes must be a bijection"
        return codes

    def render(self, obs: dict[str, Any], actions: list[dict[str, Any]]) -> tuple[str, dict[str, int]]:
        codes = self.code_list(len(actions))
        if self.menu_as_list:
            menu: Any = [{"code": c, "action": a} for c, a in zip(codes, actions)]
        else:
            menu = {c: a for c, a in zip(codes, actions)}
        fields = {**obs, "actions": menu}
        if self.reverse_fields:
            fields = {k: fields[k] for k in reversed(list(fields))}
        decoder = {c: i for i, c in enumerate(codes)}
        return json.dumps(fields, indent=2), decoder


CONDITIONS: dict[str, Condition] = {c.name: c for c in [
    Condition("R0_canonical"),
    Condition("R1_reversed_fields", reverse_fields=True),
    Condition("R2_menu_list", menu_as_list=True),
    Condition("R3_shifted_codes", codes=_shifted),
    Condition("R4_reversed_codes", codes=_reversed),
    Condition("R5_permuted_codes", codes=_permuted),
]}
BASELINE = "R0_canonical"


def parse_code(reply: str) -> str | None:
    """The first non-whitespace character of the reply, or None if there is none."""
    stripped = reply.strip()
    return stripped[0] if stripped else None
