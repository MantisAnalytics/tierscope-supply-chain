"""The episode benchmark: ShockBench events mapped onto the graph, the weekly views, the world the planner reviews
the plan in, the references and the pooled score.

No network and no ShockBench install: episodes are written here in the export's format.
"""

import json

import numpy as np
import pytest
from test_ops import tiny_company

from scgraph.ops import episode_bench as eb
from scgraph.ops.scenarios import Compound, Disruption, effects
from scgraph.ops.shock_episodes import (
    Priors,
)
from scgraph.ops.simulate import Simulator

PRIORS = Priors({"tariff": 40.0, "energy_shock": 2.0, "sanction": 60.0, "weather_closure": 1.0},
                {"tariff": 1.0, "energy_shock": 0.3, "sanction": 1.0, "weather_closure": 1.0})


def row(type_, onset, duration, *, kind="event", severity=1.0, messages=None, **extra):
    return {"kind": kind, "type": type_, "region": extra.pop("region", None), "counterpart": extra.pop("counterpart", None),
            "target_kind": extra.pop("target_kind", "region"), "commodity": None, "onset": onset, "duration": duration,
            "severity": severity, "rate": extra.pop("rate", None), "messages": messages or {}, **extra}


TARIFF = row("tariff", 6.2, 30, region="US", counterpart="CN", rate=0.25, messages={"tariff_formal": 1.5,
                                                                                     "tariff_final": 4.0})
SANCTION = row("sanction", 10.0, 100, region="US", target_kind="edge", from_region="EU", to_region="US",
               messages={"ties_threat": 3.0, "sanction_legal": 10.0})
DECOY = row("sanction", 20.0, 50, kind="decoy", region="US", target_kind="edge", from_region="CN", to_region="US",
            messages={"ties_threat": 12.0, "sanction_legal": 19.0}, channel="ties_threat")
SHOCK = row("energy_shock", 30.4, 3.0, region="CN", target_kind="node", severity=0.5, node="grid_cn",
            node_type="grid", node_region="CN")
NOTHING = row("tariff", 2.0, 10, region="GULF", counterpart="AU", rate=0.1)  # no plant of ours in the Gulf


# --- mapping ---------------------------------------------------------------------------------------------------------

def test_destination_limits_an_export_restriction():
    co = tiny_company()
    every = effects(co, Disruption("Ban", "export_restriction", 0, 4, params={"countries": ["China"]}))
    elsewhere = effects(co, Disruption("Ban", "export_restriction", 0, 4, params={
        "countries": ["China"], "destination_countries": ["United States"]}))
    assert every.components == ["C1", "C3"] and elsewhere.components == []


def test_compound_effects_merge():
    co = tiny_company()
    a = Disruption("Fire", "supplier_outage", 2, 4, params={"suppliers": ["S1"], "lost_share": 0.5})
    b = Disruption("Ban", "export_restriction", 4, 4, params={"countries": ["China"], "lost_share": 0.5})
    t = Disruption("Duty", "tariff", 1, 6, params={"origin_countries": ["China"], "rate": 0.1})
    c = Compound("Year", "compound", 0, 52, parts=[a, b, t], horizon_weeks=52)
    eff = effects(co, c)
    assert eff.capacity["C1"][4] == pytest.approx(0.25) and eff.capacity["C1"][2] == pytest.approx(0.5)
    assert eff.tariff["C1"][3] == pytest.approx(0.1) and eff.onset("C1") == 1 and eff.onset("C3") == 1
    assert "loses 50%" in eff.reasons["C1"] and "export restriction" in eff.reasons["C1"]


# --- the timeline ----------------------------------------------------------------------------------------------------

# --- the world and the runner ----------------------------------------------------------------------------------------

def test_locked_simulator_keeps_started_actions():
    co = tiny_company(single_source=False)
    d = Compound("Year", "compound", 0, 52, parts=[Disruption("Fire", "supplier_outage", 4, 10, params={
        "suppliers": ["S1"]}, horizon_weeks=52)], horizon_weeks=52)
    locked = [{"type": "spot_buy", "component": "C1", "share": 0.5, "start_week": 2}]
    sim = eb.LockedSimulator(co, d, locked, week=6)
    plan = sim.plan_of([{"type": "spot_buy", "component": "C1", "share": 0.1},
                        {"type": "second_source", "component": "C1"}])
    assert plan == [locked[0] | {"start_week": 2}, {"type": "second_source", "component": "C1", "start_week": 6}]
    assert sim.run([]).action_cost > 0  # the started spot buy is paid even if the plan leaves it out


# --- references and scoring ------------------------------------------------------------------------------------------

def test_pooled_rss_is_shockbench_formula():
    rows = [{"level": 1, "do_nothing": 10.0, "optimiser": 0.0, "agent": 5.0},
            {"level": 1, "do_nothing": 20.0, "optimiser": 10.0, "agent": 10.0},
            {"level": 2, "do_nothing": 100.0, "optimiser": 0.0, "agent": 100.0}]
    # level 1: mean g 7.5, mean D 10; level 2: g 0, D 100; weights 0.5 and 0.3
    assert eb.pooled_rss(rows, "agent") == pytest.approx((0.5 * 7.5 + 0.3 * 0) / (0.5 * 10 + 0.3 * 100), abs=1e-4)
    low, high = eb._interval(rows, "agent")
    assert low <= eb.pooled_rss(rows, "agent") <= high


