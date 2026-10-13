"""Receipt-trained H1 task outcomes for enumerated, trusted ProgramWorld patches."""

import copy
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from preact.cognition.deliberation import DeliberationPlanner
from preact.cognition.lifecycle import RoundBinding
from preact.cognition.selection import SelectionWorld
from preact.core.evidence import bind_claims, supports
from preact.core.models import Action, PredictionRequest, identity
from preact.core.registry import Registry
from preact.domains.program import ProgramWorld
from preact.domains.program_outcome_features import ProgramOutcomeAdapter
from preact.engines.deliberation_usage import usage_record
from preact.engines.learned_dynamics import LearnedDynamicsEngine
from preact.engines.local import LocalHeuristic, LocalVerifier
from preact.learning import DynamicsModel, TabularDynamicsTrainer, TrainingConfig, TransitionDataset
from preact.learning.transitions import require_disjoint


@dataclass(frozen=True)
class ProgramPromotionPolicy:
    samples_per_candidate: int = 4
    evaluation_per_candidate: int = 4
    max_preparation_actions: int = 16
    max_goal_mae: float = 0.25
    prior: float = 0.5


@dataclass(frozen=True)
class ProgramGoalAdapter:
    def signature(self):
        return identity({"adapter": "program-goal-probability/v1"})

    def evaluate(self, future, state, goals):
        if len(goals) != 1 or goals[0].metric != "goal_complete":
            raise ValueError("Program outcome value requires goal_complete")
        return {"value": future["value"], "rule": future["basis"], "calibrated": False}


class ProgramPredictionAdapter:
    def __init__(self, backend, world, checkpoint):
        self.backend, self.world, self.store = backend, world, backend.store
        self.checkpoint = copy.deepcopy(checkpoint)
        self.max_calls, self.usage = 2, []

    def signature(self):
        return identity(
            {
                "adapter": "program-h1/v1",
                "task": self.world.task.model_dump(),
                "checkpoint": self.checkpoint,
            }
        )

    async def heads(self, runs):
        return await self.store.call(
            "run_heads", list(dict.fromkeys([*runs, *self.backend.source_runs]))
        )

    async def candidates(self, state, width):
        return await self.world.propose(state, width)

    async def check_basis(self, record):
        await self.backend.validate()
        if self.backend.state() != self.checkpoint:
            raise ValueError("Program learning/promotion changed during prediction")

    async def forecast(self, state, actions, runs):
        futures = []
        model = self.backend.active_model
        engine = (
            LearnedDynamicsEngine(self.world.task, self.backend.adapter, model) if model else None
        )
        registry = Registry([engine]) if engine else None
        counts = self.checkpoint["candidate_counts"]
        for index, action in enumerate(actions):
            records = []
            code = action.payload["files"]["program.py"]
            if engine:
                request = PredictionRequest(
                    state=state,
                    actions=[action],
                    claims=[
                        c
                        for c in bind_claims(self.world.task, state.id, [action])
                        if supports(engine.capabilities, c)
                    ],
                )
                p, _ = await registry.predict(engine, request)
                self.usage.append(usage_record(p))
                records = [{"request": request.model_dump(), "prediction": p.model_dump()}]
                estimated = p.raw.get("status") == "estimated"
                if not estimated and p.raw.get("reason") != "unsupported_cell":
                    raise ValueError("Program inference failed; native fallback is not permitted")
                value = p.metrics.get("goal_complete")
                basis = "learned_observed_goal"
            else:
                estimated = True
                # Bounded preparation selects the least-observed PUBLIC code,
                # not a candidate labelled repair/competing or private oracle result.
                preparation = self.checkpoint["status"] == "collecting"
                value = -counts[identity(code)] if preparation else -index
                basis = "bounded_receipt_collection" if preparation else "native_proposal_fallback"
            futures.append(
                {
                    "action": action.model_dump(),
                    "fingerprint": action.fingerprint,
                    "status": "estimated" if estimated else "unknown",
                    "reason": None if estimated else "unsupported_cell",
                    "basis": basis,
                    "value": value,
                    "records": records,
                    "paths": [],
                }
            )
        if engine and any(future["status"] == "unknown" for future in futures):
            # Fall back as a whole comparison; learned probabilities and proposal
            # order have incompatible scales. Retain the original unknown records.
            for index, future in enumerate(futures):
                future.update(
                    status="estimated",
                    reason="unsupported_cell",
                    basis="native_unknown_input_fallback",
                    value=-index,
                    learned_status=future["status"],
                )
        return {
            "adapter": "program-h1/v1",
            "horizon": 1,
            "continuation": "environment_only",
            "model_version": model.version if model else None,
            "dataset_hash": model.dataset_hash if model else None,
            "promotion": self.backend.active_promotion,
            "health": self.checkpoint["status"],
            "candidates": futures,
            "assumptions": [
                "Closed public code vocabulary",
                "Historical H1 goal/test outcomes, not safety evidence or a successor State",
            ],
        }


