# Composable World Model Runtime architecture evolution

長期の上位目的と設計承認境界は[Runtime設計](composable-world-model-runtime.md)と
AGENTS.mdに記録する。以下の移行監査は当時の履歴として保持し、実装済みの契約を
未実装と読み替えない。PR #8の学習基盤と、承認された3 tick Action比較の現状は
[Dynamics設計](learned-dynamics.md)と[実測結果](learned-dynamics-results.md)を参照する。

Status: accepted user direction, October 8, 2026. This supersedes the narrow
"not a world model" product framing in implementation-plan.md. PreAct is not a
monolithic learned world model. Implementation must precede README claims.

## Audit and baseline

The Python 3.12 / Pydantic / asyncio Core already shares registry, adaptive
verification, best-first search/progressive widening, hard gate, durable execution
intent, observation, prediction ledger and contextual calibration across Software
and Physical Worlds. Software materializes patches and runs protected executable
probes. Physical materializes kinematic hypotheses and measures MuJoCo rollouts.
Heavy/vendor computation stays behind engines/workers. Keep the React Future Tree.

Missing semantics: precise claim identity; narrow verifiers; claim-specific routing;
multi-horizon findings; no-intervention consequence forecasts; safe sequence lineage;
provenance spanning both Prediction and Observation; sound transposition reuse.
Runtime currently sends one action at each hypothetical state. Registry checks only
the first action's state_id. A list of actions is NOT an established sequence contract.

The preceding October 8 implementation is discarded, including forced-action
workarounds, observer role, string-only claims and risk-envelope collapse. Preserve
pre-existing October 6 publication progress and all historical evaluation evidence.

## Responsibilities, ownership and lifecycle

| Concept | Responsibility / input / output | Invariants / owner / lifecycle |
| --- | --- | --- |
| State | Normalized observed or hypothetical world content | Domain observes; engines hypothesize; Core checks identity/domain/lineage |
| Action | Intervention bound to input State | Domain proposes/validates/executes; preserve fingerprint; execute one at a time |
| Prediction | Conditional engine result, not observation authority | Engine produces; Registry validates identity, capabilities and conditions; ledger retains |
| Observation | Actual observed state and execution outcome | Domain owns authority; Runtime aligns receipt with durable execution; never convert to Prediction |
| Evidence finding | Claim-qualified projection of source result | Core projects; retains source kind/reference, provenance, uncertainty and correlation |
| Capabilities | Episode-independent supported problems and roles | Engine declares; Registry freezes; no episode State/Action IDs |
| FutureEngine | Predictor/simulator/verifier computation | No observer privilege; unchanged async predict envelope; no mandatory irrelevant methods |
| Verification Planner | Select next claim/engine/request | Core plans; Runtime reserves/dispatches/cancels/persists; cannot authorize action |
| Evaluation | Fuse compatible findings per claim instance | Core retains scope/horizon, unknowns and contradictions; no fake independent votes |
| Gate | Explicit evidence-based action authorization | Core binds state/action/findings/policy; hard constraints stay hard |
| Ledger/calibration | Compare executed aligned forecasts with reality | Durable Store; only valid horizon-1 labels initially; preserve version/context/bias safeguards |

Keep State, Action, Prediction and FutureEngine names. Do not create a parallel
world-state, forecast or evidence ledger. Extend existing envelopes where coherent.

## Target contracts

ClaimDefinition describes namespace/name/version, scope kind (root action or action
sequence), value meaning (check/success/risk) and temporal interpretation. Capabilities
match definitions plus domains, continuation support and maximum horizon.
ClaimInstance binds a definition to task/context, input State ID, horizon, ordered
Action IDs/fingerprints, continuation and trusted structured conditions. Canonical
structure defines equality; hash is an index, never a replacement for that structure.
Free-form assumptions remain provenance, not proof of equivalent conditions.

Task.required_checks and success/risk metric strings remain supported. Normalize
unversioned checks to task-local legacy definitions; no automatic equivalence with
new versioned metrics. New task requirements specify definition/horizon/continuation;
Runtime binds concrete instances per action. Old task defaults remain horizon 1.

Continuation is environment_only or explicit_action_sequence. [A], horizon>1 means
A then no additional Agent intervention, using domain-defined dynamics/version/step
units. Core must not invent environmental dynamics or extra interventions.
[A,B,C] means a particular optional intervention path; its failure cannot hard-veto
A alone. Domain-defined mixed rollouts are deferred.

Multiple-action requests require explicit transitions for the first n-1 actions:
input state, producing Prediction reference, chosen hypothetical successor/outcome.
Registry validates each action.state_id and exact accepted source lineage/conditions;
terminal state belongs to the final input, not universally to the root. Ambiguous
legacy multi-action lists fail closed. Existing single-action requests remain valid.

