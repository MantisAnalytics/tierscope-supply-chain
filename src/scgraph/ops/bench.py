"""The agent benchmark: N runs of the agent per suite case, scored on the simulator against the references.

Every worker thread makes its own client with the response cache off, so repeated runs are separate conversations.
A run that fails or submits nothing counts as doing nothing (skill 0). A budget or credentials error stops the
benchmark, and a provider failure (a timeout or dropped connection) starts the run over, instead of being counted
against the agent.
"""

from __future__ import annotations

import gzip
import json
import random
import statistics
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scgraph.llm import LLMClient
from scgraph.ops.agent import AGENT_VERSION, VARIANTS, run_agent
from scgraph.ops.company import Company
from scgraph.ops.scenarios import TYPE_LABELS
from scgraph.ops.simulate import MIN_GAIN_EUR, Simulator, skill
from scgraph.ops.suite import Case, fingerprint

RESULTS_DIR = Path("benchmarks/results")
SKILL_FLOOR = -1.0  # a plan far worse than doing nothing counts as -1 in the averages
BOOTSTRAP = 1000
STEP_RESULT_CHARS = 1500  # tool results kept per step in the stored runs
STOPPING_ERRORS = ("BudgetExceededError", "MissingCredentialsError", "PriceMissingError", "PolicyError",
                   "BillingError")
# provider failures (timeouts, dropped connections, overload) say nothing about the agent: the run starts over
RETRIED_ERRORS = ("ProviderError",)
RUN_ATTEMPTS = 3


class BenchAborted(RuntimeError):
    pass


def references(company: Company, cases: list[Case]) -> dict[str, dict[str, Any]]:
    """Do-nothing, playbook and optimiser cost per case."""
    out = {}
    for case in cases:
        refs = Simulator(company, case.disruption).references()
        out[case.id] = {name: {"plan": r["plan"], "cost": r["outcome"]["total_cost"]} for name, r in refs.items() if name != "version"}
    return out


