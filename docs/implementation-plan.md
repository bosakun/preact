# PreAct Implementation Plan

Accepted implementation source of truth, recorded October 4, 2026. This document
preserves the architecture and submission strategy accepted in planning. Implement
it without redesign unless measured implementation evidence demonstrates a material
flaw. Record such evidence and the resulting decision in agent-progress.md.

## Product contract and scope

PreAct is a domain-independent counterfactual runtime, not a world model. It
coordinates interchangeable predictors and verifiers, authorizes actions using
explicit evidence, and learns contextual engine reliability from observed error.

Observe → Propose → Search branching futures → Evaluate → Verify adaptively →
Gate → Execute one action → Observe → Compare → Calibrate → Replan.

Both Software World and Physical World must use the same Core search, evaluation,
verification policy, gate, ledger, and calibration. A compelling demonstration
shows multi-step consequences, uncertainty/disagreement, verification changing a
decision, actual execution, prediction error, and historical error changing a later
decision. Never represent mocks or recorded results as live integrations.

Software scope: repository repair preserving behavioral/security invariants,
including patches, allowlisted commands, configuration, and disposable migrations.
Physical scope: simulation-first manipulation using a Franka-class arm, moving a
cube into a target around a keep-out obstacle. Real hardware is a later adapter,
not a prerequisite or a claimed validation outcome. Audience: engineers building
agents whose plausible decisions can conceal consequential errors.

## Architecture and implementation boundaries

Python/Pydantic contracts and FastAPI service; React/TypeScript/Vite/React Flow
frontend; PostgreSQL durable events, runs, ledger, calibration and worker jobs;
content-addressed artifacts on local filesystem in development and Nebius Object
Storage when deployed. Separate containers/processes for code, Cosmos and Isaac.
PostgreSQL-backed leased jobs, deadlines, cancellation and bounded concurrency;
Terraform and pinned container images for deployment. Do not add Kubernetes or an
unnecessary training platform. Local development may use SQLite through the same
storage interface; PostgreSQL validation remains a release gate.

Persistence executes off the async event loop through the same Store contract.
Drain in-flight writes before cancellation reconciles intent/status; Python thread
cancellation cannot undo a transaction. Bound PostgreSQL connection/pool/statement/
lock waits and S3 network/retry waits natively. Action deadlines stop new dispatch,
while safe I/O draining can extend cleanup beyond the episode budget. Initial run
identity survives cancelled admission; storage failures never produce observed outcomes.
This implements measured row-lock failure evidence recorded in agent-progress.md,
without changing domain, engine, search or gate responsibilities.

Locations:
- src/preact/core/: contracts, tree/search, evaluation, policy, gate, ledger, trust.
- src/preact/domains/: observation, actions, validation, execution and comparison.
- src/preact/engines/: Nemotron, Sandbox, Cosmos and Isaac integrations.
- src/preact/service/: API, events, artifact access and judge operations.
- web/: one Future Tree renderer and domain-specific evidence viewers.
- benchmarks/: task packs, baseline, ablations and reporting.
- deploy/: containers, Terraform, Serverless jobs and operations.
- docs/: requirements, architecture, feedback, release evidence and progress.

Core must import no domain-specific or vendor libraries. Engine dependencies live
outside Core. Both worlds enter the same runtime and produce the same event types.

## Domain-independent contracts

Versioned typed envelopes retain domain payloads and artifacts rather than forcing
repositories and robot observations into identical representations.

WorldState: immutable observed/hypothetical identity, timestamp, domain/schema,
payload/artifacts, observation uncertainty, provenance and parent lineage.
Action: typed operation, exact parameters/artifacts, expected starting identity,
preconditions, duration/horizon, effects and fingerprint.
TaskSpec: goal, measurable outcomes, hard constraints, stakes, budgets and terminal
conditions. ActionProposer creates diverse valid candidates for observed or
hypothetical states and can be supplied by an external agent.
FuturePrediction: samples/distributions, outcome claims, successor states,
uncertainty, assumptions, evidence, provenance and resource usage.
FutureTree: branching states/actions/outcomes, evidence, search statistics,
selected/rejected branches and actual-outcome overlays.
Evaluator: domain measurements become constraint findings, task outcomes and
conservative bounded utility. VerificationPolicy chooses evidence requests or a
reason to stop. DecisionGate returns EXECUTE, VERIFY_MORE, ABSTAIN or TASK_COMPLETE.
DomainExecutor accepts a state/action/evidence-bound authorization and returns a
receipt. OutcomeComparator aligns predictions with observations at their horizon.
PredictionLedger retains immutable prediction-to-outcome events. ReliabilityModel
provides contextual, versioned trust/calibration.
DomainAdapter bundles observation, validation, hypothetical materialization,
measurement definitions, constraints, execution and comparison.

