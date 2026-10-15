# Repository Guidelines

## Project Structure & Module Organization

`src/preact/core/` contains domain-independent contracts, search, verification, gate,
ledger and calibration. Domain adapters live in `src/preact/domains/`; vendor and
simulation engines live in `src/preact/engines/`. FastAPI is in `src/preact/service/`,
the React Future Tree in `web/src/`, and GPU worker boundaries in `workers/`.
Tests are in `tests/` and `web/e2e/`; owned fixtures are in `assets/` and
`src/preact/datasets/`. Claim-scoped evidence/consequences and their new fixtures
live in Core and `tests/fixtures/`. Architecture and acceptance criteria are in `docs/`.
`src/preact/cognition/` adds an observation-grounded executive above Core; its memory
indexes committed execution receipts. See `docs/cognitive-architecture.md`.
Receipt-backed memory uses a rebuildable run cache and read-only Store change tokens;
its consistency boundary is documented in `docs/episodic-memory-index.md`.
`src/preact/learning/` derives receipt-validated transitions and trains opt-in dynamics
models; domain features stay in adapters and learned forecasts never certify safety.
See `docs/learned-dynamics.md` and its dedicated frozen benchmark/results.
Opt-in Queue temporal analysis learns identifiable service observations separately
from total transitions and compares three-tick effects with known arrival rules.
It uses Registry continuation claims, remains inference, and never ranks or executes actions.
Opt-in Queue drift monitoring revalidates receipt/cutoff provenance, replays finite-sample
Fisher windows and suppresses invalidated forecasts through fresh private Registries.
Raw QueueTemporalEngine calls remain unmanaged; health never certifies safety.
See docs/learned-dynamics.md and the separate queue-drift protocol/results.
Explicit Queue recovery reuses receipt-backed training, independent evaluation and a
separate actual monitoring episode before promotion. Restart revalidates all sources;
neither promotion records nor learned forecasts grant execution authority.
The opt-in Queue continuous lifecycle can additionally anchor a new monitoring epoch
to a fresh mid-episode observation and recover repeatedly inside one real World episode.
It retains receipt/store/health fences and creates a fresh epoch per model; it does not
create a second execution path or treat unexecuted actions as experience. See
`docs/continuous-adaptation.md`; its deterministic Queue fixture does not establish
general task-performance improvement.
Native World proposals also support Software cognitive rounds and explicit hypothetical
search, with optional episode-wide budgets. See `docs/software-cognition.md`; Core
verification and execution authority remain unchanged. The Software CLI uses trusted local fixtures.
Opt-in episode-local inference reuse retains fresh observation and receipt retrieval;
see `docs/belief-reuse.md`.
Opt-in queue v2 adds costly, one-tick service sensing through the same Gate and receipts;
see `docs/information-seeking-action.md`. Its tick-dependent Bayesian planner does not
opt in to inference reuse. Samples describe executed ticks and never certify safety.

## Build, Test, and Development Commands

- `uv sync --python 3.12 --frozen --extra dev --extra physical --extra sandbox` installs
  locked Python dependencies.
- `npm --prefix web ci` installs locked frontend dependencies.
- `uv run preact serve` starts the local API; `npm --prefix web run dev` starts Vite.
- `uv run pytest -q` runs Python regressions; `npm --prefix web run test:e2e` runs Chromium cases.
- `uv run ruff check src tests workers scripts` and `uv run ruff format --check src tests workers scripts`
  check Python style. `npm --prefix web run build` checks TypeScript and builds assets.
- `npm --prefix web run contracts` regenerates typed API contracts; commit intentional schema changes.
- `uv run python -m scripts.verify_composable_runtime --output NEW_DIR` retains new local consequence evidence.
- Set `PREACT_E2E_ARTIFACT_DIR` to keep browser captures outside historical reports.
- `uv run python -m preact.cognition.benchmark --output NEW_DIR` runs CPU cognitive comparisons.
  Reproduce and audit Japanese findings with `python -m scripts.summarize_cognition`.
- `uv run python -m scripts.bench_cognitive_memory --output NEW_DIR` compares full-reread,
  cold, warm and invalidated memory retrieval with the frozen memory protocol.
- `uv run python -m scripts.bench_belief_reuse --output NEW_DIR` measures paired queue
  inference reuse and checks normalized Gate/observation/learning equality.