class ProgramLearningBackend:
    """Collect, train, independently assess, then await an explicit operator call.

    Evaluation factory must return a NEW actual ProgramWorld. Separate actual
    episode/run/receipt IDs and prequential predictions are retained. No source
    World reset or hidden candidate labelling occurs in learning control.
    """

    def __init__(
        self, store, artifacts, prototype, evaluation_factory, *, policy=ProgramPromotionPolicy()
    ):
        self.store, self.artifacts = store, artifacts
        self.adapter = ProgramOutcomeAdapter(prototype)
        self.evaluation_factory, self.policy = evaluation_factory, policy
        self.training = {}
        self.candidate = None
        self.evaluation = None
        self.evaluation_artifact = None
        self.active_model = None
        self.active_promotion = None
        self._counts = {identity(c): 0 for c in self.adapter.candidates}
        self._heads = None
        self._source_worlds = []
        self._status = "collecting"

    @property
    def source_runs(self):
        return [r for runs in self.training.values() for r in runs] + (
            [r for runs in self.evaluation["episodes"].values() for r in runs]
            if self.evaluation
            else []
        )

    def state(self):
        return {
            "status": self._status,
            "candidate_counts": copy.deepcopy(self._counts),
            "candidate": self.candidate,
            "evaluation": self.evaluation_artifact,
            "training": copy.deepcopy(self.training),
            "model_version": self.active_model.version if self.active_model else None,
            "promotion": self.active_promotion,
            "policy": asdict(self.policy),
        }

    async def advance(self, world, state, snapshot):
        if (
            not isinstance(world, ProgramWorld)
            or ProgramOutcomeAdapter(world).specification != self.adapter.specification
        ):
            raise ValueError("Learning backend cannot mix Program task specifications")
        if not any(world is w for w in self._source_worlds):
            self._source_worlds.append(world)
        if self.candidate is None:
            counts = {identity(c): 0 for c in self.adapter.candidates}
            # Train on initial-state actions only. Later patches are different
            # feature cells, not silently pooled with initial-state teachers.
            usable = []
            initial_code = world.spec.source(world.spec.initial)
            for row in snapshot.transitions:
                if row.before.payload.get("files", {}).get("program.py") == initial_code:
                    self.adapter.targets(row)
                    counts[identity(row.action.payload["files"]["program.py"])] += 1
                    usable.append(row)
            self._counts = counts
            if all(n >= self.policy.samples_per_candidate for n in counts.values()):
                self.training = {
                    ep: [r.run_id for r in usable if r.episode_id == ep]
                    for ep in dict.fromkeys(r.episode_id for r in usable)
                }
                self.training = dict(sorted(self.training.items()))
                model = await TabularDynamicsTrainer().fit(
                    TransitionDataset(self.store, self.training),
                    self.adapter,
                    TrainingConfig(min_samples=self.policy.samples_per_candidate, seed=0),
                )
                record = {
                    "kind": "program_candidate/v1",
                    "model": model.save(self.artifacts),
                    "training": copy.deepcopy(self.training),
                    "policy": asdict(self.policy),
                }
                self.candidate = self.artifacts.json(record)
                self._status = "evaluating"
                self.evaluation = json.loads(json.dumps(await self._evaluate(model)))
                self.evaluation_artifact = self.artifacts.json(self.evaluation)
                self._status = "await_promotion" if self.evaluation["passed"] else "rejected"
                self._heads = await self.store.call("run_heads", self.source_runs)
            elif (
                sum(len(rs) for rs in snapshot.episodes.values())
                >= self.policy.max_preparation_actions
            ):
                self._status = "insufficient_data"
        return self.state()

    async def _evaluate(self, model):
        from preact.cognition.loop import CognitiveAgent
        from preact.cognition.models import Goal
        from preact.cognition.world import WorldPlanner
        from preact.core.models import Policy

        episodes, forecasts, worlds = {}, [], []
        engine = None
        for code in self.adapter.candidates:
            for index in range(self.policy.evaluation_per_candidate):
                world = self.evaluation_factory(len(episodes))
                if (
                    not isinstance(world, ProgramWorld)
                    or any(world is w for w in worlds)
                    or any(world is w for w in self._source_worlds)
                ):
                    raise ValueError(
                        "Independent evaluation requires distinct actual ProgramWorlds"
                    )
                worlds.append(world)
                if ProgramOutcomeAdapter(world).specification != self.adapter.specification:
                    raise ValueError("Evaluation task vocabulary differs")
                state = await world.observe()
                if state.payload["stage"] != "initial":
                    raise ValueError("Evaluation World must start at its real initial state")
                action = Action(
                    name="Owned evaluation patch",
                    kind="patch",
                    state_id=state.id,
                    payload={"files": {"program.py": code}},
                )
                engine = LearnedDynamicsEngine(world.task, self.adapter, model)
                request = PredictionRequest(
                    state=state,
                    actions=[action],
                    claims=[
                        c
                        for c in bind_claims(world.task, state.id, [action])
                        if supports(engine.capabilities, c)
                    ],
                )
                prediction, _ = await Registry([engine]).predict(engine, request)

                class Generator:
                    async def propose(self, current, width):
                        return [
                            Action.model_validate({**action.model_dump(), "state_id": current.id})
                        ]

                agent = CognitiveAgent(
                    self.store,
                    self.artifacts,
                    Registry([LocalHeuristic(world), LocalVerifier(world)]),
                    WorldPlanner(world, Generator()),
                    [Goal(name="Repair", metric="goal_complete", target=1)],
                    Policy(
                        search=False,
                        width=1,
                        max_nodes=1,
                        max_calls=8,
                        max_seconds=30,
                        calibration=False,
                    ),
                )
                result = await agent.run(world, 1)
                episode = f"program-evaluation:{result.run_ids[0]}"
                episodes[episode] = result.run_ids
                forecasts.append(
                    {
                        "episode": episode,
                        "state": state.model_dump(),
                        "action": action.model_dump(),
                        "prediction": prediction.model_dump(),
                    }
                )
        episodes = dict(sorted(episodes.items()))
        evaluation = await TransitionDataset(self.store, episodes).snapshot()
        training = await TransitionDataset(self.store, self.training).snapshot()
        require_disjoint(training, evaluation)
        scores = self._scores(evaluation, forecasts)
        return {
            "candidate": self.candidate,
            "episodes": episodes,
            "forecasts": forecasts,
            "dataset_hash": evaluation.dataset_hash,
            "cutoff": datetime.now(timezone.utc).isoformat(),
            "scores": scores,
            "passed": scores["coverage"] == 1
            and scores["goal_mae"] <= self.policy.max_goal_mae
            and scores["goal_mae"] <= scores["prior_mae"],
            "model_version": model.version,
            "evaluation_actions": len(episodes),
            "local_prediction_calls": len(forecasts),
            "external_api_cost_usd": 0.0,
            "compute_dollar_cost": None,
        }

    def _scores(self, snapshot, forecasts):
        from preact.core.models import Prediction, State

        errors, prior, covered = [], [], 0
        for forecast in forecasts:
            rows = [r for r in snapshot.transitions if r.episode_id == forecast["episode"]]
            if len(rows) != 1:
                raise ValueError("Evaluation requires a complete executed patch receipt")
            row = rows[0]
            state, action = (
                State.model_validate(forecast["state"]),
                Action.model_validate(forecast["action"]),
            )
            if (
                row.before.model_dump(exclude={"timestamp"})
                != state.model_dump(exclude={"timestamp"})
                or row.before.timestamp < state.timestamp
                or row.action.fingerprint != action.fingerprint
            ):
                raise ValueError("Prequential evaluation State/Action/time mismatch")
            p = Prediction.model_validate(forecast["prediction"])
            if p.state_id != state.id or p.action_ids != [action.id]:
                raise ValueError("Evaluation prediction binding mismatch")
            actual = self.adapter.targets(row)[0]
            prior.append(abs(actual - self.policy.prior))
            if p.raw.get("status") == "estimated":
                covered += 1
                errors.append(abs(p.metrics["goal_complete"] - actual))
        return {
            "goal_mae": sum(errors) / len(errors) if errors else None,
            "prior_mae": sum(prior) / len(prior),
            "coverage": covered / len(forecasts),
            "unknown_rate": 1 - covered / len(forecasts),
        }

    async def _complete_before(self, snapshot, cutoff):
        limit = datetime.fromisoformat(cutoff)
        if limit.tzinfo is None or limit > datetime.now(timezone.utc):
            raise ValueError("Program cutoff must be an actual past time")
        for row in snapshot.transitions:
            execution = await self.store.call("execution_record", row.receipt)
            events = await self.store.call("read_events", row.run_id)
            seq = int(row.outcome_reference.rsplit(":", 1)[1])
            outcome = next(e for e in events if e["seq"] == seq)
            if (
                datetime.fromisoformat(row.after.state.timestamp) > limit
                or datetime.fromisoformat(outcome["timestamp"]) > limit
                or execution["status"] != "complete"
                or execution["receipt"] != row.after.model_dump()
            ):
                raise ValueError("Unconfirmed/future program outcome crossed evaluation cutoff")

    async def validate(self):
        if self.candidate is None:
            return
        record = json.loads(self.artifacts.read(self.candidate))
        model = DynamicsModel.load(self.artifacts, record["model"], self.adapter)
        if record["training"] != self.training or record["policy"] != asdict(self.policy):
            raise ValueError("Program candidate provenance changed")
        training = await TransitionDataset(self.store, self.training).snapshot()
        rebuilt = await TabularDynamicsTrainer().fit(
            TransitionDataset(self.store, self.training),
            self.adapter,
            TrainingConfig(min_samples=self.policy.samples_per_candidate, seed=0),
        )
        if rebuilt != model:
            raise ValueError("Program model is not the receipt-trained artifact")
        if self.evaluation:
            if json.loads(self.artifacts.read(self.evaluation_artifact)) != self.evaluation:
                raise ValueError("Evaluation artifact changed")
            evaluation = await TransitionDataset(self.store, self.evaluation["episodes"]).snapshot()
            require_disjoint(training, evaluation)
            if evaluation.dataset_hash != self.evaluation["dataset_hash"]:
                raise ValueError("Independent evaluation receipts changed")
            await self._complete_before(evaluation, self.evaluation["cutoff"])
            first_forecast = min(f["state"]["timestamp"] for f in self.evaluation["forecasts"])
            await self._complete_before(training, first_forecast)
            recomputed = self._scores(evaluation, self.evaluation["forecasts"])
            expected_pass = (
                recomputed["coverage"] == 1
                and recomputed["goal_mae"] <= self.policy.max_goal_mae
                and recomputed["goal_mae"] <= recomputed["prior_mae"]
            )
            if (
                self.evaluation["passed"] != expected_pass
                or recomputed != self.evaluation["scores"]
                or self.evaluation["candidate"] != self.candidate
            ):
                raise ValueError("Program evaluation scores changed")
            # Recompute forecasts from the exact saved pre-execution requests.
            from preact.core.models import State

            for forecast in self.evaluation["forecasts"]:
                state = State.model_validate(forecast["state"])
                action = Action.model_validate(forecast["action"])
                cell = model.lookup(self.adapter.features(state, action))
                pred = forecast["prediction"]
                metrics, _ = self.adapter.decode(state, action, cell)
                if pred["engine_version"] != model.version or pred["metrics"] != metrics:
                    raise ValueError("Program held-out prediction changed")
            if (
                self._heads is not None
                and await self.store.call("run_heads", self.source_runs) != self._heads
            ):
                raise ValueError("Program source authority updated")
        if self._status == "available" and (
            self.active_model is None or self.active_promotion is None
        ):
            raise ValueError("Available Program model lost its explicit promotion")
        if self.active_promotion:
            promotion = json.loads(self.artifacts.read(self.active_promotion))
            if (
                not self.evaluation["passed"]
                or promotion["evaluation"] != self.evaluation_artifact
                or self.active_model != model
            ):
                raise ValueError("Program promotion lost its evaluation/model binding")

    def binding(self, world, checkpoint):
        source = ProgramPredictionAdapter(self, world, checkpoint)
        planner = DeliberationPlanner(world, source, ProgramGoalAdapter())
        return RoundBinding(
            planner,
            Registry([LocalHeuristic(world), LocalVerifier(world)]),
            SelectionWorld(world, planner),
            planner,
            copy.deepcopy(checkpoint),
        )

    async def promote(self, candidate, world, state):
        await self.validate()
        if candidate != self.candidate or not self.evaluation or not self.evaluation["passed"]:
            raise ValueError("Program candidate has not passed independent evaluation")
        if ProgramOutcomeAdapter(world).specification != self.adapter.specification:
            raise ValueError("Promotion World is outside evaluated scope")
        if state.timestamp < self.evaluation["cutoff"]:
            raise ValueError("Promotion cannot precede independent evaluation")
        record = json.loads(self.artifacts.read(candidate))
        promotion = self.artifacts.json(
            {
                "candidate": candidate,
                "evaluation": self.evaluation_artifact,
                "previous": self.active_promotion,
                "state": state.model_dump(),
            }
        )
        model = DynamicsModel.load(self.artifacts, record["model"], self.adapter)
        self.active_model, self.active_promotion, self._status = model, promotion, "available"
        return promotion

    async def restore(self, checkpoint, world, episodes=None):
        self.active_model = self.active_promotion = None
        self.candidate = checkpoint["candidate"]
        self.training = copy.deepcopy(checkpoint["training"])
        self.evaluation_artifact = checkpoint["evaluation"]
        self.evaluation = (
            json.loads(self.artifacts.read(self.evaluation_artifact))
            if self.evaluation_artifact
            else None
        )
        self._counts = copy.deepcopy(checkpoint["candidate_counts"])
        self._status = checkpoint["status"]
        if (
            checkpoint["policy"] != asdict(self.policy)
            or ProgramOutcomeAdapter(world).specification != self.adapter.specification
        ):
            raise ValueError("Restored Program policy/task differs")
        self._heads = None
        # Rebuild all authority before re-enabling any active prediction.
        self._status = (
            "await_promotion"
            if self.evaluation and self.evaluation["passed"]
            else "rejected"
            if self.evaluation
            else checkpoint["status"]
        )
        await self.validate()
        if checkpoint["promotion"]:
            promotion = json.loads(self.artifacts.read(checkpoint["promotion"]))
            if (
                promotion["candidate"] != self.candidate
                or promotion["evaluation"] != self.evaluation_artifact
            ):
                raise ValueError("Restored promotion refers to different evidence")
            record = json.loads(self.artifacts.read(self.candidate))
            self.active_model = DynamicsModel.load(self.artifacts, record["model"], self.adapter)
            self.active_promotion = checkpoint["promotion"]
            self._status = "available"
            try:
                await self.validate()
            except BaseException:
                self.active_model = self.active_promotion = None
                self._status = "unavailable"
                raise
        self._heads = await self.store.call("run_heads", self.source_runs)
