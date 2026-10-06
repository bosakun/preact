# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 28.5 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 101.5 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 78.5 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 101.8 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 101.2 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 7.6 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 101.7 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 101.9 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 25.8 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 158.6 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 76.2 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 158.3 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 157.1 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 10.2 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 160.0 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 162.0 |

Protocol: `912a3c4fe5eb3c0234a8592401afb59dd58d602a1c79837499e3511ad3189673`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
