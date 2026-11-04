"""Reference policies in ShockBench's agent interface: ``Agent(config)``, then ``act(observation)`` every week."""

import numpy as np


class SendMax:
    """Every route allowed this week ships its nominal capacity (published RSS 0.756 on tiny's dev split)."""

    def __init__(self, config):
        static = config["static"]
        u0 = static["edges"]["u0"]
        self.cap = np.array([u0[e] for e in static["action_slots"]["edge"]], dtype=float)

    def act(self, observation):
        return {"flows": self.cap * observation["action_mask"]}


class SendMaxOpen(SendMax):
    """Send the maximum, scaled on chokepoint lanes by the strait's observed open fraction."""

    def __init__(self, config):
        super().__init__(config)
        static, layout = config["static"], config["layout"]
        position = {node: i for i, node in enumerate(layout["chokepoints"])}
        lane_chk = static["lanes"]["chokepoints"]
        self.through = [
            [position[c] for c in lane_chk[lane]] if lane is not None else [] for lane in static["action_slots"]["lane"]
        ]

    def act(self, observation):
        flows = super().act(observation)["flows"]
        open_now, seen = observation["graph_now.open"], observation["graph_now.open.observed"] == 1
        for s, chks in enumerate(self.through):
            for c in chks:
                if seen[c]:
                    flows[s] *= max(float(open_now[c]), 0.0)
        return {"flows": flows}


class Reroute:
    """The normal plan, with volume on banned or strait-blocked routes moved to open routes for the same good.

    A rule a planner would write by hand; the honest bar for the LLM agent (and option D of the puzzles).
    """

    def __init__(self, config):
        from supplyagent.world_text import WorldView

        self.view = WorldView(config)

    def act(self, observation):
        return {"flows": reroute_flows(self.view, observation)}


def reroute_flows(view, obs, min_open: float = 0.5) -> np.ndarray:
    n = view.n_slots
    mask = obs["action_mask"].astype(float)
    cap0 = np.array([view.e_u0[view.route_edges(s)[0]] or 0.0 for s in range(n)])
    open_frac = {c: obs["graph_now.open"][j] for j, c in enumerate(view.chokepoints)}
    blocked = np.array([any(open_frac.get(view.e_head[e], 1.0) < min_open for e in view.route_edges(s)[:-1]) for s in range(n)])
    normal = view.normal_plan.astype(float)
    flows = normal * mask * ~blocked
    usable = (mask > 0) & ~blocked
    for s in range(n):
        lost = normal[s] - flows[s]
        if lost <= 0:
            continue
        same = [a for a in range(n) if a != s and view.slot_k[a] == view.slot_k[s] and usable[a]]
        alts = [a for a in same if view.route_dest(a) == view.route_dest(s)] or same
        for a in alts:
            add = min(lost, max(cap0[a] - flows[a], 0.0))
            flows[a] += add
            lost -= add
    return flows


BASELINES = {"send_max": SendMax, "send_max_open": SendMaxOpen, "reroute": Reroute}
