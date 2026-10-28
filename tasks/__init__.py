"""Task registry. Each domain module exposes a TASKS list and a HARD_TASKS list."""

from tasks import inventory, orders, records, scheduling, support
from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

# The 25 study tasks. Kept unchanged so results already collected stay valid.
ALL_TASKS: list[Task] = [*orders.TASKS, *inventory.TASKS, *records.TASKS, *scheduling.TASKS, *support.TASKS]
# The harder set: two per domain, selected with `--tasks hard`.
HARD_TASKS: list[Task] = [
    *orders.HARD_TASKS, *inventory.HARD_TASKS, *records.HARD_TASKS, *scheduling.HARD_TASKS, *support.HARD_TASKS,
]
TASKS_BY_ID: dict[str, Task] = {t.task_id: t for t in [*ALL_TASKS, *HARD_TASKS]}

__all__ = ["ALL_TASKS", "HARD_TASKS", "TASKS_BY_ID", "CallFn", "Grade", "Task", "ToolError", "ToolSpec"]
