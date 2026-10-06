# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 27.1 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.2 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 75.4 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.6 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.9 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 4.6 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.8 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.6 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 19.3 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.4 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 59.5 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 125.4 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 122.3 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 6.0 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.1 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 131.8 |

Protocol: `cd9cf8d772f4aba7536ff3ba6a26df7ba9ecc52e05892de868155485494211b3`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
