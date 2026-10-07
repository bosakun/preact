# Composable Runtime World Model

PreAct treats a world model as a **runtime capability**, not necessarily as one learned
neural network. The runtime composes heterogeneous predictors, simulators and verifiers
around an exact current state and candidate action, preserves their provenance and
disagreement, and uses the existing evidence gate to decide whether execution is justified.

This is an architectural evolution from the original wording "PreAct is not a world
model". The invariant that still matters is narrower and stronger:

> PreAct is not a monolithic learned world model, and it never turns model agreement into
> execution authority. It constructs an inspectable action-conditioned world model at
> runtime from heterogeneous engines.

## Core objects

The existing domain-independent contracts remain authoritative:

- `State`: immutable observed or hypothetical world state.
- `Action`: exact state-conditioned operation and fingerprint.
- `Prediction`: one engine's forecast or measurement.
- `Evaluation`: conservative synthesis used by the Decision Gate.
- `Observation`: authoritative result after the one authorized action.

The new `WorldModelSnapshot` is a read-only composition over those contracts. It records:

- exact state and action identity;
- every contributing prediction and engine version;
- evidence type and correlation family;
- which contributions are measured;
- successor or stochastic future hypotheses;
- unresolved mandatory checks;
- the exact `Evaluation` used by the Decision Gate.

It does **not** invent a hidden fused probability and it does not claim that different
families are statistically independent.

## Runtime loop

```text
Authoritative State
       |
       v
Candidate Action
       |
       v
+-----------------------+
| heterogeneous engines |
|                       |
| LLM / reasoner        |
| tests / sandbox       |
| physics simulator     |
| visual world model    |
| symbolic verifier     |
+-----------+-----------+
            |
            v
       Predictions
            |
            v
  WorldModelSnapshot
            |
            v
  Evaluation + Gate
   /       |        \
EXECUTE  VERIFY    ABSTAIN
   |
   v
one real action
   |
   v
Observed Outcome
   |
   v
Prediction Ledger
   |
   v
contextual calibration
```

## Why this is not just "use a simulator"

A simulator is one possible `FutureEngine`. PreAct is responsible for:

1. selecting applicable engines under cost/deadline constraints;
2. preserving heterogeneous evidence without laundering inference into measurement;
3. escalating when required checks, uncertainty or measured disagreement remain unresolved;
4. searching multi-step consequences while authorizing only the first action;
5. comparing the executed prediction with reality and updating contextual engine reliability.

A software task may compose reasoning + tests + sandbox execution. A physical task may
compose reasoning + geometric checks + MuJoCo/Isaac + a visual world model. Other domains
can provide different engines while retaining the same Core.

## Safety properties

The composable world model is deliberately non-authoritative by itself.

- Inference cannot satisfy mandatory measured checks.
- Repeated samples from one correlation family do not gain independent votes.
- Conflicting qualified measurements remain unresolved until stronger evidence arrives.
- Unknown evidence remains unknown.
- Engine failures never become successful task evidence.
- Only an observed outcome labels calibration data.
- Unexecuted future branches remain unlabeled.
- The Decision Gate remains the sole execution authorization boundary.

## First implementation slice

`src/preact/core/world_model.py` introduces:

- `FutureHypothesis`
- `EngineContribution`
- `WorldModelSnapshot`
- `compose_world_model(...)`

The Runtime emits a `world_model_snapshot` event whenever a node receives new prediction
evidence. This makes the composed model inspectable by the API/UI without changing existing
search, gate or execution semantics.

The next implementation slices should be evidence-driven:

1. expose snapshots directly in the API/UI;
2. add engine claim/applicability metadata so routing can be claim-specific rather than
   mostly tier/domain based;
3. represent consequence propagation explicitly in the future graph;
4. use contextual reliability and expected decision impact to route additional engines;
5. validate the abstraction with at least one third domain or a deliberately heterogeneous
   Physical configuration, so "composable" is demonstrated rather than only asserted.
