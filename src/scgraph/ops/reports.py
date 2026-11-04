"""Reports as structured documents (sections of blocks) that the web app renders and `to_markdown` writes out.

Block kinds: text, metrics (label/value pairs), table (columns, rows), chart (weekly series), list.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from scgraph.llm import LLMClient, LLMError, Request
from scgraph.ops import shockbench
from scgraph.ops.agent import eur
from scgraph.ops.company import WEEKS_PER_YEAR, Company
from scgraph.ops.scenarios import TYPE_LABELS
from scgraph.ops.simulate import ACTION_LABELS, RULES, describe_action
from scgraph.ops.store import OpsStore


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _skill(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def text(t: str) -> dict[str, Any]:
    return {"kind": "text", "text": t}


def metrics(*items: tuple[str, str]) -> dict[str, Any]:
    return {"kind": "metrics", "items": [{"label": k, "value": v} for k, v in items]}


def table(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"kind": "table", "columns": columns, "rows": rows}


# --- disruption report ---------------------------------------------------------------------------------------------

def disruption_report(company: Company, detail: dict[str, Any], client: LLMClient | None = None,
                      resolved: dict[str, Any] | None = None) -> dict[str, Any]:
    """The report as of now; `resolved` ({at, by, note}) writes it as the record kept at resolution, with a
    Resolution section."""
    d, a, refs = detail["disruption"], detail["assessment"], detail["references"]
    run, accepted = detail["latest_run"], detail["accepted"]
    nothing = refs["do_nothing"]["outcome"]
    rows = [["Do nothing", nothing, None, 0], ["Standard playbook", refs["playbook"]["outcome"], refs["playbook"]["skill"],
                                               len(refs["playbook"]["plan"])],
            ["Optimiser reference", refs["optimiser"]["outcome"], refs["optimiser"]["skill"], len(refs["optimiser"]["plan"])]]
    if run and run.get("outcome"):
        rows.append([f"Agent plan ({run['model']})", run["outcome"], run["skill"], len(run["plan"] or [])])
    if accepted["outcome"]:
        rows.append(["Accepted plan", accepted["outcome"], accepted["skill"], len(accepted["plan"])])
    best = accepted["outcome"] or (run or {}).get("outcome")
    figures = {
        "disruption": d["name"], "type": TYPE_LABELS[d["type"]], "starts_week": d["start_week"],
        "weeks": d["duration_weeks"], "affected_components": len(a["affected_components"]),
        "affected_suppliers": [s["name"] for s in a["affected_suppliers"]][:6],
        "do_nothing_cost": eur(nothing["total_cost"]), "do_nothing_lost_revenue": eur(nothing["lost_revenue"]),
        "first_stop_week": nothing["first_stop_week"],
        "plan_cost": eur(best["total_cost"]) if best else None,
        "plan_lost_revenue": eur(best["lost_revenue"]) if best else None,
        "accepted_actions": [x["text"] for x in detail["actions"] if x["status"] == "accepted"],
        "rejected_actions": [x["text"] for x in detail["actions"] if x["status"] == "rejected"],
        "open_tasks": sum(t["status"] in ("open", "in_progress") for t in detail["tasks"]),
    }
    if resolved:
        figures["resolved"] = {"by": resolved["by"], "note": resolved["note"]}
    summary, written_by = executive_summary(figures, client)
    sections = [
        {"title": "Executive summary", "blocks": [text(summary)], "note": written_by},
        {"title": "Disruption", "blocks": [
            metrics(("Type", TYPE_LABELS[d["type"]]), ("Starts", f"week {d['start_week']}"),
                    ("Duration", f"{d['duration_weeks']} weeks"), ("Horizon", f"{d['horizon_weeks']} weeks"),
                    ("Stock cover assumed", f"{d['stock_cover_weeks']:g} weeks"), ("Source", d.get("source", "form"))),
            text(d.get("description") or "")]},
        {"title": "Impact if nothing is done", "blocks": [
            metrics(("Lost revenue", eur(nothing["lost_revenue"])), ("Lost margin", eur(nothing["lost_margin"])),
                    ("Tariffs and surcharges", eur(nothing["disruption_costs"])),
                    ("First week with lost output", str(nothing["first_stop_week"])
                     if nothing["first_stop_week"] is not None else "none"),
                    ("Components affected", str(len(a["affected_components"]))),
                    ("Assemblies reached", str(len(a["assemblies"])))),
            {"kind": "chart", "title": "Lost revenue per week", "unit": "EUR", "series": _series(detail)},
            table(["Component", "Description", "Supplier", "Criticality", "Single-source", "Weeks short", "Effect"],
                  [[c["id"], c["description"], f"{c['supplier_name']} ({c['supplier_country']})", c["criticality"] or "-",
                    _yes(c["single_source"]), c["weeks_short"], c["reason"]] for c in a["affected_components"]])]},
        {"title": "Response plan and decisions", "blocks": [
            table(["Action", "Reason", "Decision", "By", "Comment"],
                  [[x["text"], x["reason"] or "-", x["status"], x.get("decided_by") or "-", x.get("comment") or "-"]
                   for x in detail["actions"] if x["status"] != "superseded"])
            if detail["actions"] else text("No actions were proposed or added yet.")]},
        {"title": "Outcome against the references", "blocks": [
            table(["Plan", "Actions", "Total cost", "Lost revenue", "Action cost", "Skill"],
                  [[name, n, eur(o["total_cost"]), eur(o["lost_revenue"]), eur(o["action_cost"]), _skill(s)]
                   for name, o, s, n in rows]),
            text("Total cost is lost margin plus action costs plus tariffs and surcharges. Skill is the plan's cost "
                 "reduction against doing nothing as a share of the optimiser reference's reduction.")]},
        {"title": "Follow-up", "blocks": [
            table(["Task", "Status", "Owner", "Due week", "Notes"],
                  [[t["title"], t["status"].replace("_", " "), t["owner"] or "-", t["due_week"], t["notes"] or "-"]
                   for t in detail["tasks"]]) if detail["tasks"] else text("No tasks yet: accept actions to create them.")]},
    ]
    if resolved:
        saved = nothing["total_cost"] - (accepted["outcome"] or nothing)["total_cost"]
        sections.insert(1, {"title": "Resolution", "blocks": [
            metrics(("Resolved", resolved["at"][:10]), ("By", resolved["by"]),
                    ("Cost if nothing was done", eur(nothing["total_cost"])),
                    ("Cost with the accepted plan", eur((accepted["outcome"] or nothing)["total_cost"])),
                    ("Saved", eur(saved)), ("Model cost", f"${sum(r['cost_usd'] for r in detail['runs']):.4f}")),
            text(resolved["note"] or "No closing note.")]})
    if run:
        sections.append({"title": "Agent run", "blocks": [
            metrics(("Model", f"{run['model']} ({run['provider']})"), ("Steps", str(len(run["steps"]))),
                    ("Tokens", f"{run['input_tokens']:,} in / {run['output_tokens']:,} out"),
                    ("Model cost", f"${run['cost_usd']:.4f}"), ("Skill", _skill(run["skill"]))),
            text(run.get("summary") or ""),
            {"kind": "list", "items": [_step_line(s) for s in run["steps"]]}]})
    sections.append({"title": "Assumptions", "blocks": [_rules_table(), text(
        f"Lost margin uses a contribution margin of {company.margin:.0%}"
        f"{' from the graph' if company.margin_from_graph else ' (default; the graph has none)'}. Component values "
        "are annual spend / 52; where spend is missing, the median of the commodity stands in.")]})
    return {"kind": "disruption", "id": d["id"], "title": f"Disruption report: {d['name']}",
            "subtitle": f"{TYPE_LABELS[d['type']]}, week {d['start_week']} for {d['duration_weeks']} weeks",
            "generated_at": resolved["at"] if resolved else _now(), "sections": sections}


def _series(detail: dict[str, Any]) -> list[dict[str, Any]]:
    refs = detail["references"]
    out = [{"name": "Do nothing", "values": refs["do_nothing"]["outcome"]["weekly_lost_revenue"]}]
    run = detail["latest_run"]
    if run and run.get("outcome"):
        out.append({"name": "Agent plan", "values": run["outcome"]["weekly_lost_revenue"]})
    if detail["accepted"]["outcome"]:
        out.append({"name": "Accepted plan", "values": detail["accepted"]["outcome"]["weekly_lost_revenue"]})
    return out


def _yes(value: bool | None) -> str:
    return {True: "yes", False: "no"}.get(value, "unknown")


def _step_line(step: dict[str, Any]) -> str:
    if step["kind"] == "tool":
        args = step.get("input") or {}
        what = args.get("component") or (f"{len(args.get('actions', []))} actions" if "actions" in args else "")
        result = step.get("result")
        tail = ""
        if isinstance(result, dict) and "total_cost" in result:
            tail = f" -> {result['total_cost']}"
        return f"{step['name']}({what}){tail}"
    return f"{step['kind']}: {str(step.get('text', ''))[:200]}"


def _rules_table() -> dict[str, Any]:
    r = RULES
    return table(["Action", "Allowed when", "Effect", "Cost"], [
        [ACTION_LABELS["second_source"], "component not single-source",
         f"from +{r['second_source']['lead_weeks']} weeks, up to {r['second_source']['max_share']:.0%} of demand",
         f"EUR {r['second_source']['fixed_eur']:,} + {r['second_source']['premium']:.0%} premium"],
        [ACTION_LABELS["new_supplier"], "same sub-commodity, not hit by the disruption",
         f"from +{r['new_supplier']['lead_weeks_high']} weeks (High) or +{r['new_supplier']['lead_weeks']}, up to 100%",
         f"EUR {r['new_supplier']['fixed_eur_high']:,} (High) or {r['new_supplier']['fixed_eur']:,} + "
         f"{r['new_supplier']['premium']:.0%} premium"],
        [ACTION_LABELS["spot_buy"], "any affected component",
         f"from +{r['spot_buy']['lead_weeks']} week, up to {r['spot_buy']['max_share']:.0%} of demand",
         f"{r['spot_buy']['premium']:.0%} premium"],
        [ACTION_LABELS["air_freight"], "component on a delayed route", "bridges the delay from +1 week",
         f"{r['air_freight']['premium']:.0%} of value shipped"],
        [ACTION_LABELS["buffer"], "before the disruption starts",
         f"up to {r['buffer']['max_weeks']} weeks of stock at {r['buffer']['rate_per_week']} a week",
         f"{r['buffer']['premium']:.0%} of value bought"],
        [ACTION_LABELS["priority"], "always", "order in which assemblies get scarce parts", "none"],
    ])


SUMMARY_SYSTEM = """You write the executive summary of a supply-disruption report for a manufacturer's management. \
Four sentences, plain English, no headings or lists: what happened, what it would cost if nobody acted, what the \
response is and what it costs, and what is still open. Use only the figures given; do not invent numbers."""


