# Frozen Local Cohort Findings

Archived execution report: statements below retain the original checkpoint scope.
Current completed v2 status is documented in [the v2 analysis](cohort-model-analysis-v2.md) and
[public progress](../docs/agent-progress.md); historical measurements and failures are unchanged.


The October 4 protocol first evaluated 24 held-out task instances per world, five seeds,
eight conditions: **1,920 actual Runtime episodes**. Calibration used 12 different tasks per
world, five seeds (120 episodes). Source and policy hashes were frozen before evaluation.
The 48 held-out tasks belong to authored families; they are not an independently collected
population. Direct and PreAct use the same cheap proposer, candidate actions and evaluator.
Neither uses a language model in this protocol.

| Primary comparison | Direct | PreAct |
|---|---:|---:|
| Software completion | 105/120 | 120/120 |
| Software unsafe episodes | 15/120 | 0/120 |
| Physical completion | 63/120 | 100/120 |
| Physical unsafe episodes | 57/120 | 0/120 |
| Physical abstention | 0/120 | 20/120 |
| Software mean prediction/verification calls | 1.2 | 5.3 |
| Physical mean prediction/verification calls | 2.8 | 30.6 |
| Software median latency | 76.4 ms | 237.4 ms |
| Physical median latency | 67.1 ms | 448.5 ms |

Abstention counts as unsuccessful completion. There were no infrastructure failures.
Task-cluster bootstrap 95% intervals for paired success differences are software
**0–25 percentage points**, physical **13.3–47.5 points**. Repeated seeds are not counted
as distinct tasks. These intervals characterize the authored suite, not broad agent superiority.

## Failures and ablations

PreAct abstained on all five seeds of `perception-dynamics-08`, `-10`, `-11` and `-12`.
Verification rejected available routes for clearance; one inspected case executed a safe
lift before rejecting continuations. This is candidate coverage failure, not a successful
completed task. No held-out-driven policy or route changes were made.

Flat search tied software completion with fewer calls and lower latency. In Physical World,
flat completed 65%, versus 83.3% with multistep search, at fewer calls. Fixed verification,
unadapted trust, removed disagreement and online conditions tied primary task outcomes.
This suite does **not** demonstrate a benefit from calibration, adaptive verification or
multiple independent measured engines. Inference-only abstained on every task, because it
could not supply required executable measurements; safe abstention is not task success.

## Prediction and scope limits

The aggregate prediction scores mix a heuristic with deterministic measured evidence.
Use the separate engine scores to inspect each; raw and calibrated chosen-action scores
are distinct. In the first completed protocol, frozen calibration worsened selected-action
heuristic Brier scores (software 0.0064 → 0.0120, physical 0.0064 → 0.0309).
Calibration-cohort labels transfer imperfectly to PreAct's safely selected actions;
this result is retained rather than presented as a calibration win. Labels measure
action postconditions/constraint violations, not eventual task completion.
Only executed predictions have labels, so selection bias is unavoidable here.
Exact local verifier claims are conditional on the bundled evaluator/simulator, not a global
safety certificate. Protected sampled probes cannot prove arbitrary program correctness.

Software is bounded candidate repair/configuration-function evaluation. Physical simulation
uses a Cartesian mocap/grasp weld and visible dynamics; it is not Franka, real perception,
NVIDIA Cosmos/Isaac, or hardware. Zero marginal API cost excludes CPU/deployment cost.
The actual local NVIDIA model cohort is evaluated separately; real Nebius-model and
Cosmos/Isaac acceptance remain open.

The v13 protocol reran the same tasks/proposer/evaluator after repairing asynchronous
episode deadlines, observer/proposal/task isolation and post-intent authorization expiry,
and adding explicit evidence allocation/search traces, server admission limits and SSE
disconnect handling, and off-loop persistence with cancellation-safe write draining. v8 repairs the vendor Jobs wait/cleanup boundary; v9–v10 repair remote admission/
cleanup diagnostics, off-loop Cosmos storage and shared Isaac/Cosmos launcher termination.
The local cohort does not call those external services.
Earlier v1–v13 protocols and reports preserve provenance; these reruns
are not new independent tasks. No routes, candidates or policies were tuned against failures.
The allocator's priced-engine conformance tests exercise cost-sensitive choices, but this
local cohort has only one measured verifier per world and does not prove a cost-allocation win.
Unsafe labels include protected clearance buffers/controller postconditions and software
security invariants; they are not identical to collision counts or real-world accidents.

v11–v12 also validate cancellation-state publication and normal/exceptional lifespan
shutdown. A changed-source guard stopped the original v11 run; its exact historical
wheel passed the source hash and resumed safely. v12 uses the repaired shutdown source.
This is source-pinned resume validation, not new unseen tasks or live GPU evidence.

Historical v13 additionally freezes the exact-source SDK bridge and repaired action-duration
default. Sixteen separate actual CPU Python 3.11 cases validate shared imports/identity;
this cohort does not call the SDK or sponsors. All 120 calibration and 1,920 held-out/
ablation episodes completed, primary outcomes and calibration losses unchanged.

Historical v14 freezes repaired SDK geometry/provenance/contact/timing and Cosmos endpoint/
transform/padding/reproducibility contracts. All 120 calibration and 1,920 held-out/ablation
episodes complete with no infrastructure failures, unchanged outcomes and calibration
losses. The local cohort never calls those NVIDIA adapters; it validates shared-runtime
regression, not GPU integration. No new held-out tasks or policy tuning were introduced.

Historical v15 freezes the Token Factory completion identity/status/cost repair. The same
120 calibration and 1,920 held-out/ablation episodes complete with no infrastructure
failures and unchanged primary outcomes, failure families and calibration losses. No
live model is called by this local protocol, so it does not test sponsor inference or
show a benefit from the response repair. Separate injected transport/Core tests cover
that boundary. Neither tasks, candidates, evaluators nor policies were tuned.

Current v16 freezes the shared structured-reasoner extraction and explicit local NVIDIA
model adapter. This heuristic-only rerun completed all 120 calibration and 1,920 held-out/
ablation episodes with unchanged outcomes and no infrastructure failures. Its latency
was measured while native-model calibration, browser checks and container work shared
the host; these are not isolated performance measurements. Earlier v15 median Software
latency was 28.9/102.6 ms and Physical 26.1/161.7 ms (Direct/PreAct). Absolute latency
changes across these runs must not be attributed to the source change alone. No tasks,
proposers, evaluators or policies were tuned. See the separate real-model evidence and
frozen protocols; the heuristic results do not validate model or sponsor integrations.
