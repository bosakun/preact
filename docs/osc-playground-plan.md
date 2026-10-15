# OSC 2026 Interactive Cognitive Playground: implementation proposal

Status: approved by the user on 2026-10-15; P0 implementation in progress.
Inspection date: 2026-10-15. Exhibition target: 2026-10-17.
Source reviewed: public main `5dd9372c69b2dcc2a49590d02e8642685c79b1bf`, which merges PR #18.

The current root workspace has unrelated uncommitted changes and an older local main.
Implementation will use an isolated checkout of public main and preserve those changes.
The initial inspection phase made no implementation changes. Current implementation and validation outcomes are maintained in docs/agent-progress.md.

## Exhibition outcome and scope

A visitor selects a Queue scenario and sees an actual cognitive round connect observation,
candidate forecasts, verification, one executed action, a complete receipt and new experience.
An independently audited recorded session supplies immediate exploration of longer learning,
evaluation and explicit promotion histories. Live CPU simulation and recorded replay have
permanent, distinct labels. Both views use the same evidence projection components.

P0 includes the six-module architecture, a minimal evidence inspector, counterfactual candidate
comparison, experience/health display, playback controls, offline operation and recovery.
Detailed cross-model comparison and Physical World linking are P1, after P0 validation.
The existing Software/Physical Future Tree remains available with its current default behavior.

## Existing assets and their use

| Asset | Planned use |
| --- | --- |
| `cognition/loop.py`: `CognitiveAgent` | Own one actual episode and run each action through existing Runtime. |
| `cognition/deliberation.py`: `DeliberationPlanner` | Supply existing belief, candidate forecasts, scores, ranking and provenance. |
| `cognition/selection.py`: `SelectionWorld` | Retain qualification, fresh-state, authorization and post-intent dispatch fences. |
| `cognition/lifecycle.py`: `LearningCoordinator` | Retain receipt manifests, checkpoints, independent evaluation and explicit promotion. |
| `domains/information_queue.py`: `InformationQueueWorld` | Authoritative Queue World, costly executed probes and measured outcomes. |
| `domains/queue_learning.py`: `QueueLearningBackend` | Reuse continuous adaptation, health, recovery and existing prediction/goal adapters. |
| `engines/queue_temporal.py` and Queue adapters | Display saved H3 environment-only paths, weights and metric vectors. |
| `EpisodicMemory`, `TransitionDataset`, Store and Artifacts | Reuse committed experience and source validation; retain pending/aborted distinctions. |
| `scripts/bench_common_learning.py` | Reproduce frozen experiments into a new private output directory when original bundles are unavailable. |
| `scripts/audit_common_learning.py` | Independently audit source bundles before producing exhibition replay. |
| `web/src/main.tsx`, React Flow and existing SSE | Reuse visual language, graph infrastructure, reconnection patterns and evidence inspection. |
| `preact/replay.py` | Reuse archival distinction and checksum principles; its single-run archive is insufficient for a multi-run learning episode. |

No new cognition, prediction ranking, learning coordinator or execution engine is proposed.
Core sources, Gate rules, authorization, durable intents and receipt semantics stay intact.

## Proposed components

- Add a domain-owned Queue session factory for allowlisted constructor configurations and
  evidence-to-scene translation. It must not mutate `payload` or `_service` from UI commands.
- Add a service-owned session controller under `/api/playground/`. It manages bounded admission,
  one owner per session, round-boundary control and references to existing durable run/journal
  records. Clients cannot supply policies, model artifacts, code, file paths or private schedules.
- Add evidence projections and a versioned replay manifest outside Core. Preserve original run IDs,
  event sequence numbers, timestamps, action fingerprints, receipt bindings and artifact hashes.
  Presentation cursors are additional indices, never replacements for original evidence identities.
- Add `web/src/playground/` components behind a separate in-app entry, for example `#playground`.
  Keep the current Future Tree as the default view and scope new styles to the playground.
- Export typed service contracts without hand-editing generated frontend contracts.
- Add an explicit kiosk configuration that exposes only the bounded Queue controls and approved
  replay catalog. Hiding generic execution/admin controls in the browser is insufficient; server
  routes must enforce the restriction while normal service mode remains compatible.