Public entry point: PreActRuntime.run(task, domain_adapter, engines, policy),
returning an asynchronous RunEvent stream. HTTP interfaces cover run creation,
inspection/cancellation, tree/ledger, resumable events, engine capabilities,
artifacts and benchmark reports. Generate frontend types from backend schemas.

## FutureEngine interoperability and evidence

Engine protocol: describe() → capabilities; predict(request) → job;
poll(job) → prediction or explicit failure; cancel(job). In-process adapters can
complete immediately through the same prediction/result semantics.

Capabilities identify supported domains, schemas/modalities, maximum horizons,
sampling limits, materializable successor support, measured versus inferred claims,
action conditioning, applicability, version, correlated family, latency and cost.
Requests include exact root state, hypothetical lineage, action sequence, requested
claims, horizon, seed, budgets and deadline. Preserve raw artifacts with normalized
claims. Registry chooses eligible engines by capabilities. Verification strength
is claim-specific; there is no universal stronger-engine ordering.

Every claim includes metric definition/version, units, horizon, estimate/interval,
source, assumptions and supported/contradicted/unknown/not-applicable status.
Distinguish inference, generated visual evidence, simulator measurement, sandbox
measurement and authoritative observation. Unknown is never zero risk or success.
A passing suite certifies only checked software behavior. Generated video is not
collision geometry. Simulator success is not hardware safety.

Reject stale identity, mismatched action/horizon/schema, malformed results and
unsupported measurements. Infrastructure failure is distinct from task failure.
Cache by state/action/model/configuration/seed/metrics; cached evidence is not
independent corroboration. Persist actual model IDs and request/job IDs.

## Future Tree and intelligent search

Alternate state, action and stochastic-outcome nodes. Multiple engines attach
evidence to one action rather than becoming arbitrary probabilistic outcomes.
An imagined action can have success/failure branches and different follow-up actions.

Implement budgeted best-first search with progressive widening. Defaults: depth
three, three candidates per expansion, forty expanded action nodes per decision.
Generate diverse candidates, predict cheaply, prioritize conservative utility plus
uncertainty/exploration value, expand consequential branches and verify candidates
whose evidence can change the first-action decision. Stop at qualified decision,
goal completion or budget exhaustion.

Materialize software successors by applying patches in isolated workspaces.
Physical successors may be partial beliefs; do not turn imagined images into exact
simulator checkpoints. Validate descendants against their parent. Preserve prefix
assumptions and uncertainty. Reuse equivalent concrete states with a transposition
index while preserving display ancestry. Prune proven violations/dominated
candidates; defer uncertain branches. Do not multiply independent probabilities
without a justified conditional model. Use conservative cumulative bounds when
joint probabilities are unavailable. Execute only the first action, reobserve,
then replan; promising sequences do not authorize open-loop execution.

## Uncertainty, disagreement and Adaptive Verification

Keep outcome variability, model/context error, observation uncertainty, missing
evidence and hypothetical-state uncertainty distinguishable. Model self-confidence
is a raw feature, not trustworthy probability before measured calibration.

Compare aligned claims: probability spread/contradictions for binary outcomes,
Jensen–Shannon divergence for categorical distributions, normalized residuals and
interval overlap for continuous measurements. Compare task metrics extracted from
media rather than superficial image similarity. Supported constraint violations
can veto an action. Correlated model families and repeated prompts cannot create
independent corroboration. Agreement does not eliminate common blind spots.

Software evidence ladder: reasoning → static/targeted tests → broader regressions,
builds, behavioral probes and migration rehearsals. Physical: structured reasoning
and geometry → appropriate Cosmos visual evidence → Isaac trajectory/contact and
perturbation verification. Cosmos and Isaac are complementary, not a required
sequence for every physics question.