Evidence findings retain source_kind/source_reference. Prediction-derived evidence
retains engine/version/kind/family/cost/latency/qualification. Observation-derived
evidence references actual recorded execution receipt, input state/action and observed
trajectory. Initial observations ground current state; they do not label actions.
No Observation is attached to alternatives, later interventions or unobserved horizons.

Roles are predictor/simulator/verifier only. Do not add speculative modalities.
Narrow claim measurements do not need paired success/risk measurements. Legacy
qualification remains conservative; checks never manufacture risk probabilities.
Family is today's conservative correlation boundary, resolved centrally; it is not
permanently identical to evidence correlation. Refinement requires exact claim,
condition and family compatibility. Contradictory hard evidence remains visible.

Evaluation retains claim-instance findings and required resolution. Existing
success_lower/risk_upper summarize immediate root claims only. Gate checks each
mandatory future claim; unknown -> VERIFY_MORE when obtainable, otherwise ABSTAIN.
Preserve severity/reversibility/uncertainty per claim/horizon/path; do not invent risk
products, severity sums, or redefine the existing cumulative_risk display field.

Planner explicitly considers stakes, unresolved claims, risk, uncertainty,
disagreement, trust/drift, context, family correlation, cost/latency and budget.
Requests are tracked by engine+instance+settings, not merely engine ID. Ranking is
inspectable heuristic prioritization, not a claimed probabilistic value-of-information.

Future Tree and agent-action search stay usable. Consequence forecasts use separate
no-intervention dynamics, not forced Actions. Reuse requires compatible state,
claims/horizons, path conditions, context, remaining depth, engine config and policy;
otherwise re-expand. Cache/reuse never creates an independent source.
Authorization binds state/action/required instances/evidence/evaluation/policy and is
rechecked after durable intent. Observe after exactly one real action. Calibration
continues only on aligned executed horizon-1 results; long-horizon labeling is deferred.

## Stages and exact files

0. Scoped discard, baseline tests, save this plan, record frozen hashes. Update
   docs/implementation-plan.md and docs/agent-progress.md before implementation.
1. core/models.py, interfaces.py, registry.py; new core/evidence.py: definitions,
   instances, roles, conditioning, source-aware qualification; new contract tests.
2. core/verification.py, decision.py, disagreement.py, runtime.py: claim routing,
   narrow verifier cooperation, correlation/refinement, mandatory future gate.
3. core/runtime.py and consequence processing; new domain/task fixture and tests:
   environmental delayed damage, sequence lineage, conservative transposition.
4. core/runtime.py, comparison.py, relevant store boundaries: actual Observation
   findings, alignment, authorization binding and ledger/calibration regressions.
5. engines/local.py, reasoning.py, nebius.py, llamacpp.py, sandbox.py, isaac.py,
   cosmos.py and remote.py/workers where necessary: accurate declarations and wire
   compatibility. web generated contracts only; no graph/UI rewrite. README.md,
   AGENTS.md, pyproject description, integrations/architecture/progress docs.

At each stage run targeted tests, repair failures, record actual evidence. Remote
strict-schema workers receive legacy requests on legacy paths; expanded contracts
require proven compatibility, never silently dispatch unsupported wire fields.
No vendor/domain imports in Core. No publication/deployment/paid infrastructure.

## Validation and acceptance

Tests: definition vs instance identity; legacy checks; roles/applicability; narrow
verifier+predictor cooperation; family/cache deduplication; scoped refinement;
root-only delayed harm vs optional future harmful action; horizon-specific findings;
missing future verifier; environment intervention rejection; sequence forged lineage;
context-sensitive reuse; source-aware Observation; unexecuted/no long-horizon labels;
durable intent and authorization tampering; both real reference worlds and release.
Add new consequence fixtures. Do not edit frozen tasks/protocol/calibration/artifacts
or bypass their source hashes. New benchmark uses new protocol/output paths.

Final commands: UV_CACHE_DIR=.cache/uv uv run --offline pytest -q;
uv run --offline ruff check src tests workers scripts;
uv run --offline ruff format --check src tests workers scripts;
npm --prefix web run contracts/build/test:e2e (separate commands);
actual software/physical/release demos; local benchmark/ablations/workflows;
uv build and scripts/audit_distributions.py with new output paths.
Record commands, counts, failures and scopes; previous implementation's results do
not qualify this implementation. Verify frozen file hashes remain unchanged.

Done means real heterogeneous cooperation/routing, sound claim-scoped consequences,
shared worlds, safety/observation/calibration integrity, passing relevant regression
and accurate docs, not interfaces alone. Remaining constraints: bounded search,
model/dynamics assumptions, unknown correlations, less reuse, no long-horizon actual
labels. Nebius/ConTree/Isaac/Cosmos/GPU/cloud need real access/hardware; keep boundaries
and report pending validation without substituting local or mocked results.
