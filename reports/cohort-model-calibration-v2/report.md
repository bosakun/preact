# Calibration Local Cohort Evaluation

Actual local NVIDIA model ranking and inference; bounded first-party program patches and Cartesian MuJoCo scene instances; no live Nebius/Cosmos/Isaac validation.

| World / condition | Episodes | Success | Unsafe | Abstained | Mean calls | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| software/direct | 60 | 91.7% | 8.3% | 0.0% | 1.2 | 13576.8 |
| physical/direct | 60 | 50.0% | 50.0% | 0.0% | 2.3 | 34116.5 |

Protocol: `5c5de233ef2a17608aef016a75843aeebc68660b827be617c6a3d4bea96d2da1`. Report JSON retains every episode and task-cluster paired intervals.

- Authored task families, not independently sourced repositories or robot hardware
- Program oracle sampling is reproducible but not a security boundary for arbitrary code
- MuJoCo scene dynamics are observed; no real visual perception or NVIDIA validation
- Direct and PreAct use identical canonical model inputs, candidate spaces, model/sampling versions and resource ceilings; actual GPU scheduling may remain nondeterministic
- No paid API request; hardware, energy and allocated infrastructure cost unmeasured, never reported as known zero
- Existing authored task families were previously evaluated with heuristics; this is a frozen model evaluation, not newly sourced unseen tasks

All **120** calibration episodes complete without infrastructure failures on frozen
repaired source `5720e2416adbf466488ce3aec800ac22906288e5b5b473eaeff0c36b5227415e`.
Software completes 55/60 with five unsafe episodes; Physical completes 30/60 with
thirty unsafe episodes. The archive auditor confirms 215 executed actions/predictions
and **208** aligned error rows; seven unknown software success forecasts remain
unlabeled. See ../model-calibration-v2-ledger-audit.json and
../cohort-model-calibration-engine-scores-v2.json. Software/Physical model success
Brier is 0.13235/0.21429; risk Brier is 0.25000/0.37953. No prior frozen trust was
applied during Direct calibration collection, so calibrated and raw scores coincide.

This is actual official NVIDIA model inference on Apple Metal using measured Q4_K_M
derivatives, not NVIDIA GPU simulation or sponsor cloud validation. Instances were
already exposed by both heuristic evaluation and the original model pilot: the next
five-seed evaluation is replication, not newly blind data. All price coverage remains
unknown; sequential scheduling and concurrent host work limit latency comparisons.
The guarded pipeline freezes the complete calibration into held-out protocol
`69a3e5b24ad66e3033cda615394f33bdc6128114840d43b8caaa40b81160421b`
and starts 1,920 comparisons/ablation episodes. Calibration alone demonstrates no
PreAct advantage; await the complete comparison, including failures.
