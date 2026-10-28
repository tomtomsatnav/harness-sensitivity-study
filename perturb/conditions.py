"""Perturbation conditions.

Each condition changes presentation only. A condition is two independent parts:

- a tool transform: (tool specs) -> tool definitions in Messages API form
- an observation codec: encode(data) -> text shown to the agent, and decode(text) -> data

`decode` exists for the guard tests: it lets the reference solution read perturbed
observations and lets us check that encode/decode round-trips, i.e. that no information
was lost. The agent never sees it.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import yaml

from perturb.synonyms import SYNONYMS, SYNONYMS_INVERSE
from tasks.base import ToolSpec

Data = Any

# ---------------------------------------------------------------------------
# Generic helpers


def _stable_seed(*parts: str) -> int:
    return int.from_bytes(hashlib.sha256("\x1f".join(parts).encode()).digest()[:8], "big")


def map_keys(data: Data, fn: Callable[[str], str]) -> Data:
    if isinstance(data, dict):
        out = {fn(k): map_keys(v, fn) for k, v in data.items()}
        if len(out) != len(data):
            raise ValueError(f"key mapping collided on keys {list(data)}")
        return out
    if isinstance(data, list):
        return [map_keys(v, fn) for v in data]
    return data


def reorder_keys(data: Data, order: Callable[[list[str]], list[str]]) -> Data:
    """Reorder dict keys at every level. List element order is left alone: it can carry meaning."""
    if isinstance(data, dict):
        return {k: reorder_keys(data[k], order) for k in order(list(data))}
    if isinstance(data, list):
        return [reorder_keys(v, order) for v in data]
    return data


def _reverse(keys: list[str]) -> list[str]:
    return keys[::-1]


def _seeded_shuffle(keys: list[str]) -> list[str]:
    # Seeded by the key set, so the same object always gets the same order in every run.
    shuffled = list(keys)
    random.Random(_stable_seed("field_order", *sorted(keys))).shuffle(shuffled)
    return shuffled


def snake_to_camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(p.capitalize() for p in rest)


def camel_to_snake(key: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()


# ---------------------------------------------------------------------------
# Serialisations


def json_dumps(data: Data) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def yaml_dumps(data: Data) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip("\n")


def _xml_scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _xml_build(parent: ET.Element, data: Data) -> None:
    if isinstance(data, dict):
        for k, v in data.items():
            _xml_build(ET.SubElement(parent, k), v)
    elif isinstance(data, list):
        for v in data:
            _xml_build(ET.SubElement(parent, "item"), v)
    else:
        parent.text = _xml_scalar(data)


def xml_dumps(data: Data) -> str:
    root = ET.Element("result")
    _xml_build(root, data)
    ET.indent(root, "  ")
    return ET.tostring(root, encoding="unicode")


_INT = re.compile(r"-?(0|[1-9]\d*)")
_FLOAT = re.compile(r"-?(0|[1-9]\d*)\.\d+")


def _xml_parse_scalar(text: str) -> Any:
    if text == "null":
        return None
    if text in ("true", "false"):
        return text == "true"
    if _INT.fullmatch(text):
        return int(text)
    if _FLOAT.fullmatch(text):
        return float(text)
    return text


def _xml_parse(el: ET.Element) -> Data:
    children = list(el)
    if not children:
        text = (el.text or "").strip()
        # An element with no children and no text was an empty list.
        return [] if text == "" else _xml_parse_scalar(text)
    if all(c.tag == "item" for c in children):
        return [_xml_parse(c) for c in children]
    return {c.tag: _xml_parse(c) for c in children}


def xml_loads(text: str) -> Data:
    return _xml_parse(ET.fromstring(text))


# ---------------------------------------------------------------------------
# Conditions


def default_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [{"name": s.name, "description": s.description, "input_schema": s.input_schema} for s in specs]


@dataclass(frozen=True)
class Condition:
    name: str
    applies_to: str  # "none" | "observations" | "tools" | "both"
    summary: str
    tools_fn: Callable[[Sequence[ToolSpec]], list[dict[str, Any]]] = default_tools
    to_wire: Callable[[Data], Data] = lambda d: d  # key renaming / reordering
    from_wire: Callable[[Data], Data] = lambda d: d
    dumps: Callable[[Data], str] = json_dumps
    loads: Callable[[str], Data] = json.loads

    def tools(self, specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
        return self.tools_fn(specs)

    def encode(self, data: Data) -> str:
        return self.dumps(self.to_wire(data))

    def decode(self, text: str) -> Data:
        return self.from_wire(self.loads(text))

    def apply(self, specs: Sequence[ToolSpec], observation: Data) -> tuple[list[dict[str, Any]], str]:
        """The spec's `(tools, observation) -> (tools, observation)` view of a condition."""
        return self.tools(specs), self.encode(observation)


