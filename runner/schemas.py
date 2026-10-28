"""Episode result schema. One EpisodeResult is one line of the results JSONL."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# completed: the model ended its turn on its own.  turn_cap: still calling tools at the cap.
# refusal / max_tokens: the API stop reason.  api_error: the request failed after retries;
# these episodes are excluded from analysis and re-run on resume.
Status = Literal["completed", "turn_cap", "refusal", "max_tokens", "api_error"]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class EpisodeResult(BaseModel):
    episode_id: str
    model: str
    task_id: str
    domain: str
    condition: str
    run_idx: int
    effort: str | None = None

    status: Status
    success: bool | None  # None only for api_error
    state_correct: bool | None  # grader verdict on the final state, regardless of status
    grade_detail: str = ""

    turns: int  # model calls made
    tool_calls: int
    invalid_tool_calls: int
    tool_errors: int
    usage: Usage
    final_state: dict[str, Any] = {}
    error: str | None = None

    started_at: str
    duration_s: float
    transcript: list[dict[str, Any]] = []

    @property
    def key(self) -> tuple[str, str, str, int]:
        return (self.model, self.task_id, self.condition, self.run_idx)
