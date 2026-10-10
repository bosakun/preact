# Composable World Model Runtime

## 長期的な目的

PreActは世界モデルの機能を交換可能なモジュールとして再現し、認知・予測・
シミュレーション・意思決定・実行を統合する。目指すのは、エージェントが環境の
内部モデルを構築し、複数Actionの未来を不確実性も含めて比較し、適切に実行し、
実結果からモデルと行動を改善し続ける能力である。
特定のニューラル構造・学習手法・表現を固定しない。この目的は到達済み能力の宣言ではない。
能力・重要責務・データ意味・安全境界・大幅なスコープ変更は設計承認を要し、
承認内の実装・修正・検証は自律的に進める。詳細はAGENTS.mdを参照する。

PreAct is not a monolithic learned world model. It is a domain-independent runtime
that composes heterogeneous prediction, simulation and verification engines to
construct and verify task-relevant futures before an agent acts.

The accepted [migration plan](composable-world-model-runtime-plan.md) is the source
of truth for this evolution. Python 3.12, Pydantic and asyncio remain the Core;
heavy computation and vendors remain behind engine/worker boundaries. Software
and Physical Worlds are reference domains using that same Core.

## Responsibilities and contracts

State remains the normalized observed/hypothetical state. Domain Adapters own
observation, proposal, validation and real execution; sensing is outside Core.
Action remains an intervention bound by state_id and its existing fingerprint.
Prediction remains the conditional engine result envelope. No WorldState/Forecast
rename or parallel evidence ledger is introduced.

ClaimDefinition describes namespace, name, definition version, value kind,
scope and temporal meaning. Capabilities match that episode-independent definition,
domain, continuation and maximum horizon. They never contain episode State/Action IDs.
ClaimInstance adds the task/context, input State ID, horizon, ordered Action IDs and
fingerprints, continuation, and structured conditions. Its canonical structure is
retained alongside an indexing hash. Engine/version/cost/family belong to provenance.

Task.required_checks and existing metric/version strings remain supported. Legacy
checks are task-local definitions, not automatically equivalent to newly versioned
checks. Task.future_requirements adds mandatory root-only requirements; Runtime binds
them to each candidate. A new definition remains a separate obligation even at
horizon 1. Evaluation.immediate_claim_keys explicitly identifies the original
immediate summary claims.

FutureEngine retains async predict(request) -> Prediction. Roles are predictor,
simulator and verifier; none grants observation authority. Capabilities declares
supported_claims, continuations, max_horizon, successor support and existing
identity/version/family/refinement/resource metadata. No speculative modality
fields are added. A narrow verifier returns only requested ClaimResults and may
leave paired success/risk estimates unknown.

Registry records the admitted request scope as Prediction.requested_claims. Legacy
envelope fields are projected only into those exact instances; an unrequested check
or a conditional horizon-1 result cannot resolve an unconditional immediate claim.
Unmodified historical single-action envelopes retain their compatibility path.

## Sources, qualification and decisions

EvidenceFinding is a claim-qualified projection retaining source_kind and
source_reference; it is not a replacement for source records. Prediction findings
retain the complete source digest, engine/version, evidence kind, correlation
boundary, assumptions, refinement, uncertainty, cost and latency. Executable or
simulation evidence only measures its actual declared claims. Legacy paired
measurement safeguards remain; inference/video cannot promote confidence into
measurement, and a successful check does not create a risk probability.

Observation findings are created separately after a durable execution record is
complete. They reference its receipt, input State/action, observed State and actual
outcome event. Observation is never converted into Prediction. Pending/aborted
execution, another action, unexecuted alternatives and unobserved long horizons
cannot receive those findings. Initial normalized State observations ground current
state and completion; they do not create action outcome labels.

Family is currently a conservative correlation boundary, resolved centrally in
core/evidence.py. It is not a permanent equivalence between engine taxonomy and
evidence correlation. Repeated source IDs/cache results are deduplicated before
all evaluation; conflicting reuse of a source ID fails. Family opinions have one
pooled influence, and qualified bounds stay conservative. Exact-instance/family
refinement can resolve uncertainty but cannot erase a false hard check.

The explicit verification planner chooses Engine + ClaimInstance groups rather
than calling every available engine. It records capability admission, unresolved
coverage, stakes, risk, uncertainty/disagreement, contextual trust/drift,
correlation, estimated cost/latency, horizon and remaining budget. Unadvertised
legacy checks receive no assumed coverage. Estimates and scores are prioritization
heuristics, not billing guarantees or mathematically established VOI.

Evaluation retains findings/resolution per claim and horizon. Its legacy
success_lower/risk_upper summarize immediate action claims only. Gate checks every
mandatory root-only future requirement separately: obtain further evidence when
possible (VERIFY_MORE), otherwise ABSTAIN. Hard checks stay hard; infrastructure
failure leaves evidence missing. Sequence-conditioned negative findings are not
projected into a veto on the root action alone.

## Continuation and lineage

`actions=[A], horizon>1` uses environment_only: execute A, then no additional Agent
intervention, following the Domain's explicit dynamics name/version/time unit.
The dynamics condition declares agent_interventions=none. Core does not fabricate
environment steps or silently propose future actions inside this forecast.
Prediction.future_states can retain its bounded ordered hypothetical trace;
each parent and domain is checked. Trace results cannot masquerade as an immediate
successor. Severity and reversibility remain attached to their claim findings.
Unknown claims and differing horizons stay distinct; no arbitrary probability
product or severity sum is used.

