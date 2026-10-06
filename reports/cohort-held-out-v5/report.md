# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 26.8 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.4 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 75.0 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.1 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.1 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 4.7 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.0 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.6 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 19.4 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.7 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 59.4 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.6 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 122.5 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 6.0 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.2 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 132.1 |

Protocol: `83d82e951291e3659fe1501d8db244b29c97f1d54fc201de142603fda1103f01`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
