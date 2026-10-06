# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 26.7 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.4 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 74.1 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.9 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.6 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 4.3 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.9 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.7 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 19.3 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 121.2 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 56.7 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 120.9 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 117.8 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 5.5 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 121.0 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 125.8 |

Protocol: `ee22319bcd9cc728a8bd34c99dd67013dd320a929d302f1e2828f6493ff2db2b`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
