"""Operations: snapshot, disruptions, the weekly simulator, references, the agent loop, decisions and reports.

No network: the agent and the news draft run on scripted fake transports.
"""


import pytest

from fakes import FakeTransport, make_config, text_response, tool_response
from scgraph.llm import LLMClient
from scgraph.ops.agent import run_agent
from scgraph.ops.company import Assembly, Company, Component, Site, Supplier, country_name
from scgraph.ops.news import to_draft
from scgraph.ops.scenarios import Disruption, DisruptionError, effects
from scgraph.ops.simulate import ActionError, Simulator, skill


def tiny_company(single_source: bool = True) -> Company:
    """One assembly of EUR 52M a year (EUR 1M a week) built from two bought-in parts."""
    suppliers = {"S1": Supplier("S1", "Alpha", "China", "Asia", 1, 40.0),
                 "S2": Supplier("S2", "Beta", "Germany", "Europe", 1, 20.0),
                 "S3": Supplier("S3", "Gamma", "China", "Asia", 2, 10.0)}
    sites = {"P1": Site("P1", "Plant 1", "Portugal", "Europe", 6.0)}
    components = {
        "C1": Component("C1", "Inflator", "CHEMICALS", "Pyrotechnics", "High", single_source, "S1", ["P1"],
                        10_000.0, False, ["A1"]),
        "C2": Component("C2", "Bracket", "STEEL", "Stampings", "Standard", False, "S2", ["P1"], 1_000.0, False, ["A1"]),
        "C3": Component("C3", "Igniter", "CHEMICALS", "Pyrotechnics", "High", True, "S3", ["P1"], 5_000.0, False, []),
    }
    assemblies = {"A1": Assembly("A1", "RB", 52e6, ["C1", "C2"])}
    return Company(assemblies, components, suppliers, sites, margin=0.2, margin_from_graph=True)


def outage(weeks: int, start: int = 0, cover: float = 4.0, supplier: str = "S1") -> Disruption:
    return Disruption("Fire", "supplier_outage", start, weeks, params={"suppliers": [supplier], "lost_share": 1.0},
                      stock_cover_weeks=cover)


# --- snapshot and disruptions ---------------------------------------------------------------------------------------

def test_country_names_are_normalised():
    assert country_name("USA") == "United States" and country_name("China (Jilin)") == "China"
    assert country_name("  ") is None


def test_export_restriction_selects_country_and_commodity():
    co = tiny_company()
    d = Disruption("Licences", "export_restriction", 2, 8, params={"countries": ["China"], "commodities": ["CHEMICALS"]})
    eff = effects(co, d)
    assert eff.components == ["C1", "C3"]
    assert eff.capacity["C1"][1] == 1 and eff.capacity["C1"][2] == 0 and eff.capacity["C1"][9] == 0
    assert 0 < eff.capacity["C1"][10] < 1  # recovery ramp


def test_route_closure_delays_only_matching_lanes():
    co = tiny_company()
    d = Disruption("Suez", "route_closure", 0, 10,
                   params={"origin_countries": ["Asia"], "destination_countries": ["Europe"], "extra_weeks": 3})
    eff = effects(co, d)
    assert set(eff.gap) == {"C1", "C3"} and eff.gap["C1"][:3].all() and not eff.gap["C1"][3]


def test_disruption_validation():
    with pytest.raises(DisruptionError, match="needs suppliers"):
        Disruption("x", "supplier_outage", params={}).validate()
    with pytest.raises(DisruptionError, match="not in the graph"):
        Disruption("x", "supplier_outage", params={"suppliers": ["NOPE"]}).validate(tiny_company())


# --- simulation -----------------------------------------------------------------------------------------------------

def test_stock_bridges_a_short_outage():
    o = Simulator(tiny_company(), outage(3)).run([])
    assert o.lost_revenue == 0 and o.first_stop_week is None


def test_longer_outage_stops_production_when_stock_runs_out():
    o = Simulator(tiny_company(), outage(8)).run([])
    assert o.first_stop_week == 4  # weeks 0-3 live on the 4 weeks of stock
    assert o.assembly_output["A1"][4] == 0
    assert o.lost_revenue == pytest.approx(o.lost_margin / 0.2)


def test_second_source_refused_for_single_source_part():
    sim = Simulator(tiny_company(single_source=True), outage(8))
    o = sim.run([{"type": "second_source", "component": "C1"}])
    assert o.refused and "no approved second source" in o.refused[0]["reason"]
    with pytest.raises(ActionError):
        sim.run([{"type": "second_source", "component": "C1"}], strict=True)


def test_second_source_cuts_the_loss():
    sim = Simulator(tiny_company(single_source=False), outage(10))
    nothing = sim.run([])
    plan = sim.run([{"type": "second_source", "component": "C1"}])
    assert not plan.refused and plan.total_cost < nothing.total_cost
    assert plan.action_costs["second_source:C1"] >= 10_000


