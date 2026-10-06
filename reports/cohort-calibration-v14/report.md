# Calibration Local Cohort Evaluation

Bounded first-party program patches and Cartesian MuJoCo scene instances; local heuristic, not sponsor-model evaluation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 60 | 91.7% | 8.3% | 0.0% | 1.5 | 43.2 |
| physical/direct | 60 | 75.0% | 25.0% | 0.0% | 3.2 | 26.0 |

Protocol: `6a0b493ae84a5ab3f39e31dd879a8771d10adb2fd2b13d9d9471a77d80292984`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical candidates and cheap ranking; no LLM in this local protocol
- Zero local API cost excludes CPU and operational infrastructure cost
