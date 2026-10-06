# Calibration Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 60 | 91.7% | 8.3% | 0.0% | 1.5 | 40.8 |
| physical/direct | 60 | 75.0% | 25.0% | 0.0% | 3.2 | 25.9 |

Protocol: `6eba6af340c35b74ed0faee306d1288cc79ff600313ccd2a962ab8b879b03739`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
