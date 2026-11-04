"""The agent benchmark: the suite, the stronger optimiser, the runner and its summaries.

No network: the agent runs on fake transports.
"""

import json

import pytest

from fakes import FakeTransport, make_config, tool_response
from scgraph.llm import LLMClient
from scgraph.ops.agent import run_agent
from scgraph.ops.simulate import Simulator
from test_ops import outage, tiny_company


# --- suite ------------------------------------------------------------------------------------------------------------

# --- optimiser --------------------------------------------------------------------------------------------------------

def test_component_costs_add_up():
    co, d = tiny_company(single_source=False), outage(10, cover=1.0)
    sim = Simulator(co, d)
    o = sim.run([{"type": "second_source", "component": "C1"}, {"type": "spot_buy", "component": "C1", "share": 0.3}])
    assert sum(o.component_costs.values()) == pytest.approx(o.action_cost + o.disruption_costs, abs=1.0)
    assert set(o.component_costs) == {"C1"}


# --- runner -----------------------------------------------------------------------------------------------------------

def _submitting(plan: list[dict]) -> FakeTransport:
    class Scripted(FakeTransport):
        def __call__(self, request):
            self.requests.append(request)
            return tool_response("submit_plan", {"actions": plan, "summary": "s"}, id=f"t{len(self.requests)}")

    return Scripted()


def test_the_planner_has_one_variant(tmp_path):
    client = LLMClient(make_config(tmp_path), transport=_submitting([]), cache_mode="off")
    with pytest.raises(ValueError, match="variant"):
        run_agent(tiny_company(), outage(10), client, variant="nope")


# --- agent v2 tools ---------------------------------------------------------------------------------------------------

def test_agent_reads_actions_sent_as_a_string(tmp_path):
    plan = [{"type": "spot_buy", "component": "C1", "share": 0.5, "reason": "bridge"}]
    transport = FakeTransport([tool_response("simulate_plan", {"actions": json.dumps(plan[0])}, id="t1"),
                               tool_response("submit_plan", {"actions": json.dumps(plan), "summary": "s"}, id="t2")])
    client = LLMClient(make_config(tmp_path), transport=transport, cache_mode="off")
    steps = []
    result = run_agent(tiny_company(), outage(10), client, on_step=steps.append)
    assert result.status == "done" and result.plan[0]["share"] == 0.5
    simulated = next(s for s in steps if s.get("name") == "simulate_plan")
    assert not simulated["error"] and simulated["result"]["money_by_component"]["C1"].startswith("EUR")
    broken = FakeTransport([tool_response("submit_plan", {"actions": "[{oops", "summary": "s"}, id="t1"),
                            tool_response("submit_plan", {"actions": plan, "summary": "s"}, id="t2")])
    result = run_agent(tiny_company(), outage(10), LLMClient(make_config(tmp_path), transport=broken,
                                                             cache_mode="off"))
    assert result.status == "done"
    assert "could not read the string" in broken.requests[1].messages[-1]["content"][0]["content"]


def test_overview_lists_the_allowed_actions(tmp_path):
    transport = _submitting([])
    client = LLMClient(make_config(tmp_path), transport=transport, cache_mode="off")
    run_agent(tiny_company(single_source=True), outage(10, start=2), client)
    overview = transport.requests[0].messages[0]["content"]
    line = next(x for x in overview.splitlines() if x.startswith("- id C1"))
    assert "allowed: new_supplier (1 candidate), spot_buy, buffer (start before week 2)" in line
    assert "second_source" not in line.split("allowed:")[1]


def test_rescore_keeps_costs_and_moves_the_yardstick():
    from scgraph.ops.bench import rescore

    record = {"case": "x", "valid": True, "cost": 50.0e3, "do_nothing": 100e3, "playbook": 60e3, "optimiser": 40e3,
              "skill": 50 / 60, "playbook_skill": 40 / 60}
    invalid = {**record, "valid": False, "cost": 100e3}
    refs = {"x": {"do_nothing": {"cost": 100e3}, "playbook": {"cost": 60e3}, "optimiser": {"cost": 0.0}}}
    a, b = rescore([record, invalid], refs)
    assert a["cost"] == 50e3 and a["skill"] == 0.5 and a["playbook_skill"] == 0.4
    assert b["skill"] == 0.0 and b["cost"] == 100e3


def test_cut_off_submission_is_asked_again(tmp_path):
    from scgraph.llm.types import Response, Usage

    cut = Response(model="muse-spark-1.1", stop_reason="max_tokens", usage=Usage(100, 8000),
                   content=[{"type": "tool_use", "id": "t1", "name": "submit_plan", "input": {}}])
    plan = [{"type": "spot_buy", "component": "C1", "share": 0.5, "reason": "bridge"}]
    transport = FakeTransport([cut, tool_response("submit_plan", {"summary": "s"}, id="t2"),
                               tool_response("submit_plan", {"actions": plan, "summary": "s"}, id="t3")])
    result = run_agent(tiny_company(), outage(10), LLMClient(make_config(tmp_path), transport=transport,
                                                             cache_mode="off"))
    assert result.status == "done" and result.plan[0]["type"] == "spot_buy"
    assert "cut off" in transport.requests[1].messages[-1]["content"][0]["content"]
    assert "needs `actions`" in transport.requests[2].messages[-1]["content"][0]["content"]
