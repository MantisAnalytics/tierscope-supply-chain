"""The agent engine: worlds, the approval gate, plan versions, the shared tools, the recorded loop and profiles.

No network: the model is a scripted fake transport. Tests that change the graph work on a copy of the reference
graph database.
"""


import pytest

from fakes import FakeTransport, make_config, text_response, tool_response
from scgraph.agent.gate import Gate, GateError, Write
from scgraph.agent.loop import run_loop
from scgraph.agent.profiles import CHAT, PLANNER, context, run_turn
from scgraph.agent.tools import Tool, Turn
from scgraph.agent.tools.ops import OpsTools
from scgraph.agent.tools.write import WriteTools
from scgraph.agent.world import SnapshotWorld, View
from scgraph.llm import LLMClient, ProviderError
from scgraph.ops.scenarios import library
from test_ops import outage, tiny_company


def client(tmp_path, responses=()):
    transport = FakeTransport(responses)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


# --- world and gate ---------------------------------------------------------------------------------------------


class ScriptedWorld(SnapshotWorld):
    """An in-memory world that also records what the tools asked of it."""

    name = "scripted"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.views = 0

    def view(self, did):
        self.views += 1
        return super().view(did)


def test_planner_runs_against_a_scripted_world_without_databases(tmp_path):
    world = ScriptedWorld(tiny_company(single_source=False), {7: outage(10)}, week=3)
    plan = [{"type": "second_source", "component": "C1", "reason": "approved second source"}]
    llm, transport = client(tmp_path, [
        tool_response("component_details", {"component": "C1"}, id="t0"),
        tool_response("simulate_plan", {"actions": plan}, id="t1"),
        tool_response("submit_plan", {"actions": plan, "summary": "Use the second source."}, id="t2")])
    result = run_turn(PLANNER, Turn(world, "agent:test", disruption_id=7), llm, [])
    assert result.status == "done" and world.views >= 1
    [write] = world.submitted
    assert write.kind == "plan" and write.disruption_id == 7 and write.data["actions"][0]["type"] == "second_source"
    assert result.blocks == [{"type": "plan", "ref": 1}]
    assert "World: scripted. Current week: 3." in transport.requests[0].system
    assert {t["name"] for t in transport.requests[0].tools} == {
        "component_details", "alternative_suppliers", "simulate_plan", "submit_plan"}  # no graph in this world


class Sink:
    def __init__(self):
        self.stored, self.applied = [], []

    def store(self, write):
        self.stored.append(write)
        return {"block": {"type": write.kind, "ref": len(self.stored)}, "message": "Stored."}

    def apply(self, write, stored):
        self.applied.append(write)


def test_gate_human_proposes_auto_applies_and_refuses_over_the_limit():
    sink = Sink()
    human = Gate().submit(Write("plan", {"action_cost": 900_000}, "agent:m"), sink)
    assert human.status == "proposed" and sink.applied == [] and "applied" not in human.block
    auto = Gate({"plan": "auto"}).submit(Write("plan", {"action_cost": 100_000}, "agent:m"), sink)
    assert auto.status == "applied" and auto.block["applied"] and sink.applied[0].author == "agent:m"
    refused = Gate({"plan": "auto"}, spend_limit_eur=250_000).submit(
        Write("plan", {"action_cost": 300_000}, "agent:m"), sink)
    assert refused.status == "refused" and "EUR 300k" in refused.message and "EUR 250k" in refused.message
    assert len(sink.stored) == 2
    with pytest.raises(GateError):
        Gate({"plan": "sometimes"})


def test_agent_settings_from_the_environment():
    from scgraph.config import Config, ConfigError

    default = Config.from_env({})
    assert (default.agent_triggers, default.budget_auto_usd, default.agent_spend_limit_eur) == (True, 2.0, 250_000)
    custom = Config.from_env({"SCGRAPH_AGENT_TRIGGERS": "off", "SCGRAPH_BUDGET_AUTO_USD": "0.5",
                              "SCGRAPH_AGENT_SPEND_LIMIT_EUR": "1000"})
    assert (custom.agent_triggers, custom.budget_auto_usd, custom.agent_spend_limit_eur) == (False, 0.5, 1000)
    with pytest.raises(ConfigError):
        Config.from_env({"SCGRAPH_AGENT_SPEND_LIMIT_EUR": "lots"})


