"""Small domain-independent supervised dynamics learner and JSON model format."""

import hashlib
import inspect
import json
import math
from pathlib import Path
from statistics import fmean, pvariance
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from preact.core.models import Action, Contract, State, identity
from preact.core.store import Artifacts

from .transitions import Transition, TransitionDataset


class DynamicsAdapter(Protocol):
    """Pure conversion of visible data. All configuration must appear in specification.

    Targets may only use the aligned before/action/after, never private World data,
    predictions or beliefs. Features also support hypothetical inputs without
    claiming that those inputs are observations. Adapter code is version/hash bound.
    """

    specification: dict
    feature_names: tuple[str, ...]
    target_names: tuple[str, ...]

    def features(self, state: State, action: Action) -> tuple[float, ...]: ...
    def targets(self, transition: Transition) -> tuple[float, ...]: ...
    def decode(
        self, state: State, action: Action, cell: "DynamicsCell"
    ) -> tuple[dict[str, float], dict[str, tuple[float, float]]]: ...


def adapter_identity(adapter: DynamicsAdapter) -> str:
    source = inspect.getsourcefile(type(adapter))
    if source is None:
        raise ValueError("Adapter must have inspectable versioned source")
    return identity(
        {
            "specification": adapter.specification,
            "features": adapter.feature_names,
            "targets": adapter.target_names,
            "code": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        }
    )


def _vector(values: tuple[float, ...], size: int) -> tuple[float, ...]:
    if len(values) != size or any(not math.isfinite(v) for v in values):
        raise ValueError("Adapter returned an invalid feature/target vector")
    return tuple(float(v) for v in values)


class TrainingConfig(Contract):
    schema_version: Literal["1"] = "1"
    min_samples: int = Field(default=4, ge=1)
    seed: int = 0


class DynamicsCell(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"
    features: tuple[float, ...]
    count: int = Field(ge=1)
    mean: tuple[float, ...]
    variance: tuple[float, ...]
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]


class DynamicsModel(Contract):
    """Immutable scalar/tuple model, derived data rather than current authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal["1"] = "1"
    algorithm: Literal["conditional-mean/v1"] = "conditional-mean/v1"
    domain: str
    adapter_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    feature_names: tuple[str, ...]
    target_names: tuple[str, ...]
    dataset_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    training_code_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    episode_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    receipts: tuple[str, ...]
    outcome_references: tuple[str, ...]
    min_samples: int = Field(ge=1)
    seed: int
    cells: tuple[DynamicsCell, ...]

    @model_validator(mode="after")
    def valid_cells(self):
        keys = set()
        for cell in self.cells:
            if cell.features in keys or len(cell.features) != len(self.feature_names):
                raise ValueError("Invalid or duplicate dynamics feature cell")
            keys.add(cell.features)
            if any(
                len(vector) != len(self.target_names)
                for vector in (cell.mean, cell.variance, cell.minimum, cell.maximum)
            ) or any(
                not lo <= mean <= hi or variance < 0
                for lo, mean, hi, variance in zip(
                    cell.minimum, cell.mean, cell.maximum, cell.variance
                )
            ):
                raise ValueError("Invalid dynamics target statistics")
        count = sum(cell.count for cell in self.cells)
        if (
            count != len(self.receipts)
            or len(set(self.receipts)) != count
            or len(self.outcome_references) != count
            or len(set(self.run_ids)) != len(self.run_ids)
            or len(set(self.episode_ids)) != len(self.episode_ids)
        ):
            raise ValueError("Invalid training record identities")
        return self

    @property
    def version(self) -> str:
        return identity(self.model_dump())

    def compatible(self, adapter: DynamicsAdapter) -> None:
        if (
            self.adapter_hash != adapter_identity(adapter)
            or self.domain != adapter.specification["domain"]
            or self.feature_names != adapter.feature_names
            or self.target_names != adapter.target_names
        ):
            raise ValueError("Dynamics model and adapter are incompatible")

    def lookup(self, features: tuple[float, ...]) -> DynamicsCell | None:
        key = _vector(features, len(self.feature_names))
        return next(
            (
                cell
                for cell in self.cells
                if cell.features == key and cell.count >= self.min_samples
            ),
            None,
        )

    def save(self, artifacts: Artifacts) -> str:
        return artifacts.json({"model_version": self.version, "model": self.model_dump()})

    @classmethod
    def load(cls, artifacts: Artifacts, digest: str, adapter: DynamicsAdapter) -> "DynamicsModel":
        payload = json.loads(artifacts.read(digest))
        if set(payload) != {"model_version", "model"}:
            raise ValueError("Invalid dynamics artifact envelope")
        model = cls.model_validate(payload["model"])
        if model.version != payload["model_version"]:
            raise ValueError("Dynamics model identity mismatch")
        model.compatible(adapter)
        return model


class TabularDynamicsTrainer:
    """Batch conditional means of actual observed state changes; no EMA or oracle.

    The recorded seed is reserved for provenance: this algorithm uses no randomness.
    The dataset is reread/receipt-validated for every fit; callers cannot inject an
    unverified TransitionSnapshot into this public training entry point.
    """

    async def fit(
        self,
        dataset: TransitionDataset,
        adapter: DynamicsAdapter,
        config: TrainingConfig | None = None,
        *,
        limit: int | None = None,
    ) -> DynamicsModel:
        if limit is not None and limit < 0:
            raise ValueError("Training limit must be nonnegative")
        config = TrainingConfig.model_validate((config or TrainingConfig()).model_dump())
        signature = adapter_identity(adapter)
        snapshot = await dataset.snapshot()
        snapshot.verify_identity()
        rows = snapshot.transitions if limit is None else snapshot.transitions[:limit]
        groups: dict[tuple[float, ...], list[tuple[float, ...]]] = {}
        for row in rows:
            if row.before.domain != adapter.specification["domain"]:
                raise ValueError("Training domain mismatch")
            # Adapters receive copies; they cannot silently rewrite the teacher.
            isolated = row.model_copy(deep=True)
            features = _vector(
                adapter.features(isolated.before, isolated.action), len(adapter.feature_names)
            )
            target = _vector(adapter.targets(isolated), len(adapter.target_names))
            if isolated != row:
                raise ValueError("Adapter mutated training data")
            groups.setdefault(features, []).append(target)
        cells = []
        for features, targets in sorted(groups.items()):
            columns = list(zip(*targets))
            cells.append(
                DynamicsCell(
                    features=features,
                    count=len(targets),
                    mean=tuple(fmean(col) for col in columns),
                    variance=tuple(pvariance(col) for col in columns),
                    minimum=tuple(min(col) for col in columns),
                    maximum=tuple(max(col) for col in columns),
                )
            )
        if adapter_identity(adapter) != signature:
            raise ValueError("Adapter changed during training")
        return DynamicsModel(
            domain=adapter.specification["domain"],
            adapter_hash=signature,
            feature_names=adapter.feature_names,
            target_names=adapter.target_names,
            dataset_hash=identity([r.model_dump() for r in rows]),
            training_code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            episode_ids=tuple(dict.fromkeys(r.episode_id for r in rows)),
            run_ids=tuple(dict.fromkeys(r.run_id for r in rows)),
            receipts=tuple(r.receipt for r in rows),
            outcome_references=tuple(r.outcome_reference for r in rows),
            min_samples=config.min_samples,
            seed=config.seed,
            cells=tuple(cells),
        )
