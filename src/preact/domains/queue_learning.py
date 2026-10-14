"""Queue lifecycle backend: existing drift/recovery policy behind common round control."""

import copy
import json
from dataclasses import asdict
from datetime import datetime, timezone

from preact.cognition.deliberation import DeliberationPlanner
from preact.cognition.lifecycle import RoundBinding
from preact.cognition.selection import SelectionWorld
from preact.core.models import State, identity
from preact.core.registry import Registry
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_deliberation import QueueGoalAdapter, QueuePredictionAdapter
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.deliberation_usage import usage_record
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_temporal import QueueTemporalEngine, compare_actions
from preact.engines.queue_temporal_guard import QueueTemporalGuard
from preact.learning import DynamicsModel, TransitionDataset
from preact.learning.recovery import CandidateEvaluation, CandidateModel, RecoveryOrigin


class QueueLearningBackend:
    """Reuse the continuous lifecycle; do not weaken its H3 promotion conditions.

    evaluation_factory is an operator-declared independent environment factory,
    not an oracle for the live World's private schedule. Promotion stays explicit.
    """

    def __init__(self, lifecycle, evaluation_factory, *, recover=True):
        self.lifecycle = lifecycle
        self.store, self.artifacts = lifecycle.store, lifecycle.artifacts
        self.evaluation_factory, self.recover = evaluation_factory, recover
        self.origin = self.candidate = self.evaluation = None
        self.pending_guard = self.anchor = None
        self.phase = "monitor"
        self.monitor_runs = []
        self._epoch_runs = []
        self._checkpoint = None
        self._effective = 0
        self._world = None

    def _load(self, digest, kind):
        return kind.model_validate(json.loads(self.artifacts.read(digest)))

    async def advance(self, world, state, snapshot):
        if not isinstance(world, InformationQueueWorld):
            raise ValueError("Queue backend requires its actual versioned World")
        if self._world is not None and self._world is not world:
            raise ValueError("Continuous Queue cannot replace its World")
        self._world = world
        epoch = self.lifecycle.epoch
        if world.task != epoch.task:
            raise ValueError("Queue lifecycle Task changed")
        runs = list(snapshot.episodes.get(epoch.world_episode_id, []))
        if any(row.episode_id != epoch.world_episode_id for row in snapshot.transitions):
            raise ValueError("Continuous Queue manifest mixed another episode")
        if epoch.generation == 0:
            self._epoch_runs = runs
        else:
            # Anchor tick preserves real history; never relabel or reset World.
            self._epoch_runs = [
                r.run_id
                for r in snapshot.transitions
                if r.before.payload["tick"] >= epoch.anchor.payload["tick"]
            ]
        health = await self.lifecycle.health(state, self._epoch_runs)
        if (
            health.status == "invalidated"
            and self.origin is None
            and self.phase == "monitor"
            and self.recover
        ):
            self.origin = await self.lifecycle.begin_recovery(state, self._epoch_runs)
            self.phase = "collect"
        if self.phase == "collect":
            origin = self.lifecycle.recovery._load(self.origin, RecoveryOrigin)
            rows = [
                r
                for r in snapshot.transitions
                if r.run_id not in origin.runs and r.before.timestamp >= origin.cutoff.timestamp
            ]
            self._effective = sum(int(QueueServiceAdapter().targets(r)[0]) for r in rows)
            if (
                self._effective >= 16
                and state.payload["episode_ticks"] - state.payload["tick"] > 11
            ):
                self.candidate = await self.lifecycle.recovery.prepare_candidate(
                    self.origin, state, self._epoch_runs
                )
                cases = await self.evaluation_factory(
                    self.lifecycle.recovery, self.candidate, epoch.generation + 1
                )
                self.evaluation = await self.lifecycle.recovery.evaluate_candidate(
                    self.candidate, cases, datetime.now(timezone.utc).isoformat()
                )
                report = self._load(self.evaluation, CandidateEvaluation)
                if not report.passed:
                    self.phase = "rejected"
                else:
                    self.anchor = await world.observe()
                    candidate = self._load(self.candidate, CandidateModel)
                    model = DynamicsModel.load(
                        self.artifacts, candidate.model_artifact, QueueServiceAdapter()
                    )
                    self.pending_guard = QueueTemporalGuard(
                        self.store,
                        world.task,
                        model,
                        candidate.training,
                        episode_id=epoch.world_episode_id,
                        initial=self.anchor,
                        monitoring_epoch=identity(
                            {
                                "generation": epoch.generation + 1,
                                "model": model.version,
                                "episode": epoch.world_episode_id,
                                "anchor": self.anchor.model_dump(),
                            }
                        ),
                        allow_same_episode=True,
                        config=self.lifecycle._guard._config,
                    )
                    self.phase, self.monitor_runs = "await_promotion", []
        candidate_health = None
        if self.phase == "await_promotion":
            state = await world.observe()
            self.monitor_runs = [
                r.run_id
                for r in snapshot.transitions
                if r.before.payload["tick"] >= self.anchor.payload["tick"]
            ]
            candidate_health = await self.pending_guard.health(state, self.monitor_runs)
            if candidate_health.status == "invalidated":
                self.phase = "rejected"
        self._checkpoint = {
            "status": health.status,
            "health": asdict(health),
            "phase": self.phase,
            "episode": epoch.world_episode_id,
            "epoch": epoch.epoch_id,
            "generation": epoch.generation,
            "model_version": epoch.model_version,
            "dataset_hash": epoch.dataset_hash,
            "promotion": epoch.promotion_artifact,
            "origin": self.origin,
            "candidate": self.candidate,
            "evaluation": self.evaluation,
            "effective_training": self._effective,
            "monitor_runs": list(self._epoch_runs),
            "candidate_health": asdict(candidate_health) if candidate_health else None,
        }
        self._checkpoint["restore"] = self.state()
        return copy.deepcopy(self._checkpoint)

    def state(self):
        epoch = self.lifecycle.epoch
        return {
            "lifecycle_journal": self.lifecycle.journal_run,
            "epoch": epoch.model_dump(),
            "phase": self.phase,
            "origin": self.origin,
            "candidate": self.candidate,
            "evaluation": self.evaluation,
            "anchor": self.anchor.model_dump() if self.anchor else None,
            "monitor_runs": list(self.monitor_runs),
            "recover": self.recover,
        }

    async def restore(self, checkpoint, world, episodes):
        from preact.engines.queue_continuous import ContinuousQueueAdaptation
        from preact.learning.continuous import MonitoringEpoch

        checkpoint = checkpoint.get("restore", checkpoint)
        epoch = MonitoringEpoch.model_validate(checkpoint["epoch"])
        snapshot = await TransitionDataset(self.store, episodes).snapshot()
        runs = [
            r.run_id
            for r in snapshot.transitions
            if r.episode_id == epoch.world_episode_id
            and r.before.payload["tick"] >= epoch.anchor.payload["tick"]
        ]
        lifecycle = ContinuousQueueAdaptation(
            self.store, self.artifacts, checkpoint["lifecycle_journal"]
        )
        await lifecycle.restore(await world.observe(), runs)
        if lifecycle.epoch != epoch or checkpoint["recover"] != self.recover:
            raise ValueError("Restored Queue epoch/configuration differs")
        self.lifecycle = lifecycle
        self.origin, self.candidate, self.evaluation = (
            checkpoint["origin"],
            checkpoint["candidate"],
            checkpoint["evaluation"],
        )
        self.phase = checkpoint["phase"]
        self.anchor = State.model_validate(checkpoint["anchor"]) if checkpoint["anchor"] else None
        self._world = world
        if self.evaluation and self.phase == "await_promotion":
            candidate = self._load(self.candidate, CandidateModel)
            model = DynamicsModel.load(
                self.artifacts, candidate.model_artifact, QueueServiceAdapter()
            )
            self.pending_guard = QueueTemporalGuard(
                self.store,
                world.task,
                model,
                candidate.training,
                episode_id=epoch.world_episode_id,
                initial=self.anchor,
                monitoring_epoch=identity(
                    {
                        "generation": epoch.generation + 1,
                        "model": model.version,
                        "episode": epoch.world_episode_id,
                        "anchor": self.anchor.model_dump(),
                    }
                ),
                allow_same_episode=True,
                config=self.lifecycle._guard._config,
            )
        await self.advance(world, await world.observe(), snapshot)
        await self.validate()
        return epoch.world_episode_id

    async def validate(self):
        # Lost promoted Recovery is corruption, never ordinary insufficient-data fallback.
        self.lifecycle.selection_source()
        if self.lifecycle.epoch.promotion_artifact and self.lifecycle.recovery._active is None:
            raise ValueError("Promoted Queue model is no longer active")
        if self._checkpoint and (
            self.lifecycle.epoch.epoch_id != self._checkpoint["epoch"]
            or self.lifecycle._guard._latched
            and self._checkpoint["status"] == "available"
        ):
            raise ValueError("Queue model/Health changed during the round")

    def binding(self, world, checkpoint):
        source = _QueueLearningPrediction(self, checkpoint)
        planner = DeliberationPlanner(
            world, source, _QueueLearningGoal(), prefix_runs=checkpoint["monitor_runs"]
        )
        registry = Registry(
            [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        )
        return RoundBinding(
            planner, registry, SelectionWorld(world, planner), planner, copy.deepcopy(checkpoint)
        )

    async def promote(self, candidate, world, state):
        await self.validate()
        if (
            world is not self._world
            or candidate != self.candidate
            or self.phase != "await_promotion"
            or (await self.pending_guard.health(state, self.monitor_runs)).status != "available"
        ):
            raise ValueError("Queue candidate is not ready for explicit promotion")
        promotion = await self.lifecycle.promote(
            self.evaluation, anchor=self.anchor, state=state, monitor_runs=self.monitor_runs
        )
        self.origin = self.candidate = self.evaluation = None
        self.pending_guard = self.anchor = None
        self.phase, self._effective = "monitor", 0
        self._checkpoint = None
        return promotion


class _QueueLearningPrediction(QueuePredictionAdapter):
    def __init__(self, backend, checkpoint):
        super().__init__(backend.lifecycle.selection_source())
        self.backend, self.checkpoint = backend, copy.deepcopy(checkpoint)
        self.max_calls += 3

    def signature(self):
        return identity({"queue": super().signature(), "checkpoint": self.checkpoint})

    async def candidates(self, state, width):
        missing = (
            self.checkpoint["status"] != "available"
            and self.checkpoint["phase"] != "rejected"
            and (self.backend.recover or self.checkpoint["status"] == "insufficient_data")
        )
        if missing and state.payload["episode_ticks"] - state.payload["tick"] > 3:
            return [InformationQueueWorld.probe(state)]
        return await super().candidates(state, width)

    async def heads(self, runs):
        heads = await super().heads(runs)
        journal = self.backend.lifecycle.journal_run
        heads.update(await self.store.call("run_heads", [journal]))
        return heads

    async def check_basis(self, record):
        await self.backend.validate()
        await super().check_basis({**record, "monitor_runs": self.checkpoint["monitor_runs"]})

    async def forecast(self, state, actions, runs):
        if actions[0].kind == "probe_service":
            # Declared readiness collection, not a fabricated measurement forecast.
            return {
                "adapter": "queue-readiness-collection/v1",
                "horizon": 1,
                "continuation": "environment_only",
                "health": self.checkpoint["health"],
                "candidates": [
                    {
                        "action": a.model_dump(),
                        "fingerprint": a.fingerprint,
                        "status": "estimated",
                        "reason": "receipt_readiness_collection",
                        "basis": "readiness_not_learned_value",
                        "records": [],
                        "paths": [],
                    }
                    for a in actions
                ],
            }
        result = await super().forecast(state, actions, self.checkpoint["monitor_runs"])
        if all(f["status"] == "estimated" for f in result["candidates"]):
            return result
        if result.get("health", {}).get("status") not in {
            "insufficient_data",
            "invalidated",
            "suspected",
        }:
            return result
        engine = QueueTemporalEngine(self.monitored.guard._task, prior=0.5)
        fallback = await compare_actions(
            Registry([engine]),
            engine,
            state,
            actions,
            horizon=min(3, state.payload["episode_ticks"] - state.payload["tick"]),
        )
        for f, p in zip(result["candidates"], fallback["predictions"], strict=True):
            self.usage.append(usage_record(p, "local_fixed_prior_fallback"))
            f.update(
                status=p.raw["status"],
                basis="fixed_prior_fallback",
                records=[{"prediction": p.model_dump()}],
                paths=p.raw.get("branches", []),
            )
        result["fallback"] = "declared_fixed_prior"
        return result


class _QueueLearningGoal(QueueGoalAdapter):
    def evaluate(self, future, state, goals):
        if future["action"]["kind"] == "probe_service":
            return {
                "value": -state.payload["probe_cost"],
                "rule": "bounded_health_readiness_collection",
                "calibrated": False,
            }
        return super().evaluate(future, state, goals)
