# ShockBench exporter

Two scripts that turn [ShockBench-Flow](https://pypi.org/project/shockbench-flow/) 0.1.2 runs into the files Tierscope
reads. This is a separate Python project because ShockBench-Flow needs Python 3.13 and numba, so it cannot share the
app's environment.

| Script | Writes | Used by the app for |
|---|---|---|
| `scripts/export_shockbench.py` | `../benchmarks/shockbench.json` | Chip-shortage disruptions: the weekly share of chip demand a semiconductor market serves through strait closures, export bans, tariffs and plant outages (task `tiny`, 50 episodes) |
| `scripts/export_episodes.py` | `../benchmarks/shockbench_episodes.json.gz` | The episode benchmark: years of events and decoy announcements from the public generator of the `small` task, on our own root, so that none of them is a ShockBench dev or hidden episode. The app maps them onto its own graph (`scgraph.ops.shock_episodes`). |

```bash
cd benchmark
uv sync
uv run python scripts/export_shockbench.py ../benchmarks/shockbench.json
uv run python scripts/export_episodes.py ../benchmarks/shockbench_episodes.json.gz --episodes 2000
```

`src/supplyagent/sim.py` plays a policy through a ShockBench environment, and `baselines.py` holds the rule policy
(send the maximum, less into a closed strait) that produces the shortage curves. Neither script calls a model.

## Built during the hackathon vs. what existed before

ShockBench-Flow 0.1.2 (Mantis Analytics' semiconductor-network benchmark) existed before the hackathon. It is used
unchanged: its environments, its event generator and its scoring. The exporter scripts and the two modules in this
folder were written during the hackathon.