## Visitor experience

The main screen uses large Japanese labels with technical English module names. The 2D World
and the integrated cognitive loop share the primary workspace: submitted work in transit,
waiting work, processing and delivered work alongside that round's observed/Belief input,
recorded internal model, prediction, simulation, decision, execution and memory feedback.
Tick, delivered/goal, cumulative receipt reward and overflow checks remain visible. Six clickable
modules expose concise input/output beside the loop. Each data-flow link resolves to a real
source record; feedback uses exact complete receipts referenced in the next deliberation.
The service distribution is displayed only from predictions that actually used it. Known
arrival rules, inferred parameters and unknown future service remain distinct.
A candidate comparison connects numerical forecasts to the selected action and actual outcome.
Detailed JSON and provenance live in a collapsible inspector instead of the primary visitor flow.
Replay supports both original-event steps and a whole-cycle navigator with stage seeking.

The existing Queue models fungible work quantities, not task identities. Each animated token
represents one work unit; it must not imply an observed task ID, per-job priority or completion
latency. Transitions animate between recorded quantities. No animation advances authoritative
tick, queue, processing or reward before the corresponding evidence exists. Reduced-motion mode
shows the same exact quantities without interpolation.

The public `capacity` is queue occupancy capacity, not service rate. Show actual processed work
per executed tick. Show service bounds and the last `probe_service` measurement with its tick;
otherwise label current service as unmeasured. Do not display the private service schedule or
pass scenario labels/configuration to predictor inputs.

P0 scenarios use existing constructor parameters: stable service, High-to-Low and Low-to-High,
with an optional stable-low preset. A start-time preset fixes the change tick and environment.
P0 does not allow mid-session environment editing. The UI states that changes are configured
before Start. Runtime boundary intervention is deferred unless a separately validated external
event contract can be added without altering observation/state semantics or leaking future data.

## Control semantics

Live Start creates one real session, initializes the existing pipeline and releases its first
round. Next Step releases exactly one further cognitive round. Auto Play repeatedly releases
rounds; Pause prevents the next round. An already admitted round finishes through its normal
receipt and learning checkpoint, with a visible 'pausing after this round' status.

Use the existing asynchronous `round_observer` boundary to wait between rounds, outside Runtime
authorization deadlines. A single running `CognitiveAgent.run` owns the episode; do not call the
same Agent's one-shot `run` repeatedly. Pauses must also stop the next learning prepare operation.
Existing post-receipt learning already admitted for a round may finish before pause settles.
Repeated clicks and retried commands must not grant duplicate round permits. New Session stops
future admissions, settles any in-flight round, retains its records and creates a fresh World.
Pausing and connection loss never convert an actual operation into a replay or fabricate success.

Recorded Replay Start/Next Step/Auto Play/Pause only move a presentation cursor. Provide event
and round seeking, backward seeking and module/action selection without executing anything.
Opening historical events from a live session visibly switches to recorded exploration.

## Evidence semantics

| Module | Existing evidence used |
| --- | --- |
| Cognition / Perception | `observed`, belief in `deliberation`, `cognitive_update` and its memory references. |
| Prediction | Predictions inside `deliberation.simulation.candidates[].records`; distinguish Core verification predictions. |
| Simulation | Existing candidate paths, metric vectors, weights, horizon and declared continuation. |
| Decision / Planning | Saved scores/ranking, `candidate_qualification`, `candidate_selection` and final decision. |
| Verification & Execution | Verification records, Gate preview/final authorization, execution intent, dispatch and complete receipt/outcome. |
| Memory & Learning | Receipt-backed memory references, `learning_experience`, checkpoints, candidate/evaluation artifacts and explicit promotion records. |

One `deliberation` event may contain perception, prediction, simulation and soft ranking.
Highlight those modules together with that source reference. Do not invent separate start/end
events, elapsed durations or a synthetic sequence of module execution. Waiting indicators may
describe a pending round, but cannot claim a specific internal phase has occurred.
Missing or unsupported evidence is displayed as unavailable, never filled with plausible data.