def test_build_suite_levels_and_splits(tmp_path):
    company = tiny_company(single_source=False)  # Chinese suppliers, a plant in Portugal
    tariff = dict(TARIFF, region="EU", counterpart="CN")
    export = {"source": "test", "entropy": 1, "episodes": [
        {"episode": n, "events": [dict(tariff, onset=float(n % 20), rate=0.1 + 0.02 * n),
                                  dict(SHOCK, onset=float(5 + n), duration=2.0 + n, severity=0.1 + n / 15)]}
        for n in range(12)]}
    suite = eb.build_suite(company, export, dev_per_level=1, test_per_level=1, workers=1)
    assert suite["candidates"] == 12 and len(suite["episodes"]) == 8 - sum(suite["missing"].values())
    assert [e["split"] for e in suite["episodes"]].count("dev") <= 4 and suite["harm_cuts"] == sorted(
        suite["harm_cuts"])
    for e in suite["episodes"]:
        refs = e["references"]
        assert refs["optimiser"]["cost"] <= refs["do_nothing"]["cost"] + 1e-6
        assert set(refs) == {"do_nothing", "optimiser", "playbook", "replanner"}
    eb.save_suite(suite, tmp_path / "s.json")
    assert json.loads((tmp_path / "s.json").read_text())["version"] == eb.SUITE_VERSION
    eb.check_graph(suite, company)
    with pytest.raises(ValueError, match="another graph"):
        eb.check_graph(suite, _other_company())


def _other_company():
    co = tiny_company()
    del co.components["C3"]
    return co


def test_summary_and_markdown():
    records = [{"episode": "e1", "split": "test", "level": 1, "run": 0, "do_nothing": 10.0, "optimiser": 0.0,
                "playbook": 5.0, "replanner": 2.0, "cost": 4.0, "turns": [{"status": "done"}, {"status": "failed"}],
                "cost_usd": 0.01}]
    meta = {"name": "x", "model": "m", "provider": "meta", "agent_version": "v3", "variant": "full",
            "prompt_version": "p", "split": "test", "runs_per_episode": 1, "date": "d", "commit": "c"}
    s = eb.summarize(records, meta)
    assert s["rss"] == {"agent": 0.6, "playbook": 0.5, "replanner": 0.8} and s["failed_turns"] == 1
    assert "**0.60" in eb.summary_markdown(s) and np.isfinite(s["usd_total"])


def test_benchmark_report_shows_episode_results():
    from scgraph.ops.reports import benchmark_report

    records = [{"episode": "e1", "split": "test", "level": 1, "run": 0, "do_nothing": 10e6, "optimiser": 0.0,
                "playbook": 5e6, "replanner": 2e6, "cost": 4e6, "turns": [{"status": "done"}], "cost_usd": 0.01}]
    meta = {"name": "spark-episodes", "model": "m", "provider": "meta", "agent_version": "v3", "variant": "full",
            "prompt_version": "p", "split": "test", "runs_per_episode": 1, "date": "d", "commit": "c"}
    doc = benchmark_report([], [eb.summarize(records, meta)])
    assert doc["sections"][0]["title"] == "ShockBench episodes"
    assert doc["sections"][0]["blocks"][0]["rows"][0][4].startswith("0.60")


def test_a_later_event_does_not_undo_a_new_supplier():
    co = tiny_company()  # C1 single-source from S1 (China); S3 (China) also makes pyrotechnics
    co.suppliers["S3"].country = "Germany"
    fire = Disruption("Fire", "supplier_outage", 2, 20, params={"suppliers": ["S1"]}, horizon_weeks=52)
    later = Disruption("Ban", "export_restriction", 30, 10, params={"countries": ["Germany"]}, horizon_weeks=52)
    soon = Disruption("Ban", "export_restriction", 5, 10, params={"countries": ["Germany"]}, horizon_weeks=52)
    action = {"type": "new_supplier", "component": "C1", "supplier": "S3", "start_week": 0}
    for parts, refused in (([fire, later], False), ([fire, soon], True)):
        d = Compound("Year", "compound", 0, 52, parts=parts, horizon_weeks=52)
        sim = Simulator(co, d)
        assert bool(sim.run([action]).refused) == refused


def test_build_suite_resumes_from_its_cache(tmp_path, monkeypatch):
    company = tiny_company(single_source=False)
    export = {"source": "test", "entropy": 1, "episodes": [
        {"episode": n, "events": [dict(TARIFF, region="EU", counterpart="CN", onset=float(n), rate=0.1 + 0.05 * n)]}
        for n in range(4)]}
    first = eb.build_suite(company, export, dev_per_level=1, test_per_level=1, workers=1, cache_dir=tmp_path)
    calls = []
    monkeypatch.setattr(eb, "references", lambda *a: calls.append(a) or {})
    again = eb.build_suite(company, export, dev_per_level=1, test_per_level=1, workers=1, cache_dir=tmp_path)
    assert calls == [] and again["episodes"] == first["episodes"]


def test_policy_refusals_are_told_apart_from_bad_requests():
    from scgraph.llm.providers import is_policy_refusal

    assert is_policy_refusal(400, "The response was filtered due to the prompt triggering our content management policy.")
    assert is_policy_refusal(403, "Your access has been restricted due to repeated policy violations.")
    assert not is_policy_refusal(400, "`max_tokens` The number must be `>= 16`.")
    assert not is_policy_refusal(500, "content management policy")
