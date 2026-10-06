# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 26.7 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.8 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 74.0 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.1 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.1 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 4.2 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.2 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.5 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 19.0 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 120.7 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 56.4 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 120.0 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 117.4 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 5.5 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 120.6 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.9 |

Protocol: `dfcae226a0426cb3f14bae7060d102c6787cf353214ec9a72e15358be85f1f9b`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
