"""The episode benchmark: the planner runs a year of ShockBench events on our graph, reviewing the plan as news comes.

An episode (`scgraph.ops.shock_episodes`) is 52 weeks of events and decoys. The planner gets a turn in each review
week (`review_weeks`: a week where an event is announced or starts, or outlasts its estimate; at most one review
every `REVIEW_GAP` weeks) and sees only what is known then: events announced or in force, with the median duration of
their kind (doubled each time an event outlasts it) until they end, and decoys that look like any announcement. Actions that have started stay; new ones start in the review week or
later. The plan in force at the end is scored on what really happened.

Scoring follows ShockBench's Resilience Skill Score (55)-(56): per episode g = J(do nothing) - J(plan) and
D = J(do nothing) - J(hindsight optimiser), pooled over four harm levels with ShockBench's weights,
RSS = sum_s p_s mean_s(g) / sum_s p_s mean_s(D). 0 is doing nothing, 1 the plan made knowing the whole year.
Harm is the do-nothing cost on our graph; the levels are its 50/80/95% quantiles over the candidate episodes, as
ShockBench cuts its own (ShockBench's harm H(omega) is about its network, not ours).

References: do nothing; the playbook reacting when each component is hit; the informed re-planner (the optimiser on
each review week's view, ShockBench's mpc_det counterpart); the hindsight optimiser (1, clairvoyant).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from scgraph.agent.gate import Gate, eur
from scgraph.agent.loop import LoopResult
from scgraph.agent.profiles import run_turn
from scgraph.agent.tools import Turn
from scgraph.agent.world import BaseWorld
from scgraph.llm import LLMClient
from scgraph.ops.agent import AGENT_VERSION, VARIANTS
from scgraph.ops.company import Company
from scgraph.ops.scenarios import Compound, Effects, effects
from scgraph.ops.shock_episodes import (
    EVENT_TYPES,
    HORIZON,
    Episode,
    Priors,
    episodes_of,
)
from scgraph.ops.simulate import (
    RULES,
    ActionError,
    Simulator,
    action_key,
    check,
    describe_action,
    normalize,
)
from scgraph.ops.suite import fingerprint

SUITE_PATH = Path("benchmarks/episode_suite.json")
RESULTS_DIR = Path("benchmarks/episode_results")
# 2: economic and operational events only (shock_episodes.EVENT_TYPES); 3: reviews when an event outlasts its estimate
SUITE_VERSION = 3
REVIEW_GAP = 2  # weeks between reviews at least
HARM_QUANTILES = (0.50, 0.80, 0.95)  # ShockBench's cut points (strata.py)
LEVEL_WEIGHTS = (0.50, 0.30, 0.15, 0.05)  # ShockBench's p_s (rss.py)
DEV_PER_LEVEL, TEST_PER_LEVEL = 5, 25
BOOTSTRAP, LEVEL = 2000, 0.90  # ShockBench's interval: percentile bootstrap within levels
STOPPING_ERRORS = ("BudgetExceededError", "MissingCredentialsError", "PriceMissingError", "PolicyError",
                   "BillingError")
RETRIED_ERRORS = ("ProviderError",)
RETRY_WAITS = (0, 10, 30, 90)  # seconds before each attempt at a review that failed on the provider
TURN_ATTEMPTS = len(RETRY_WAITS)
STEP_RESULT_CHARS = 1500


class BenchAborted(RuntimeError):
    def __init__(self, message: str, records: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.records = records or []  # the episodes finished before it stopped


# --- simulation with a plan in force ---------------------------------------------------------------------------------

def with_components(company: Company, d: Compound, cids: set[str]) -> Effects:
    """The disruption's effects, plus unaffected entries for components the plan acts on, so that actions taken on
    a threat that did not come true are still paid for."""
    eff = effects(company, d)
    for cid in sorted(cids - set(eff.reasons)):
        if cid in company.components:
            eff.capacity[cid] = np.ones(eff.horizon)
            eff.reasons[cid] = "not affected"
    return eff


class LockedSimulator(Simulator):
    """Simulates on a review week's view with the started actions fixed: they are always in the plan, and any other
    action starts in the review week or later (an earlier start week is moved to it)."""

    def __init__(self, company: Company, d: Compound, locked: list[dict[str, Any]], week: int) -> None:
        cids = {a["component"] for a in locked if a.get("component")}
        super().__init__(company, d, with_components(company, d, cids))
        self.locked, self.week = [normalize(a) for a in locked], week

    def plan_of(self, actions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        fixed = {action_key(a) for a in self.locked}
        out = list(self.locked)
        for raw in actions or []:
            try:
                a = normalize(raw)
            except ActionError:
                out.append(raw)  # run() refuses and lists it
                continue
            if action_key(a) in fixed:
                continue
            if a["type"] != "priority" and a.get("start_week", 0) < self.week:
                a = {**a, "start_week": self.week}
            out.append(a)
        return out

    def run(self, actions=None, *, strict=False):
        return super().run(self.plan_of(actions), strict=strict)


def score_plan(company: Company, episode: Episode, plan: list[dict[str, Any]]) -> float:
    truth = episode.truth()
    cids = {a["component"] for a in plan if a.get("component")}
    return Simulator(company, truth, with_components(company, truth, cids)).run(plan).total_cost


def started(plan: list[dict[str, Any]], week: int) -> list[dict[str, Any]]:
    return [a for a in plan if a["type"] != "priority" and a.get("start_week", 0) < week]


def review_weeks(episode: Episode, priors: Priors | None = None, gap: int = REVIEW_GAP) -> list[int]:
    """Weeks where an event is announced or starts, at most one review every `gap` weeks; with `priors`, also weeks
    where an event in force outlasts its estimate, where they keep `gap` from every other review (news comes first:
    an overrun check never displaces a review of news)."""
    out = _spaced(sorted({it.known_week for it in episode.items}
                         | {it.start_week for it in episode.items if it.kind == "event"}), gap)
    if priors is not None:
        for w in sorted({w for it in episode.items for w in episode.overrun_weeks(it, priors)}):
            if w < HORIZON and all(abs(w - r) >= gap for r in out):
                out = sorted(out + [w])
    return out


def _spaced(weeks: list[int], gap: int) -> list[int]:
    out: list[int] = []
    for w in weeks:
        if w < HORIZON and (not out or w - out[-1] >= gap):
            out.append(w)
    return out


# --- the world the planner works in ----------------------------------------------------------------------------------

class EpisodeWorld(BaseWorld):
    """One episode at one review week: the view as the disruption (id 0), the plan in force, writes kept in memory.
    The gate is in human mode, so no spending limit applies; the benchmark applies the submitted plan itself."""

    name = "episode"

    def __init__(self, company: Company, episode: Episode, priors: Priors) -> None:
        self._company, self.episode, self.priors = company, episode, priors
        self.gate = Gate()
        self.week = 0
        self.plan: list[dict[str, Any]] = []  # normalised actions with "reason"
        self.summary = ""
        self.versions = 0
        self.submitted: list[dict[str, Any]] = []

    def now(self) -> int:
        return self.week

    def company(self) -> Company:
        return self._company

    def view(self, did: int) -> Compound:
        return self.episode.view(self.week, self.priors)

    def simulator(self, did: int) -> LockedSimulator:
        return LockedSimulator(self._company, self.view(did), started(self.plan, self.week), self.week)

    def submit(self, write):
        return self.gate.submit(write, self)

    def store(self, write) -> dict[str, Any]:
        reasons = {}
        for a in write.data["actions"]:
            try:
                reasons[action_key(normalize(a))] = a.get("reason", "")
            except ActionError:
                continue
        sim = self.simulator(0)
        plan = []
        for a in sim.plan_of(write.data["actions"]):
            old = next((p for p in self.plan if action_key(p) == action_key(a)), {})
            plan.append({**a, "reason": reasons.get(action_key(a)) or old.get("reason", "")})
        self.plan, self.summary = plan, write.data.get("summary", "")
        self.versions += 1
        self.submitted.append({"week": self.week, "plan": plan, "summary": self.summary})
        kept = len(started(plan, self.week))
        return {"block": {"type": "plan", "ref": self.versions},
                "message": f"Plan version {self.versions} in force from week {self.week}"
                           + (f" ({kept} started action(s) kept)" if kept else "") + "."}

    def apply(self, write, stored) -> None:
        pass

    def plan_context(self) -> dict[str, Any] | None:
        if not self.versions:
            return None
        return {"version": self.versions, "summary": self.summary, "stale": False, "actions": [
            {"text": describe_action(self._company, a), "reason": a.get("reason", ""),
             "status": "started (fixed)" if a in started(self.plan, self.week) else "accepted"} for a in self.plan]}


def review_message(episode: Episode, week: int, last: int | None, priors: Priors | None = None) -> str:
    new = [it for it in episode.visible(week)
           if last is None or it.known_week > last or (it.kind == "event" and last < it.start_week <= week)]
    lines = [f"- {it.title} ({episode.status(it, week)})" for it in new]
    if priors is not None and last is not None:
        lines += [f"- {it.title} (still in force after {week - it.start_week} weeks, past its estimate)"
                  for it in episode.visible(week) if it not in new and episode.status(it, week) == "in force"
                  and any(last < w <= week for w in episode.overrun_weeks(it, priors))]
    rules = (f"Actions that have started (marked fixed in the context) stay in the plan and are part of every "
             f"simulation, so an empty action list means the started actions only; any action you add starts in week "
             f"{week} or later (an earlier start week is moved to week {week}). ") if last is not None else ""
    return (f"Week {week} review. " + ("New since the last review:\n" + "\n".join(lines) + "\n" if lines else "")
            + rules + f"Submit the plan for the rest of the {HORIZON} weeks: what to keep, add or drop.")


def run_episode(company: Company, episode: Episode, priors: Priors, client: LLMClient, variant: str = "full",
                weeks: list[int] | None = None) -> dict[str, Any]:
    """The planner through the episode's review weeks; the plan in force at the end, scored on the truth."""
    profile = VARIANTS[variant]
    world = EpisodeWorld(company, episode, priors)
    turns, steps = [], []
    last = None
    for week in weeks if weeks is not None else review_weeks(episode, priors):
        world.week = week
        turn = Turn(world, author=f"agent:{client.default_model}", disruption_id=0, plan=world.plan_context())
        message = review_message(episode, week, last, priors)
        for wait in RETRY_WAITS:
            time.sleep(wait)
            before = world.versions
            loop: LoopResult = run_turn(profile, turn, client, [{"role": "user", "content": message}])
            if not (loop.error and loop.error.startswith(RETRIED_ERRORS)) or world.versions != before:
                break
        if loop.error and loop.error.startswith(STOPPING_ERRORS):
            raise BenchAborted(loop.error)
        if loop.error and loop.error.startswith(RETRIED_ERRORS) and not loop.steps[:-1]:
            # the provider failed on every attempt before the model said anything: the connection is down, and
            # scoring on would record a failed review for every episode left
            raise BenchAborted(f"provider unreachable at {episode.id} week {week}: {loop.error}")
        tools = [s for s in loop.steps if s["kind"] == "tool"]
        turns.append({"week": week, "status": loop.status, "error": loop.error, "plan_version": world.versions,
                      "actions": len(world.plan), "tool_calls": len(tools),
                      "simulations": sum(1 for s in tools if s.get("name") == "simulate_plan"),
                      "input_tokens": loop.input_tokens, "output_tokens": loop.output_tokens,
                      "cost_usd": round(loop.cost_usd, 6)})
        steps.append({"week": week, "steps": loop.steps})
        last = week
    return {"plan": world.plan, "summary": world.summary, "versions": world.submitted, "turns": turns,
            "steps": steps, "cost": score_plan(company, episode, world.plan)}


