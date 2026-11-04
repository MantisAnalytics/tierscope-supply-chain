"""Play an agent through one ShockBench episode with gymnasium and keep the weekly cost breakdown."""

from dataclasses import dataclass, field

import gymnasium as gym
import numpy as np
import shockbench_flow_gym  # noqa: F401  (registers ShockBench/*-v0)
from shockbench_flow_gym.play import agent_config_from_reset

COST_NAMES = ("freight", "war_risk", "tariff", "holding", "queue_holding", "shortage", "disposal", "shed")
ENV_IDS = {"tiny": "ShockBench/Tiny-v0", "small": "ShockBench/Small-v0", "full": "ShockBench/Full-v0"}


@dataclass
class EpisodeResult:
    episode: int
    total_cost: float  # USD, minus the episode's return (salvage included)
    by_component: dict[str, float]
    weekly_cost: list[float] = field(default_factory=list)
    market: list[dict] = field(default_factory=list)  # per week: chip demand and served at the market


def make_env(task: str = "tiny"):
    return gym.make(ENV_IDS[task])


def play(agent_factory, episode: int, task: str = "tiny", env=None, on_week=None) -> EpisodeResult:
    """``on_week(obs, config)`` is called before each decision (to record what the agent saw)."""
    env = env or make_env(task)
    obs, info = env.reset(options={"episode": episode})
    config = agent_config_from_reset(env, obs, info)
    agent = agent_factory(config)
    comps, weekly, market, cents, done = np.zeros(len(COST_NAMES)), [], [], 0, False
    while not done:
        if on_week:
            on_week(obs, config)
        obs, reward, terminated, truncated, info = env.step(agent.act(obs))
        cents -= int(info["reward_cents"])
        weekly.append(-int(info["reward_cents"]) / 100)
        comps += np.array([info["cost_components"][n] for n in COST_NAMES])
        market.append(
            {"demand": float(obs["last_week.sinks.demand"].sum()), "served": float(obs["last_week.sinks.served"].sum())}
        )
        done = terminated or truncated
    return EpisodeResult(episode, cents / 100, dict(zip(COST_NAMES, comps.tolist())), weekly, market)
