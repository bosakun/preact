# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 27.8 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.8 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 75.9 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.3 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.7 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.5 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.8 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 97.5 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.6 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.8 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 76.9 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 155.0 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 152.8 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 9.8 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 155.9 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 161.8 |

Protocol: `ea98196689c15aae7e1da5185f554b9e60b8f8536a208189c3de5821c25a0ea1`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