# --- live world, plan versions ------------------------------------------------------------------------------------


def fire(service):
    co = service.snapshot()
    d = next(x for x in library(co) if x.type == "supplier_outage")
    did = service.create(co, d.to_dict(), "ana")
    comps = [c["id"] for c in service.assessment(co, service.disruption(did))["affected_components"]]
    return did, comps


def spot(comp, share=0.5):
    return {"type": "spot_buy", "component": comp, "share": share, "reason": f"bridge {comp}"}


# --- tools --------------------------------------------------------------------------------------------------------


def test_the_view_not_the_stored_duration_reaches_the_overview():
    truth = outage(10)
    view = View.of(truth, duration_weeks=7, duration_range=(4, 12))
    world = SnapshotWorld(tiny_company(), {1: view})
    overview = OpsTools(Turn(world, "agent:m", disruption_id=1)).overview()
    assert "lasts an estimated 4 to 12 weeks (not known exactly yet)" in overview
    assert "10 weeks" not in overview and "lasts 7" not in overview


def test_simulate_fills_gaps_and_names_the_blocked_assemblies():
    world = SnapshotWorld(tiny_company(single_source=False), {1: outage(10)}, week=2)
    tools = OpsTools(Turn(world, "agent:m", disruption_id=1))
    bare = tools.simulate([])
    assert bare["blocked_assemblies"] and bare["components_still_short"]
    filled = tools.simulate([], fill_gaps=True)
    added = filled["added_actions"]
    assert added and all(a["start_week"] == 2 for a in added)
    assert {a["component"] for a in added} == set(bare["components_still_short"])
    def same(out):
        return {k: v for k, v in out.items() if k not in ("added_actions", "simulation_id")}
    assert same(tools.simulate(added)) == same(filled)


def test_lookups_take_several_components_and_tolerate_whitespace():
    world = SnapshotWorld(tiny_company(), {1: outage(10)})
    tools = OpsTools(Turn(world, "agent:m", disruption_id=1))
    assert tools.component_details({"component": "\nC1 "})["id"] == "C1"
    assert [c["id"] for c in tools.component_details({"components": ["C1", " C3"]})] == ["C1", "C3"]
    assert set(tools.alternative_suppliers({"components": ["C1", "C3"]})) == {"C1", "C3"}


def test_submit_reads_actions_and_summary_mixed_up_in_one_string():
    world = SnapshotWorld(tiny_company(), {1: outage(10)})
    out = WriteTools(Turn(world, "agent:m", disruption_id=1)).submit_plan(
        {"actions": '[],\n"summary">No action lowers the total cost: stock covers it.'})
    assert out.value["accepted"] is True
    assert world.submitted[-1].data["summary"] == "No action lowers the total cost: stock covers it."


def test_submit_by_simulation_id_and_the_cheaper_plan_guard():
    world = SnapshotWorld(tiny_company(single_source=False), {1: outage(10)}, week=2)
    turn = Turn(world, "agent:m", disruption_id=1)
    ops, writes = OpsTools(turn), WriteTools(turn)
    filled = ops.simulate([], fill_gaps=True)
    nothing = ops.simulate([])
    assert (filled["simulation_id"], nothing["simulation_id"]) == (1, 2)
    first = writes.submit_plan({"actions": [], "summary": "Wait."})
    assert first.value["accepted"] is False and "simulation_id 1" in first.value["message"]
    out = writes.submit_plan({"simulation_id": 1, "summary": "Cover the gaps."})
    assert out.value["accepted"] is True
    stored = world.submitted[-1].data["actions"]
    assert {(a["type"], a["component"]) for a in stored} == {(a["type"], a["component"]) for a in filled["added_actions"]}
    assert all(a["reason"] for a in stored)
    with pytest.raises(ValueError, match="no simulation"):
        writes.submit_plan({"simulation_id": 9, "summary": "x"})


def test_a_tool_name_with_a_suffix_runs_the_tool(tmp_path):
    world = SnapshotWorld(tiny_company(single_source=False), {7: outage(10)}, week=3)
    llm, _ = client(tmp_path, [
        tool_response("component_details.component", {"component": "C1"}, id="t0"),
        tool_response("submit_plan", {"actions": [], "summary": "Nothing to do."}, id="t1")])
    result = run_turn(PLANNER, Turn(world, "agent:test", disruption_id=7), llm, [])
    assert result.steps[0]["error"] is False and result.steps[0]["result"]["id"] == "C1"


