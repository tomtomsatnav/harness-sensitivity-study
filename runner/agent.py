"""The agent loop: one episode of one model on one task under one condition."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from perturb import Condition
from runner.env import Environment
from runner.schemas import EpisodeResult, Usage
from tasks import Task

# Identical in every condition. It deliberately says nothing about observation format.
SYSTEM_PROMPT = (
    "You are an operations assistant with access to a company's internal systems through tools. "
    "Carry out the user's request by calling the tools. The user is not available for follow-up "
    "questions, so act on the information you have. When the request is complete, reply with a "
    "short summary and stop calling tools."
)

MAX_TURNS = 15
MAX_TOKENS = 16000


def _dump(block: Any) -> dict[str, Any]:
    return block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump") else dict(block)


def _add_usage(total: Usage, usage: Any) -> None:
    for field in Usage.model_fields:
        setattr(total, field, getattr(total, field) + (getattr(usage, field, 0) or 0))


def run_episode(
    client: anthropic.Anthropic,
    model: str,
    task: Task,
    condition: Condition,
    run_idx: int,
    *,
    effort: str | None = None,
    max_turns: int = MAX_TURNS,
) -> EpisodeResult:
    env = Environment(task, condition)
    messages: list[dict[str, Any]] = [{"role": "user", "content": task.instruction}]
    transcript: list[dict[str, Any]] = [{"role": "user", "content": task.instruction}]
    usage = Usage()
    turns = tool_calls = 0
    status = "turn_cap"
    error = None
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    request: dict[str, Any] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "tools": env.tools,
        # Automatic caching: the breakpoint follows the end of the conversation, so each turn
        # reads the tools, system prompt and earlier turns from cache.
        "cache_control": {"type": "ephemeral"},
    }
    if effort:
        request["output_config"] = {"effort": effort}

    try:
        while turns < max_turns:
            response = client.messages.create(messages=messages, **request)
            turns += 1
            _add_usage(usage, response.usage)
            # Pass the content back unchanged (thinking blocks included).
            messages.append({"role": "assistant", "content": response.content})
            transcript.append({"role": "assistant", "stop_reason": response.stop_reason,
                               "content": [_dump(b) for b in response.content]})

            if response.stop_reason == "refusal":
                status = "refusal"
                break
            if response.stop_reason == "max_tokens":
                status = "max_tokens"
                break
            if response.stop_reason not in ("end_turn", "tool_use"):
                status = "other_stop"
                error = f"stop_reason={response.stop_reason}"
                break
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                status = "completed"
                break

            results = []
            for block in tool_uses:
                tool_calls += 1
                text, is_error = env.call(block.name, block.input)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            # All results for one assistant turn go back in a single user message.
            messages.append({"role": "user", "content": results})
            transcript.append({"role": "user", "content": results})
    except anthropic.APIStatusError as e:
        # A 4xx other than 429 (bad request, bad key, unknown model) is a setup or harness bug,
        # not a data point. Stop the run. Overloads and 5xx fall through to api_error.
        if e.status_code < 500 and e.status_code != 429:
            raise
        status = "api_error"
        error = f"{type(e).__name__}: {e}"
    except anthropic.APIError as e:
        status = "api_error"
        error = f"{type(e).__name__}: {e}"

    grade = env.grade()
    return EpisodeResult(
        episode_id=f"{model}|{task.task_id}|{condition.name}|{run_idx}",
        model=model,
        task_id=task.task_id,
        domain=task.domain,
        condition=condition.name,
        run_idx=run_idx,
        effort=effort,
        max_turns=max_turns,
        status=status,
        success=None if status == "api_error" else (status == "completed" and grade.success),
        state_correct=None if status == "api_error" else grade.success,
        grade_detail=grade.detail,
        turns=turns,
        tool_calls=tool_calls,
        invalid_tool_calls=env.invalid_tool_calls,
        tool_errors=env.tool_errors,
        usage=usage,
        final_state=env.backend.snapshot(),
        error=error,
        started_at=started.isoformat(),
        duration_s=round(time.monotonic() - t0, 3),
        transcript=transcript,
    )
