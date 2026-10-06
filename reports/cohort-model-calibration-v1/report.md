# Calibration Local Cohort Evaluation

Actual local NVIDIA model ranking and inference; bounded first-party program patches and Cartesian MuJoCo scene instances; no live Nebius/Cosmos/Isaac validation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 12 | 91.7% | 8.3% | 0.0% | 1.2 | 8775.9 |
| physical/direct | 12 | 58.3% | 41.7% | 0.0% | 2.6 | 46084.2 |

Protocol: `2696cf8cac3191eb2eec6a6cd12217938ce509ae8c527ad31fa081947bc4ab1a`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical canonical model inputs, candidate spaces, model/sampling versions and resource ceilings; actual GPU scheduling may remain nondeterministic
- No paid API request; hardware, energy and allocated infrastructure cost unmeasured, never reported as known zero
- Existing authored task families were previously evaluated with heuristics; this is a frozen model evaluation, not newly sourced unseen tasks

The simulation limitation above concerns physical NVIDIA validation: the inference
engine is the actual official NVIDIA checkpoint on Apple Metal. All 24 episodes completed
without infrastructure failures. Engine-separated scores and contextual labels are in
../cohort-model-calibration-engine-scores.json. Labels measure executed action
postconditions, not eventual task completion; calibrated and raw scores coincide in
this calibration collection because no prior frozen model trust was applied.
