"""Core task abstractions shared by every domain module.

A task bundles a seeded mock backend, the tools that expose it, a deterministic
grader over the backend's final state, and a reference solution. Backends return
plain Python data (dicts, lists, scalars); how that data is presented to the
agent is decided by a perturbation condition, never by the task.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from pydantic import BaseModel, ConfigDict, model_validator


class ToolError(Exception):
    """A request the backend refuses. The message is returned to the agent as an error result."""


class ToolSpec(BaseModel):
    """One tool offered to the agent.

    `paraphrase` is a rewording of `description` with the same meaning and a length
    within ±10%; the description-wording condition swaps it in.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    paraphrase: str
    input_schema: dict[str, Any]

    @model_validator(mode="after")
    def _check_paraphrase(self) -> "ToolSpec":
        ratio = len(self.paraphrase) / len(self.description)
        if not 0.9 <= ratio <= 1.1:
            raise ValueError(f"{self.name}: paraphrase length ratio {ratio:.2f} is outside ±10%")
        if self.paraphrase == self.description:
            raise ValueError(f"{self.name}: paraphrase is identical to the description")
        return self


class Grade(BaseModel):
    success: bool
    detail: str


class Backend(Protocol):
    """Each tool name is a method on the backend taking the tool's input as keyword args."""

    def snapshot(self) -> dict[str, Any]:
        """The full mutable state, for logging and grading."""
        ...


# `call(tool_name, **args)` returns the decoded observation, or raises on an error result.
CallFn = Callable[..., Any]


@dataclass(frozen=True)
class Task:
    task_id: str
    domain: str
    instruction: str
    tools: tuple[ToolSpec, ...]
    make_backend: Callable[[], Backend]
    grade: Callable[[Backend], Grade]
    reference_solution: Callable[[CallFn], None]

    def __post_init__(self) -> None:
        if not 3 <= len(self.tools) <= 6:
            raise ValueError(f"{self.task_id}: tasks must offer 3 to 6 tools, got {len(self.tools)}")
        names = [t.name for t in self.tools]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.task_id}: duplicate tool names")
