# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 29.4 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 104.3 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 81.1 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 105.2 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 104.3 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.7 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 104.7 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 104.1 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.3 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.6 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 74.8 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.4 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.4 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 10.0 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 159.7 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 160.1 |

Protocol: `580b9e26ea8dfa1245306462c3328c6a56584dce881892fdf97da9bda9bec979`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
