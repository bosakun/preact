/* Generated from PreAct playground contracts. Run npm run contracts; do not edit manually. */

export type Playground =
  PlaygroundStart | PlaygroundCommand | PlaygroundEvent | PlaygroundSession | PlaygroundSnapshot | PlaygroundRecording;
export type SchemaVersion = string;
export type Scenario = "stable_high" | "stable_low" | "high_to_low" | "low_to_high";
export type Seed = number;
export type SchemaVersion1 = string;
export type Command = "step" | "auto" | "pause" | "stop";
export type Revision = number;
export type SchemaVersion2 = string;
export type Index = number;
export type Reference = string;
export type Scope = "world" | "learning" | "lifecycle";
export type SchemaVersion3 = string;
export type RunId = string;
export type Seq = number;
export type Kind = string;
export type Timestamp = string;
export type ReceiptVerified = boolean;
export type SchemaVersion4 = string;
export type Id = string;
export type Label = string;
export type Mode = "live" | "recorded";
export type Scenario1 = "stable_high" | "stable_low" | "high_to_low" | "low_to_high";
export type Status = string;
export type Revision1 = number;
export type Rounds = number;
export type Auto = boolean;
export type EventCount = number;
export type Error = string | null;
export type SchemaVersion5 = string;
export type Events = PlaygroundEvent[];
export type Reset = boolean;
export type SchemaVersion6 = string;
export type Events1 = PlaygroundEvent[];
export type Digest = string;

export interface PlaygroundStart {
  schema_version: SchemaVersion;
  scenario: Scenario;
  seed: Seed;
}
export interface PlaygroundCommand {
  schema_version: SchemaVersion1;
  command: Command;
  revision: Revision;
}
export interface PlaygroundEvent {
  schema_version: SchemaVersion2;
  index: Index;
  reference: Reference;
  scope: Scope;
  event: RunEvent;
  receipt_verified: ReceiptVerified;
}
export interface RunEvent {
  schema_version: SchemaVersion3;
  run_id: RunId;
  seq: Seq;
  kind: Kind;
  timestamp: Timestamp;
  data: Data;
}
export interface Data {
  [k: string]: unknown;
}
export interface PlaygroundSession {
  schema_version: SchemaVersion4;
  id: Id;
  label: Label;
  mode: Mode;
  scenario: Scenario1;
  status: Status;
  revision: Revision1;
  rounds: Rounds;
  auto: Auto;
  event_count: EventCount;
  error: Error;
  provenance: Provenance;
}
export interface Provenance {
  [k: string]: unknown;
}
export interface PlaygroundSnapshot {
  schema_version: SchemaVersion5;
  session: PlaygroundSession;
  events: Events;
  artifacts: Artifacts;
  reset: Reset;
}
export interface Artifacts {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface PlaygroundRecording {
  schema_version: SchemaVersion6;
  session: PlaygroundSession;
  events: Events1;
  artifacts: Artifacts1;
  digest: Digest;
}
export interface Artifacts1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
