"""Episode-local reuse of inference only, after fresh receipt-backed retrieval."""

import asyncio
from dataclasses import dataclass
from typing import Any

from preact.core.models import State, Task, identity

from .memory import EpisodicMemory
from .models import Belief, BeliefReusePolicy, CognitivePlanner, Inference


@dataclass
class ReuseCounts:
    hits: int = 0
    misses: int = 0
    bypasses: int = 0


@dataclass(frozen=True)
class _Estimate:
    key: str
    planner: CognitivePlanner
    memory: EpisodicMemory
    store: Any
    algorithm: Any
    code: Any
    inferred: dict[str, Inference]
    unknown: tuple[str, ...]


def reuse_policy(planner: CognitivePlanner) -> BeliefReusePolicy | None:
    """Require a concrete opt-in; inherited declarations do not cover new algorithms."""
    if "belief_reuse_policy" not in vars(type(planner)):
        return None
    try:
        policy = planner.belief_reuse_policy()
        if (
            isinstance(policy, BeliefReusePolicy)
            and type(policy.token) is str
            and policy.token
            and type(policy.timestamp_independent) is bool
        ):
            return policy
    except Exception:
        # A capability we cannot establish never justifies a hit.
        pass
    return None


class BeliefEstimator:
    """Derived inference cache; no observations, proposals or safety evidence cached.

    Every infer call retrieves/validates Memory when enabled by use_memory, even on
    a cache hit. State.id is insufficient: all State fields are bound except an
    explicitly timestamp-independent planner's timestamp. Experience content/order
    and Memory's local invalidation generation are both bound. That generation is
    only a hint; the receipt-backed retrieval is still the authority check.
    """

    def __init__(self, *, enabled: bool = False):
        self.enabled = enabled
        self.counts = ReuseCounts()
        self._task: Task | None = None
        self._entry: _Estimate | None = None
        self._epoch = 0
        self._lock = asyncio.Lock()

    def clear(self) -> None:
        self._entry = None
        self._epoch += 1

    def begin(self, task: Task) -> None:
        self.clear()
        self.counts = ReuseCounts()
        self._task = task.model_copy(deep=True)

    def end(self) -> None:
        self.clear()
        self._task = None

    async def infer(
        self,
        state: State,
        planner: CognitivePlanner,
        memory: EpisodicMemory,
        *,
        task: Task,
        use_memory: bool = True,
    ) -> Belief:
        try:
            async with self._lock:
                epoch = self._epoch
                current = State.model_validate(state.model_dump())
                if current.kind != "observed":
                    raise ValueError("Belief requires authoritative observed facts")
                records = await memory.retrieve(current.domain) if use_memory else []
                generation = memory.generation
                manifest = tuple(memory.run_ids)
                store = memory.store
                policy = (
                    reuse_policy(planner)
                    if (
                        self.enabled
                        and epoch == self._epoch
                        and self._task is not None
                        and self._task == task
                    )
                    else None
                )
                algorithm = getattr(planner.infer, "__func__", planner.infer)
                code = getattr(algorithm, "__code__", None)
                key = None
                record_hash = None
                if policy is not None:
                    try:
                        record_hash = identity([r.model_dump() for r in records])
                        facts = current.model_dump()
                        if policy.timestamp_independent:
                            facts.pop("timestamp")
                        key = identity(
                            {
                                "state": facts,
                                "experience": record_hash,
                                "generation": generation,
                                "memory_runs": manifest,
                                "use_memory": use_memory,
                                "task": task.model_dump(),
                                "policy": {
                                    "token": policy.token,
                                    "timestamp_independent": policy.timestamp_independent,
                                },
                            }
                        )
                    except (TypeError, ValueError):
                        # Unrepresentable inputs force ordinary inference.
                        policy = None
                entry = self._entry
                if (
                    policy is not None
                    and key is not None
                    and epoch == self._epoch
                    and entry is not None
                    and entry.key == key
                    and entry.planner is planner
                    and entry.memory is memory
                    and entry.store is memory.store
                    and entry.algorithm is algorithm
                    and entry.code is code
                ):
                    self.counts.hits += 1
                    return Belief(
                        observed=current.model_copy(deep=True),
                        inferred={k: v.model_copy(deep=True) for k, v in entry.inferred.items()},
                        unknown=list(entry.unknown),
                    )
                self.clear()
                epoch = self._epoch
                if policy is None:
                    self.counts.bypasses += 1
                else:
                    self.counts.misses += 1
                belief = planner.infer(current.model_copy(deep=True), records)
                if not isinstance(belief, Belief) or belief.observed != current:
                    raise ValueError("Inference cannot alter observed facts")
                if (
                    policy is not None
                    and key is not None
                    and epoch == self._epoch
                    and memory.generation == generation
                    and tuple(memory.run_ids) == manifest
                    and memory.store is store
                    and reuse_policy(planner) == policy
                    and getattr(planner.infer, "__func__", planner.infer) is algorithm
                    and getattr(algorithm, "__code__", None) is code
                    and identity([r.model_dump() for r in records]) == record_hash
                ):
                    self._entry = _Estimate(
                        key,
                        planner,
                        memory,
                        memory.store,
                        algorithm,
                        code,
                        {k: v.model_copy(deep=True) for k, v in belief.inferred.items()},
                        tuple(belief.unknown),
                    )
                return belief.model_copy(deep=True)
        except BaseException:
            self.clear()
            raise
