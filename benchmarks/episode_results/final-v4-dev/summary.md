# Episode benchmark: final-v4-dev

muse-glimmer (glimmer), planner v4 (full, prompt 68248f42aa79), dev split, 20 episodes x 1 runs, 2026-11-04 08:54 UTC, commit 4785675+dirty.

Resilience Skill Score pooled over harm levels (ShockBench's (56)): 0 = do nothing, 1 = the hindsight optimiser; 90% bootstrap interval within levels.

| Policy | RSS | Total cost, EUR M |
|---|---|---|
| Hindsight optimiser | 1.00 | 5.1 |
| **Planner (agent)** | **0.96 [0.93, 0.99]** | 28.1 |
| Informed re-planner | 0.95 [0.92, 0.98] | 42.0 |
| Playbook, reacting when hit | 0.89 [0.80, 0.96] | 176.2 |
| Do nothing | 0.00 | 1,381.7 |

| Harm level | Episodes | Agent | Re-planner | Playbook | Do nothing, EUR M |
|---|---|---|---|---|---|
| 1 | 5 | - | - | - | 0.0 |
| 2 | 5 | 0.82 | 0.88 | 0.86 | 26.7 |
| 3 | 5 | 0.95 | 0.93 | 0.94 | 272.2 |
| 4 | 5 | 1.00 | 0.99 | 0.86 | 1,082.8 |

Episodes where the agent is at least as good as the playbook: 90%. Reviews per episode: 5.25; failed reviews: 0. Model cost $0.0000 per episode, $0.00 in total.