Policy considers stakes, uncertainty, disagreement, context shift, historical error,
decision impact, deadlines, cost and budget. Begin with explicit rules and rank
eligible evidence by expected decision impact per cost. Mandatory checks override
low estimated value of information. Cheap decisions remain cheap when evidence is
sufficient; unresolved decisions escalate or abstain.

Implementation contract: `core/verification.py` performs explicit heuristic allocation
over unresolved mandatory-check coverage, measured risk/uncertainty/disagreement,
stakes and the frozen contextual reliability weight, relative to declared request cost/
latency and remaining resources. Estimates remain advisory and unknown when absent;
this is not a learned probability of decision improvement. Record all candidate scores
and admission reasons. Actual evidence and the unchanged hard gate determine execution.
Server-owned API policy ceilings prevent public requests from relaxing these constraints.

## Decision Gate and execution safety

EXECUTE only if current-state preconditions hold, required checks passed, risk
satisfies the task policy and mandatory uncertainty is resolved. VERIFY_MORE when
an eligible evidence request can resolve blocking claims within budget. ABSTAIN
when all candidates fail, necessary evidence is unavailable or budgets expire.
TASK_COMPLETE requires authoritative goal confirmation.

Initial simulation statistical profile permits residual risk ≤5% at 95% confidence
where applicable; small samples retain wide bounds. This is an experimental policy,
not hardware certification. Hard constraints never become soft utility penalties.
Infrastructure failure never relaxes gates. Authorization binds state identity,
action fingerprint, evidence set, policy version and expiration. Staleness revokes
it. Durable intent and receipt prevent blind retries; uncertain crash outcomes
halt pending reconciliation. Domain executors must be idempotent or explicitly
report unknown execution state.

## Prediction Ledger and calibration

Append-only proposal, prediction, verification request/result, evaluation, decision,
execution intent/receipt, observation, comparison and reliability-update events.
Record lineage, raw/calibrated values, truth source, versions, policy, usage and
content hashes. Durable ordered events reconstruct UI without reexecuting actions.

Compare binary success/risk probabilities using Brier/log loss, continuous claims
using absolute/normalized error and coverage, safety using false-safe/false-unsafe
rates, and visuals using object movement/task predicates plus extractor uncertainty.
Long-horizon comparisons require the actual conditioning sequence. Unexecuted
branches stay unlabeled. Verification performance is separate from actual execution
performance. Count each eligible prediction once, not every verification attempt.

Trust context: engine version × domain × task family × horizon × claim type × truth
source. Conservative pooled priors/shrinkage for sparse contexts, beta-binomial
error estimates, regularized logistic calibration after sufficient labels,
empirical residual intervals and recent-error drift monitoring. New versions/OOD
contexts are uncertain. A decision freezes its reliability snapshot; update afterward.
Executed-action calibration is selection-biased: dedicated calibration/evaluation
rollouts broaden evidence without pretending all alternative outcomes were observed.

## Software World

State captures source/tree hash, dependencies/lockfiles, tests/build/runtime evidence
and environment fingerprint. Start with small trusted Python repositories. Actions
are patches, allowlisted commands, configuration and disposable database migrations.
Nemotron proposes actions and forecasts consequences.

Token Factory Sandboxes/ConTree checkpoints materialize sibling futures from a
common filesystem state. Local isolated execution uses the same adapter contract
for independent development; it is not sponsor validation. Separate authoritative
episode branch from exploratory branches. Apply authorized action from the original
state, reobserve and run independent outcome checks. Preserve partial failures/logs.

Protected evaluator tests stay inaccessible to the agent/prediction workers. Never
reward disabling tests. Restrict networking/resources and keep cloud/deploy secrets
out of workers. Git worktrees alone are not sandbox isolation. The key demo rejects
an attractive visible-test fix that violates an invariant, then executes a sound
alternative. Descendants demonstrate follow-up dependencies and regression checks.

## Physical World and NVIDIA

Franka-class arm/cube/target/keep-out scene. State: cameras, joints, end-effector,
estimated object poses, contacts, scene hash, units/frames and uncertainty. Bounded
approach/grasp/lift/move/place/release/retreat primitives become timestamped
trajectories through a deterministic controller; generated prose never drives motors.

