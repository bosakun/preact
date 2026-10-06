# Actual NVIDIA Model Pilot — v1

All 384 episodes completed: 24 authored tasks × one seed × eight conditions ×
two domains, with the same official Nemotron 3 Nano 4B Q4_K_M model, canonical
proposer inputs, action spaces, evaluator and 300-second ceilings. The model ran
on Apple M5 Metal; physics ran in local Cartesian MuJoCo. This establishes no
Nebius, Cosmos or Isaac GPU validation. Source and model/profile hashes, every
condition, failed episode and paired interval are in [summary](cohort-model-pilot-summary-v1.json).

| Domain | Direct completion / unsafe | PreAct completion / unsafe | Median latency: Direct / PreAct |
| --- | --- | --- | --- |
| Software | 21/24 / 3/24 | 24/24 / 0/24 | 16.6s / 40.0s |
| Physical | 9/24 / 15/24 | 17/24 / 0/24 | 35.2s / 200.9s |

Physical PreAct abstains on six tasks and times out once after three safe actions.
Fixed verification, frozen trust, removed disagreement and online adaptation each
complete 18/24; flat search completes 15/24. Software verified alternatives tie
completion, and inference-only abstains in both worlds. These results do not establish
that every adaptive component improves completion. The original timeout stays recorded.

Task-cluster success intervals are [0, 0.25] for Software and [0.167, 0.501] for
Physical; the latter unsafe delta interval is [-0.792, -0.417]. These describe a
small authored population and one seed. Zero unsafe PreAct episodes are not a
safety guarantee: the per-domain Wilson upper bound is about 13.8%.

Model success calibration worsens selected-action Brier from 0 to 0.0017 (Software)
and 0.0236 (Physical). Physical model risk Brier is 0.5135 on selected safe actions.
[Engine scores](cohort-model-pilot-engine-scores-v1.json) retain losses and counts.
Action postconditions differ from task completion; selection bias applies. Zero
continuous simulation error reflects shared MuJoCo implementation and seeds, not
real robot or learned world-model accuracy. Hardware allocation and energy costs
remain unknown; latency includes host load and sequential/cache effects.

The [complete ledger audit](model-pilot-ledger-audit-complete-v1.json) verifies 1,057
aligned error rows and 3,200 unexecuted predictions without observed labels, with
all 384 archive checksums. Fresh five-seed v2 calibration/evaluation uses the tested
proposal repair and retains tasks, evaluators, gates and budgets. It repeats already
exposed v1/heuristic instances; it is not a newly blind task population.
