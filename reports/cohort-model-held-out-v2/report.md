# Held Out Local Cohort Evaluation

Actual local NVIDIA model ranking and inference; bounded first-party program patches and Cartesian MuJoCo scene instances; no live Nebius/Cosmos/Isaac validation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 120 | 87.5% | 12.5% | 0.0% | 1.1 | 14455.5 |
| software/preact | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 30031.0 |
| software/flat | 120 | 100.0% | 0.0% | 0.0% | 4.0 | 19757.1 |
| software/fixed | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 27095.4 |
| software/frozen | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 27229.4 |
| software/single | 120 | 0.0% | 0.0% | 100.0% | 2.0 | 19584.4 |
| software/no_disagreement | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 27123.8 |
| software/online | 120 | 100.0% | 0.0% | 0.0% | 5.3 | 26839.6 |
| physical/direct | 120 | 37.5% | 62.5% | 0.0% | 1.9 | 31466.4 |
| physical/preact | 120 | 75.0% | 0.0% | 20.0% | 25.8 | 176308.9 |
| physical/flat | 120 | 68.3% | 0.0% | 31.7% | 11.0 | 68843.8 |
| physical/fixed | 120 | 80.0% | 0.0% | 20.0% | 26.0 | 141835.5 |
| physical/frozen | 120 | 80.0% | 0.0% | 20.0% | 26.0 | 142162.3 |
| physical/single | 120 | 0.0% | 0.0% | 100.0% | 3.0 | 28578.4 |
| physical/no_disagreement | 120 | 80.0% | 0.0% | 20.0% | 26.0 | 142754.8 |
| physical/online | 120 | 80.0% | 0.0% | 20.0% | 26.0 | 142901.8 |

Protocol: `69a3e5b24ad66e3033cda615394f33bdc6128114840d43b8caaa40b81160421b`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical canonical model inputs, candidate spaces, model/sampling versions and resource ceilings; actual GPU scheduling may remain nondeterministic
- No paid API request; hardware, energy and allocated infrastructure cost unmeasured, never reported as known zero
- Existing authored task families were previously evaluated with heuristics; this is a frozen model evaluation, not newly sourced unseen tasks