Engines: Nemotron structured reasoning; Cosmos action-conditioned visual futures;
Isaac Sim executable rollouts measuring collision/contact/displacement/completion.
Use a tested action-conditioned Cosmos checkpoint. Translate scaling, coordinate
frames, gripper representation, frame rate and horizon explicitly; measure camera/
robot applicability and post-train if necessary. Visual predicates and sample-based
probabilities retain extractor uncertainty; generated video supplies neither exact
geometry nor native success probability.

Authoritative simulation owns execution labels and hidden dynamics. Prediction
workers receive only allowed observations/reconstructed state. Held-out friction,
mass, pose and actuator variation exposes simulator mismatch. Validate reconstruction,
reset and transient contact behavior; prediction must not mutate authority. Local
non-NVIDIA simulation is labeled development-only and never passes Isaac acceptance.

Authority workers reserve each observed episode transactionally before SDK dispatch;
process-local locks alone do not protect multiple instances. Publish the matched
pending receipt and actual observation atomically. Unresolved execution blocks new
actions and ordinary observation until reconciled, including after restart. Duplicate
completed receipts return their original result without re-execution. The measured
two-instance dispatch defect and repair evidence are recorded in agent-progress.md.

Isaac and Cosmos use separately pinned Linux containers/GPU workers. Isaac needs
RTX hardware (RTX PRO 6000/L40S), not H100/A100 without RT cores. Local macOS hosts
Core/UI tooling; NVIDIA validation runs remotely. Verify CUDA/driver/checkpoint
licenses/memory/latency early. Isaac Lab is added only if batched evaluation or
policy training materially helps, not just to name another sponsor component.

Setup audit confirms the pinned Isaac 5.1 SDK uses Python 3.11 while the API/locked
numeric stack uses 3.12. Preserve the accepted separate SDK process: supply only the
compatible shared first-party models/geometry/artifact code and SDK-side dependencies,
not the API's binary environment. Validate that bridge under 3.11, then require actual
GPU conformance; no Core/runtime architecture or SDK target is changed from syntax checks.

Implemented bridge: `engines/sdk_bridge.py` bundles exact installed shared sources per
invocation; `workers/isaac_step.py` verifies hashes/import origins and Python 3.11 before
GPU initialization. A separate dependency installer pins SDK helpers while constraining
its existing NumPy. Sixteen actual CPU cross-interpreter cases validate identity, action
fingerprints, geometry and local artifacts. This evidence exposed and repaired a numeric
default that changed action fingerprints after JSON validation; no architecture change.
CPU imports do not establish SDK/asset/RTX compatibility or a complete GPU dependency lock.

Boundary audit correction (October 4): `workers/isaac_contract.py` validates all declared
obstacles/cube dimensions and measures the actual SDK build and NVIDIA device inventory;
engine versions bind exact worker sources. Authority execution rejects changed runtime
provenance until reset/re-observation. Inventory is not active-renderer/RTX certification.
Transport uses the declared total duration with linear fixed-downward commands; implicit
grasp/release settling are separately recorded phases. Held state requires active bilateral
finger contact, not just endpoint proximity. These SDK measurements require live conformance.

Cosmos accepts only already-held fixed-downward transport with matching endpoint camera,
object/joint/tool pose, grasp evidence and SDK provenance. It uses the final rendered frame
and actual end-effector pose, rejects grasp/release conditioning and records 20Hz rounding
and explicit closed-gripper chunk padding. Native chunk-index seeds are distinguished from
the requested seed. Clean pinned upstream revision plus adapter/contract source hashes
version this boundary. CPU fixture tests establish rejection/transform contracts only;
checkpoint identity, camera synchronization, physical transfer and applicability need GPU
evidence. This repairs implementation mismatches without changing accepted Core design.

## Nebius integration and deployment

