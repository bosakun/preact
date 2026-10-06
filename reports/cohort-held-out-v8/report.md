# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 27.6 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.6 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 75.3 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.0 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.0 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.4 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.6 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.1 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.1 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 154.4 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 74.3 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 153.6 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 152.9 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 9.8 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.3 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.7 |

Protocol: `e3dc9a09a2d6a37a554cf14a0fe6df0b04997218a8b976ea8a9118b578ffefc0`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
