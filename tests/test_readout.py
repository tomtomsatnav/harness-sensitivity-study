"""Validity tests for the readout study (no API calls).

The guard: a scripted solver that picks actions by semantic content succeeds on every instance
under every condition; every condition's code menu is a bijection over the same semantic actions
as R0; every condition's observation carries the same information as R0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from analysis import stats
from readout.conditions import ALPHABET, BASELINE, CONDITIONS, Condition, parse_code
from readout.episode import MAX_TOKENS, ReadoutResult, run_episode, system_prompt
from readout.tasks import (
    ALL_INSTANCES, ASCENDING, CHECKBOXES, CHECKBOXES_SOFT, FAMILIES, INSTANCES_BY_ID, LABELS, MAX_DECISIONS,
    SUBMIT, SYNONYMS, Instance, Solver, make_instances, make_sim, observation,
)

R0 = CONDITIONS[BASELINE]
IDS = [i.task_id for i in ALL_INSTANCES]


def menu_pairs(rendered: str) -> dict[str, dict]:
    """code -> action, read back from the rendered observation, whichever menu shape it uses."""
    menu = json.loads(rendered)["actions"]
    if isinstance(menu, list):
        return {r["code"]: r["action"] for r in menu}
    return dict(menu)


def play(inst: Instance, condition: Condition, choose) -> tuple[bool, int]:
    """Run an episode where `choose(obs, actions)` returns a semantic action; the action is
    sent as its code under `condition`, exactly as the harness would decode it."""
    sim = make_sim(inst)
    decisions = 0
    while not sim.done and decisions < MAX_DECISIONS:
        actions = sim.actions()
        rendered, decoder = condition.render(observation(sim, MAX_DECISIONS - decisions), actions)
        action = choose(json.loads(rendered), actions)
        [code] = [c for c, a in menu_pairs(rendered).items() if a == action]
        sim.step(actions[decoder[code]])
        decisions += 1
    return sim.success(), decisions


# ---------------------------------------------------------------------------
# Instances


def test_instances_are_deterministic_and_well_formed():
    assert len(ALL_INSTANCES) == 120 and len(INSTANCES_BY_ID) == 120
    for fam in FAMILIES:
        assert make_instances(fam) == [i for i in ALL_INSTANCES if i.family == fam]
    for inst in ALL_INSTANCES:
        if inst.family == ASCENDING:
            nums = [t[3] for t in inst.tiles]
            assert len(inst.tiles) == 5 and len(set(nums)) == 5
            assert nums.index(min(nums)) < 3  # first required number in the first three positions
            assert len({(x, y) for _, x, y, _ in inst.tiles}) == 5
            assert [(t[2], t[1]) for t in inst.tiles] == sorted((t[2], t[1]) for t in inst.tiles)  # reading order
        else:
            assert 2 <= len(inst.labels) <= 4 and 1 <= len(inst.targets) <= len(inst.labels)
            assert inst.targets <= set(inst.labels)


def test_synonym_vocabulary_has_one_match_per_word():
    assert set(SYNONYMS) == set(LABELS)
    assert set(SYNONYMS.values()).isdisjoint(LABELS)
    assert len(set(SYNONYMS.values())) == len(SYNONYMS)


# ---------------------------------------------------------------------------
# The guard


@pytest.mark.parametrize("cond", list(CONDITIONS))
@pytest.mark.parametrize("task_id", IDS)
def test_semantic_solver_succeeds_under_every_condition(task_id, cond):
    inst = INSTANCES_BY_ID[task_id]
    solver = Solver(inst.family)
    ok, decisions = play(inst, CONDITIONS[cond], lambda obs, actions: solver.act(obs))
    assert ok and decisions <= MAX_DECISIONS


@pytest.mark.parametrize("cond", list(CONDITIONS))
@pytest.mark.parametrize("task_id", IDS)
def test_menu_is_bijection_over_r0_actions_and_decoder_matches(task_id, cond):
    inst = INSTANCES_BY_ID[task_id]
    condition = CONDITIONS[cond]
    sim = make_sim(inst)
    solver = Solver(inst.family)
    while not sim.done:
        actions = sim.actions()
        obs = observation(sim, MAX_DECISIONS)
        r0_text, r0_decoder = R0.render(obs, actions)
        text, decoder = condition.render(obs, actions)
        pairs, r0_pairs = menu_pairs(text), menu_pairs(r0_text)
        # Bijection: distinct codes from the alphabet, one per action, over the same actions.
        assert len(pairs) == len(actions) and all(c in ALPHABET for c in pairs)
        assert sorted(pairs.values(), key=json.dumps) == sorted(r0_pairs.values(), key=json.dumps) == sorted(actions, key=json.dumps)
        # Menu order and the full arguments are unchanged; only the labels move.
        assert list(pairs.values()) == list(r0_pairs.values()) == actions
        # The decoder agrees with what the model is shown.
        assert {c: actions[i] for c, i in decoder.items()} == pairs
        sim.step(solver.act(obs))


@pytest.mark.parametrize("cond", list(CONDITIONS))
@pytest.mark.parametrize("task_id", IDS)
def test_observation_has_same_information_as_r0(task_id, cond):
    inst = INSTANCES_BY_ID[task_id]
    sim = make_sim(inst)
    solver = Solver(inst.family)
    decisions = 0
    while not sim.done:
        actions = sim.actions()
        obs = observation(sim, MAX_DECISIONS - decisions)
        r0 = json.loads(R0.render(obs, actions)[0])
        got = json.loads(CONDITIONS[cond].render(obs, actions)[0])

        def normalise(o):
            o = dict(o)
            o["actions"] = sorted(menu_pairs(json.dumps(o)).values(), key=json.dumps)
            return o

        assert set(got) == set(r0) == {"instruction", "page", "decisions_remaining", "controller", "actions"}
        assert normalise(got) == normalise(r0)
        sim.step(solver.act(obs))
        decisions += 1


def test_conditions_differ_from_r0_in_presentation():
    inst = INSTANCES_BY_ID["ascending_00"]
    obs, actions = observation(make_sim(inst), MAX_DECISIONS), make_sim(inst).actions()
    r0_text, r0_dec = R0.render(obs, actions)
    assert list(json.loads(r0_text)) == ["instruction", "page", "decisions_remaining", "controller", "actions"]
    assert list(r0_dec) == ["A", "B", "C", "D", "E"]
    assert list(json.loads(CONDITIONS["R1_reversed_fields"].render(obs, actions)[0])) == list(json.loads(r0_text))[::-1]
    assert isinstance(json.loads(CONDITIONS["R2_menu_list"].render(obs, actions)[0])["actions"], list)
    assert list(CONDITIONS["R3_shifted_codes"].render(obs, actions)[1]) == ["B", "C", "D", "E", "A"]
    assert list(CONDITIONS["R4_reversed_codes"].render(obs, actions)[1]) == ["E", "D", "C", "B", "A"]
    for m in range(3, 7):
        r5 = CONDITIONS["R5_permuted_codes"].code_list(m)
        assert r5 not in (R0.code_list(m), CONDITIONS["R3_shifted_codes"].code_list(m), CONDITIONS["R4_reversed_codes"].code_list(m))
        assert r5 == CONDITIONS["R5_permuted_codes"].code_list(m)  # fixed, not per call


def test_system_prompt_is_identical_across_conditions_and_states_rules():
    for fam in FAMILIES:
        s = system_prompt(fam)
        assert "one character" in s and "case-sensitive" in s and fam in s


# ---------------------------------------------------------------------------
# Wrong strategies the grader rejects


def test_wrong_strategies_fail():
    inst = INSTANCES_BY_ID["checkboxes_01"]  # one target among four boxes
    assert play(inst, R0, lambda obs, actions: dict(SUBMIT))[0] is False  # submit immediately
    sim = make_sim(inst)
    for a in sim.actions():  # select every box, then submit
        sim.step(a)
    assert sim.done and not sim.success()

    soft = INSTANCES_BY_ID["soft_02"]  # instruction says "automobile"; a box is labelled "car"
    sim = make_sim(soft)
    words = set(w.strip('"') for w in soft.instruction.split() if w.startswith('"'))
    for a in sim.actions():
        if a.get("label") in words:  # literal label match finds nothing
            sim.step(a)
    sim.step(SUBMIT)
    assert sim.done and not sim.success()

    asc = INSTANCES_BY_ID["ascending_00"]  # t2 holds the lowest number
    sim = make_sim(asc)
    sim.step(sim.actions()[0])  # click in reading order
    assert sim.done and not sim.success()
    sim = make_sim(asc)
    sim.step({"click": "tile", "id": "t2"})
    assert not sim.done and sim.page()["tiles"][0].keys() == {"id", "x", "y"}  # numbers hidden
    assert sim.actions() == [{"click": "tile", "id": t} for t in ["t1", "t3", "t4", "t5"]]
    sim.step({"click": "tile", "id": "t4"})  # wrong next (9, not 7)
    assert sim.done and not sim.success()


# ---------------------------------------------------------------------------
# Episode loop against a fake client


@dataclass
class Block:
    type: str = "text"
    text: str = ""


@dataclass
class FakeUsage:
    input_tokens: int = 300
    output_tokens: int = 1
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class Response:
    content: list
    stop_reason: str = "end_turn"
    usage: FakeUsage = field(default_factory=FakeUsage)


class FakeClient:
    """Replies with `reply(user_text)`; records every request."""

    def __init__(self, reply):
        self.reply = reply
        self.requests = []
        self.messages = self

    def create(self, **kw):
        self.requests.append({**kw, "messages": [dict(m) for m in kw["messages"]]})
        r = self.reply(kw["messages"][-1]["content"])
        if r is None:
            return Response([], "refusal")
        return Response([Block(text=r)], "max_tokens" if len(r) > 1 else "end_turn")


def solver_client(family: str) -> FakeClient:
    """A fake model that solves by content: parses the observation and answers the code
    listed next to the semantically right action."""
    solver = Solver(family)

    def reply(user_text):
        obs = json.loads(user_text)
        want = solver.act(obs)
        [code] = [c for c, a in menu_pairs(user_text).items() if a == want]
        return code
    return FakeClient(reply)


@pytest.mark.parametrize("cond", list(CONDITIONS))
@pytest.mark.parametrize("task_id", ["checkboxes_01", "soft_00", "ascending_00"])
def test_episode_with_content_solver_succeeds(task_id, cond):
    inst = INSTANCES_BY_ID[task_id]
    client = solver_client(inst.family)
    r = run_episode(client, "claude-haiku-5-5", inst, CONDITIONS[cond], 0)
    assert r.status == "completed" and r.success and r.state_correct
    assert r.invalid_replies == 0 and r.tool_calls == r.turns == len(r.codes)
    assert r.usage.input_tokens == 300 * r.turns
    EpisodeResult = ReadoutResult.__mro__[1]
    EpisodeResult.model_validate_json(r.model_dump_json())  # analysis-compatible


def test_request_shape_and_multiturn_history():
    inst = INSTANCES_BY_ID["ascending_00"]
    client = solver_client(ASCENDING)
    r = run_episode(client, "claude-haiku-5-5", inst, CONDITIONS["R1_reversed_fields"], 0)
    first, last = client.requests[0], client.requests[-1]
    assert first["max_tokens"] == MAX_TOKENS and first["thinking"] == {"type": "disabled"}
    assert first["cache_control"] == {"type": "ephemeral"} and "temperature" not in first
    assert first["system"] == system_prompt(ASCENDING) == last["system"]
    assert [m["role"] for m in last["messages"]] == ["user", "assistant"] * 4 + ["user"]
    assert '"number"' in last["messages"][0]["content"] and '"number"' not in last["messages"][-1]["content"]
    assert r.turns == 5 and r.codes == [parse_code(m["content"]) for m in r.transcript if m["role"] == "assistant"]


def test_wrong_tile_ends_episode_as_failure():
    inst = INSTANCES_BY_ID["ascending_00"]
    r = run_episode(FakeClient(lambda _: "A"), "claude-haiku-5-5", inst, R0, 0)  # t1 first: wrong
    assert r.status == "completed" and r.success is False and r.turns == 1


def test_garbage_and_illegal_replies_consume_decisions():
    inst = INSTANCES_BY_ID["checkboxes_00"]  # 3 actions: codes A, B, C
    replies = iter(["z", "  \n", "", "The answer is A", "a", "C"])
    client = FakeClient(lambda _: next(replies))
    r = run_episode(client, "claude-haiku-5-5", inst, R0, 0)
    assert r.codes == ["z", None, None, "T", "a", "C"]
    assert r.invalid_replies == 5 and r.tool_calls == 1 and r.turns == 6
    assert r.status == "completed" and r.success is False  # submitted with nothing selected
    assert client.requests[-1]["messages"][5]["content"] == "(empty reply)"
    assert json.loads(client.requests[-1]["messages"][-1]["content"])["decisions_remaining"] == 5


def test_budget_exhausted_is_turn_cap():
    inst = INSTANCES_BY_ID["checkboxes_00"]
    r = run_episode(FakeClient(lambda _: "?"), "claude-haiku-5-5", inst, R0, 0)
    assert r.status == "turn_cap" and r.success is False and r.turns == MAX_DECISIONS == r.invalid_replies


def test_case_sensitive_codes():
    inst = INSTANCES_BY_ID["checkboxes_00"]
    r = run_episode(FakeClient(lambda _: "a"), "claude-haiku-5-5", inst, R0, 0)
    assert r.invalid_replies == MAX_DECISIONS


def test_refusal():
    inst = INSTANCES_BY_ID["checkboxes_00"]
    r = run_episode(FakeClient(lambda _: None), "claude-haiku-5-5", inst, R0, 0)
    assert r.status == "refusal" and r.success is False


# ---------------------------------------------------------------------------
# Ollama backend (urllib is patched; no server needed)


class FakeHTTP:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_ollama_client_request_and_response(monkeypatch):
    import urllib.request
    from readout import ollama

    seen = []

    def fake_urlopen(req, timeout):
        seen.append((req.full_url, json.loads(req.data)))
        return FakeHTTP({"message": {"role": "assistant", "content": "B\n"}, "done_reason": "length",
                         "prompt_eval_count": 479, "eval_count": 3})
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    inst = INSTANCES_BY_ID["ascending_00"]
    r = run_episode(ollama.OllamaClient(), "qwen3.5:2b-bf16", inst, R0, 0)
    url, body = seen[0]
    assert url == "http://localhost:11434/api/chat"
    assert body["model"] == "qwen3.5:2b-bf16" and body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 1, "num_predict": 3}
    assert body["messages"][0] == {"role": "system", "content": system_prompt(ASCENDING)}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    # The history grows like a chat, with the system message always first.
    assert [m["role"] for m in seen[-1][1]["messages"]] == ["system"] + ["user", "assistant"] * (r.turns - 1) + ["user"]
    assert r.codes == ["B"] * r.turns and r.usage.input_tokens == 479 * r.turns and r.usage.output_tokens == 3 * r.turns


def test_ollama_errors_are_api_errors(monkeypatch):
    import urllib.error
    import urllib.request
    from readout import ollama

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    r = run_episode(ollama.OllamaClient(), "qwen3.5:2b-bf16", INSTANCES_BY_ID["checkboxes_00"], R0, 0)
    assert r.status == "api_error" and r.success is None and "URLError" in r.error


def test_ollama_model_costs_nothing():
    from analysis import cost
    from runner.schemas import Usage

    assert cost.episode_cost("qwen3.5:2b-bf16", Usage(input_tokens=10_000, output_tokens=100)) == 0.0


# ---------------------------------------------------------------------------
# CLI and analysis compatibility


def test_run_cli_dry_run_and_resume(tmp_path, capsys):
    from readout.run import main

    out = tmp_path / "readout.jsonl"
    assert main(["--models", "haiku", "--conditions", "all", "--runs", "3", "--out", str(out), "--dry-run"]) == 0
    assert "2160 episodes in grid, 0 already done, 2160 to run" in capsys.readouterr().out

    inst = INSTANCES_BY_ID["soft_00"]
    r = run_episode(solver_client(CHECKBOXES_SOFT), "claude-haiku-5-5", inst, R0, 0)
    out.write_text(r.model_dump_json() + "\n")
    assert main(["--tasks", "soft_00", "--conditions", "R0_canonical", "--runs", "2", "--out", str(out), "--dry-run"]) == 0
    assert "2 episodes in grid, 1 already done, 1 to run" in capsys.readouterr().out


def test_stats_against_r0_baseline(tmp_path):
    rows = []
    for task_id in ["checkboxes_00", "checkboxes_01"]:
        inst = INSTANCES_BY_ID[task_id]
        for cond in ["R0_canonical", "R1_reversed_fields"]:
            for run in range(2):
                good = cond == "R0_canonical" or task_id == "checkboxes_01"
                client = solver_client(CHECKBOXES) if good else FakeClient(lambda _: "?")
                rows.append(run_episode(client, "claude-haiku-5-5", inst, CONDITIONS[cond], run))
    path = tmp_path / "readout.jsonl"
    path.write_text("".join(r.model_dump_json() + "\n" for r in rows))
    results = stats.load_results(path)
    assert stats.detect_baseline(results) == "R0_canonical"
    [d] = stats.all_deltas(results, n_boot=100)
    assert d.condition == "R1_reversed_fields" and d.baseline_rate == 1.0 and d.condition_rate == 0.5 and d.delta == -0.5
    assert stats.main([str(path), "--out", str(tmp_path / "d.csv")]) == 0