October 4 supplemental feasibility: the available Apple M5 runs the public official
`nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` checkpoint through pinned llama.cpp, after
verifying NVIDIA's weight hash and locally converting/quantizing it. Actual structured
Software/Physical predictions pass; initial schema-wrapper failure is retained as evidence.
Add an explicit `local-model` engine/operation mode using the existing Core, model proposer
and local executable verifiers. Pin checkpoint, conversion, runner and serving configuration;
require authenticated loopback access and reject changed identity. Model confidence cannot
become measured evidence; missing runner never falls back to the heuristic. Run equivalent
model/candidate Direct vs PreAct comparisons with honest latency, errors and unknown
allocated cost. This supplements M1/M10 without replacing Token Factory, Sandbox, Jobs,
Cosmos, Isaac or public judge-access acceptance. Apple Metal is not NVIDIA GPU validation.

October 4 boundary correction: an injected response from a different model with a
truncated finish reason was accepted under the requested model's reliability version.
The official completion contract now fences reported model identity and complete assistant
JSON output before predictions/ranking enter Core. Unknown usage/pricing remains unknown;
one registered pricing calculation supplies consistent costs. API identifiers do not claim
unexposed checkpoint revisions. This is a measured implementation repair, not a redesign.

Token Factory runs real NVIDIA Nemotron action proposal and cheap/strong reasoning.
Resolve available model IDs/output modes at feasibility, then freeze manifests.
Token Factory Sandboxes are beta: secure actual access and test checkpoint branching.
Use Serverless Jobs for real physical evaluation batches; Endpoints for compatible
inference where useful. Persistent RTX workers can support interactive Isaac where
cold starts are unsuitable. CPU infrastructure hosts API/Core/UI/software workloads.
PostgreSQL and Object Storage preserve events and artifacts with explicit checksums.
Keep ordering in the database, not S3 notifications. Never assume quota or credits.

Bound the entire Jobs polling phase, including a stalled status request. Once a job
ID is known, failed/cancelled waiting requests bounded remote cancellation and retains
non-secret cleanup evidence. Drain cleanup through repeated caller cancellation;
an acknowledged request is not proof of terminal resource state. Unknown submission
outcomes are not blindly retried. Close owned clients, retain borrowed client ownership.

Record model/request/job/checkpoint IDs, image digests, predictions, outcomes and
resource costs. Distinguish marginal token/job cost from allocated CPU/GPU idle cost,
storage and networking. Credentials stay server-side. Infrastructure estimates and
Terraform plans precede provisioning. Missing access blocks real integration gates;
continue independent work while reporting precise blockers. No mocked-success claims.

## Frontend

One shared React Flow renderer. Current state root, candidate actions, predicted
futures, success intervals, risk/severity, uncertainty, disagreement, evidence level,
selected action, actual outcome and prediction error. Unknown values are explicit.
Use labels/shapes as well as color for imagined/verified/observed states.

Dominant central tree, compact task/world view, evidence inspector, decision strip,
timeline and domain selector. Expand/collapse descendants, compare engines, inspect
patch/tests or generated/simulated videos, view gate reasons, overlay actual paths,
and replay trust updates. Domain viewers vary; Core/tree/event schemas do not.
Initial presentation shows current state and first actions; later futures carry
an expansion cue and remain inspectable one layer at a time. October 5 actual
archived Physical-tree review showed that fitting every descendant made initial
cards unreadable. This measured presentation correction preserves the complete
search/event record; its browser regression checks card size and branch expansion.
Reliability display follows the inspected decision's observation boundary: show
its recorded input snapshot and only the update following that action's observed
outcome. Unexecuted futures and earlier replay points receive no update. Expose
engine version, weight, contextual label counts, prior-smoothed Brier and drift,
with raw snapshots available. An October 5 actual-event regression showed the
previous latest-per-run display incorrectly used later-round statistics; this
presentation repair preserves Core and the event schema.
Resumable SSE sequence IDs recover from durable events without rerunning actions.
Recorded evidence has a prominent replay label; it supplements rather than replaces
required operational judge access. The main experience is not a chatbot.

## Benchmarks

Per domain: 24 held-out tasks × five seeds; distinct six-task development and
twelve-task calibration packs. Software families: logic, behavioral/security and
dependency/config failures. Physical: clearance, grasp/place and perception/dynamics.
Freeze tasks/policies/analysis before held-out evaluation.