def rescore(records: list[dict[str, Any]], refs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The same runs scored against new references (the plans' costs do not change; only the yardstick does)."""
    out = []
    for r in records:
        nothing, playbook, optimiser = (refs[r["case"]][k]["cost"] for k in ("do_nothing", "playbook", "optimiser"))
        out.append({**r, "do_nothing": nothing, "playbook": playbook, "optimiser": optimiser,
                    "cost": r["cost"] if r["valid"] else nothing,
                    "skill": skill(nothing, optimiser, r["cost"] if r["valid"] else nothing) or 0.0,
                    "playbook_skill": skill(nothing, optimiser, playbook) or 0.0})
    return out


def run_case(company: Company, case: Case, client: LLMClient, refs: dict[str, Any], variant: str = "full",
             index: int = 0) -> dict[str, Any]:
    started = time.time()
    for _ in range(RUN_ATTEMPTS):
        result = run_agent(company, case.disruption, client, variant=variant)
        if not (result.error and result.error.startswith(RETRIED_ERRORS)):
            break
    if result.error and result.error.startswith(STOPPING_ERRORS):
        raise BenchAborted(result.error)
    nothing, playbook, optimiser = (refs[k]["cost"] for k in ("do_nothing", "playbook", "optimiser"))
    valid = result.status == "done"
    cost = Simulator(company, case.disruption).run(result.plan).total_cost if valid else nothing
    tools = [s for s in result.steps if s["kind"] == "tool"]
    return {
        "case": case.id, "split": case.split, "type": case.disruption.type, "family": case.family, "run": index,
        "variant": variant, "agent_version": AGENT_VERSION, "provider": client.provider, "model": client.default_model,
        "valid": valid, "error": result.error, "plan": result.plan, "summary": result.summary,
        "cost": cost, "skill": skill(nothing, optimiser, cost) or 0.0,
        "do_nothing": nothing, "playbook": playbook, "optimiser": optimiser,
        "playbook_skill": skill(nothing, optimiser, playbook) or 0.0,
        "tool_calls": len(tools), "simulations": sum(1 for s in tools if s.get("name") == "simulate_plan"),
        "refused_submissions": sum(1 for s in tools if s.get("name") == "submit_plan"
                                   and isinstance(s.get("result"), dict) and not s["result"].get("accepted")),
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens, "cost_usd": result.cost_usd,
        "seconds": round(time.time() - started, 1), "steps": result.steps,
    }


def run_bench(company: Company, cases: list[Case], client_factory: Callable[[], LLMClient], *, runs: int = 3,
              workers: int = 4, variant: str = "full", on_record: Callable[[dict[str, Any]], None] | None = None,
              refs: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {', '.join(VARIANTS)}")
    refs = refs or references(company, cases)
    local = threading.local()
    stop = threading.Event()

    def work(case: Case, index: int) -> dict[str, Any] | None:
        if stop.is_set():
            return None
        if not hasattr(local, "client"):
            local.client = client_factory()
        try:
            return run_case(company, case, local.client, refs[case.id], variant, index)
        except BenchAborted:
            stop.set()
            raise

    records: list[dict[str, Any]] = []
    aborted: BenchAborted | None = None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, case, i) for i in range(runs) for case in cases]
        for future in as_completed(futures):
            try:
                record = future.result()
            except BenchAborted as e:
                aborted = aborted or e
                continue
            if record is not None:
                records.append(record)
                if on_record:
                    on_record(record)
    if aborted:
        raise BenchAborted(f"{aborted} ({len(records)} runs finished before it stopped)")
    return sorted(records, key=lambda r: (r["case"], r["run"]))


# --- summary ---------------------------------------------------------------------------------------------------------

def _floor(x: float) -> float:
    return max(SKILL_FLOOR, x)


def _bootstrap(per_case: list[float], seed: int = 0) -> tuple[float, float]:
    if len(per_case) < 2:
        return (per_case[0], per_case[0]) if per_case else (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(per_case, k=len(per_case))) for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def _block(records: list[dict[str, Any]]) -> dict[str, Any]:
    cases = sorted({r["case"] for r in records})
    per_case = [statistics.fmean(_floor(r["skill"]) for r in records if r["case"] == c) for c in cases]
    playbook = {r["case"]: _floor(r["playbook_skill"]) for r in records}
    first = {r["case"]: r for r in records}
    low, high = _bootstrap(per_case)
    return {
        "cases": len(cases), "runs": len(records),
        "skill_mean": round(statistics.fmean(per_case), 4) if per_case else None,
        "skill_ci95": [round(low, 4), round(high, 4)],
        "skill_median": round(statistics.median(_floor(r["skill"]) for r in records), 4) if records else None,
        "playbook_skill_mean": round(statistics.fmean(playbook.values()), 4) if playbook else None,
        "valid_rate": round(sum(r["valid"] for r in records) / len(records), 4) if records else None,
        "at_least_playbook": round(sum(r["cost"] <= r["playbook"] + MIN_GAIN_EUR for r in records) / len(records), 4)
        if records else None,
        "near_optimiser": round(sum(r["skill"] >= 0.95 for r in records) / len(records), 4) if records else None,
        # money over the cases (mean over runs per case), in EUR
        "cost_agent": round(sum(statistics.fmean(r["cost"] for r in records if r["case"] == c) for c in cases)),
        "cost_playbook": round(sum(first[c]["playbook"] for c in cases)),
        "cost_optimiser": round(sum(first[c]["optimiser"] for c in cases)),
        "cost_do_nothing": round(sum(first[c]["do_nothing"] for c in cases)),
        "usd_per_run": round(statistics.fmean(r["cost_usd"] for r in records), 5) if records else None,
        "tokens_per_run": round(statistics.fmean(r["input_tokens"] + r["output_tokens"] for r in records))
        if records else None,
        "tool_calls_per_run": round(statistics.fmean(r["tool_calls"] for r in records), 1) if records else None,
    }


def summarize(records: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    types = sorted({r["type"] for r in records}, key=lambda t: list(TYPE_LABELS).index(t))
    per_case = []
    for c in sorted({r["case"] for r in records}):
        rs = [r for r in records if r["case"] == c]
        per_case.append({"case": c, "type": rs[0]["type"], "runs": len(rs),
                         "skill": [round(r["skill"], 4) for r in rs], "valid": [r["valid"] for r in rs],
                         "playbook_skill": round(rs[0]["playbook_skill"], 4), "do_nothing": round(rs[0]["do_nothing"]),
                         "optimiser": round(rs[0]["optimiser"]), "cost": [round(r["cost"]) for r in rs]})
    splits = [x for x in ("test", "dev") if any(r["split"] == x for r in records)]
    return {**meta, "overall": _block(records),
            "by_split": {x: _block([r for r in records if r["split"] == x]) for x in splits} if len(splits) > 1 else {},
            "by_type": {t: _block([r for r in records if r["type"] == t]) for t in types},
            "cases": per_case, "usd_total": round(sum(r["cost_usd"] for r in records), 4)}


def git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,
                             check=False)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                               text=True, timeout=5, check=False).stdout.strip()
        return out.stdout.strip() + ("+dirty" if dirty else "") if out.returncode == 0 else None
    except OSError:
        return None