def _reversed_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return default_tools(list(specs)[::-1])


def _shuffled_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    specs = list(specs)
    random.Random(_stable_seed("tool_order", *sorted(s.name for s in specs))).shuffle(specs)
    return default_tools(specs)


def _paraphrased_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [{"name": s.name, "description": s.paraphrase, "input_schema": s.input_schema} for s in specs]


def _stacked_tools(specs: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return _paraphrased_tools(list(specs)[::-1])


def _synonym_keys(data: Data) -> Data:
    return map_keys(data, lambda k: SYNONYMS.get(k, k))


def _original_keys(data: Data) -> Data:
    return map_keys(data, lambda k: SYNONYMS_INVERSE.get(k, k))


CONDITIONS: dict[str, Condition] = {
    c.name: c
    for c in [
        Condition("baseline", "none", "Natural field order, snake_case keys, JSON, tools in logical order"),
        Condition(
            "field_order_reverse", "observations", "JSON keys reversed at every level",
            to_wire=lambda d: reorder_keys(d, _reverse),
        ),
        Condition(
            "field_order_shuffle", "observations", "JSON keys shuffled at every level (seeded)",
            to_wire=lambda d: reorder_keys(d, _seeded_shuffle),
        ),
        Condition(
            "key_naming_camel", "observations", "snake_case keys rewritten as camelCase",
            to_wire=lambda d: map_keys(d, snake_to_camel),
            from_wire=lambda d: map_keys(d, camel_to_snake),
        ),
        Condition(
            "key_naming_synonym", "observations", "Keys replaced by neutral synonyms (qty for quantity)",
            to_wire=_synonym_keys,
            from_wire=_original_keys,
        ),
        Condition("format_yaml", "observations", "Observations as YAML", dumps=yaml_dumps, loads=yaml.safe_load),
        Condition("format_xml", "observations", "Observations as XML", dumps=xml_dumps, loads=xml_loads),
        Condition("tool_order_reverse", "tools", "Tool list reversed", tools_fn=_reversed_tools),
        Condition("tool_order_shuffle", "tools", "Tool list shuffled (seeded)", tools_fn=_shuffled_tools),
        Condition(
            "description_paraphrase", "tools", "Tool descriptions paraphrased, same meaning, length ±10%",
            tools_fn=_paraphrased_tools,
        ),
        # Worst case: the five study perturbations at once. Keys are renamed first, then the order is
        # reversed, then the result is rendered as XML; decoding only needs the inverse renaming.
        Condition(
            "stacked", "both",
            "Synonym keys, reversed field order and XML, with the tool list reversed and descriptions paraphrased",
            tools_fn=_stacked_tools,
            to_wire=lambda d: reorder_keys(_synonym_keys(d), _reverse),
            from_wire=_original_keys,
            dumps=xml_dumps,
            loads=xml_loads,
        ),
    ]
}

# The six conditions in the main grid: baseline plus one variant per perturbation family.
# The other variants stay available for follow-ups and are covered by the same guard tests.
STUDY_CONDITIONS: list[str] = [
    "baseline",
    "field_order_reverse",
    "key_naming_synonym",
    "format_xml",
    "tool_order_reverse",
    "description_paraphrase",
]