Direct Agent observes/reasons/selects/executes/repeats. PreAct uses the same proposer,
actions, observations, intrinsic command/actuator restrictions and authoritative
evaluator plus search/verification/gating. No weaker baseline model. Baseline chosen-
action probability may be logged without adding counterfactual verification.
Compare equal budgets and normal operating modes. Ablations: flat search, one engine,
fixed verification, frozen trust and removed disagreement signals. Keep hard gates
in PreAct variants. Count abstained episodes as unsuccessful completion.

Metrics: success; unsafe execution and unsafe attempt rates; Brier/log loss,
continuous error/coverage/ECE/sample counts; disagreement versus error; engine/tier/
reason calls; decision/episode p50/p95 and warm/cold latency; marginal and allocated
cost; abstention and unresolved reasons. Show success–risk–cost tradeoffs. Pair seeds
and tasks with task-clustered confidence intervals. Freeze calibration for primary
comparison and assess chronological online adaptation separately.
Every bundle records source revision, images, model IDs, prompts, task hashes,
seeds, hardware, policy/calibration, budgets, raw responses/events and artifact hashes.
Replay archived outputs independently of nondeterministic fresh model reruns.

## Official hackathon constraints and strategy

Authoritative source: https://nebiusglobalaihackathon.devpost.com/rules ; Overview,
Resources, Schedule and official updates rechecked October 6, 2026. Detailed IDs and
conflicts live in hackathon-requirements.md and submission-checklist.md. Rules prevail.

Working NVIDIA-model project on Nebius runtime, qualifying category, public GitHub/
GitLab/Bitbucket repository, visible detectable open-source license, complete source/
assets/setup instructions, English description/video/testing instructions, working
demo/test-build URL for the chosen non-Physical track, public YouTube video, integration
feedback and development-period explanation where applicable. Registration, entrant
eligibility, team representative, ownership/third-party permissions and support
restrictions are administrative release gates, not facts inferable from the repo.

Primary: one Coding and Agentic Engineering submission with physical simulation
proving generality. If Sandbox access cannot be secured, freeze category as Best Apps
and Agents while preserving both worlds and real Nemotron use. Personal AI is out of
scope. Physical AI route requires runtime coordination, Serverless Jobs and at least
one minute of real hardware or functioning modules when no hardware is present.
No duplicated submissions derived from two demos. No cosmetic Tavily integration.

Apache-2.0 for original PreAct; retain third-party/model/asset licenses. Video target
170 seconds: 0–15 problem; 15–65 software; 65–135 operating physical modules; 135–155
error/trust/benchmarks; 155–170 shared Core/integrations/impact. Real footage, disclose
compression/replay, speak technology names/roles. Description/Built With matches
actual evidence. Collect tool-specific onboarding, strengths, issues, examples and
reuse feedback from first real integration.

Submission: August 26 2026 09:00 PDT to October 30 2026 10:00 PDT (=17:00 UTC,
October 31 02:00 JST). Judging December 1 09:00 PST to December 15 12:00 PST
(availability through December 15 20:00 UTC / December 16 05:00 JST). Winners around
January 11 2027. Internal release target October 29 17:00 UTC. Keep judge testing
free/unrestricted through judging, fund capacity, provide credentials if private.
Public abuse controls must not prevent documented judge tests. Replay alone cannot
satisfy operational access. Freeze submission behavior/artifacts; post-deadline
repairs restore availability without silently replacing submitted functionality.

Judging: viability/theme pass/fail then four equally weighted 1–5 criteria:
technological implementation, coherent design, credible specific impact and idea
quality. Demonstrate real decisions and measured benefits, not superficial rebranding.

Ambiguities: city attendance differs between Resources/Rules (apply Rules attendance);
older hardware announcements conflict with software-only footage alternative (apply
current Rules); cached Rules mention Token Factory Sandboxes while opened wording
says Token Factory (recheck and implement real adapter); beta/GPU/model/funding access
unconfirmed; authenticated form fields unavailable; two discussion pages failed;
future credit/model availability not guaranteed. Recheck before freeze. No unsolicited
organizer communications or credit claims. Exact URLs/provenance are recorded.

## Measured proposal efficiency correction

