/* Generated from PreAct Core. Run npm run contracts; do not edit manually. */

export type Contracts =
  | State
  | Action
  | Capabilities
  | FutureOutcome
  | GateResult
  | Observation
  | Policy
  | Prediction
  | PredictionRequest
  | RunEvent
  | Task
  | TreeNode;
export type SchemaVersion = string;
export type Id = string;
export type Domain = string;
export type Kind = "observed" | "hypothetical";
export type Timestamp = string;
export type ParentId = string | null;
export type Uncertainty = number;
export type Provenance = string;
export type SchemaVersion1 = string;
export type Id1 = string;
export type Name = string;
export type Kind1 = string;
export type StateId = string;
export type Rationale = string;
export type Duration = number;
export type SchemaVersion2 = string;
export type EngineId = string;
export type Version = string;
export type Family = string;
export type Domains = string[];
export type EvidenceKind = "inference" | "generated_visual" | "executable" | "simulation" | "observation";
export type Tier = number;
export type MaxHorizon = number;
export type ProducesSuccessor = boolean;
export type Applicability = string;
export type RefinesEngineIds = string[];
export type MaxSamples = number;
export type VerificationChecks = string[] | null;
export type EstimatedCostUsd = number | null;
export type EstimatedLatencySeconds = number | null;
export type SchemaVersion3 = string;
export type Label = string;
export type SchemaVersion4 = string;
export type Value = number | null;
export type Lower = number | null;
export type Upper = number | null;
export type Uncertainty1 = number;
export type Measured = boolean;
export type Assumptions = string[];
export type SchemaVersion5 = string;
export type Decision = "execute" | "verify_more" | "abstain" | "task_complete";
export type Reasons = string[];
export type StateId1 = string;
export type ActionHash = string | null;
export type EvidenceIds = string[];
export type PolicyHash = string;
export type ExpiresAt = number;
export type SchemaVersion6 = string;
export type Success = boolean;
export type Unsafe = boolean;
export type Receipt = string;
export type SuccessMetric = string;
export type RiskMetric = string;
export type ExecutionCalls = number;
export type CostUsd = number | null;
export type SchemaVersion7 = string;
export type MaxDepth = number;
export type Width = number;
export type MaxNodes = number;
export type MaxCalls = number;
export type MaxSeconds = number;
export type MaxCostUsd = number;
export type MaxRisk = number;
export type MinSuccess = number;
export type DisagreementThreshold = number;
export type UncertaintyThreshold = number;
export type Search = boolean;
export type Adaptive = boolean;
export type Calibration = boolean;
export type SchemaVersion8 = string;
export type Id2 = string;
export type EngineId1 = string;
export type EngineVersion = string;
export type Family1 = string;
export type StateId2 = string;
export type ActionIds = string[];
export type Horizon = number;
export type SuccessMetric1 = string;
export type RiskMetric1 = string;
export type Violations = string[];
/**
 * @maxItems 10
 */
export type Outcomes =
  | []
  | [FutureOutcome]
  | [FutureOutcome, FutureOutcome]
  | [FutureOutcome, FutureOutcome, FutureOutcome]
  | [FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome]
  | [FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome]
  | [FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome]
  | [FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome, FutureOutcome]
  | [
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome
    ]
  | [
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome
    ]
  | [
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome,
      FutureOutcome
    ];
export type Assumptions1 = string[];
export type LatencyMs = number;
export type CostUsd1 = number;
export type RefinesEngineIds1 = string[];
export type SampleCount = number;
export type SchemaVersion9 = string;
/**
 * @minItems 1
 */
export type Actions = [Action, ...Action[]];
export type Seed = number;
export type Horizon1 = number;
export type DeadlineSeconds = number;
export type SampleBudget = number;
export type SchemaVersion10 = string;
export type RunId = string;
export type Seq = number;
export type Kind2 = string;
export type Timestamp1 = string;
export type SchemaVersion11 = string;
export type Id3 = string;
export type Domain1 = string;
export type Title = string;
export type Goal = string;
export type RequiredChecks = string[];
export type Seed1 = number;
export type MaxSteps = number;
export type SuccessMetric2 = string;
export type RiskMetric2 = string;
export type Stakes = number;
export type SchemaVersion12 = string;
export type Id4 = string;
export type ParentId1 = string | null;
export type Kind3 = "state" | "action" | "outcome";
export type Label1 = string;
export type Depth = number;
export type Predictions = Prediction[];
export type SchemaVersion13 = string;
export type Success1 = number | null;
export type Stakes1 = number;
export type SuccessLower = number;
export type RiskUpper = number;
export type Uncertainty2 = number;
export type Disagreement = number;
export type UnresolvedDisagreement = number;
export type Utility = number;
export type Violations1 = string[];
export type EvidenceIds1 = string[];
export type Status = string;
export type Actual = {
  [k: string]: unknown;
} | null;
export type CumulativeRisk = number;
export type Value1 = number;
export type ReusedFrom = string | null;

