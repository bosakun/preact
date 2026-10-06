# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 28.0 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 99.0 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 76.8 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 99.8 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.6 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.5 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 99.2 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 98.7 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.4 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.3 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 74.9 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.0 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 156.0 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 10.1 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 160.1 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 162.6 |

Protocol: `52dda82cd4c2873592bcde2efc1d1cbe4d0c38280d467e5d649743e98c62e5ea`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
