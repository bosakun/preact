# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 27.9 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.7 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 76.2 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.8 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.2 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.5 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.6 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.1 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.4 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.8 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 75.2 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.2 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 155.4 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 10.1 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 159.0 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 163.6 |

Protocol: `96036aec67b65cd3203fe71255369fad3dcebdd865aa6a79935d621440c22f03`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
