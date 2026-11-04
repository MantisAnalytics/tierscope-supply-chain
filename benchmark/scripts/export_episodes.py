"""Export ShockBench episodes as event timelines for the Tierscope app's episode benchmark.

Draws episodes of the `small` task's public generator (the anchor rung, ShockBench-Flow unchanged) on our own root,
so none of them is a ShockBench dev or hidden episode, and keeps per episode the events that start inside the 52
weeks (or up to 4 weeks before) and the decoys (shadow threads: announced, never happening), each with the weeks its
messages are published. The app maps them onto its own graph (`scgraph.ops.shock_episodes`).

    cd benchmark && uv run python scripts/export_episodes.py ../benchmarks/shockbench_episodes.json.gz --episodes 2000
"""

import argparse
import gzip
import json
import math
from concurrent.futures import ProcessPoolExecutor
from importlib.metadata import version
from pathlib import Path

import numpy as np
from shockbench_flow.hosting.tasks import TASKS, scenario, task_generator
from shockbench_flow.marks import read_events
from shockbench_flow.omega.codes import CHANNELS, EVENT_TYPES, TARGET_KINDS

TASK = "small"
ENTROPY = 20261102  # our own root: ShockBench labels its episodes "train", never dev or hidden
CARRIED_IN_WEEKS = 4  # events that began this many weeks before week 0 are still news


def _num(x: float) -> float | None:
    return None if math.isnan(x) else round(float(x), 3)


def _target(inst, kind: str, target: int) -> dict:
    if kind == "chokepoint":
        return {"chokepoint": inst.nodes[target].id}
    if kind == "region":
        return {"region": inst.regions[target]}
    if kind == "node":
        node = inst.nodes[target]
        return {"node": node.id, "node_type": node.type, "node_region": inst.regions[node.region]}
    edge = inst.edges[target]
    tail, head = inst.nodes[edge.tail], inst.nodes[edge.head]
    region = lambda n: inst.regions[n.region] if n.region is not None and n.region >= 0 else None  # noqa: E731
    return {"edge": edge.id, "mode": edge.mode, "from_region": region(tail), "to_region": region(head),
            "edge_goods": [inst.commodities[k].id for k in edge.K]}


def _rows(inst, arrays, prefix: str, horizon: int) -> list[dict]:
    out = []
    for e in read_events(inst, arrays, prefix):
        if not (-CARRIED_IN_WEEKS <= e.onset < horizon):
            continue
        lead = arrays[f"{prefix}_lead"][e.row]
        messages = {CHANNELS[j]: round(float(e.onset - x), 3) for j, x in enumerate(lead) if not np.isnan(x)}
        kind = TARGET_KINDS[e.target_kind]
        row = {
            "kind": "event" if prefix == "ev" else "decoy", "type": EVENT_TYPES[e.type],
            "region": inst.regions[e.region] if e.region >= 0 else None,
            "counterpart": inst.regions[e.counterpart] if e.counterpart >= 0 else None,
            "target_kind": kind, **_target(inst, kind, e.target),
            "commodity": inst.commodities[e.commodity].id if e.commodity >= 0 else None,
            "onset": round(e.onset, 3), "duration": round(e.duration, 3), "severity": round(e.severity, 4),
            "rate": _num(e.rate), "messages": messages,
        }
        if prefix == "sh":
            row["channel"] = CHANNELS[int(arrays["sh_channel"][e.row])]
        out.append(row)
    return out


def episode(n: int) -> dict:
    inst, _ = task_generator(TASK)
    arrays = scenario(TASK, n, entropy=ENTROPY).arrays
    events = _rows(inst, arrays, "ev", inst.T) + _rows(inst, arrays, "sh", inst.T)
    return {"episode": n, "events": sorted(events, key=lambda r: r["onset"])}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--episodes", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    inst, _ = task_generator(TASK)
    with ProcessPoolExecutor(args.workers) as pool:
        episodes = list(pool.map(episode, range(args.episodes), chunksize=8))
    data = {"source": f"ShockBench-Flow {version('shockbench-flow')}, task {TASK} (gamma {TASKS[TASK].gamma}), "
                      f"public generator on root {ENTROPY}",
            "task": TASK, "entropy": ENTROPY, "weeks": inst.T, "regions": list(inst.regions),
            "episodes": episodes}
    with gzip.open(args.out, "wt") as f:
        json.dump(data, f, separators=(",", ":"))
    n = sum(len(e["events"]) for e in episodes)
    print(f"{len(episodes)} episodes, {n} events and decoys -> {args.out}")


if __name__ == "__main__":
    main()
