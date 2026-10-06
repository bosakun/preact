# Actual NVIDIA Model Comparison — v2

Completed October 6, 2026: **1,920** episodes, 24 authored tasks × five seeds ×
eight conditions × two domains, following 120 independent calibration episodes.
The same official Nemotron 3 Nano 4B Q4_K_M model, canonical proposer inputs, action
spaces, evaluators, sampling and 300-second ceilings apply to Direct and PreAct.
Inference ran on Apple M5 Metal; physical measurement used local Cartesian MuJoCo.
This establishes no Nebius, Cosmos or Isaac GPU validation. [Reproduced findings](model-v2-reproduced-findings.json)
retain all metrics, 494 unsuccessful episodes, engine scores, source/model/protocol
identities and task-cluster paired intervals. Original archives remain unchanged.

| World / condition | Completion | Unsafe episodes | Abstained | Failed | Median / p95 latency | Mean verification simulations |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| software / direct | 105/120 | 15/120 | 0 | 0 | 14.5s / 21.7s | 0.00 |
| software / preact | 120/120 | 0/120 | 0 | 0 | 30.0s / 47.8s | 0.00 |
| software / flat | 120/120 | 0/120 | 0 | 0 | 19.8s / 27.9s | 0.00 |
| software / fixed | 120/120 | 0/120 | 0 | 0 | 27.1s / 41.5s | 0.00 |
| software / frozen | 120/120 | 0/120 | 0 | 0 | 27.2s / 41.7s | 0.00 |
| software / single | 0/120 | 0/120 | 120 | 0 | 19.6s / 28.0s | 0.00 |
| software / no_disagreement | 120/120 | 0/120 | 0 | 0 | 27.1s / 41.4s | 0.00 |
| software / online | 120/120 | 0/120 | 0 | 0 | 26.8s / 41.3s | 0.00 |
| physical / direct | 45/120 | 75/120 | 0 | 0 | 31.5s / 68.8s | 0.00 |
| physical / preact | 90/120 | 0/120 | 24 | 6 | 176.3s / 299.4s | 12.89 |
| physical / flat | 82/120 | 0/120 | 38 | 0 | 68.8s / 109.9s | 5.52 |
| physical / fixed | 96/120 | 0/120 | 24 | 0 | 141.8s / 239.9s | 12.98 |
| physical / frozen | 96/120 | 0/120 | 24 | 0 | 142.2s / 239.4s | 12.98 |
| physical / single | 0/120 | 0/120 | 120 | 0 | 28.6s / 48.0s | 0.00 |
| physical / no_disagreement | 96/120 | 0/120 | 24 | 0 | 142.8s / 234.3s | 12.98 |
| physical / online | 96/120 | 0/120 | 24 | 0 | 142.9s / 235.3s | 12.98 |

Physical PreAct retains **24 abstentions and six episode-deadline failures** after
one to three safe first-action executions. Five failures concern `clearance-09`
(seeds 0–4); one concerns `clearance-06` (seed 0). They are failures even when
earlier actions were safe. Fixed verification, frozen trust, removed disagreement
and online adaptation each complete 96/120, exceeding primary PreAct’s 90/120.
Flat search completes 82/120 with lower latency; all verified Software variants
complete 120/120. Inference-only abstains on all 120 instances in both worlds.
These results do not establish causal benefits for every adaptive component.
Sequential order, shared host work and cache effects confound latency attribution.
A separate [bounded diagnostic](model-v2-deadline-diagnosis.json) reruns exposed
`clearance-09`, seed 0, on applied defensive source with the same model/policies/
300-second ceiling. PreAct completes safely in 142.3s; frozen trust in 159.1s.
Both execute the same three action semantics; the first two match the original
timeout episode. The deadline did not recur on this case. Sequential host execution
cannot establish a causal explanation or isolate component benefits; no architecture
or policy change is justified, and all six original timeouts remain cohort failures.

Paired task-cluster completion deltas are +0.125, 95% interval [0, 0.25], for
Software and +0.375, [0.1583, 0.575], for Physical. Physical unsafe delta is
−0.625, [−0.8083, −0.4333]. Repeated authored tasks are the cluster unit; seeds
are not independent new tasks. Zero observed unsafe PreAct episodes is not a
safety guarantee: the per-domain Wilson upper bound is about **3.10%**.

## Prediction quality and limitations

| Selected model forecasts / PreAct | Labels | Raw success Brier | Calibrated success Brier | Risk Brier |
| --- | ---: | ---: | ---: | ---: |
| software | 119 | 0.008403 | 0.010621 | 0.126050 |
| physical | 295 | 0.000000 | 0.091178 | 0.425642 |

Historical calibration worsens these selected-success scores; Physical model risk
forecasts remain poor. Simulation scores stay separate: their zero continuous
error reflects shared simulator implementation and seeds, not learned world-model
or real robot accuracy. Action postconditions differ from task completion, and
selection bias prevents scoring unexecuted alternatives. Costs have **0% measured
coverage**; hardware, energy and infrastructure allocation remain unknown.

The [complete ledger audit](model-v2-ledger-audit-complete.json) verifies 21,511
predictions, 2,881 observed first actions, 5,385 aligned error rows and 16,119
unexecuted predictions without observed labels; seven executed prediction labels
remain legitimately unknown. All 1,920 archive bytes and all 1,847 prior snapshot
checksums were independently rechecked. The [private bundle proof](model-v2-bundle-validation.json)
covers original events/artifacts/calibration and actual recorded imports with zero
new execution or calibration. Export is not publication or fresh inference.

Both pilot and replication reuse exposed, authored program/scene families. They
are reproducible frozen comparisons, **not a newly blind population** or evidence
of arbitrary repository, real perception, sponsor infrastructure or robot safety.
Benchmark source remains `5720e2416adbf466488ce3aec800ac22906288e5b5b473eaeff0c36b5227415e`; the later defensive evaluator
mitigation has its own [main regression evidence](defensive-evaluator-bindings.json).
