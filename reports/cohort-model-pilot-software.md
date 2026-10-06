# Actual NVIDIA Model Pilot — Software Complete

Archived execution report: statements below retain the original checkpoint scope.
Current completed v2 status is documented in [the v2 analysis](cohort-model-analysis-v2.md) and
[public progress](../docs/agent-progress.md); historical measurements and failures are unchanged.


Partial cohort: all 192 Software episodes are complete; Physical and the accepted
five-seed evaluation remain incomplete. Source, candidates and policies are unchanged.

| Condition | Completed / 24 | Unsafe / 24 | Abstained / 24 | Prediction / verification calls | Agent calls | Median seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| direct | 21 | 3 | 0 | 1.1 | 1.1 | 16.57 |
| preact | 24 | 0 | 0 | 5.3 | 1.7 | 40.03 |
| flat | 24 | 0 | 0 | 4.0 | 1.0 | 22.18 |
| fixed | 24 | 0 | 0 | 5.3 | 1.7 | 31.67 |
| frozen | 24 | 0 | 0 | 5.3 | 1.7 | 32.66 |
| single | 0 | 0 | 24 | 2.0 | 1.0 | 21.89 |
| no_disagreement | 24 | 0 | 0 | 5.3 | 1.7 | 33.19 |
| online | 24 | 0 | 0 | 5.3 | 1.7 | 32.93 |

Direct and PreAct use the same actual NVIDIA Nemotron checkpoint, sampling, candidates,
evaluators and equal 300-second resource ceilings. Actual Python probes supply measured
verification. The local NVIDIA inference runs on Apple Metal; no Nebius, Cosmos or Isaac
execution is claimed. There were no infrastructure failures in this completed domain.

Abstentions count as failed completion. Other verification-enabled variants tie primary
completion: this pilot does not establish benefits from multistep search, adaptation,
calibration or removed disagreement in Software World. Inference alone never obtains
the required measurement authority. There are no paid API requests; allocated hardware/
energy cost is unknown and never reported as measured zero.

These 24 authored tasks were previously evaluated with heuristics. This single-seed
pilot is not evidence from newly collected repositories or broad agent superiority.
Aggregate prediction metrics combine inference and executable evidence; use the separate
engine scores, sample counts and selected-action scope in the JSON. Frozen calibration
worsened selected-safe model Brier from 0.0000 to 0.0017 (24 labels). This is selection-biased postcondition scoring, not eventual task probability
or a calibration win. The paired success-difference interval spans 0–25 percentage
points; zero observed unsafe PreAct episodes is not a global safety guarantee. Physical
and five-seed results must be assessed before final conclusions.

Conditions ran sequentially on one shared native server/cache. Earlier host validation
activity overlapped some Software cases. Latencies are observed operating measurements;
do not attribute condition timing differences solely to verification or calibration.

[Source/profile, paired intervals, engine metrics and all archive checksums](cohort-model-pilot-software-summary.json).
