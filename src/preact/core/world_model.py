from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field

from .disagreement import measured as is_measured
from .models import (
    Action,
    Contract,
    Estimate,
    Evaluation,
    EvidenceKind,
    Prediction,
    State,
    Task,
    identity,
)


class FutureHypothesis(Contract):
    """One engine's explicit contribution to the runtime-composed future model."""

    state: State
    source_prediction_id: str
    engine_id: str
    engine_version: str
    family: str
    evidence: EvidenceKind
    measured: bool
    label: str
    probability: Estimate | None = None


class EngineContribution(Contract):
    """How one prediction participates in the composed model.

    Distinct families are reported for provenance and disagreement accounting only.
    They are never claimed to be statistically independent.
    """

    prediction_id: str
    engine_id: str
    engine_version: str
    family: str
    evidence: EvidenceKind
    roles: list[Literal["forecast", "measurement", "successor_model"]]
    measured: bool
    mandatory_checks: dict[str, bool | None] = Field(default_factory=dict)
    violations: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class WorldModelSnapshot(Contract):
    """Action-conditioned runtime world model assembled from heterogeneous engines."""

    snapshot_id: str
    state_id: str
    domain: str
    action_id: str
    action_fingerprint: str
    horizon: int
    prediction_ids: list[str]
    evidence_kinds: list[EvidenceKind]
    families: list[str]
    correlated_families: list[str]
    measured_families: list[str]
    contributions: list[EngineContribution]
    futures: list[FutureHypothesis]
    evaluation: Evaluation
    unresolved_checks: list[str]
    composition_scope: str = (
        "Runtime composition of heterogeneous forecasts and measurements; "
        "family diversity is provenance, not an independence claim"
    )


def compose_world_model(
    state: State,
    action: Action,
    predictions: list[Prediction],
    evaluation: Evaluation,
    task: Task,
) -> WorldModelSnapshot:
    """Build a conservative, inspectable runtime model for one state/action pair.

    This function does not average heterogeneous futures into a single hidden belief.
    It preserves every engine's provenance and delegates aligned claim semantics to the
    existing Evaluation used by the Decision Gate.
    """

    if action.state_id != state.id:
        raise ValueError("Action is not conditioned on the supplied state")
    if state.domain != task.domain:
        raise ValueError("State and task domains do not match")
    if not predictions:
        raise ValueError("A runtime world model requires at least one prediction")

    horizons = {prediction.horizon for prediction in predictions}
    if len(horizons) != 1:
        raise ValueError("Cannot compose predictions with different horizons")
    horizon = next(iter(horizons))

    for prediction in predictions:
        if prediction.state_id != state.id or prediction.action_ids != [action.id]:
            raise ValueError("Prediction lineage does not match the composed action")

    contributions: list[EngineContribution] = []
    futures: list[FutureHypothesis] = []
    family_counts = Counter(prediction.family for prediction in predictions)

    for prediction in predictions:
        measurement = is_measured(prediction)
        roles: list[Literal["forecast", "measurement", "successor_model"]] = ["forecast"]
        if measurement:
            roles.append("measurement")
        if prediction.successor is not None or prediction.outcomes:
            roles.append("successor_model")

        contributions.append(
            EngineContribution(
                prediction_id=prediction.id,
                engine_id=prediction.engine_id,
                engine_version=prediction.engine_version,
                family=prediction.family,
                evidence=prediction.evidence,
                roles=roles,
                measured=measurement,
                mandatory_checks=prediction.mandatory_checks,
                violations=prediction.violations,
                assumptions=prediction.assumptions,
            )
        )

        if prediction.successor is not None:
            futures.append(
                FutureHypothesis(
                    state=prediction.successor,
                    source_prediction_id=prediction.id,
                    engine_id=prediction.engine_id,
                    engine_version=prediction.engine_version,
                    family=prediction.family,
                    evidence=prediction.evidence,
                    measured=measurement,
                    label="successor",
                )
            )
        for outcome in prediction.outcomes:
            futures.append(
                FutureHypothesis(
                    state=outcome.state,
                    source_prediction_id=prediction.id,
                    engine_id=prediction.engine_id,
                    engine_version=prediction.engine_version,
                    family=prediction.family,
                    evidence=prediction.evidence,
                    measured=measurement,
                    label=outcome.label,
                    probability=outcome.probability,
                )
            )

    prediction_ids = [prediction.id for prediction in predictions]
    snapshot_id = identity(
        {
            "state_id": state.id,
            "action_fingerprint": action.fingerprint,
            "horizon": horizon,
            "prediction_ids": sorted(prediction_ids),
        }
    )

    return WorldModelSnapshot(
        snapshot_id=snapshot_id,
        state_id=state.id,
        domain=state.domain,
        action_id=action.id,
        action_fingerprint=action.fingerprint,
        horizon=horizon,
        prediction_ids=prediction_ids,
        evidence_kinds=sorted(
            {prediction.evidence for prediction in predictions},
            key=lambda evidence: evidence.value,
        ),
        families=sorted(family_counts),
        correlated_families=sorted(
            family for family, count in family_counts.items() if count > 1
        ),
        measured_families=sorted(
            {prediction.family for prediction in predictions if is_measured(prediction)}
        ),
        contributions=contributions,
        futures=futures,
        evaluation=evaluation,
        unresolved_checks=sorted(
            check for check, result in evaluation.checks.items() if result is not True
        ),
    )
