# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 76.4 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 237.4 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 203.3 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 245.1 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 242.3 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 20.5 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 239.7 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 243.2 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 67.1 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 448.5 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 206.7 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 441.4 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 430.2 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 27.2 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 432.9 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 478.4 |

Protocol: `acd93060e3d1523a8cb7cae38452bc6466f0c3ad4ad088fb428c7f4c6d9c0f3e`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