- `uv run python -m scripts.bench_information_seeking --protocol benchmarks/information-seeking-v1.json --output NEW_DIR --report NEW_REPORT`
  audits paired v2 sensing, receipt/Gate integrity, semantic replication and budget controls.

## Coding Style & Naming Conventions

Use Python 3.12, four-space indentation, descriptive snake_case functions and PascalCase
classes. Ruff enforces formatting and a 100-character line target. Follow established
TypeScript/React component conventions. Keep Core independent of domain/vendor imports.

## Testing Guidelines

Use pytest, pytest-asyncio and Hypothesis; name tests `test_*.py` with behavioral names.
No numeric coverage threshold is configured. Verify meaningful state, authority, failure
and ledger properties. Security checks use bounded owned structural negatives; rejected
fixtures must not execute. Fixtures and CPU imports are not live sponsor validation.
Preserve hard constraints, aligned observation/calibration and one-action execution.
Never modify frozen tasks/protocols/evidence to fit new behavior; add new fixtures.

## Commit & Pull Request Guidelines

Use focused feature branches and short imperative commit subjects.
Describe behavior, validation and limitations in pull requests; link issues and include
screenshots for visible changes. Preserve measured benchmark failures and unknown costs.

## Agent & Publication Instructions

Follow `docs/composable-world-model-runtime-plan.md` for the current architecture; maintain concise progress in `docs/agent-progress.md`.
Do not redesign working architecture without measured evidence. Keep credentials, private
caches, raw benchmark bundles, footage and model weights outside tracked files. Distinguish
local execution, archived evaluation and unvalidated Nebius/Cosmos/Isaac integrations.
Publication requires explicit approval of the exact candidate; never publish private history.

## Autonomous Development Rules

### Autonomous Decision Making

#### Long-term purpose and design approval

PreAct modularly reproduces world-model functions and integrates cognition,
prediction, simulation, decision and execution. The intended capability is for an
agent to build an internal environmental model, compare uncertain action futures,
execute appropriately and improve its model and behavior from actual outcomes.
This purpose does not fix a model architecture, learning algorithm or representation.

Obtain developer approval before changing the intended capability, important module
responsibilities, major data/state semantics, safety or execution authority boundaries,
or substantially expanding agreed scope. Stop and present alternatives when an
implementation would materially differ from the approved behavior.
Within an approved design, choose implementation details, algorithms and structures,
fix bugs, improve tests, perform local cleanup and performance work autonomously.
Continue through validation and completion; do not request routine design approval again.
These boundaries supplement, rather than remove, the autonomous rules below.

#### Functional completion before computational optimization

Prioritize working, verifiable cognitive/world-model capabilities over routine
speedups, caching, parallelization and performance refactoring. Measure and record
CPU time, wall time, memory and execution cost. Slow experimental implementations
are acceptable when they complete and preserve correctness and safety. Optimize
early only when resource exhaustion, noncompletion or inability to validate blocks
the capability or threatens safety/correctness. Prediction accuracy, model validity,
learning effects, independent evaluation and safety must be evaluated now; they
are not deferred computational optimizations. Preserve benchmarks, audits and failures.
Model recovery does not change this priority into optimization of drift monitoring.

This repository is intended to be developed autonomously by Codex for long-running sessions.

Do not ask the user for routine implementation decisions.

When multiple reasonable implementation approaches exist:

1. inspect available evidence,
2. select the approach that best preserves the architecture and project goal,
3. implement it,
4. test it,
5. revise it if evidence shows the decision was poor.

Do not stop merely because:
- the task is large,
- the architecture is complex,
- multiple reasonable choices exist,
- implementation details were not explicitly specified.

Escalate to the user only when progress genuinely requires information or access that cannot be inferred, obtained, substituted, or deferred.

### Goal Persistence

When an active Goal exists, continue working toward it until its completion criteria are verifiably satisfied.

Do not treat any of the following as completion:

- scaffolding,
- placeholders,
- TODOs,
- mocked core behavior,
- documentation-only claims,
- disconnected demonstrations,
- code that has not been executed,
- tests that have not actually been run.

After each substantial change:

1. run the relevant validation,
2. inspect the evidence,
3. identify the highest-impact remaining gap,
4. continue working.

### Failure Handling