October 4 actual-model audit found seven single-candidate ranking requests in one
budget-failed Physical episode, spanning 37.814 seconds of proposal-stage wall time.
The only valid ranking is `[0]`; requesting inference cannot change candidate ordering.
After the original frozen pilot finishes, skip ranking for zero/one choices and clone
those domain-valid actions. Prediction, verification, gate authorization and execution
remain unchanged. Generic Core accounting may refund unused proposal reservations only
when a successful adapter explicitly certifies complete usage receipts; failed and
uninstrumented adapters retain conservative accounting/unknown cost. A subsequent
isolated cancellation reproduction showed that event writes can interrupt accounting
between completed receipts, incorrectly marking a partial fee sum known. Snapshot and
account every completed receipt before the first cancellable event write, including
refund events; test cancellation at both refund and usage publication boundaries.
Test call, cost, budget and failure behavior in both baseline/Core paths, then validate development
runs. Preserve the original failed pilot; use fresh source/model versions, independent
calibration and the accepted five-seed cohort. Do not tune actions, evaluators, safety
constraints or resource ceilings against this held-out failure. This repairs measured
unnecessary work without changing the accepted architecture.


October 5 evidence: the original 384-episode pilot sealed before both tested repairs
were applied. Root validation passes 278 tests and actual development through both
worlds; latency is higher than the earlier trial, so no isolated speedup is claimed.
Fresh five-seed v2 calibration uses source
`5720e2416adbf466488ce3aec800ac22906288e5b5b473eaeff0c36b5227415e`.
V2 retains the original tasks and repeats already exposed pilot/heuristic instances;
report it as replication, not a newly blind population. Preserve the original timeout,
all ablation losses and calibration degradation. See the complete pilot analysis and
repair evidence in `reports/`; source remains frozen through the new protocols.

October 6 evidence: v2 completes all 1,920 paired/ablation episodes, complete ledger
alignment, reproduced findings and private recorded bundle validation. Six Physical
primary timeouts, inferior completion to several ablations, calibration losses and
unknown costs remain reported. The cohort retains original source `5720e241…`.
After sealing, both minimal evaluator binding mitigations apply to main source
`44a3b88e…`; 330 main tests and lint/format pass. This changes no Core architecture
or benchmark policy. Diagnose retained deadlines with separate bounded development
checks, then refresh release-matched operations; actual sponsor/GPU gates remain open.

## Milestones and validation gates

### Defensive repair grammar correction

First-party structural testing found that the generated-repair grammar did not
enforce immutable bindings for approved operations and module helper call targets.
The minimal mitigation reserves those bindings, rejects ambiguous/reserved
definitions and retains valid existing candidate repairs. Rejected regression
fixtures are never executed; no bypass or escape demonstration is required.
The isolated defensive branch passes 317 tests. Both pinned commits applied only
after the frozen cohort sealed and audited all 1,920 episodes; main validation now
passes 330 tests on source `44a3b88e…`. Historical benchmark provenance retains its
original source. The main binding regression gate passes; actual admitted Sandbox
acceptance remains open. This tightens the existing DomainAdapter boundary without
redesigning Core or claiming live Sandbox validation.
Evidence: `reports/defensive-evaluator-bindings.json`.

Implementation evidence has tightened existing cancellation boundaries without redesign:
remote admission/polling share a deadline; known jobs request/drain cancellation and retain
bounded diagnostics; unconfirmed submissions are never blindly retried. Cosmos artifact I/O
runs off-loop and drains before scratch removal; CLI cancellation terminates the invocation's
process group and bounds reaping through a helper shared with Isaac. Job protocol version 2
keeps active cancellation in progress until consumer drain, suppresses publication immediately,
retains strict cleanup diagnostics and explicit termination evidence, and interrupts abandoned
cancellations rather than requeueing them. Local lifecycle tests do not satisfy live GPU acceptance.

Targets are dependency-ordered, not promises of external access. No milestone passes
on scaffolding, fixtures, documentation claims or unexecuted tests.

