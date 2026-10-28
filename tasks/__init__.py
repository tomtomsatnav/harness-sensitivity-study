"""Task registry. Each domain module exposes a TASKS list."""

from tasks import inventory, orders, records
from tasks.base import CallFn, Grade, Task, ToolError, ToolSpec

ALL_TASKS: list[Task] = [*orders.TASKS, *records.TASKS, *inventory.TASKS]
TASKS_BY_ID: dict[str, Task] = {t.task_id: t for t in ALL_TASKS}

__all__ = ["ALL_TASKS", "TASKS_BY_ID", "CallFn", "Grade", "Task", "ToolError", "ToolSpec"]
