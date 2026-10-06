# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 27.7 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.5 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 75.7 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.1 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.5 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.4 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.0 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.5 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.2 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.0 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 75.0 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.1 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 155.4 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 10.0 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 158.4 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 161.4 |

Protocol: `e1656d93643f8d9532bf6a4cbd6e90e0cfc34a63f1b9de8fb1fa17d4a5b61f9e`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