def executive_summary(figures: dict[str, Any], client: LLMClient | None) -> tuple[str, str]:
    if client is not None:
        try:
            r = client.create(Request(messages=[{"role": "user", "content": repr(figures)}], system=SUMMARY_SYSTEM,
                                      max_tokens=400))
            if r.text.strip():
                return r.text.strip(), f"Written by {r.model}"
        except LLMError:
            pass
    f = figures
    parts = [f"{f['disruption']} ({f['type'].lower()}) starts in week {f['starts_week']} and lasts {f['weeks']} weeks, "
             f"affecting {f['affected_components']} bought-in components."]
    stop = f"from week {f['first_stop_week']}" if f["first_stop_week"] is not None else "without stopping production"
    parts.append(f"If nothing is done it costs {f['do_nothing_cost']} ({f['do_nothing_lost_revenue']} of lost revenue, "
                 f"{stop}).")
    if f["plan_cost"]:
        parts.append(f"The response plan brings the cost to {f['plan_cost']} with {len(f['accepted_actions'])} accepted "
                     "actions.")
    else:
        parts.append("No response plan has been accepted yet.")
    parts.append(f"{f['open_tasks']} follow-up tasks are open.")
    if f.get("resolved"):
        parts.append(f"It was resolved by {f['resolved']['by']}" + (f": {f['resolved']['note']}" if
                                                                    f["resolved"]["note"] else "."))
    return " ".join(parts), "Template (no model configured)"