The primary replay timeline spans actual-episode Runtime runs and learning/lifecycle journals. Preparation and independent evaluation are linked through validated manifests and artifact summaries; full evaluation-branch drilldown is P1, and its raw source remains private. Establish deterministic
presentation ordering from original timestamps, per-run sequence and explicit causal references;
validate required dependencies. Concurrent/unordered records must not imply a measured total
processing order. Preserve every source reference and reconcile the final archive after SSE.

Candidate cards use actual action fingerprints to join forecasts, scores, qualified/rejected
status, final choice and receipt. QueueTemporalEngine supplies numeric paths and vectors, not
complete successor State objects. Display those as metric trajectories/distributions; only a
recorded explicit successor State may be rendered as a hypothetical State. Do not manufacture
State IDs or mean successor States. Preserve zero-weight support branches as such if shown.

H3 Queue forecasts assume no later intervention. The live agent re-observes and may act again,
so its next three closed-loop ticks are not a matching H3 actual label. Compute selected-action
H1 errors only where a matching prediction and complete outcome exist, with coverage and sample
counts. Independent H3 evaluation can be inspected separately with its own receipts/conditions.
Unchosen live candidates never acquire actual labels. Probes have no invented service forecast.

## Learning and replay preparation

Live sessions start with a receipt-validated bootstrap model and its separate training provenance,
using existing trainer/lifecycle initialization. Generate preparation receipts ahead of the
exhibition when possible. Source training must be independent of the live private schedule.
Display preparation cost separately. New experiences, health invalidation, candidate training
and independent evaluation continue through the existing coordinator/backend without relaxed
thresholds. Show 'accumulating experience', 'evaluating' or 'awaiting explicit promotion' accurately.

Public Live controls have no promotion operation and never invoke a benchmark promotion observer.
Long learning/promotion stories are shown through recorded explicit operator actions. A future
staff-only promotion tool can call the existing API at a valid round boundary as P1.

The repository contains public aggregate results and audit reports, but those do not contain the
Observation/Prediction/Decision/Receipt stream needed to reconstruct a session. Original raw
common-learning bundles were not located in the initial root cache inventory, and the reviewed
CI workflow has no raw bundle upload step. Early P0 work must recover a matching retained bundle
or rerun the unchanged frozen protocol into a new private directory and run the existing auditor.
Do not infer event records from aggregate reports or depend on an unverified download.

Export only reviewed, allowlisted exhibit fields and required evidence artifacts from successfully
audited sources. Retain source, protocol, audit and export checksums and verify projection against
the source. Raw databases, protected evaluator inputs and private cache/history are not public PR
assets. Keep full audit sources locally; the browser receives the validated exhibit projection.
Catalog at least one stable session plus High-to-Low and Low-to-High with and without recovery.
Record all recorded promotions as historical operator actions, not Replay button effects.

Preserve the Low-to-High adverse result: frozen recovery reward 32.9 versus no-recovery 115.4.
Also preserve Goal999 as an intentional continuous fixture with zero task goal completion; do
not relabel it a successful task. Distinguish health, independent evaluation, promotion, prediction
error and actual task reward. Model validity or lower conditional error does not imply benefit.

## Delivery sequence and completion gates

These are work blocks, not a guarantee that unmeasured live computation finishes instantly.
Given the two-day deadline, complete and test P0 before undertaking P1.

1. Establish the isolated main baseline, record available dependencies, and begin replay source
   recovery/reproduction early. Validate the source audit before advertising any replay as audited.
2. Implement service session contracts/control and a minimal Live end-to-end round, then verify
   actual observation, frozen forecast, qualification, intent and complete receipt bindings.
3. Implement the shared evidence reducer, 2D World, six modules, candidate graph and inspector.
   Connect it to both Live and audited Replay, then add accessible controls and scene interpolation.
4. Complete offline/kiosk mode, reconnection, restart/new-session behavior, regression tests and
   an actual browser rehearsal. Record CPU/wall/memory and preparation/evaluation costs separately.
