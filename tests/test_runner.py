"""Agent loop tests against a scripted fake client (no API calls)."""

from dataclasses import dataclass, field
from typing import Any

from perturb import CONDITIONS
from runner.agent import run_episode
from runner.schemas import EpisodeResult
from tasks import TASKS_BY_ID

INVENTORY = TASKS_BY_ID["inventory_01_single_transfer"]


@dataclass
class Block:
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: dict | None = None

    def model_dump(self, **_):
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 20
    cache_read_input_tokens: int = 50
    cache_creation_input_tokens: int = 0


@dataclass
class Response:
    content: list[Block]
    stop_reason: str
    usage: FakeUsage = field(default_factory=FakeUsage)


def tool(name, **args):
    return Response([Block("tool_use", id=f"tu_{name}", name=name, input=args)], "tool_use")


DONE = Response([Block("text", text="Done.")], "end_turn")


class FakeClient:
    """Plays back a fixed list of responses and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0) if self.responses else self.responses_default()

    def responses_default(self):
        return tool("list_warehouses")


SOLVE = [
    tool("list_warehouses"),
    tool("get_stock", sku="WID-204", warehouse_id="DAL"),
    tool("transfer_stock", sku="WID-204", from_warehouse_id="DAL", to_warehouse_id="BOS", quantity=28),
    DONE,
]


def test_successful_episode():
    client = FakeClient(SOLVE)
    r = run_episode(client, "fake-model", INVENTORY, CONDITIONS["baseline"], 0)
    assert r.status == "completed" and r.success and r.state_correct
    assert r.turns == 4 and r.tool_calls == 3 and r.invalid_tool_calls == 0
    assert r.usage.input_tokens == 400 and r.usage.cache_read_input_tokens == 200
    EpisodeResult.model_validate_json(r.model_dump_json())


def test_request_shape():
    client = FakeClient(SOLVE)
    run_episode(client, "fake-model", INVENTORY, CONDITIONS["tool_order_reverse"], 0, effort="low")
    req = client.requests[0]
    assert [t["name"] for t in req["tools"]] == ["transfer_stock", "get_stock", "list_warehouses"]
    assert req["cache_control"] == {"type": "ephemeral"}
    assert req["output_config"] == {"effort": "low"}
    assert "temperature" not in req
    # Second request carries the assistant turn and a user turn of tool results.
    second = client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[2]["content"][0]["type"] == "tool_result"


def test_observations_use_condition_format():
    client = FakeClient(SOLVE)
    run_episode(client, "fake-model", INVENTORY, CONDITIONS["format_xml"], 0)
    result_text = client.requests[1]["messages"][2]["content"][0]["content"]
    assert result_text.startswith("<result>")


def test_turn_cap_is_failure():
    client = FakeClient([])  # calls list_warehouses forever
    r = run_episode(client, "fake-model", INVENTORY, CONDITIONS["baseline"], 0, max_turns=15)
    assert r.status == "turn_cap" and r.success is False
    assert r.turns == 15


def test_correct_state_but_capped_is_failure():
    client = FakeClient(SOLVE[:3])  # solves, then keeps calling tools
    r = run_episode(client, "fake-model", INVENTORY, CONDITIONS["baseline"], 0, max_turns=5)
    assert r.status == "turn_cap" and r.state_correct and r.success is False


def test_invalid_tool_call_recorded_and_returned_as_error():
    client = FakeClient([tool("teleport_stock", sku="WID-204"), DONE])
    r = run_episode(client, "fake-model", INVENTORY, CONDITIONS["baseline"], 0)
    assert r.invalid_tool_calls == 1 and r.success is False
    assert client.requests[1]["messages"][2]["content"][0]["is_error"] is True


def test_refusal():
    client = FakeClient([Response([], "refusal")])
    r = run_episode(client, "fake-model", INVENTORY, CONDITIONS["baseline"], 0)
    assert r.status == "refusal" and r.success is False