When a command, test, build, benchmark, integration, or implementation fails:

1. diagnose the failure,
2. attempt a repair,
3. rerun the relevant validation,
4. continue iterating.

Do not immediately ask the user how to fix ordinary engineering failures.

If one external integration is blocked, continue all independent work that can still be completed.

### External Dependencies

If credentials, paid services, unavailable hardware, quota limitations, or external infrastructure block one part of the project:

- preserve the real integration boundary,
- do not fake successful integration,
- document the blocker precisely,
- continue all other independent implementation and validation work.

Mocks may be used for isolated tests, but mocked behavior must never be represented as a completed real integration.

### Usage Limit Policy

Never use or request:

- banked resets,
- one-time resets,
- promotional resets,
- paid usage resets,
- extra-credit purchases,
- plan upgrades,

for the purpose of continuing autonomous development.

If the normal usage allowance is exhausted, stop cleanly and preserve progress so work can resume when the standard usage window becomes available again.

Do not intentionally consume any reset entitlement.

### Progress Persistence

Maintain:

docs/agent-progress.md

Update it during long-running work with:

- completed milestones,
- current implementation state,
- validation performed,
- known failures,
- blocked external dependencies,
- remaining work,
- next highest-priority action.

The repository must remain recoverable after interruption.

### Architecture Integrity

Avoid implementing Software World and Physical World as unrelated demos.

Both must use the same domain-independent PreAct Core.

Avoid shortcuts that make the demo appear functional while bypassing the intended architecture.

### Repository Instructions

Keep AGENTS.md synchronized with the actual repository.

If statements about the repository state become outdated as implementation progresses, update them.

Do not remove these autonomous-development rules unless explicitly instructed by the user.

### Opt-in world-model action selection

QueueModelPlanner can rank monitored H3 predictions and propose exactly one action
through QueueSelectionWorld and CognitiveAgent. Keep width=1/max_nodes=1/search=False;
Core still independently verifies and gates execution. Selection traces, local
prediction usage, health and actual complete receipts have separate meanings.
Receipt corruption, source updates, cancellation or inference errors must not become
ordinary model-unavailable fallback. No raw learned score certifies safety.
Frozen selection protocol/results are separate from temporal, drift and recovery
benchmarks. Count formal warmup actions and distinguish fallback-only episodes.
Do not reinterpret environment-only forecasts as closed-loop outcome labels.
Keep the long-term purpose, design approval and functional completion rules above.

### Opt-in verified multi-candidate root selection

Runtime may accept an explicit candidate_selector only for flat root comparison.
With None, preserve default Core utility/search behavior. Runtime owns the frozen
proposal set, independent verification, structured Gate qualification and completion
proof. Unexpanded/unresolved candidates forbid selection; hard vetoes remain vetoes.
The synchronous selector receives defensive copies and returns one qualified node
ID only. It must not predict, call APIs, execute, write Store or alter safety data;
failure never falls back to utility. This is a trusted in-process contract, not an
OS sandbox. Fresh observation, final Gate, authorization, intent and receipt stay
in Runtime. Keep prediction ranking, qualification, final choice and actual outcome
separate. QueueMultiSelectionWorld fences complete State/source/model dependencies
after selection and again after intent. The owned submission-permit fixture is not
normal Queue v2. Preserve all historical protocols/results; measure extra verification
cost and report failed, adverse, tied and fallback-only conditions.

### Opt-in task/information scheduling

QueueTaskPlanner adds non-authoritative task/probe arbitration above the existing
multi-candidate planner. Normal task forecasts, scheduler assumptions/VOI and
Runtime qualification remain separate records. Credit only receipt-identifiable
ordinary observations; never convert censored samples to low service labels.
Local reference calls must use proposal reservation/usage accounting. Runtime
still owns verifier, Gate, authorization, intent and one-action execution.
Continuous recovery keeps the original post-cutoff training, independent H3
assessment and disjoint fresh monitoring requirements. Promotion is an explicit
operator/harness instruction, not scheduler authority. A lost/invalid Recovery
must not become ordinary guard fallback. Preserve all pilot/confirmatory results,
including incomplete recovery, adverse costs and Goal-active model usage.
See docs/task-aware-continuous-adaptation.md and its separate protocol/results.

### Blender visual representation

