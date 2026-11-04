# Episode benchmark: final-v4-tune

muse-glimmer (glimmer), planner v4 (full, prompt 68248f42aa79), tune split, 40 episodes x 1 runs, 2026-11-04 08:54 UTC, commit 4785675+dirty.

Resilience Skill Score pooled over harm levels (ShockBench's (56)): 0 = do nothing, 1 = the hindsight optimiser; 90% bootstrap interval within levels.

| Policy | RSS | Total cost, EUR M |
|---|---|---|
| Hindsight optimiser | 1.00 | 9.8 |
| **Planner (agent)** | **0.97 [0.94, 0.98]** | 96.7 |
| Informed re-planner | 0.95 [0.92, 0.98] | 147.9 |
| Playbook, reacting when hit | 0.88 [0.82, 0.95] | 318.2 |
| Do nothing | 0.00 | 3,233.8 |

| Harm level | Episodes | Agent | Re-planner | Playbook | Do nothing, EUR M |
|---|---|---|---|---|---|
| 1 | 10 | - | - | - | 0.1 |
| 2 | 10 | 0.45 | 0.92 | 0.84 | 17.2 |
| 3 | 10 | 0.98 | 0.95 | 0.86 | 943.2 |
| 4 | 10 | 0.97 | 0.96 | 0.92 | 2,273.3 |

Episodes where the agent is at least as good as the playbook: 82%. Reviews per episode: 5.5; failed reviews: 1. Model cost $0.0000 per episode, $0.00 in total.
