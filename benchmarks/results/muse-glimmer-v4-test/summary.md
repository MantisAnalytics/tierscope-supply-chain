# Agent benchmark: muse-glimmer-v4-test

glimmer / muse-glimmer, agent v4 (full), split test, 22 cases x 3 runs, 2026-11-04 12:51 UTC, commit 4785675+dirty. Model cost $0.00.

Skill: share of the optimiser's saving against doing nothing (1 = optimiser, 0 = doing nothing; runs below -1 count as -1). Mean over cases with a 95% bootstrap interval.

| | Cases | Runs | Agent skill | Playbook skill | Valid plans | At least playbook | Near optimiser |
|---|---|---|---|---|---|---|---|
| All | 22 | 66 | 0.98 [0.96, 1.00] | 0.84 | 100% | 98% | 92% |
| Supplier outage | 5 | 15 | 1.00 [1.00, 1.00] | 0.99 | 100% | 100% | 100% |
| Export restriction | 5 | 15 | 1.00 [1.00, 1.00] | 1.00 | 100% | 100% | 100% |
| Route closure | 3 | 9 | 0.99 [0.98, 1.00] | 0.97 | 100% | 100% | 100% |
| Tariff | 5 | 15 | 0.98 [0.96, 1.00] | 0.46 | 100% | 100% | 93% |
| Supply shortage | 4 | 12 | 0.92 [0.81, 0.99] | 0.83 | 100% | 92% | 67% |

Total cost over the cases (EUR millions; the agent's is the mean over its runs):

| Do nothing | Playbook | Agent | Optimiser |
|---|---|---|---|
| 822.74 | 165.70 | 104.30 | 48.59 |

Per run: $0.0000, 183,626 tokens, 16.0 tool calls.

| Case | Type | Do nothing | Optimiser | Playbook skill | Agent skill per run |
|---|---|---|---|---|---|
| export_restriction-2 | Export restriction | 5.99 | 0.06 | 1.00 | 1.00, 1.00, 1.00 |
| export_restriction-3 | Export restriction | 18.23 | 0.00 | 1.00 | 1.00, 1.00, 1.00 |
| export_restriction-4 | Export restriction | 29.69 | 0.07 | 1.00 | 1.00, 1.00, 1.00 |
| export_restriction-5 | Export restriction | 4.02 | 0.00 | 1.00 | 1.00, 1.00, 1.00 |
| export_restriction-6 | Export restriction | 19.92 | 0.13 | 1.00 | 1.00, 1.00, 1.00 |
| route_closure-2 | Route closure | 7.79 | 0.01 | 0.93 | 1.00, 0.98, 0.96 |
| route_closure-3 | Route closure | 2.56 | 0.06 | 0.99 | 1.00, 0.99, 1.00 |
| route_closure-4 | Route closure | 20.13 | 1.05 | 0.98 | 1.00, 1.00, 1.00 |
| shortage-2 | Supply shortage | 190.47 | 27.59 | 0.72 | 1.00, 1.00, 1.00 |
| shortage-3 | Supply shortage | 109.02 | 1.42 | 0.89 | 0.95, 0.93, 1.00 |
| shortage-4 | Supply shortage | 96.76 | 1.70 | 0.99 | 0.99, 0.99, 0.99 |
| shortage-5 | Supply shortage | 207.05 | 12.22 | 0.71 | 0.72, 0.80, 0.72 |
| supplier_outage-2 | Supplier outage | 5.70 | 0.79 | 1.00 | 1.00, 1.00, 1.00 |
| supplier_outage-3 | Supplier outage | 25.47 | 0.60 | 1.00 | 1.00, 1.00, 1.00 |
| supplier_outage-4 | Supplier outage | 12.88 | 0.03 | 1.00 | 1.00, 1.00, 1.00 |
| supplier_outage-5 | Supplier outage | 25.43 | 0.02 | 1.00 | 1.00, 1.00, 1.00 |
| supplier_outage-6 | Supplier outage | 38.15 | 0.03 | 0.97 | 1.00, 1.00, 1.00 |
| tariff-2 | Tariff | 0.45 | 0.41 | -5.10 | 1.00, 0.81, 1.00 |
| tariff-3 | Tariff | 0.22 | 0.15 | 1.00 | 1.00, 1.00, 1.00 |
| tariff-4 | Tariff | 0.81 | 0.59 | 0.59 | 0.99, 0.99, 0.99 |
| tariff-5 | Tariff | 1.56 | 1.32 | 0.73 | 0.99, 1.00, 1.00 |
| tariff-6 | Tariff | 0.45 | 0.35 | 1.00 | 1.00, 1.00, 1.00 |

\* no valid plan (scored as doing nothing).