# --- references ------------------------------------------------------------------------------------------------------

def playbook_reactive(company: Company, episode: Episode) -> list[dict[str, Any]]:
    """The playbook's actions per component, each from the week the component is first hit (no foresight, no
    reaction to announcements)."""
    sim = Simulator(company, episode.truth())
    plan = []
    for c in sim.comps:
        start = sim.eff.onset(c)
        if start >= HORIZON or c in sim.eff.usable:
            continue
        options = ([{"type": "air_freight", "component": c}] if c in sim.eff.gap else
                   [{"type": "second_source", "component": c},
                    {"type": "spot_buy", "component": c, "share": RULES["spot_buy"]["max_share"]}])
        for a in options:
            a = normalize({**a, "start_week": start})
            if check(company, sim.d, sim.eff, a) is None:
                plan.append(a)
                break
    return plan


def replanner(company: Company, episode: Episode, priors: Priors, sweeps: int = 2) -> list[dict[str, Any]]:
    """The optimiser on each review week's view, with the started actions fixed: the best this search does with only
    what is known at the time."""
    plan: list[dict[str, Any]] = []
    for week in review_weeks(episode, priors):
        sim = LockedSimulator(company, episode.view(week, priors), started(plan, week), week)
        plan = sim.plan_of(sim.optimise(sweeps=sweeps))
    return plan