# --- portfolio report ----------------------------------------------------------------------------------------------

def portfolio_report(company: Company, pending: dict[str, int], disruptions: list[dict[str, Any]]) -> dict[str, Any]:
    comps = list(company.components.values())
    single = [c for c in comps if c.single_source is True]
    critical_single = sorted([c for c in single if c.high_criticality],
                             key=lambda c: -company.exposure_pct.get(c.id, 0.0))
    spend = sum(c.weekly_value for c in comps) * WEEKS_PER_YEAR
    by_country: dict[str, list] = defaultdict(list)
    for c in comps:
        by_country[company.suppliers[c.supplier].country or "unknown"].append(c)
    top = sorted(company.supplier_impact.items(), key=lambda kv: -kv[1])[:10]
    sections = [
        {"title": "Summary", "blocks": [metrics(
            ("Assembly revenue", eur(company.total_revenue)), ("Bought-in components", str(len(comps))),
            ("Suppliers of them", str(len({c.supplier for c in comps}))),
            ("Single-source", f"{len(single)} ({len(single) / max(len(comps), 1):.0%})"),
            ("Single-source, High criticality", str(len(critical_single))),
            ("Annual spend (bought-in)", eur(spend)))]},
        {"title": "Suppliers with the largest Business Impact", "blocks": [table(
            ["Supplier", "Country", "Components", "Business Impact", "Risk flags"],
            [[f"{company.suppliers[s].name} ({s})", company.suppliers[s].country or "-",
              sum(c.supplier == s for c in comps), eur(v), ", ".join(company.suppliers[s].flags) or "-"]
             for s, v in top]) if top else text("Business Impact needs accepted revenue, margin and recovery times.")]},
        {"title": "Single-source components of High criticality", "blocks": [table(
            ["Component", "Description", "Supplier", "Country", "Business Exposure", "Assemblies"],
            [[c.id, c.description, company.suppliers[c.supplier].name, company.suppliers[c.supplier].country or "-",
              f"{company.exposure_pct.get(c.id, 0.0):.1f}%", len(c.assemblies)] for c in critical_single[:20]])]},
        {"title": "Concentration by supplier country", "blocks": [table(
            ["Country", "Components", "Share of spend", "Single-source", "Highest exposure"],
            [[country, len(cs), f"{sum(c.weekly_value for c in cs) * WEEKS_PER_YEAR / max(spend, 1):.1%}",
              sum(c.single_source is True for c in cs),
              f"{max((company.exposure_pct.get(c.id, 0.0) for c in cs), default=0):.1f}%"]
             for country, cs in sorted(by_country.items(), key=lambda kv: -sum(c.weekly_value for c in kv[1]))[:15]])]},
        {"title": "Data waiting for review", "blocks": [
            metrics(*[(k, str(v)) for k, v in sorted(pending.items())]) if any(pending.values())
            else text("Nothing waits for review.")]},
        {"title": "Active disruptions", "blocks": [table(
            ["Disruption", "Type", "Starts", "Weeks", "Cost if nothing is done"],
            [[x["name"], TYPE_LABELS[x["type"]], f"week {x['start_week']}", x["duration_weeks"],
              eur(x["references"]["do_nothing"]["outcome"]["total_cost"]) if x.get("references") else "not assessed"]
             for x in disruptions]) if disruptions else text("No active disruptions.")]},
    ]
    return {"kind": "portfolio", "id": "portfolio", "title": "Portfolio risk report",
            "subtitle": f"{len(company.suppliers)} suppliers, {len(comps)} bought-in components",
            "generated_at": _now(), "sections": sections}


