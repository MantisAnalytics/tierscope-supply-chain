# Episode benchmark: muse-glimmer-v4-test

muse-glimmer (glimmer), planner v4 (full, prompt 68248f42aa79), test split, 100 episodes x 1 runs, 2026-11-04 12:51 UTC, commit 4785675+dirty.

Resilience Skill Score pooled over harm levels (ShockBench's (56)): 0 = do nothing, 1 = the hindsight optimiser; 90% bootstrap interval within levels.

| Policy | RSS | Total cost, EUR M |
|---|---|---|
| Hindsight optimiser | 1.00 | 61.3 |
| **Planner (agent)** | **0.96 [0.94, 0.97]** | 278.9 |
| Informed re-planner | 0.94 [0.92, 0.96] | 455.5 |
| Playbook, reacting when hit | 0.90 [0.86, 0.93] | 824.4 |
| Do nothing | 0.00 | 7,691.7 |

| Harm level | Episodes | Agent | Re-planner | Playbook | Do nothing, EUR M |
|---|---|---|---|---|---|
| 1 | 25 | - | - | - | 0.1 |
| 2 | 25 | 0.87 | 0.90 | 0.85 | 189.5 |
| 3 | 25 | 0.95 | 0.94 | 0.93 | 1,917.5 |
| 4 | 25 | 0.98 | 0.95 | 0.89 | 5,584.6 |

Episodes where the agent is at least as good as the playbook: 86%. Reviews per episode: 5.52; failed reviews: 11. Model cost $0.0000 per episode, $0.00 in total.