def test_new_supplier_must_be_unaffected():
    co = tiny_company()
    d = Disruption("Licences", "export_restriction", 0, 8, params={"countries": ["China"]})
    o = Simulator(co, d).run([{"type": "new_supplier", "component": "C1", "supplier": "S3"}])
    assert o.refused and "same disruption" in o.refused[0]["reason"]


def test_buffer_only_before_the_disruption():
    co = tiny_company()
    active = Simulator(co, outage(8, start=0)).run([{"type": "buffer", "component": "C1", "weeks": 4}])
    assert active.refused and "before the disruption" in active.refused[0]["reason"]
    later = Simulator(co, outage(8, start=4)).run([{"type": "buffer", "component": "C1", "weeks": 4}])
    assert not later.refused and later.action_costs.get("buffer:C1", 0) > 0


def test_air_freight_bridges_a_route_delay():
    co = tiny_company()
    d = Disruption("Suez", "route_closure", 0, 10, stock_cover_weeks=1,
                   params={"origin_countries": ["China"], "extra_weeks": 4, "surcharge": 0.2})
    sim = Simulator(co, d)
    nothing, air = sim.run([]), sim.run([{"type": "air_freight", "component": "C1"}])
    assert nothing.lost_revenue > 0 and air.lost_revenue < nothing.lost_revenue
    assert air.action_costs["air_freight:C1"] > 0 and nothing.disruption_costs > 0


def test_simulation_is_deterministic():
    sim = Simulator(tiny_company(False), outage(10))
    plan = [{"type": "spot_buy", "component": "C1", "share": 0.5}]
    assert sim.run(plan).to_dict() == sim.run(plan).to_dict()


def test_references_and_skill():
    sim = Simulator(tiny_company(single_source=False), outage(12))
    refs = sim.references()
    nothing = refs["do_nothing"]["outcome"]["total_cost"]
    best = refs["optimiser"]["outcome"]["total_cost"]
    assert best <= refs["playbook"]["outcome"]["total_cost"] + 1e-6 and best < nothing
    assert refs["optimiser"]["skill"] == 1.0
    assert skill(nothing, best, nothing) == 0.0 and skill(nothing, best, best) == 1.0
    assert skill(100.0, 100.0, 50.0) is None


# --- agent ----------------------------------------------------------------------------------------------------------

def _client(tmp_path, responses):
    transport = FakeTransport(responses)
    return LLMClient(make_config(tmp_path, SCGRAPH_LLM_CACHE="off"), transport=transport), transport


def test_agent_simulates_then_submits(tmp_path):
    co, d = tiny_company(single_source=False), outage(10)
    plan = [{"type": "second_source", "component": "C1", "reason": "approved second source"}]
    client, transport = _client(tmp_path, [
        tool_response("simulate_plan", {"actions": plan}, id="t1"),
        tool_response("submit_plan", {"actions": plan, "summary": "Use the second source."}, id="t2"),
    ])
    steps = []
    result = run_agent(co, d, client, on_step=steps.append)
    assert result.status == "done" and result.plan[0]["type"] == "second_source"
    assert result.plan[0]["reason"] == "approved second source" and result.summary
    assert [s["name"] for s in steps if s["kind"] == "tool"] == ["simulate_plan", "submit_plan"]
    first = transport.requests[0]
    assert "Inflator" in first.messages[0]["content"] and {t["name"] for t in first.tools} >= {"submit_plan"}


def test_agent_refused_plan_is_sent_back(tmp_path):
    co, d = tiny_company(single_source=True), outage(10)
    bad = [{"type": "second_source", "component": "C1", "reason": "x"}]
    good = [{"type": "spot_buy", "component": "C1", "share": 0.5, "reason": "bridge"}]
    client, transport = _client(tmp_path, [
        tool_response("submit_plan", {"actions": bad, "summary": "s"}, id="t1"),
        tool_response("submit_plan", {"actions": good, "summary": "s"}, id="t2"),
    ])
    result = run_agent(co, d, client)
    assert result.status == "done" and result.plan[0]["type"] == "spot_buy"
    sent_back = transport.requests[1].messages[-1]["content"][0]["content"]
    assert "no approved second source" in sent_back


def test_agent_without_a_plan_fails(tmp_path):
    client, _ = _client(tmp_path, [text_response("thinking"), text_response("still thinking")])
    result = run_agent(tiny_company(), outage(10), client)
    assert result.status == "failed" and "without submitting" in result.error and not result.plan


def test_news_draft_matches_the_graph():
    draft = to_draft(tiny_company(), {"name": "Licences", "type": "export_restriction", "description": "d",
                                      "start_week": 1, "duration_weeks": 6, "countries": ["PRC", "Atlantis"],
                                      "commodities": ["chemicals"]})
    assert draft.disruption.params["countries"] == ["China"]
    assert draft.disruption.params["commodities"] == ["CHEMICALS"]
    assert any("Atlantis" in w for w in draft.warnings)


# --- decisions, tasks, reports --------------------------------------------------------------------------------------

# --- API ------------------------------------------------------------------------------------------------------------