5. Freeze the exhibition candidate with operator instructions, screenshots, replay provenance and
   actual test outcomes. Create a focused PR after checks; leave it open without auto-merge.

If time is constrained, defer live environment editing, live staff promotion UI, extensive model
comparisons and Physical linking. Do not defer evidence validation or claim P0 complete with
mocked behavior, a disconnected scene, untested controls or missing audited replay.

| P0 requirement | Verifiable completion condition |
| --- | --- |
| Interactive World | All three scenario types start real Queue episodes. Visual quantities match formal state/outcome, and boundary changes affect later actual behavior. No UI schedule/state writes. |
| Architecture | Every module and highlighted flow resolves to an existing source record. A module inspector exposes implementation class, actual input/output and provenance. |
| Futures / decision / actual | Candidate fingerprints join predictions, values, qualification, selection and the one complete receipt. Unknown and numeric-only futures are rendered accurately. |
| Controls | Live Next Step admits one round; settled Pause admits none; Auto Play respects bounds; rapid/retried clicks and reconnect do not duplicate execution. Replay seeking performs zero execution/learning writes. |
| Recorded experiments | Audited stable/change sessions and adverse comparisons are seekable at event/round level; displayed fields match exported source records and manifests. |
| Experience | Experience counts derive from complete receipts. Version changes require a recorded explicit promotion. Predictions/pending/aborted records never become teachers. |
| Offline / recovery | Built UI, dependencies and replay assets run with external networking disabled. Restart retains records, marks interrupted work honestly, opens Replay and permits a fresh session without redispatch. |
| Compatibility / public mode | Existing Software/Physical UI and API tests pass. Kiosk server rejects generic execution, promotion and arbitrary artifact/model inputs. Core sources and frozen evidence remain unchanged. |
| Exhibit usability | At 1080p, essential quantities and six-module flow remain readable without opening JSON. Rehearse scenario -> step -> selected future -> receipt -> memory within 30 seconds using Replay if Live is waiting. |

The 30-second path is an interface/rehearsal criterion; actual first-time visitor comprehension
requires a human dry run and must not be claimed from automated tests alone.

## Validation and operating instructions

Add meaningful Python integration/negative tests for control admission, pause boundaries,
candidate/receipt joins, private-input absence, replay tampering and projection equivalence,
pending/aborted receipts, public API restrictions and restart/no-redispatch behavior.
Use isolated fixture outputs; preserve every historical benchmark and report byte-for-byte.
Add Playwright coverage for all controls, backward seek, module/branch inspection, mode labels,
disconnect/reconnect, reduced motion and existing Software/Physical views. Exercise an actual
local API and Chromium rather than reporting frontend mocks as integration.

Run the relevant tests after each substantial change, then the repository's Python suite, Ruff
lint/format, generated-contract consistency, TypeScript/Vite build and Chromium E2E. Audit source
and exported replay evidence, compare frozen-file/Core hashes to the approved baseline, and run
an external-network-blocked localhost rehearsal and bounded long-running multi-session check.
Report optional/external validation separately. Do not run benchmark reproduction during a booth
session; provision dependencies, compiled assets and audited local records beforehand.

Provide one documented localhost launch path, a health/preflight check, Pause/New Session,
browser reload/reconnect, process restart and immediate recorded replay fallback. On restart,
retain incomplete execution records for inspection; never automatically replay an intent.
Maintain milestones, command outcomes, remaining gaps and the next action in
`docs/agent-progress.md` in the implementation checkout.

## Reviewed references

- [PR #18](https://github.com/bosakun/preact/pull/18)
- [Common learning design](https://github.com/bosakun/preact/blob/5dd9372c69b2dcc2a49590d02e8642685c79b1bf/docs/common-cognitive-learning.md)
- [Measured results and limitations](https://github.com/bosakun/preact/blob/5dd9372c69b2dcc2a49590d02e8642685c79b1bf/docs/common-cognitive-learning-results.md)
- [Existing independent auditor](https://github.com/bosakun/preact/blob/5dd9372c69b2dcc2a49590d02e8642685c79b1bf/scripts/audit_common_learning.py)