# --- loop and profiles --------------------------------------------------------------------------------------------


def test_both_modes_record_one_prompt_version_and_differ_only_in_context(tmp_path):
    systems = []
    for gate in (Gate(), Gate({"plan": "auto"})):
        world = SnapshotWorld(tiny_company(single_source=False), {1: outage(10)}, gate=gate)
        plan = [{"type": "second_source", "component": "C1", "reason": "r"}]
        llm, transport = client(tmp_path, [tool_response("submit_plan", {"actions": plan, "summary": "s"})])
        assert run_turn(PLANNER, Turn(world, "agent:m", disruption_id=1), llm, []).status == "done"
        systems.append(transport.requests[0].system)
        assert (world.applied != []) == (gate.mode("plan") == "auto")
    human, auto = (s.splitlines() for s in systems)
    assert [i for i, (a, b) in enumerate(zip(human, auto, strict=True)) if a != b] == [
        human.index(next(line for line in human if line.startswith("Approval modes")))]
    assert systems[0].startswith(PLANNER.template.split("{")[0])
    assert len(PLANNER.version) == 12 and PLANNER.version != CHAT.version


def test_context_states_the_current_plan_with_decisions():
    world = SnapshotWorld(tiny_company(), {1: outage(10)})
    plan = {"version": 2, "summary": "s", "stale": True, "actions": [
        {"text": "Spot buy C1", "status": "accepted", "reason": "bridge"},
        {"text": "Qualify Gamma for C1", "status": "rejected", "comment": "no suppliers in China"}]}
    text = context(Turn(world, "agent:m", disruption_id=1, plan=plan))
    assert "Current plan: version 2. It is stale" in text
    assert '- [rejected with the comment "no suppliers in China"] Qualify Gamma for C1' in text
    assert "Current plan: none submitted yet." in context(Turn(world, "agent:m", disruption_id=1))
    assert "general thread" in context(Turn(world, "agent:m"))


def test_loop_end_conditions(tmp_path):
    echo = Tool("echo", "e", {"type": "object", "properties": {}}, lambda args: {"ok": True})
    llm, _ = client(tmp_path, [tool_response("echo", {}), text_response("The answer.")])
    chat = run_loop(llm, system="s", messages=[{"role": "user", "content": "q"}], tools=[echo], end="text")
    assert chat.status == "done" and chat.text == "The answer." and [s["kind"] for s in chat.steps] == ["tool", "thought"]
    llm, transport = client(tmp_path, [text_response("hm")] * 3)
    nudged = run_loop(llm, system="s", messages=[{"role": "user", "content": "q"}], tools=[echo], end="submit",
                      nudge="Submit now.")
    assert nudged.status == "failed" and "without submitting" in nudged.error
    assert transport.requests[1].messages[-1] == {"role": "user", "content": "Submit now."}
    assert len(transport.requests) == 3  # asked twice, then it gives up
    llm, transport = client(tmp_path, [tool_response("echo", {}, id=f"t{i}") for i in range(5)])
    limited = run_loop(llm, system="s", messages=[{"role": "user", "content": "q"}], tools=[echo], end="submit",
                       max_turns=5)
    assert limited.status == "failed" and limited.error == "no valid plan after 5 turns"
    # the last turns say how many are left, so a model that keeps refining submits in time
    notes = [b["text"] for r in transport.requests for b in r.messages[-1]["content"]
             if isinstance(r.messages[-1]["content"], list) and b.get("type") == "text"]
    assert notes == ["3 turns left: call submit_plan now with the cheapest plan you have simulated "
                     "(its simulation_id submits it as simulated).",
                     "2 turns left: call submit_plan now with the cheapest plan you have simulated "
                     "(its simulation_id submits it as simulated).",
                     "1 turn left: call submit_plan now with the cheapest plan you have simulated "
                     "(its simulation_id submits it as simulated)."]
    assert [s["kind"] for s in limited.steps].count("note") == 3
    llm, _ = client(tmp_path, [ProviderError("overloaded")])
    broken = run_loop(llm, system="s", messages=[{"role": "user", "content": "q"}], tools=[echo], end="text")
    assert broken.status == "failed" and "overloaded" in broken.error and broken.steps[-1]["kind"] == "error"