`src/preact/visual/` and `workers/blender.py` translate physical State and retained
MuJoCo position traces into synthetic RGB, axial metric depth and object IDs.
The opt-in adapter and VisualRuntime observer advertise no safety claims; keep Core,
Verifier, Gate and receipt authority unchanged. State does not retain cube rotation
or gripper mocap position; preserve the documented visual approximations.
Use PREACT_BLENDER=/path/to/blender with `uv run pytest -q tests/test_blender_integration.py`
for actual Blender processes. Contract/process-stub tests alone are not real rendering.
Run `uv run python -m scripts.bench_blender_visual --output NEW_DIR` and the separate
`uv run python -m scripts.audit_blender_visual NEW_DIR`. Keep frozen v1/v2 protocols
and historical failures; retain raw bundles in excluded output paths.

Post-execution visual work belongs to VisualPostprocessor after Core finishes.
Its failure/cancellation/retry must never rewrite run results, receipts, observations
or calibration. Preserve independent failure records and saved-source/receipt fences.

### Opt-in modular deliberation

DeliberationPlanner composes verified Memory/Belief, bounded domain Prediction and
Goal adapters and the existing pure root selector. Core utility/search/defaults and
execution authority stay unchanged. Software explicit paths require Registry-accepted
H1 lineage and declared sequence claims; Queue v1 uses monitored environment-only H3
branches and refuses explicit intervention sequences. Never feed a mean State into
rollout, promote inference to safety evidence, or learn from unexecuted branches.
All costly proposal work must reserve/account calls before Runtime qualification;
selector does no IO or prediction. Reuse the existing selection dispatch fences and
complete receipts. Retain historical protocols and report ties, adverse reward,
uncalibrated uncertainty and unsupported forecasts. See docs/modular-deliberation.md.

### Opt-in common cognitive learning

CognitiveAgent may use a LearningCoordinator at quiescent round boundaries.
Coordinator owns lifecycle control and receipt manifests only; domain LearningBackend
owns features, health, eligibility, evaluation conditions and prediction adapters.
Queue delegates to the existing continuous lifecycle and retains its promotion policy.
Software ProgramWorld learning is closed-vocabulary H1 outcome memory; do not claim
unseen-candidate/task generalization. Protected evaluator/oracle data is not a predictor
input. Numeric outcome forecasts never synthesize successor State or safety evidence.
Explicit promotion reconstructs a coherent planner/adapter/private prediction registry/
selector binding for the next round. Core verification Registry and execution authority
remain independent. None preserves the existing Agent path. Restart must revalidate
receipts, artifacts, held-out evaluation and explicit promotion; cancellation/corruption
must not revive old or unapproved models. Operator round observers may explicitly call
promotion after complete receipts; production defaults never auto-promote. Preserve
all frozen protocols/results and count preparation/evaluation costs separately.
Backend restart returns a receipt-verified continuing episode or None for a new World;
restore Queue ownership and committed run prefixes before the next decision. Never
relabel a restored World. Missing Program cells inside the trusted vocabulary use
labelled native proposal-order fallback for the whole comparison; retain unknown
model predictions and stop on inference/validation corruption instead of falling back.

### OSC cognitive playground

`src/preact/playground/` contains non-authoritative evidence/replay contracts and
projections; `src/preact/service/playground.py` manages bounded exhibition sessions.
Queue fixture construction/preparation/evaluation stays in `domains/queue_playground.py`.
Reuse CognitiveAgent, DeliberationPlanner, LearningCoordinator, QueueLearningBackend
and the ordinary Runtime. Do not add a second decision/learning/execution path.
The `#playground` web entry is separate from the default Software/Physical Future Tree.
Live controls admit one round at a time and pause at the existing observer boundary;
server Policy limits/calibration still apply. Public presets are constructor-only;
never edit authoritative payload/private schedule or expose public promotion.
Replay is a pinned, read-only projection of independently audited source events and
complete receipts. It never imports executions, teachers or calibration observations.
Do not invent module timestamps, predicted States from metric vectors or Actual for
unchosen candidates. Keep historical adverse results and absent promotions visible.
Use the offline launch/recovery and new-output reproduction commands in
`docs/osc-playground.md`; preserve original frozen evidence. Keep captures in a new
PREACT_E2E_ARTIFACT_DIR. Exact projection audits need the retained private source bundle.