def bench_meta(company: Company, suite: dict[str, Any], split: str, runs: int, variant: str,
               client: LLMClient, name: str) -> dict[str, Any]:
    return {"name": name, "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_commit(),
            "agent_version": AGENT_VERSION, "prompt_version": VARIANTS[variant].version, "variant": variant,
            "provider": client.provider,
            "model": client.default_model, "split": split, "runs_per_case": runs, "suite_seed": suite.get("seed"),
            "suite_generator": suite.get("generator_version"), "graph": fingerprint(company)}


def _compact(step: dict[str, Any]) -> dict[str, Any]:
    if "result" not in step:
        return step
    text = step["result"] if isinstance(step["result"], str) else json.dumps(step["result"], ensure_ascii=False)
    return {**step, "result": text if len(text) <= STEP_RESULT_CHARS else text[:STEP_RESULT_CHARS] + " ...(cut)"}


def write_results(out_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    """runs.jsonl.gz (tool results in the steps cut short) and the summary as JSON and Markdown."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_dir / "runs.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps({**r, "steps": [_compact(s) for s in r.get("steps", [])]}, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    (out_dir / "summary.md").write_text(summary_markdown(summary))


def load_runs(out_dir: Path) -> list[dict[str, Any]]:
    path = out_dir / "runs.jsonl.gz"
    if not path.is_file():
        raise FileNotFoundError(f"no runs.jsonl.gz in {out_dir}")
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_summaries(root: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    out = []
    for path in sorted(root.glob("*/summary.json")):
        try:
            out.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


# --- Markdown --------------------------------------------------------------------------------------------------------

def _m(eur: float) -> str:
    return f"{eur / 1e6:,.2f}"


def _skill(b: dict[str, Any]) -> str:
    lo, hi = b["skill_ci95"]
    return f"{b['skill_mean']:.2f} [{lo:.2f}, {hi:.2f}]"


def summary_markdown(s: dict[str, Any]) -> str:
    o = s["overall"]
    lines = [f"# Agent benchmark: {s['name']}", "",
             (f"{s['provider']} / {s['model']}, agent {s['agent_version']} ({s['variant']}), split {s['split']}, "
              f"{o['cases']} cases x {s['runs_per_case']} runs, {s['date']}, commit {s['commit']}. "
              f"Model cost ${s['usd_total']:.2f}."
              + (f" Scored again against the current references on {s['rescored']}." if s.get("rescored") else "")
              + (f" Note: {s['note']}" if s.get("note") else "")),
             "",
             ("Skill: share of the optimiser's saving against doing nothing (1 = optimiser, 0 = doing nothing; "
              f"runs below {SKILL_FLOOR:g} count as {SKILL_FLOOR:g}). Mean over cases with a 95% bootstrap interval."),
             "",
             "| | Cases | Runs | Agent skill | Playbook skill | Valid plans | At least playbook | Near optimiser |",
             "|---|---|---|---|---|---|---|---|"]
    split_rows = [(f"{x.title()} split" + (" (held out)" if x == "test" else ""), b)
                  for x, b in s.get("by_split", {}).items()]
    for label, b in [("All", o)] + split_rows + [(TYPE_LABELS[t], b) for t, b in s["by_type"].items()]:
        lines.append(f"| {label} | {b['cases']} | {b['runs']} | {_skill(b)} | {b['playbook_skill_mean']:.2f} | "
                     f"{b['valid_rate']:.0%} | {b['at_least_playbook']:.0%} | {b['near_optimiser']:.0%} |")
    lines += ["", "Total cost over the cases (EUR millions; the agent's is the mean over its runs):", "",
              "| Do nothing | Playbook | Agent | Optimiser |", "|---|---|---|---|",
              (f"| {_m(o['cost_do_nothing'])} | {_m(o['cost_playbook'])} | {_m(o['cost_agent'])} | "
               f"{_m(o['cost_optimiser'])} |"), "",
              f"Per run: ${o['usd_per_run']:.4f}, {o['tokens_per_run']:,} tokens, {o['tool_calls_per_run']} tool calls.",
              "", "| Case | Type | Do nothing | Optimiser | Playbook skill | Agent skill per run |",
              "|---|---|---|---|---|---|"]
    for c in s["cases"]:
        runs = ", ".join(f"{k:.2f}" + ("" if v else "*") for k, v in zip(c["skill"], c["valid"], strict=True))
        lines.append(f"| {c['case']} | {TYPE_LABELS[c['type']]} | {_m(c['do_nothing'])} | {_m(c['optimiser'])} | "
                     f"{c['playbook_skill']:.2f} | {runs} |")
    lines += ["", "\\* no valid plan (scored as doing nothing)."]
    return "\n".join(lines) + "\n"


def compare_markdown(summaries: list[dict[str, Any]]) -> str:
    if not summaries:
        return "No benchmark results.\n"
    types = []
    for s in summaries:
        types += [t for t in s["by_type"] if t not in types]
    head = "| | " + " | ".join(f"{s['name']}" for s in summaries) + " |"
    lines = ["# Agent benchmark comparison", "", head, "|---|" + "---|" * len(summaries)]

    def row(label: str, cell: Callable[[dict[str, Any]], str]) -> None:
        lines.append(f"| {label} | " + " | ".join(cell(s) for s in summaries) + " |")

    row("Model", lambda s: f"{s['model']}")
    row("Agent", lambda s: f"{s['agent_version']} ({s['variant']})")
    row("Split, cases x runs", lambda s: f"{s['split']}, {s['overall']['cases']} x {s['runs_per_case']}")
    row("**Skill, all**", lambda s: f"**{_skill(s['overall'])}**")
    row("Skill, test split (held out)", lambda s: _skill(s["by_split"]["test"]) if "test" in s.get("by_split", {})
        else (_skill(s["overall"]) if s["split"] == "test" else "-"))
    row("Skill, dev split", lambda s: _skill(s["by_split"]["dev"]) if "dev" in s.get("by_split", {})
        else (_skill(s["overall"]) if s["split"] == "dev" else "-"))
    row("Playbook skill", lambda s: f"{s['overall']['playbook_skill_mean']:.2f}")
    for t in types:
        row(f"Skill, {TYPE_LABELS[t].lower()}", lambda s, t=t: _skill(s["by_type"][t]) if t in s["by_type"] else "-")
    row("Valid plans", lambda s: f"{s['overall']['valid_rate']:.0%}")
    row("At least playbook", lambda s: f"{s['overall']['at_least_playbook']:.0%}")
    row("Total cost, EUR M (playbook / optimiser)", lambda s: f"{_m(s['overall']['cost_agent'])} "
        f"({_m(s['overall']['cost_playbook'])} / {_m(s['overall']['cost_optimiser'])})")
    row("USD per run", lambda s: f"{s['overall']['usd_per_run']:.4f}")
    row("Tool calls per run", lambda s: f"{s['overall']['tool_calls_per_run']}")
    return "\n".join(lines) + "\n"
