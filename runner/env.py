"""One episode's environment: a fresh backend, the condition's tool list, and tool dispatch."""

from __future__ import annotations

from typing import Any

from perturb import Condition
from tasks import Task, ToolError

_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_args(schema: dict[str, Any], args: Any) -> list[str]:
    """Check tool input against the flat object schemas tasks use. Returns a list of problems."""
    if not isinstance(args, dict):
        return ["input must be an object"]
    props = schema.get("properties", {})
    problems = [f"missing required field {k!r}" for k in schema.get("required", []) if k not in args]
    if schema.get("additionalProperties") is False:
        problems += [f"unexpected field {k!r}" for k in args if k not in props]
    for k, v in args.items():
        prop = props.get(k)
        if prop is None:
            continue
        expected = _JSON_TYPES[prop["type"]]
        # bool is a subclass of int in Python; JSON keeps them apart.
        if not isinstance(v, expected) or (isinstance(v, bool) and prop["type"] != "boolean"):
            problems.append(f"field {k!r} must be of type {prop['type']}")
        elif "enum" in prop and v not in prop["enum"]:
            problems.append(f"field {k!r} must be one of {prop['enum']}")
    return problems


class Environment:
    def __init__(self, task: Task, condition: Condition) -> None:
        self.task = task
        self.condition = condition
        self.backend = task.make_backend()
        self.tools = condition.tools(task.tools)
        self._specs = {s.name: s for s in task.tools}
        self.invalid_tool_calls = 0  # unknown tool or input that fails the schema
        self.tool_errors = 0  # valid input the backend refused

    def call(self, name: str, args: Any) -> tuple[str, bool]:
        """Run one tool call. Returns (observation text, is_error)."""
        spec = self._specs.get(name)
        if spec is None:
            self.invalid_tool_calls += 1
            return self.condition.encode({"error": f"Unknown tool {name!r}."}), True
        problems = validate_args(spec.input_schema, args)
        if problems:
            self.invalid_tool_calls += 1
            return self.condition.encode({"error": "Invalid input: " + "; ".join(problems) + "."}), True
        try:
            result = getattr(self.backend, name)(**args)
        except ToolError as e:
            self.tool_errors += 1
            return self.condition.encode({"error": str(e)}), True
        return self.condition.encode(result), False

    def grade(self):
        return self.task.grade(self.backend)


def run_reference(task: Task, condition: Condition) -> tuple[Environment, list[tuple[str, Any, Any]]]:
    """Run the task's reference solution through the perturbed interface.

    The solution only ever sees decoded observations, so a perturbation that loses or
    corrupts information makes it fail. Returns the environment and a log of
    (tool name, args, original observation) for each call.
    """
    env = Environment(task, condition)
    log: list[tuple[str, Any, Any]] = []

    def call(name: str, **args: Any) -> Any:
        text, is_error = env.call(name, args)
        decoded = condition.decode(text)
        if is_error:
            raise AssertionError(f"reference call {name}({args}) failed under {condition.name}: {decoded}")
        log.append((name, args, decoded))
        return decoded

    task.reference_solution(call)
    return env, log
