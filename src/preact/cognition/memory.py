import asyncio
from dataclasses import dataclass

from sqlalchemy.exc import NoResultFound

from preact.core.models import Action, Observation, State
from preact.core.store import RunHead, Store

from .models import Experience


@dataclass(frozen=True)
class _RunIndex:
    head: RunHead
    experiences: tuple[Experience, ...]


class EpisodicMemory:
    """Rebuildable derived cache; only Ledger and execution receipts are authority.

    Every retrieval checks Store change tokens for the runs it consults. Changed runs
    are fully reread and receipt validated; unchanged runs reuse private experiences.
    Tokens include execution content because receipt transitions need not append an
    event. No imagined branch is indexed and nothing here is Gate evidence.

    Restore run_ids from an episode manifest, or use remember after execution. One
    async lock serializes this instance's operations; separate writers are detected
    through database reads, not this lock. See docs/episodic-memory-index.md for the
    append-only and point-in-time consistency boundary.
    """

    def __init__(self, store: Store):
        self.store = store
        self.run_ids: list[str] = []
        self._runs: dict[str, _RunIndex] = {}
        self._lock = asyncio.Lock()

    @property
    def cached_experiences(self) -> int:
        return sum(len(index.experiences) for index in self._runs.values())

    async def clear(self) -> None:
        """Discard derived state, retaining manifest run IDs for reconstruction."""
        async with self._lock:
            self._runs.clear()

    async def read(self, run_id: str) -> list[Experience]:
        """Uncached authoritative read, also usable as the full-reread baseline."""
        events = await self.store.call("read_events", run_id)
        intents = {
            e["data"]["receipt"]: e["data"] for e in events if e["kind"] == "execution_intent"
        }
        nodes = {
            e["data"]["node"]["id"]: e["data"]["node"]
            for e in events
            if e["kind"] in {"node", "node_updated"}
        }
        predictions: dict[str, list[str]] = {}
        for event in events:
            if event["kind"] == "prediction":
                data = event["data"]
                predictions.setdefault(data["node_id"], []).append(data["prediction"]["id"])
        result = []
        seen_receipts = set()
        for event in events:
            if event["kind"] != "outcome":
                continue
            data = event["data"]
            observation = Observation.model_validate(data["observation"])
            receipt = observation.receipt
            try:
                execution = await self.store.call("execution_record", receipt)
                node = nodes[data["node_id"]]
            except (NoResultFound, KeyError) as error:
                raise ValueError(
                    "Memory requires an aligned committed execution receipt"
                ) from error
            state = State.model_validate(node["state"])
            action = Action.model_validate(node["action"])
            if (
                observation.state.kind != "observed"
                or receipt not in intents
                or execution["run_id"] != run_id
                or execution["status"] != "complete"
                or execution["state_id"] != state.id
                or execution["action_hash"] != action.fingerprint
                or execution["receipt"] != observation.model_dump()
            ):
                raise ValueError("Memory requires an aligned committed execution receipt")
            if receipt in seen_receipts:
                continue
            seen_receipts.add(receipt)
            result.append(
                Experience(
                    reference=f"{run_id}:{event['seq']}",
                    run_id=run_id,
                    input_state=state,
                    action=action,
                    prediction_ids=predictions.get(data["node_id"], []),
                    observation=observation,
                )
            )
        return result

    async def _refresh(self, run_ids: list[str]) -> None:
        """Publish a batch only after stable before/after authority checks."""
        try:
            heads = await self.store.call("run_heads", run_ids)
            if set(heads) != set(run_ids) or any(
                not isinstance(head, RunHead) or head.next_seq < 0 for head in heads.values()
            ):
                raise ValueError("Memory cannot establish current run authority")
            changed = [
                run_id
                for run_id in run_ids
                if run_id not in self._runs or self._runs[run_id].head != heads[run_id]
            ]
            for run_id in changed:
                self._runs.pop(run_id, None)
            staged = {
                run_id: _RunIndex(heads[run_id], tuple(await self.read(run_id)))
                for run_id in changed
            }
            if changed and await self.store.call("run_heads", run_ids) != heads:
                raise ValueError(
                    "Memory authority changed during receipt validation; retry retrieval"
                )
            self._runs.update(staged)
        except BaseException:
            # Never serve stale entries after failed validation, I/O or cancellation.
            for run_id in run_ids:
                self._runs.pop(run_id, None)
            raise

    async def remember(self, run_id: str) -> None:
        async with self._lock:
            await self._refresh([run_id])
            if self._runs[run_id].experiences and run_id not in self.run_ids:
                self.run_ids.append(run_id)

    async def retrieve(self, domain: str, limit: int = 12) -> list[Experience]:
        if limit <= 0:
            return []
        async with self._lock:
            remaining = list(reversed(list(dict.fromkeys(self.run_ids))))
            selected: list[Experience] = []
            receipts = set()
            offset = 0
            while offset < len(remaining) and len(selected) < limit:
                batch = []
                expected = len(selected)
                # Batch known candidates, stopping at an unknown run to preserve
                # the old newest-run-first scan and its early termination behavior.
                while offset < len(remaining) and expected < limit and len(batch) < 256:
                    run_id = remaining[offset]
                    offset += 1
                    batch.append(run_id)
                    cached = self._runs.get(run_id)
                    if cached is None:
                        break
                    expected += sum(e.input_state.domain == domain for e in cached.experiences)
                await self._refresh(batch)
                for run_id in batch:
                    for experience in reversed(self._runs[run_id].experiences):
                        receipt = experience.observation.receipt
                        if experience.input_state.domain == domain and receipt not in receipts:
                            receipts.add(receipt)
                            selected.append(experience)
                    if len(selected) >= limit:
                        break
            return [e.model_copy(deep=True) for e in reversed(selected[:limit])]