`actions=[A,B,C]` uses explicit_action_sequence. PredictionRequest.conditioning
contains the accepted single-action Prediction references and chosen intermediate
successors/outcomes for A and B, with explicit source claim/context bindings.
Legacy sources lacking those bindings must be reforecast before sequence reuse.
Registry verifies A:S0, B:S1, C:S2, exact state
contents, predecessor identity and compatible source conditions. The terminal
successor belongs to the final Action input. A plain multi-action list is rejected.
The default Future Tree still explores such interventions one node at a time;
automatic combined-sequence request synthesis is not enabled.

Transposition reuse checks state, input/action/path, claims/horizons/conditions,
Task/context, Policy, engine configuration and remaining depth. Free-form assumptions
cannot prove equivalence; uncertain compatibility causes re-expansion. This is
intentionally conservative and may do more work. State content identity still
represents equivalent states without requiring a frontend graph rewrite. The old
cumulative_risk tree field remains a bounded search heuristic, not a fused future
probability or a root authorization criterion.

## Execution, learning and compatibility

Gate authorization binds State, Action fingerprint, required instances, source
findings, Evaluation snapshot and Policy. After durable intent, Runtime rechecks
those bindings and recomputes evaluation from current source records before any
side effect. It executes one action, observes actual outcome, compares aligned
forecasts, updates calibration, then replans. Direct Agent remains an explicitly
ungated experimental baseline used for comparison.

Existing engine/version/context calibration and selection-bias safeguards remain.
Only the actually executed aligned horizon-1 forecast receives a label. A single
Observation cannot calibrate horizon 3 or an unexecuted sequence. Immediate
envelope probabilities from a conditional or check-only request are also excluded
from comparison and calibration, even when their horizon is 1. Immediate
calibration is not transferred as certified long-horizon reliability; unknown
future-context reliability uses a neutral routing prior.

Old single-action engines and serialized envelopes remain accepted. Expanded
claim/conditioning worker requests require claim_contract_version=1. Legacy
workers receive the original wire fields for legacy immediate requests and fail
before submission for unsupported new semantics. Historical archives are not
rewritten. Existing frozen fixtures/protocols/evidence remain unchanged; new
consequence fixtures/protocols have separate paths.

## Executed examples and limits

Real protected software probes cooperate with claim-specific narrow verifiers;
MuJoCo remains a real local simulation under the same Core. New tests/fixtures/
consequences.py executes bounded queue dynamics: fast ingestion is immediately
safe but later overflows without further Agent action. Core rejects it and
executes bounded ingestion once. Separate software sequence tests prove that
choosing a harmful later patch does not veto the safe initial preparation.

Run the reviewable development fixture with:

```bash
uv run python -m scripts.verify_composable_runtime --output .cache/new-consequence-evidence
```

The directory must be new. It retains task/policy/fixture protocol, actual ordered
events, receipt/observation, source findings and the ledger. This is a bounded
first-party development example, not a held-out generalization result.

Limits: finite horizons and budgets; domain/model assumptions; unknown correlations;
conservative reuse; single-path materialized environment traces (branching immediate
outcomes remain supported); no automatic long-horizon observation alignment or
calibration; legacy immediate evaluation remains an incremental compatibility layer.
Cosmos currently has no extracted claim predicate, so video artifacts cannot resolve
Gate claims. Real Nebius/ConTree/Isaac/Cosmos/GPU/cloud acceptance remains pending
where access/hardware is unavailable. This architecture is not a complete world
model, perfect simulator, safety guarantee, human cognitive system or AGI.

## Opt-in model lifecycle boundary

Queue temporalモデルの監視付き入口は`QueueTemporalGuard.compare`である。
`TransitionDataset`が再検証した実経験を基に、固定学習分布と最近の有効標本を比較する。
統計判定の履歴と利用可否は学習処理・Calibration・実行許可から分離される。
Healthごとに不変のEngine viewを作り、呼出しごとに新しいprivate Registryを使う。
失効時はINFERENCE unknownになり、古いRegistryのキャッシュを持ち越さない。
既存`QueueTemporalEngine`の直接利用は従来どおり監視対象外である。

これはQueue専用の初期lifecycle実装であり、世界モデル全体の定義ではない。
receipt検証失敗・不整合・キャンセルは例外で停止し、古い利用可能状態へ戻らない。
Core/Gate/Authorizationを変更せず、Action比較は読取専用のままである。
詳細な統計的仮定・適用範囲は`docs/learned-dynamics.md`を参照する。

### Explicit model recovery boundary

承認済みの次段階は、失効後の確定経験で候補を作成し、別episodeで独立評価、
さらに別の実監視episodeで新しいHealthを確認して明示的に切り替えることである。
候補作成・評価合格・昇格承認・active切替を区別し、既存Artifacts/Storeへ記録する。
再起動時は全根拠と現在観測を再検証する。旧モデルの失効を解除せず、新Guardと
呼出しごとのprivate Registryを構築する。予測はINFERENCEのままで、Gate/Verifier/
Authorizationへの権限変更はない。詳細・固定受入条件はlearned-dynamics.mdを参照。
これはepisodeをまたぐ回復であり、同一Worldの連続回復・自動昇格・rollbackは未実装。
機能的完成を通常の計算性能最適化より優先し、正確性・安全性と計算費用を測定する。
