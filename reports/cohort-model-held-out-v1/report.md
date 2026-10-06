# Held Out Local Cohort Evaluation

Actual local NVIDIA model ranking and inference; bounded first-party program patches and Cartesian MuJoCo scene instances; no live Nebius/Cosmos/Isaac validation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 24 | 87.5% | 12.5% | 0.0% | 1.1 | 16565.8 |
| software/preact | 24 | 100.0% | 0.0% | 0.0% | 5.3 | 40025.6 |
| software/flat | 24 | 100.0% | 0.0% | 0.0% | 4.0 | 22178.6 |
| software/fixed | 24 | 100.0% | 0.0% | 0.0% | 5.3 | 31670.2 |
| software/frozen | 24 | 100.0% | 0.0% | 0.0% | 5.3 | 32660.7 |
| software/single | 24 | 0.0% | 0.0% | 100.0% | 2.0 | 21894.2 |
| software/no_disagreement | 24 | 100.0% | 0.0% | 0.0% | 5.3 | 33188.2 |
| software/online | 24 | 100.0% | 0.0% | 0.0% | 5.3 | 32931.9 |
| physical/direct | 24 | 37.5% | 62.5% | 0.0% | 1.9 | 35170.7 |
| physical/preact | 24 | 70.8% | 0.0% | 25.0% | 25.5 | 200921.2 |
| physical/flat | 24 | 62.5% | 0.0% | 37.5% | 10.9 | 65543.4 |
| physical/fixed | 24 | 75.0% | 0.0% | 25.0% | 25.6 | 133518.0 |
| physical/frozen | 24 | 75.0% | 0.0% | 25.0% | 25.6 | 134625.2 |
| physical/single | 24 | 0.0% | 0.0% | 100.0% | 3.0 | 29562.0 |
| physical/no_disagreement | 24 | 75.0% | 0.0% | 25.0% | 25.6 | 137128.8 |
| physical/online | 24 | 75.0% | 0.0% | 25.0% | 25.6 | 134735.8 |

Protocol: `7c4bb73a5f74c9703cc7f539ef9bd39edbdc549452abbef3455e394b31ffd49d`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical canonical model inputs, candidate spaces, model/sampling versions and resource ceilings; actual GPU scheduling may remain nondeterministic
- No paid API request; hardware, energy and allocated infrastructure cost unmeasured, never reported as known zero
- Existing authored task families were previously evaluated with heuristics; this is a frozen model evaluation, not newly sourced unseen tasks