# --- scorecard -----------------------------------------------------------------------------------------------------

def scorecard(store: OpsStore) -> dict[str, Any]:
    runs = store.runs()
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for r in runs:
        groups[(r["provider"], r["model"])].append(r)
    rows = []
    for (provider, model), rs in sorted(groups.items()):
        done = [r for r in rs if r["status"] == "done"]
        skills = [r["skill"] for r in done if r["skill"] is not None]
        reductions = []
        for r in done:
            refs = store.disruption(r["disruption_id"]).get("references")
            if refs and r["outcome"]:
                base = refs["do_nothing"]["outcome"]["total_cost"]
                if base > 0:
                    reductions.append(1 - r["outcome"]["total_cost"] / base)
        rows.append([model, provider, len(rs), len(done), _skill(statistics.mean(skills) if skills else None),
                     _pct(statistics.mean(reductions) if reductions else None),
                     f"${statistics.mean(r['cost_usd'] for r in rs):.4f}" if rs else "-"])
    per_disruption = []
    for d in store.disruptions(include_archived=True):
        refs = d.get("references")
        mine = [r for r in runs if r["disruption_id"] == d["id"] and r["status"] == "done"]
        if not mine:
            continue
        per_disruption.append([d["name"], _skill(refs["playbook"]["skill"]) if refs else "-",
                               ", ".join(f"{r['model']}: {_skill(r['skill'])}" for r in mine[:4])])
    sections = [
        {"title": "Agent runs by model", "blocks": [
            table(["Model", "Provider", "Runs", "Finished", "Mean skill", "Mean cost reduction", "Model cost per run"],
                  rows) if rows else text("No agent runs yet."),
            text("Skill 1.0 matches the optimiser reference; 0 is doing nothing. Cost reduction is against doing "
                 "nothing.")]},
        {"title": "By disruption", "blocks": [
            table(["Disruption", "Playbook skill", "Agent runs (latest first)"], per_disruption)
            if per_disruption else text("No finished runs yet.")]},
    ]
    network = shockbench.network_results()
    if network:
        sections.append({"title": "ShockBench network benchmark", "blocks": [
            table(["Policy", "Resilience Skill Score", "Kind"],
                  [[r["policy"], f"{r['score']:.3f}", r["kind"]] for r in network["rows"]]),
            text(f"ShockBench-Flow task {network['task']}, {network['split']}: the agent runs a 12-node semiconductor "
                 "network for 26 weeks through strait closures, export bans, tariffs and plant outages. 0 is the naive "
                 "rule, 1 the clairvoyant plan; one standard error is about 0.16 to 0.20 on 20 episodes.")]})
    return {"kind": "scorecard", "id": "scorecard", "title": "Agent scorecard",
            "subtitle": f"{len(runs)} agent runs", "generated_at": _now(), "sections": sections}


