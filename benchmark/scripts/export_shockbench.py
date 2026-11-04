"""Export ShockBench chip-market shortage curves for the Tierscope app.

Plays the send-the-maximum-less-into-a-closed-strait policy on the tiny task and keeps, per episode, the weekly
share of chip demand served at the market. Writes the JSON the Tierscope app reads from `benchmarks/shockbench.json`
in this repository: `cd benchmark && uv run python scripts/export_shockbench.py ../benchmarks/shockbench.json`.
"""

import json
import sys
from pathlib import Path

from supplyagent.baselines import SendMaxOpen
from supplyagent.sim import play

EPISODES = range(0, 50)


if __name__ == "__main__":
    out = Path(sys.argv[1])
    curves = {}
    for ep in EPISODES:
        result = play(SendMaxOpen, ep)
        fill = [round(min(1.0, w["served"] / w["demand"]), 4) if w["demand"] > 0 else 1.0 for w in result.market]
        curves[str(ep)] = {"fill": fill, "min": min(fill), "weeks_short": sum(f < 0.99 for f in fill)}
        print(ep, curves[str(ep)]["min"], curves[str(ep)]["weeks_short"], flush=True)
    out.write_text(json.dumps({"source": "ShockBench-Flow 0.1.2, task tiny, policy send_max_open",
                               "curves": curves}, indent=1) + "\n")
