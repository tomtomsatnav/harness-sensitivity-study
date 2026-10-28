"""One readout episode: one model on one instance under one condition.

Per decision the model gets the system message (task + harness rules, identical across
conditions) and the conversation so far, ending in a user message with the JSON observation.
It replies with a single code; the first non-whitespace character is parsed, decoded and
executed. The full multi-turn history is kept, like a normal chat.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import anthropic

from readout.conditions import Condition, parse_code
from readout.ollama import OllamaError
from readout.tasks import ASCENDING, CHECKBOXES, CHECKBOXES_SOFT, MAX_DECISIONS, Instance, make_sim, observation
from runner.schemas import EpisodeResult, Usage

MAX_TOKENS = 5
# With --constrained, each reply must be JSON {"code": <one of the listed codes>}, enforced by
# structured outputs. This stands in for the paper's readout, which normalises the next-token
# logits over the legal menu codes, so the model cannot produce an invalid action.
CONSTRAINED_MAX_TOKENS = 20


def code_schema(codes: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"code": {"type": "string", "enum": codes}},
        "required": ["code"],
        "additionalProperties": False,
    }


def parse_json_code(reply: str) -> str | None:
    try:
        code = json.loads(reply).get("code")
    except (ValueError, AttributeError):
        return None
    return code if isinstance(code, str) else None

HARNESS_RULES = (
    "You are controlling a web page through a harness. Each turn you receive one observation as a "
    "JSON object. It contains the task instruction, the current page state, the number of decisions "
    "you have left, the controller state and the available actions. Each available action is "
    "labelled with a single-character code; codes are case-sensitive.\n\n"
    "Reply with the code of the action you choose and nothing else: one character, no explanation, "
    "no punctuation. A reply that is not exactly one of the listed codes is an invalid action: it "
    "does nothing and still uses up one decision. The episode ends when the task is complete, when "
    "the task fails, or when you run out of decisions. You get a reward of 1 only if the task is "
    "completed exactly as instructed, and 0 otherwise, including for partial completion."
)

TASK_RULES = {
    CHECKBOXES: (
        "Task: click-checkboxes. The page shows a few labelled checkboxes and a Submit button. "
        "Clicking a checkbox toggles it. Select exactly the checkboxes the instruction names, then "
        "click Submit. The task succeeds only if the set of selected checkboxes exactly matches the "
        "instruction when Submit is clicked."
    ),
    CHECKBOXES_SOFT: (
        "Task: click-checkboxes-soft. The page shows a few labelled checkboxes and a Submit button. "
        "Clicking a checkbox toggles it. The instruction describes each checkbox to select by a "
        "synonym of its label rather than by the label itself. Select exactly the checkboxes whose "
        "labels mean the same as the words in the instruction, then click Submit. The task succeeds "
        "only if the set of selected checkboxes exactly matches the instruction when Submit is clicked."
    ),
    ASCENDING: (
        "Task: ascending-numbers. The page shows five numbered tiles. Click them in ascending numeric "
        "order, lowest number first. The numbers are shown only in the first observation: after your "
        "first click the page shows only tile IDs and positions, so remember which tile has which "
        "number from the first observation. Clicking a tile out of order fails the task. The task "
        "succeeds when all five tiles have been clicked in ascending order."
    ),
}


def system_prompt(family: str) -> str:
    return TASK_RULES[family] + "\n\n" + HARNESS_RULES


# Thinking is off. Claude Haiku 5.5 accepts {"type": "disabled"} at its default effort; Claude
# Sonnet 5.5 rejects "disabled" and turns thinking off with "between_tools"; Claude Opus 5.5
# cannot turn thinking off at all, so it cannot run this protocol with a 5-token reply budget.
THINKING = {
    "claude-haiku-5-5": {"type": "disabled"},
    "claude-sonnet-5-5": {"type": "between_tools"},
}


class ReadoutResult(EpisodeResult):
    """An EpisodeResult (so analysis.stats / analysis.cost work unchanged) plus readout fields.

    Field mapping: domain = task family, turns = model calls = decisions used, tool_calls =
    actions executed, invalid_tool_calls = invalid_replies, status "completed" = the episode
    ended on its own (task complete or failed), "turn_cap" = decision budget exhausted.
    """

    invalid_replies: int = 0
    codes: list[str | None] = []  # parsed code per decision; None for an empty reply
    replies: list[str] = []  # raw reply text per decision


def _add_usage(total: Usage, usage: Any) -> None:
    for field in Usage.model_fields:
        setattr(total, field, getattr(total, field) + (getattr(usage, field, 0) or 0))


def run_episode(
    client: anthropic.Anthropic,
    model: str,
    inst: Instance,
    condition: Condition,
    run_idx: int,
    *,
    max_decisions: int = MAX_DECISIONS,
    constrained: bool = False,
) -> ReadoutResult:
    sim = make_sim(inst)
    messages: list[dict[str, Any]] = []
    usage = Usage()
    codes: list[str | None] = []
    replies: list[str] = []
    turns = executed = invalid = 0
    status = "turn_cap"
    error = None
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()

    request: dict[str, Any] = {
        "model": model,
        "max_tokens": CONSTRAINED_MAX_TOKENS if constrained else MAX_TOKENS,
        "system": system_prompt(inst.family),
        # Automatic caching, as in runner/agent.py: the system prompt and earlier turns are
        # read from cache when the prefix is long enough to be cacheable.
        "cache_control": {"type": "ephemeral"},
    }
    if model in THINKING:
        request["thinking"] = THINKING[model]

    try:
        while turns < max_decisions and not sim.done:
            actions = sim.actions()
            text, decoder = condition.render(observation(sim, max_decisions - turns), actions)
            messages.append({"role": "user", "content": text})
            if constrained:
                request["output_config"] = {"format": {"type": "json_schema", "schema": code_schema(sorted(decoder))}}
            response = client.messages.create(messages=messages, **request)
            turns += 1
            _add_usage(usage, response.usage)
            if response.stop_reason == "refusal":
                status = "refusal"
                break
            reply = "".join(b.text for b in response.content if b.type == "text")
            replies.append(reply)
            # The API rejects an empty assistant message, so an empty reply is kept in the
            # history as a placeholder; it is still an invalid action.
            messages.append({"role": "assistant", "content": reply if reply.strip() else "(empty reply)"})
            code = parse_json_code(reply) if constrained else parse_code(reply)
            codes.append(code)
            if code in decoder:
                sim.step(actions[decoder[code]])
                executed += 1
            else:
                invalid += 1
        else:
            if sim.done:
                status = "completed"
    except anthropic.APIStatusError as e:
        # As in runner/agent.py: a 4xx other than 429 is a harness bug, so stop the run.
        if e.status_code < 500 and e.status_code != 429:
            raise
        status = "api_error"
        error = f"{type(e).__name__}: {e}"
    except (anthropic.APIError, OllamaError) as e:
        status = "api_error"
        error = f"{type(e).__name__}: {e}"

    success = sim.success()
    return ReadoutResult(
        episode_id=f"{model}|{inst.task_id}|{condition.name}|{run_idx}",
        model=model,
        task_id=inst.task_id,
        domain=inst.family,
        condition=condition.name,
        run_idx=run_idx,
        max_turns=max_decisions,
        status=status,
        success=None if status == "api_error" else (status == "completed" and success),
        state_correct=None if status == "api_error" else success,
        grade_detail=sim.detail(),
        turns=turns,
        tool_calls=executed,
        invalid_tool_calls=invalid,
        tool_errors=0,
        invalid_replies=invalid,
        codes=codes,
        replies=replies,
        usage=usage,
        error=error,
        started_at=started.isoformat(),
        duration_s=round(time.monotonic() - t0, 3),
        transcript=messages,
    )