def episode_sections(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The episode benchmark's result sets (see `scgraph ops episodes-bench`): pooled scores and harm levels."""
    if not summaries:
        return []
    ordered = sorted(summaries, key=lambda s: s["date"], reverse=True)

    def rss(s: dict[str, Any], who: str) -> str:
        v, ci = s["rss"][who], s["rss_interval"][who]
        return "-" if v is None else f"{v:.2f}" + (f" ({ci[0]:.2f}-{ci[1]:.2f})" if ci else "")

    sets = [[s["name"], f"{s['model']}, {s['agent_version']} ({s['variant']})", s["split"],
             f"{s['episodes']} x {s['runs_per_episode']}", rss(s, "agent"), rss(s, "replanner"), rss(s, "playbook"),
             f"{s['turns_per_episode']:.1f}", str(s["failed_turns"]), f"${s['usd_per_episode']:.4f}"] for s in ordered]
    money = [[s["name"]] + [_eur(s["cost_total"][k]) for k in ("do_nothing", "playbook", "replanner", "agent",
                                                              "optimiser")] for s in ordered]
    head = ordered[0]
    levels = [[str(level), str(b["episodes"]), _skill(b["agent"]), _skill(b["replanner"]), _skill(b["playbook"]),
               _eur(b["do_nothing"]), _eur(b["optimiser"])] for level, b in head["by_level"].items()]
    return [
        {"title": "ShockBench episodes", "note": "pooled Resilience Skill Score with a 90% bootstrap interval",
         "blocks": [
             table(["Result set", "Model and agent", "Split", "Episodes x runs", "Agent", "Re-planner", "Playbook",
                    "Reviews per episode", "Failed reviews", "Model cost per episode"], sets),
             text("A year of events from ShockBench-Flow's generator (tariffs, sanctions, strait closures, energy "
                  "shocks, decoy announcements) mapped onto our suppliers and lanes. The planner reviews the plan "
                  "whenever news arrives and sees only what is known that week. Score as ShockBench: 0 = doing "
                  "nothing, 1 = the plan made knowing the whole year, pooled over four harm levels with weights "
                  "50/30/15/5%. The re-planner is the optimiser run on each week's news; the playbook reacts when "
                  "a part is hit.")]},
        {"title": "Episodes: total cost", "note": "EUR over the episodes; the agent's is the mean over its runs",
         "blocks": [table(["Result set", "Do nothing", "Playbook", "Re-planner", "Agent", "Hindsight optimiser"],
                          money)]},
        {"title": f"Episodes by harm level: {head['name']}", "note": "levels cut at the 50/80/95% quantiles of the "
                                                                      "do-nothing cost", "blocks": [
            table(["Level", "Episodes", "Agent", "Re-planner", "Playbook", "Do nothing", "Hindsight optimiser"],
                  levels)]},
    ]


def benchmark_report(summaries: list[dict[str, Any]],
                     episode_summaries: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The committed agent benchmark result sets side by side (see `scgraph ops bench`), then the episode
    benchmark's (`scgraph ops episodes-bench`)."""
    episodes = episode_sections(episode_summaries or [])
    if not summaries and episodes:
        return {"kind": "benchmark", "id": "benchmark", "title": "Agent benchmark",
                "subtitle": f"{len(episode_summaries)} episode result sets", "generated_at": _now(),
                "sections": episodes}
    if not summaries:
        sections = [{"title": "No results yet", "blocks": [text(
            "Run `uv run scgraph ops bench --split test` to benchmark the agent on the held-out scenario suite; "
            "result sets are written to benchmarks/results/ and shown here.")]}]
        return {"kind": "benchmark", "id": "benchmark", "title": "Agent benchmark", "subtitle": "no result sets",
                "generated_at": _now(), "sections": sections}
    # newest first; on the same date the full agent before its ablations
    ordered = sorted(summaries, key=lambda s: (s["date"], s["variant"] == "full", s["agent_version"]), reverse=True)

    sets = []
    for s in ordered:
        blocks = [(s["split"], s["overall"])] + ([(f"{x} only", b) for x, b in s["by_split"].items()]
                                                 if s.get("by_split") else [])
        for split, b in blocks:
            lo, hi = b["skill_ci95"]
            sets.append([s["name"], f"{s['model']}, {s['agent_version']} ({s['variant']})", split,
                         f"{b['cases']} x {s['runs_per_case']}", f"{b['skill_mean']:.2f}", f"{lo:.2f}-{hi:.2f}",
                         _skill(b["playbook_skill_mean"]), _pct(b["valid_rate"]), _pct(b["at_least_playbook"]),
                         f"${b['usd_per_run']:.4f}"])
    types = []
    for s in ordered:
        types += [t for t in s["by_type"] if t not in types]
    by_type = [[s["name"]] + [f"{s['by_type'][t]['skill_mean']:.2f}" if t in s["by_type"] else "-" for t in types]
               + [_skill(s["overall"]["playbook_skill_mean"])] for s in ordered]
    money = [[s["name"], _eur(s["overall"]["cost_do_nothing"]), _eur(s["overall"]["cost_playbook"]),
              _eur(s["overall"]["cost_agent"]), _eur(s["overall"]["cost_optimiser"])] for s in ordered]
    head = next((s for s in ordered if s["variant"] == "full"), ordered[0])
    cases = [[c["case"], TYPE_LABELS.get(c["type"], c["type"]), _eur(c["do_nothing"]), _eur(c["optimiser"]),
              _skill(c["playbook_skill"]),
              ", ".join(f"{k:.2f}" + ("" if v else "*") for k, v in zip(c["skill"], c["valid"], strict=True))]
             for c in head["cases"]]
    sections = [
        {"title": "Result sets", "note": "mean skill over cases with a 95% bootstrap interval", "blocks": [
            table(["Result set", "Model and agent", "Split", "Cases x runs", "Agent skill", "95% interval",
                   "Playbook skill", "Valid plans", "At least playbook", "Model cost per run"], sets),
            text("Skill is the share of the optimiser's saving against doing nothing that a plan achieves: 1 matches "
                 "the optimiser, 0 is doing nothing, below 0 costs more than doing nothing (counted as at most -1 in "
                 "the means). Test cases are held out: the agent was developed on the dev split only.")]},
        {"title": "Skill by disruption type", "note": "all cases of each result set", "blocks": [
            table(["Result set"] + [TYPE_LABELS.get(t, t) for t in types] + ["Playbook, all"], by_type)]},
        {"title": "Total cost over the cases", "note": "EUR; the agent's is the mean over its runs", "blocks": [
            table(["Result set", "Do nothing", "Playbook", "Agent", "Optimiser"], money)]},
        {"title": f"Cases: {head['name']}", "note": "* no valid plan, scored as doing nothing", "blocks": [
            table(["Case", "Type", "Do nothing", "Optimiser", "Playbook skill", "Agent skill per run"], cases)]},
    ] + episodes
    subtitle = f"{len(summaries)} result sets on the scenario suite" + (
        f", {len(episode_summaries)} on ShockBench episodes" if episodes else "")
    return {"kind": "benchmark", "id": "benchmark", "title": "Agent benchmark", "subtitle": subtitle,
            "generated_at": _now(), "sections": sections}


def _eur(value: float) -> str:
    return f"EUR {value / 1e6:,.2f}M"


# --- markdown ------------------------------------------------------------------------------------------------------

def to_markdown(report: dict[str, Any]) -> str:
    out = [f"# {report['title']}", "", f"_{report.get('subtitle', '')}. Generated {report['generated_at']}._", ""]
    for section in report["sections"]:
        out += [f"## {section['title']}", ""]
        if section.get("note"):
            out += [f"_{section['note']}_", ""]
        for block in section["blocks"]:
            out += _block_md(block) + [""]
    return "\n".join(out).rstrip() + "\n"


def _cell(value: Any) -> str:
    return str(value if value is not None else "-").replace("|", "\\|").replace("\n", " ")


def _block_md(block: dict[str, Any]) -> list[str]:
    kind = block["kind"]
    if kind == "text":
        return [block["text"]] if block["text"] else []
    if kind == "metrics":
        return [f"- **{i['label']}**: {i['value']}" for i in block["items"]]
    if kind == "list":
        return [f"- {_cell(i)}" for i in block["items"]]
    if kind == "table":
        if not block["rows"]:
            return ["_None._"]
        head = "| " + " | ".join(block["columns"]) + " |"
        sep = "|" + "|".join("---" for _ in block["columns"]) + "|"
        return [head, sep] + ["| " + " | ".join(_cell(c) for c in row) + " |" for row in block["rows"]]
    if kind == "chart":
        series = block["series"]
        weeks = max((len(s["values"]) for s in series), default=0)
        lines = [f"**{block['title']}** ({block.get('unit', '')})", "",
                 "| Week | " + " | ".join(s["name"] for s in series) + " |",
                 "|---|" + "|".join("---" for _ in series) + "|"]
        for w in range(weeks):
            vals = [eur(s["values"][w]) if w < len(s["values"]) else "-" for s in series]
            if any(s["values"][w] for s in series if w < len(s["values"])):
                lines.append(f"| {w} | " + " | ".join(vals) + " |")
        return lines
    return []


__all__ = ["describe_action", "disruption_report", "portfolio_report", "scorecard", "to_markdown"]