| Milestone | Target and dependencies | Acceptance evidence |
| --- | --- | --- |
| M0 Requirements/provenance | Oct 5; none | Eight-section requirements and checklist, source/conflicts, eligibility/ownership/track/release fields |
| M1 Real feasibility | Oct 7; M0 | Actual Nemotron, admitted Sandbox branching, real Job/artifact round trip, Isaac step, Cosmos conditioned example; licenses/quota/cost/memory/latency |
| M2 Core/durability | Oct 8; M0 | Schema/conformance, observed/hypothetical distinction, SSE replay, cancellation, crash safety/no duplicate execution |
| M3 Engines/software | Oct 11; M1–M2 | Real branch predictions/repair; invariant-breaking fix rejected; sound alternative executed; protected evaluation |
| M4 Search | Oct 13; M3 | Delayed consequence depth-three task; bounded expansion/state reuse/uncertainty; no authority mutation |
| M5 Verification/gate | Oct 15; M4 | Cheap resolution, disagreement escalation, violation veto, unavailable-evidence abstention; failures never success |
| M6 Ledger/calibration | Oct 17; M5 | Aligned errors, alternatives unlabeled, error changes future policy, held-out calibration |
| M7 Physical shared Core | Oct 19; M1/M2/M5 | Real sensing/action; safe/obstructed rollout; authority/reset separation; same runtime as software |
| M8 Cosmos/cloud | Oct 21; M7 | Real conditioned video and uncertain metrics; applicability; altered verification allocation; completed cloud batch |
| M9 Product UI | Oct 23; M4–M8 | One renderer both worlds; browser tests/reconnect/replay; 4/5 new viewers distinguish evidence in 30 seconds |
| M10 Benchmarks | Oct 25; M6–M8 | Paired/ablation runs including failures/cost; reproducible report; no held-out leakage |
| M11 Rehearsal | Oct 27; M9–M10 | Independent setup; detectable license/assets; integrations match video/description/feedback; real test access |
| M12 Release | Oct 29 17:00 UTC; M11 | Recheck Rules/form/category; all evidence rows audited; public links; frozen release; confirmed submission receipt |
| M13 Judging operations | Through Dec 15 20:00 UTC; M12 | Accessible tested service, funded inference/workers, artifact/backup recovery, preserved release |

## Risk controls and final acceptance

Risks: engine correlation, imagined-state compounding, calibration selection bias,
simulator leakage/mismatch, model applicability, beta/GPU access, untrusted code,
cost/latency, crashes, rule drift, expired credentials, model deprecation, incomplete
assets and claims inconsistent with evidence. Address through the boundaries/gates
above; record failures rather than weakening scope.

Completion requires both domains executing through one Core; multi-step search
changing a decision; adaptive verification handling disagreement; constraints and
abstention; actual prediction/error comparison; historical trust affecting future
verification; real Nebius/NVIDIA computation; comprehensible Future Tree; honest
paired evidence including losses; complete operational submission artifacts.
Submission-checklist.md maps every requirement to feature, implementation, test and
artifact. Progress records current milestone, work, actual validation, blockers and
next task. External access does not block independent implementation but remains a
real integration/release blocker.


### October 6 applied-source local release evidence

The architecture is unchanged. Applied defensive source `44a3b88e…` independently
passes 330 private-checkout tests, contracts/build and six fresh-SQLite browser cases;
the actual rebuilt non-root PostgreSQL app passes six browser cases and preserves
all 116 prior histories. `reports/defensive-release-validation.json` retains exact
revision/image/asset/log scopes. Fresh private distribution integrity passes; an
identical wheel reuses prior installed execution only for exact byte identity.

Actual current Nemotron/Python/MuJoCo footage executes both domains safely through
Core, with five first actions, ten aligned errors and 28 unlabeled alternatives.
`reports/local-model-defensive-footage.json` records the 148.52s decoded edit,
compressed waiting and retained raw footage. These are local M11 proofs, not public
release, sponsor integration, human comprehension or Physical-track qualification.
Actual Token Factory/Sandbox/Nebius cloud/Cosmos/Isaac access, public repository/
judge/YouTube URLs and entrant administration remain requirements, not substituted
by this local evidence. Freeze final source/payload from the persistent release record.


### Clean publication history

The public candidate preserves this accepted architecture and unchanged runtime.
It starts fresh Git history from cleaned source; private development history and
operational journals remain private. Public receipt paths are illustrative normalized
locations, not new executions. Historical source/model/protocol/artifact hashes and
benchmark failures are retained with their original scopes. Publication stays gated
on a separate exact-tree audit and explicit approval; live sponsor/GPU/human and
submission administration requirements are unchanged.
