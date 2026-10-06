# Held Out Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.2 | 26.7 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.8 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 73.8 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.1 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.4 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 4.2 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 96.4 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 95.3 |
| physical/direct | 120 | 52.5% | 47.5% | 0.0% | 2.8 | 19.7 |
| physical/preact | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 121.1 |
| physical/flat | 120 | 65.0% | 0.0% | 35.0% | 11.9 | 57.4 |
| physical/fixed | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 120.9 |
| physical/frozen | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 118.2 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 5.5 |
| physical/no_disagreement | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 121.0 |
| physical/online | 120 | 83.3% | 0.0% | 16.7% | 30.6 | 126.2 |

Protocol: `54e9abc2ecf586d989d31a4764c3a4905ba35870236925443fbebf75719b93a9`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