export interface State {
  schema_version: SchemaVersion;
  id: Id;
  domain: Domain;
  kind: Kind;
  payload: Payload;
  timestamp: Timestamp;
  parent_id: ParentId;
  uncertainty: Uncertainty;
  provenance: Provenance;
}
export interface Payload {
  [k: string]: unknown;
}
export interface Action {
  schema_version: SchemaVersion1;
  id: Id1;
  name: Name;
  kind: Kind1;
  state_id: StateId;
  payload: Payload1;
  rationale: Rationale;
  duration: Duration;
}
export interface Payload1 {
  [k: string]: unknown;
}
export interface Capabilities {
  schema_version: SchemaVersion2;
  engine_id: EngineId;
  version: Version;
  family: Family;
  domains: Domains;
  evidence: EvidenceKind;
  tier: Tier;
  max_horizon: MaxHorizon;
  produces_successor: ProducesSuccessor;
  applicability: Applicability;
  refines_engine_ids: RefinesEngineIds;
  max_samples: MaxSamples;
  verification_checks: VerificationChecks;
  estimated_cost_usd: EstimatedCostUsd;
  estimated_latency_seconds: EstimatedLatencySeconds;
}
export interface FutureOutcome {
  schema_version: SchemaVersion3;
  label: Label;
  probability: Estimate;
  state: State;
  assumptions: Assumptions;
}
export interface Estimate {
  schema_version: SchemaVersion4;
  value: Value;
  lower: Lower;
  upper: Upper;
  uncertainty: Uncertainty1;
  measured: Measured;
}
export interface GateResult {
  schema_version: SchemaVersion5;
  decision: Decision;
  reasons: Reasons;
  state_id: StateId1;
  action_hash: ActionHash;
  evidence_ids: EvidenceIds;
  policy_hash: PolicyHash;
  expires_at: ExpiresAt;
}
export interface Observation {
  schema_version: SchemaVersion6;
  state: State;
  success: Success;
  unsafe: Unsafe;
  metrics: Metrics;
  vectors: Vectors;
  checks: Checks;
  receipt: Receipt;
  success_metric: SuccessMetric;
  risk_metric: RiskMetric;
  artifacts: Artifacts;
  execution_calls: ExecutionCalls;
  cost_usd: CostUsd;
}
export interface Metrics {
  [k: string]: number;
}
export interface Vectors {
  [k: string]: number[];
}
export interface Checks {
  [k: string]: boolean;
}
export interface Artifacts {
  [k: string]: string;
}
export interface Policy {
  schema_version: SchemaVersion7;
  max_depth: MaxDepth;
  width: Width;
  max_nodes: MaxNodes;
  max_calls: MaxCalls;
  max_seconds: MaxSeconds;
  max_cost_usd: MaxCostUsd;
  max_risk: MaxRisk;
  min_success: MinSuccess;
  disagreement_threshold: DisagreementThreshold;
  uncertainty_threshold: UncertaintyThreshold;
  search: Search;
  adaptive: Adaptive;
  calibration: Calibration;
}
export interface Prediction {
  schema_version: SchemaVersion8;
  id: Id2;
  engine_id: EngineId1;
  engine_version: EngineVersion;
  family: Family1;
  state_id: StateId2;
  action_ids: ActionIds;
  horizon: Horizon;
  evidence: EvidenceKind;
  success_metric: SuccessMetric1;
  risk_metric: RiskMetric1;
  success: Estimate;
  risk: Estimate;
  violations: Violations;
  successor: State | null;
  outcomes: Outcomes;
  metrics: Metrics1;
  metric_intervals: MetricIntervals;
  vectors: Vectors1;
  artifacts: Artifacts1;
  assumptions: Assumptions1;
  mandatory_checks: MandatoryChecks;
  latency_ms: LatencyMs;
  cost_usd: CostUsd1;
  raw: Raw;
  refines_engine_ids: RefinesEngineIds1;
  sample_count: SampleCount;
}
export interface Metrics1 {
  [k: string]: number;
}
export interface MetricIntervals {
  /**
   * @minItems 2
   * @maxItems 2
   */
  [k: string]: [unknown, unknown];
}
export interface Vectors1 {
  [k: string]: number[];
}
export interface Artifacts1 {
  [k: string]: string;
}
export interface MandatoryChecks {
  [k: string]: boolean | null;
}
export interface Raw {
  [k: string]: unknown;
}
export interface PredictionRequest {
  schema_version: SchemaVersion9;
  state: State;
  actions: Actions;
  seed: Seed;
  horizon: Horizon1;
  deadline_seconds: DeadlineSeconds;
  sample_budget: SampleBudget;
}
export interface RunEvent {
  schema_version: SchemaVersion10;
  run_id: RunId;
  seq: Seq;
  kind: Kind2;
  timestamp: Timestamp1;
  data: Data;
}
export interface Data {
  [k: string]: unknown;
}
export interface Task {
  schema_version: SchemaVersion11;
  id: Id3;
  domain: Domain1;
  title: Title;
  goal: Goal;
  required_checks: RequiredChecks;
  seed: Seed1;
  max_steps: MaxSteps;
  success_metric: SuccessMetric2;
  risk_metric: RiskMetric2;
  metric_scales: MetricScales;
  stakes: Stakes;
}
export interface MetricScales {
  [k: string]: number;
}
export interface TreeNode {
  schema_version: SchemaVersion12;
  id: Id4;
  parent_id: ParentId1;
  kind: Kind3;
  label: Label1;
  outcome_probability: Estimate | null;
  state: State;
  action: Action | null;
  depth: Depth;
  predictions: Predictions;
  evaluation: Evaluation | null;
  status: Status;
  actual: Actual;
  cumulative_risk: CumulativeRisk;
  value: Value1;
  reused_from: ReusedFrom;
}
export interface Evaluation {
  schema_version: SchemaVersion13;
  success: Success1;
  stakes: Stakes1;
  success_lower: SuccessLower;
  risk_upper: RiskUpper;
  uncertainty: Uncertainty2;
  disagreement: Disagreement;
  unresolved_disagreement: UnresolvedDisagreement;
  disagreement_by_claim: DisagreementByClaim;
  utility: Utility;
  checks: Checks1;
  violations: Violations1;
  evidence_ids: EvidenceIds1;
}
export interface DisagreementByClaim {
  [k: string]: number;
}
export interface Checks1 {
  [k: string]: boolean | null;
}
