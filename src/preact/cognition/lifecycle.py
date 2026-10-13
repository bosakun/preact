"""Optional round-boundary learning control. No safety or execution authority."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from preact.core.candidates import CandidateSelector
from preact.core.interfaces import World
from preact.core.models import State, identity
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store

if TYPE_CHECKING:
    from preact.learning.transitions import TransitionSnapshot

from .deliberation import DeliberationPlanner
from .models import CognitivePlanner


def _dataset(store, episodes):
    # learning.transitions uses cognition.memory; avoid import-order-dependent cycles.
    from preact.learning.transitions import TransitionDataset

    return TransitionDataset(store, episodes)


@dataclass(frozen=True)
class RoundBinding:
    planner: CognitivePlanner
    registry: Registry
    world: World
    selector: CandidateSelector | None
    provenance: dict


class LearningBackend(Protocol):
    """Domain-specific eligibility, health, evaluation and immutable binding factories.

    advance may train/evaluate but never promotes or executes the source World.
    Evaluation actions use a separate, declared budget and the ordinary Runtime.
    """

    store: Store
    artifacts: Artifacts

    async def advance(self, world: World, state: State, snapshot: TransitionSnapshot) -> dict: ...
    async def validate(self) -> None: ...
    def binding(self, world: World, checkpoint: dict) -> RoundBinding: ...
    async def promote(self, candidate: str, world: World, state: State) -> str: ...
    def state(self) -> dict: ...
    async def restore(self, checkpoint: dict, world, episodes: dict) -> None: ...


class LearningCoordinator:
    """Single-owner coordinator, reusable across distinct actual episodes.

    Lifecycle events are derived records in the existing Store. Receipt-backed
    datasets remain authority. Explicit promotion is only legal between rounds.
    A failed operation latches unavailability until a newly validated coordinator
    is constructed; no cached active state is restored optimistically.
    """

    def __init__(self, backend: LearningBackend, journal_run: str, *, max_seconds=600):
        if max_seconds <= 0:
            raise ValueError("Lifecycle deadline must be positive")
        self.backend, self.store = backend, backend.store
        self.journal_run = journal_run
        self.max_seconds = max_seconds
        self.episodes: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._in_round = False
        self._failed = False
        self._checkpoint = None
        self._seal = None
        self._heads = None
        self._episode = None
        self._world = None
        self._world_episodes = {}

    @classmethod
    async def create(cls, backend, *, max_seconds=600):
        journal = await backend.store.call(
            "create_run", {"kind": "cognitive_learning", "version": "1"}
        )
        return cls(backend, journal, max_seconds=max_seconds)

    async def _journal(self, kind, data):
        await self.store.call("append", self.journal_run, kind, copy.deepcopy(data))

    async def prepare(self, world, episode_id, runs) -> RoundBinding:
        async with self._lock:
            if self._failed or self._in_round:
                raise ValueError("Learning coordinator unavailable or already deciding")
            if not episode_id:
                raise ValueError("Explicit actual episode identity required")
            if self._episode == episode_id and self._world is not world:
                raise ValueError("Cannot replace the World within an episode")
            if self._episode != episode_id and episode_id in self.episodes:
                raise ValueError("Cannot relabel an old episode as a new task")
            if (
                id(world) in self._world_episodes
                and self._world_episodes[id(world)][0] is world
                and self._world_episodes[id(world)][1] != episode_id
            ):
                raise ValueError("Cannot relabel the same actual World as another episode")
            self._world_episodes[id(world)] = (world, episode_id)
            old = self.episodes.get(episode_id, [])
            if list(runs[: len(old)]) != old:
                raise ValueError("Episode manifest cannot drop or reorder committed runs")
            self._episode, self._world = episode_id, world
            self.episodes[episode_id] = list(runs)
            cpu, wall = time.process_time(), time.perf_counter()
            try:
                state = State.model_validate((await world.observe()).model_dump())
                if state.kind != "observed":
                    raise ValueError("Lifecycle requires fresh authoritative observation")
                async with asyncio.timeout(self.max_seconds):
                    snapshot = await _dataset(self.store, self.episodes).snapshot()
                    if snapshot.unlabelled_receipts:
                        raise ValueError("Incomplete receipt cannot enter learning control")
                    checkpoint = await self.backend.advance(world, state, snapshot)
                    await self.backend.validate()
                record = {
                    "episode": episode_id,
                    "state": state.model_dump(),
                    "episodes": copy.deepcopy(self.episodes),
                    "dataset_hash": snapshot.dataset_hash,
                    "backend": copy.deepcopy(checkpoint),
                    "cpu_seconds": time.process_time() - cpu,
                    "wall_seconds": time.perf_counter() - wall,
                    "external_api_cost_usd": 0.0,
                    "compute_dollar_cost": None,
                }
                await self._journal("learning_checkpoint", record)
                self._checkpoint, self._seal = record, identity(record)
                dependencies = [self.journal_run, *[r for rs in self.episodes.values() for r in rs]]
                self._heads = await self.store.call("run_heads", dependencies)
                binding = self.backend.binding(world, checkpoint)
                if isinstance(binding.planner, DeliberationPlanner):
                    binding.planner.source = _FencedSource(binding.planner.source, self)
                    binding.planner._configuration = (
                        binding.planner.source.signature(),
                        binding.planner.value.signature(),
                    )
                self._in_round = True
                return binding
            except BaseException:
                self._failed = True
                raise

    async def bind_run(self, run_id, binding):
        await self.check()
        await self._journal("learning_round_open", {"episode": self._episode, "run": run_id})
        updated = await self.store.call("run_heads", list(self._heads))
        if any(
            updated.get(run) != head for run, head in self._heads.items() if run != self.journal_run
        ):
            self._failed = True
            raise ValueError("Learning sources changed during run binding")
        self._heads = updated
        await self.store.call(
            "append",
            run_id,
            "learning_binding",
            {
                "journal": self.journal_run,
                "checkpoint_hash": self._seal,
                "provenance": copy.deepcopy(binding.provenance),
            },
        )

    async def check(self):
        if self._failed or self._checkpoint is None or identity(self._checkpoint) != self._seal:
            raise ValueError("Learning checkpoint unavailable or changed")
        try:
            if await self.store.call("run_heads", list(self._heads)) != self._heads:
                raise ValueError("Learning authority changed during decision")
            await self.backend.validate()
        except BaseException:
            self._failed = True
            raise

    def finish_round(self, *, failed=False):
        self._in_round = False
        if failed:
            self._failed = True

    async def remember(self, episode_id, runs):
        if self._failed or self._in_round:
            raise ValueError("Cannot update learning manifest during/after a failed decision")
        old = self.episodes.get(episode_id, [])
        if list(runs[: len(old)]) != old:
            raise ValueError("Committed episode prefix changed")
        # Re-read authority after Runtime commits, rather than accepting result flags.
        confirmed = await _dataset(self.store, {episode_id: list(runs)}).snapshot()
        if confirmed.unlabelled_receipts:
            self._failed = True
            raise ValueError("Reconcile incomplete receipts before learning")
        self.episodes[episode_id] = list(runs)
        await self._journal("learning_experience", {"episode": episode_id, "runs": list(runs)})
        cpu, wall = time.process_time(), time.perf_counter()
        try:
            async with asyncio.timeout(self.max_seconds):
                state = State.model_validate((await self._world.observe()).model_dump())
                snapshot = await _dataset(self.store, self.episodes).snapshot()
                checkpoint = await self.backend.advance(self._world, state, snapshot)
                await self.backend.validate()
            record = {
                "episode": episode_id,
                "state": state.model_dump(),
                "episodes": copy.deepcopy(self.episodes),
                "dataset_hash": snapshot.dataset_hash,
                "backend": copy.deepcopy(checkpoint),
                "cpu_seconds": time.process_time() - cpu,
                "wall_seconds": time.perf_counter() - wall,
                "external_api_cost_usd": 0.0,
                "compute_dollar_cost": None,
            }
            await self._journal("learning_checkpoint", record)
            self._checkpoint, self._seal = record, identity(record)
            self._heads = await self.store.call(
                "run_heads", [self.journal_run, *[r for rs in self.episodes.values() for r in rs]]
            )
        except BaseException:
            self._failed = True
            raise

    async def promote(self, candidate, world):
        async with self._lock:
            if self._failed or self._in_round:
                raise ValueError("Explicit promotion requires a healthy round boundary")
            try:
                state = State.model_validate((await world.observe()).model_dump())
                snapshot = await _dataset(self.store, self.episodes).snapshot()
                await self.backend.advance(world, state, snapshot)
                result = await self.backend.promote(candidate, world, state)
                await self._journal(
                    "learning_explicit_promotion",
                    {
                        "candidate": candidate,
                        "promotion": result,
                        "state": state.model_dump(),
                        "backend": copy.deepcopy(self.backend.state()),
                    },
                )
                self._checkpoint, self._seal, self._heads = None, None, None
                return result
            except BaseException:
                self._failed = True
                raise

    @classmethod
    async def restore(cls, backend, journal_run, world, *, max_seconds=600):
        coordinator = cls(backend, journal_run, max_seconds=max_seconds)
        run = await backend.store.call("get_run", journal_run)
        if run is None or run["config"] != {"kind": "cognitive_learning", "version": "1"}:
            raise ValueError("Invalid common lifecycle journal")
        events = await backend.store.call("read_events", journal_run)
        state = None
        for event in events:
            data = event["data"]
            if event["kind"] == "learning_checkpoint":
                coordinator.episodes = copy.deepcopy(data["episodes"])
                state = data["backend"]
            elif event["kind"] == "learning_round_open":
                runs = coordinator.episodes.setdefault(data["episode"], [])
                if data["run"] not in runs:
                    runs.append(data["run"])
            elif event["kind"] == "learning_experience":
                if coordinator.episodes.get(data["episode"], []) != data["runs"]:
                    raise ValueError("Restored completed manifest differs from opened rounds")
            elif event["kind"] == "learning_explicit_promotion":
                state = data["backend"]
        snapshot = await _dataset(backend.store, coordinator.episodes).snapshot()
        if snapshot.unlabelled_receipts:
            raise ValueError("Reconcile incomplete execution before resuming learning")
        if state is None:
            raise ValueError("No validated lifecycle checkpoint to restore")
        try:
            await backend.restore(state, world, coordinator.episodes)
            restored_state = backend.state()
            if (
                state.get("status") in {"rejected", "insufficient_data", "unavailable"}
                and "status" in restored_state
                and restored_state["status"] != state["status"]
            ):
                raise ValueError("Restore cannot silently change an unavailable lifecycle state")
        except BaseException:
            coordinator._failed = True
            raise
        return coordinator


class _FencedSource:
    """Keep learning/promotion authority frozen through post-intent dispatch."""

    def __init__(self, source, coordinator):
        self.source, self.coordinator = source, coordinator
        self.store, self.max_calls, self.usage = source.store, source.max_calls, source.usage

    def signature(self):
        return identity({"adapter": self.source.signature(), "checkpoint": self.coordinator._seal})

    async def heads(self, runs):
        await self.coordinator.check()
        return await self.source.heads(runs)

    async def candidates(self, state, width):
        await self.coordinator.check()
        return await self.source.candidates(state, width)

    async def forecast(self, state, actions, runs):
        await self.coordinator.check()
        return await self.source.forecast(state, actions, runs)

    async def check_basis(self, record):
        await self.coordinator.check()
        await self.source.check_basis(record)