def references(company: Company, episode: Episode, priors: Priors) -> dict[str, Any]:
    truth = episode.truth()
    sim = Simulator(company, truth)
    nothing = sim.run([]).total_cost
    opt = sim.optimise() if sim.comps else []
    out = {"do_nothing": {"cost": nothing, "plan": []}, "optimiser": {"cost": sim.run(opt).total_cost, "plan": opt}}
    for name, plan in (("playbook", playbook_reactive(company, episode)), ("replanner",
                                                                           replanner(company, episode, priors))):
        out[name] = {"cost": score_plan(company, episode, plan), "plan": plan}
    return out


# --- the suite -------------------------------------------------------------------------------------------------------

_WORKER: dict[str, Any] = {}


def _init_worker(company: Company, priors: Priors) -> None:
    _WORKER["company"], _WORKER["priors"] = company, priors


def _refs_job(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ep = Episode.from_dict(data)
    return ep.id, references(_WORKER["company"], ep, _WORKER["priors"])


def build_suite(company: Company, export: dict[str, Any], *, dev_per_level: int = DEV_PER_LEVEL,
                test_per_level: int = TEST_PER_LEVEL, workers: int = 8, cache_dir: Path | None = None,
                progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Map every exported episode, cut harm levels, fill dev then test per level in episode order, compute the
    references of the chosen episodes. With `cache_dir`, each episode's references are kept there as they finish
    (keyed by the episode, the priors, the graph and the suite version), so an interrupted build resumes."""
    priors = Priors.of(export["episodes"])
    episodes = [e for e in episodes_of(company, export) if e.items]  # something of ours is hit or threatened
    harm = {e.id: Simulator(company, e.truth()).run([]).total_cost for e in episodes}
    cuts = [float(np.quantile(list(harm.values()), q)) for q in HARM_QUANTILES]
    level_of = {k: 1 + sum(h > c for c in cuts) for k, h in harm.items()}
    taken = {s: 0 for s in range(1, 5)}
    chosen: list[tuple[Episode, str, int]] = []
    for e in sorted(episodes, key=lambda e: e.episode):
        s = level_of[e.id]
        if taken[s] < dev_per_level + test_per_level:
            chosen.append((e, "dev" if taken[s] < dev_per_level else "test", s))
            taken[s] += 1
    if progress:
        progress(f"{len(episodes)} candidate episodes; levels cut at {', '.join(eur(c) for c in cuts)}; "
                 f"{len(chosen)} chosen; computing references")
    refs: dict[str, Any] = {}
    cache = None
    if cache_dir is not None:
        key = hashlib.sha256(json.dumps([SUITE_VERSION, fingerprint(company), priors.duration, priors.severity],
                                        sort_keys=True).encode()).hexdigest()[:16]
        cache = cache_dir / key
        cache.mkdir(parents=True, exist_ok=True)

    def cached(data: dict[str, Any]) -> Path | None:
        if cache is None:
            return None
        return cache / (hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24] + ".json")

    def done(eid: str, r: dict[str, Any], data: dict[str, Any]) -> None:
        refs[eid] = r
        if (path := cached(data)) is not None and not path.exists():
            path.write_text(json.dumps(r))
        if progress:
            progress(f"  [{len(refs)}/{len(chosen)}] {eid}: do nothing {eur(r['do_nothing']['cost'])}, optimiser "
                     f"{eur(r['optimiser']['cost'])}")

    jobs = []
    for e, _, _ in chosen:
        data = e.to_dict()
        if (path := cached(data)) is not None and path.is_file():
            done(e.id, json.loads(path.read_text()), data)
        else:
            jobs.append(data)
    if workers > 1 and jobs:
        with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(company, priors)) as pool:
            futures = {pool.submit(_refs_job, data): data for data in jobs}
            for future in as_completed(futures):
                eid, r = future.result()
                done(eid, r, futures[future])
    else:
        _init_worker(company, priors)
        for data in jobs:
            eid, r = _refs_job(data)
            done(eid, r, data)
    return {"version": SUITE_VERSION, "event_types": list(EVENT_TYPES), "source": export["source"],
            "entropy": export["entropy"],
            "candidates": len(episodes), "graph": fingerprint(company), "review_gap": REVIEW_GAP,
            "priors": {"duration": priors.duration, "severity": priors.severity}, "harm_cuts": cuts,
            "level_weights": LEVEL_WEIGHTS, "missing": {s: dev_per_level + test_per_level - n for s, n in taken.items()
                                                       if n < dev_per_level + test_per_level},
            "episodes": [{**e.to_dict(), "split": split, "level": s, "references": refs[e.id]}
                         for e, split, s in chosen]}


def _replanner_job(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    ep = Episode.from_dict(data)
    plan = replanner(_WORKER["company"], ep, _WORKER["priors"])
    return ep.id, {"cost": score_plan(_WORKER["company"], ep, plan), "plan": plan}


def refresh_replanner(company: Company, suite: dict[str, Any], *, workers: int = 8,
                      progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The suite at SUITE_VERSION with only the informed re-planner recomputed: the one reference that depends on
    the review weeks and the views (do nothing, the hindsight optimiser and the playbook use the truth only)."""
    check_graph(suite, company)
    priors = suite_priors(suite)
    rows = {e["id"]: e for e in suite["episodes"]}
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(company, priors)) as pool:
        futures = [pool.submit(_replanner_job, e) for e in suite["episodes"]]
        for k, future in enumerate(as_completed(futures), start=1):
            eid, ref = future.result()
            old = rows[eid]["references"]["replanner"]["cost"]
            rows[eid]["references"]["replanner"] = ref
            if progress:
                progress(f"  [{k}/{len(futures)}] {eid}: re-planner {eur(old)} -> {eur(ref['cost'])}")
    return {**suite, "version": SUITE_VERSION}


def add_tuning(company: Company, export: dict[str, Any], suite: dict[str, Any], *, per_level: int = 10,
               workers: int = 8, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """The suite with a `tune` split: the next `per_level` episodes per harm level (the suite's cut points) that it
    does not hold yet, with their references. Changes to the agent are tried on dev and tune; test stays held out."""
    check_graph(suite, company)
    priors, cuts = suite_priors(suite), suite["harm_cuts"]
    have = {e["id"] for e in suite["episodes"]}
    taken = {s: 0 for s in range(1, 5)}
    chosen: list[tuple[Episode, int]] = []
    for e in sorted(episodes_of(company, export), key=lambda e: e.episode):
        if e.id in have or not e.items:
            continue
        s = 1 + sum(Simulator(company, e.truth()).run([]).total_cost > c for c in cuts)
        if taken[s] < per_level:
            chosen.append((e, s))
            taken[s] += 1
        if all(n >= per_level for n in taken.values()):
            break
    refs: dict[str, Any] = {}
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(company, priors)) as pool:
        futures = [pool.submit(_refs_job, e.to_dict()) for e, _ in chosen]
        for future in as_completed(futures):
            eid, r = future.result()
            refs[eid] = r
            if progress:
                progress(f"  [{len(refs)}/{len(chosen)}] {eid}: do nothing {eur(r['do_nothing']['cost'])}, "
                         f"optimiser {eur(r['optimiser']['cost'])}")
    return {**suite, "episodes": suite["episodes"] + [{**e.to_dict(), "split": "tune", "level": s,
                                                       "references": refs[e.id]} for e, s in chosen]}


def save_suite(suite: dict[str, Any], path: Path = SUITE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(suite, indent=1, ensure_ascii=False) + "\n")


def load_suite(path: Path = SUITE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"no episode suite at {path}: run `scgraph ops episodes-suite` first")
    return json.loads(path.read_text())


def suite_priors(suite: dict[str, Any]) -> Priors:
    return Priors(suite["priors"]["duration"], suite["priors"]["severity"])


def check_graph(suite: dict[str, Any], company: Company) -> None:
    if suite["graph"] != fingerprint(company):
        raise ValueError(f"the episode suite was built on another graph ({suite['graph']}); rebuild it")


# --- runs ------------------------------------------------------------------------------------------------------------

def run_record(company: Company, row: dict[str, Any], priors: Priors, client: LLMClient, variant: str,
               index: int) -> dict[str, Any]:
    started_at = time.time()
    ep = Episode.from_dict(row)
    refs = row["references"]
    result = run_episode(company, ep, priors, client, variant)
    nothing, optimiser = refs["do_nothing"]["cost"], refs["optimiser"]["cost"]
    turns = result["turns"]
    return {"episode": ep.id, "split": row["split"], "level": row["level"], "run": index, "variant": variant,
            "agent_version": AGENT_VERSION, "provider": client.provider, "model": client.default_model,
            "cost": result["cost"], "do_nothing": nothing, "optimiser": optimiser,
            "playbook": refs["playbook"]["cost"], "replanner": refs["replanner"]["cost"],
            "plan": result["plan"], "summary": result["summary"], "versions": result["versions"], "turns": turns,
            "failed_turns": sum(t["status"] != "done" for t in turns),
            "input_tokens": sum(t["input_tokens"] for t in turns), "output_tokens": sum(t["output_tokens"]
                                                                                       for t in turns),
            "cost_usd": round(sum(t["cost_usd"] for t in turns), 6), "seconds": round(time.time() - started_at, 1),
            "steps": result["steps"]}


def run_bench(company: Company, rows: list[dict[str, Any]], priors: Priors, client_factory: Callable[[], LLMClient],
              *, runs: int = 1, workers: int = 4, variant: str = "full",
              on_record: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    local = threading.local()
    stop = threading.Event()

    def work(row: dict[str, Any], index: int) -> dict[str, Any] | None:
        if stop.is_set():
            return None
        if not hasattr(local, "client"):
            local.client = client_factory()
        try:
            return run_record(company, row, priors, local.client, variant, index)
        except BenchAborted:
            stop.set()
            raise

    records, aborted = [], None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, row, i) for i in range(runs) for row in rows]
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
    records.sort(key=lambda r: (r["episode"], r["run"]))
    if aborted:
        raise BenchAborted(f"{aborted} ({len(records)} runs finished before it stopped)", records)
    return records


# --- scoring ---------------------------------------------------------------------------------------------------------

def pooled_rss(rows: list[dict[str, float]], key: str) -> float | None:
    """ShockBench's (56) over per-episode rows with `do_nothing`, `optimiser` and the policy's cost under `key`."""
    num = den = 0.0
    for s, p in enumerate(LEVEL_WEIGHTS, start=1):
        level = [r for r in rows if r["level"] == s]
        if not level:
            continue
        num += p * statistics.fmean(r["do_nothing"] - r[key] for r in level)
        den += p * statistics.fmean(r["do_nothing"] - r["optimiser"] for r in level)
    return round(num / den, 4) if den > 0 else None


def _interval(rows: list[dict[str, float]], key: str, seed: int = 0) -> tuple[float, float] | None:
    rng = random.Random(seed)
    by_level = {s: [r for r in rows if r["level"] == s] for s in range(1, 5)}
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [r for level in by_level.values() if level for r in rng.choices(level, k=len(level))]
        value = pooled_rss(sample, key)
        if value is not None:
            draws.append(value)
    if not draws:
        return None
    draws.sort()
    return round(draws[int((1 - LEVEL) / 2 * len(draws))], 4), round(draws[int((1 + LEVEL) / 2 * len(draws)) - 1], 4)


def episode_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per episode, the agent's cost the mean over its runs."""
    out = []
    for eid in sorted({r["episode"] for r in records}):
        rs = [r for r in records if r["episode"] == eid]
        out.append({k: rs[0][k] for k in ("episode", "split", "level", "do_nothing", "optimiser", "playbook",
                                          "replanner")} | {"agent": statistics.fmean(r["cost"] for r in rs),
                                                           "runs": len(rs)})
    return out


def summarize(records: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    rows = episode_rows(records)
    policies = ("agent", "playbook", "replanner")
    by_level = {}
    for s in range(1, 5):
        level = [r for r in rows if r["level"] == s]
        if level:
            by_level[s] = {"episodes": len(level), **{p: pooled_rss(level, p) for p in policies},
                           "do_nothing": round(sum(r["do_nothing"] for r in level)),
                           "optimiser": round(sum(r["optimiser"] for r in level))}
    turns = [t for r in records for t in r["turns"]]
    return {**meta, "episodes": len(rows), "runs": len(records),
            "rss": {p: pooled_rss(rows, p) for p in policies},
            "rss_interval": {p: _interval(rows, p) for p in policies},
            "by_level": by_level,
            "cost_total": {k: round(sum(r[k] for r in rows)) for k in ("do_nothing", "optimiser", "playbook",
                                                                       "replanner", "agent")},
            "agent_better_than_playbook": round(sum(r["agent"] <= r["playbook"] + 1_000 for r in rows) / len(rows), 4)
            if rows else None,
            "turns_per_episode": round(len(turns) / len(records), 2) if records else None,
            "failed_turns": sum(t["status"] != "done" for t in turns),
            "usd_per_episode": round(statistics.fmean(r["cost_usd"] for r in records), 5) if records else None,
            "usd_total": round(sum(r["cost_usd"] for r in records), 4),
            "rows": [{k: (round(v) if isinstance(v, float) else v) for k, v in r.items()} for r in rows]}


def git_commit() -> str | None:
    from scgraph.ops.bench import git_commit as commit

    return commit()


def bench_meta(company: Company, suite: dict[str, Any], split: str, runs: int, variant: str, client: LLMClient,
               name: str) -> dict[str, Any]:
    return {"name": name, "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_commit(),
            "agent_version": AGENT_VERSION, "prompt_version": VARIANTS[variant].version, "variant": variant,
            "provider": client.provider, "model": client.default_model, "split": split, "runs_per_episode": runs,
            "suite_version": suite["version"], "suite_source": suite["source"], "graph": fingerprint(company)}


def _compact(step: dict[str, Any]) -> dict[str, Any]:
    if "result" not in step:
        return step
    text = step["result"] if isinstance(step["result"], str) else json.dumps(step["result"], ensure_ascii=False)
    return {**step, "result": text if len(text) <= STEP_RESULT_CHARS else text[:STEP_RESULT_CHARS] + " ...(cut)"}


def write_results(out_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_dir / "runs.jsonl.gz", "wt", encoding="utf-8") as f:
        for r in records:
            steps = [{"week": s["week"], "steps": [_compact(x) for x in s["steps"]]} for s in r.get("steps", [])]
            f.write(json.dumps({**r, "steps": steps}, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    (out_dir / "summary.md").write_text(summary_markdown(summary))


def _rss(v: float | None, ci: list[float] | tuple[float, float] | None = None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}" + (f" [{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "")


def summary_markdown(s: dict[str, Any]) -> str:
    lines = [f"# Episode benchmark: {s['name']}", "",
             (f"{s['model']} ({s['provider']}), planner {s['agent_version']} ({s['variant']}, prompt "
             f"{s['prompt_version']}), {s['split']} split, {s['episodes']} episodes x {s['runs_per_episode']} runs, "
             f"{s['date']}, commit {s['commit']}."), "",
             ("Resilience Skill Score pooled over harm levels (ShockBench's (56)): 0 = do nothing, 1 = the hindsight "
             "optimiser; 90% bootstrap interval within levels."), "",
             "| Policy | RSS | Total cost, EUR M |", "|---|---|---|",
             f"| Hindsight optimiser | 1.00 | {s['cost_total']['optimiser'] / 1e6:,.1f} |",
             (f"| **Planner (agent)** | **{_rss(s['rss']['agent'], s['rss_interval']['agent'])}** | "
             f"{s['cost_total']['agent'] / 1e6:,.1f} |"),
             (f"| Informed re-planner | {_rss(s['rss']['replanner'], s['rss_interval']['replanner'])} | "
             f"{s['cost_total']['replanner'] / 1e6:,.1f} |"),
             (f"| Playbook, reacting when hit | {_rss(s['rss']['playbook'], s['rss_interval']['playbook'])} | "
             f"{s['cost_total']['playbook'] / 1e6:,.1f} |"),
             f"| Do nothing | 0.00 | {s['cost_total']['do_nothing'] / 1e6:,.1f} |", "",
             "| Harm level | Episodes | Agent | Re-planner | Playbook | Do nothing, EUR M |", "|---|---|---|---|---|---|"]
    for level, b in s["by_level"].items():
        lines.append(f"| {level} | {b['episodes']} | {_rss(b['agent'])} | {_rss(b['replanner'])} | "
                     f"{_rss(b['playbook'])} | {b['do_nothing'] / 1e6:,.1f} |")
    lines += ["", (f"Episodes where the agent is at least as good as the playbook: "
                  f"{s['agent_better_than_playbook']:.0%}. Reviews per episode: {s['turns_per_episode']}; failed "
                  f"reviews: {s['failed_turns']}. Model cost ${s['usd_per_episode']:.4f} per episode, "
                  f"${s['usd_total']:.2f} in total."), ""]
    return "\n".join(lines)
