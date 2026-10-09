"""Verified, rebuildable training snapshots derived from Ledger and receipts."""

from datetime import datetime
from typing import Literal

from pydantic import Field

from preact.cognition.memory import EpisodicMemory
from preact.core.models import Action, Contract, Observation, State, identity
from preact.core.store import Store


class Transition(Contract):
    schema_version: Literal["1"] = "1"
    episode_id: str
    run_id: str
    before: State
    action: Action
    after: Observation
    receipt: str
    receipt_status: Literal["complete"] = "complete"
    before_reference: str
    authorization_reference: str
    intent_reference: str
    outcome_reference: str
    observed_elapsed_seconds: float = Field(ge=0)
    declared_action_duration: float = Field(gt=0)
    continuous_from_previous: bool | None = None


class TransitionSnapshot(Contract):
    schema_version: Literal["1"] = "1"
    dataset_hash: str
    episodes: dict[str, list[str]]
    transitions: list[Transition]
    runs_without_outcomes: list[str]
    unlabelled_receipts: dict[str, Literal["pending", "aborted", "complete"]]

    def verify_identity(self) -> None:
        if self.dataset_hash != identity(self.model_dump(exclude={"dataset_hash"})):
            raise ValueError("Transition snapshot content changed")


def _timestamp(state: State) -> datetime:
    timestamp = datetime.fromisoformat(state.timestamp)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Transition timestamps must be timezone aware")
    return timestamp


class TransitionDataset:
    """Explicit episode manifest; each snapshot revalidates the existing authority.

    A before/after run-head fence detects append and receipt-only external updates.
    In-place Ledger rewrites and database rollback are outside Store's contract.
    Complete receipts lacking an outcome are omitted, never reconstructed as labels.
    Failed/task-incomplete observations remain actual experience when aligned.
    """

    def __init__(self, store: Store, episodes: dict[str, list[str]]):
        self.store = store
        self._episodes = {episode: list(runs) for episode, runs in episodes.items()}
        runs = [run for episode in self._episodes.values() for run in episode]
        if len(runs) != len(set(runs)) or any(not e for e in self._episodes):
            raise ValueError("Each run must belong to exactly one nonempty episode")

    async def snapshot(self) -> TransitionSnapshot:
        episodes = {episode: list(runs) for episode, runs in self._episodes.items()}
        runs = [run for episode in episodes.values() for run in episode]
        heads = await self.store.call("run_heads", runs)
        if set(heads) != set(runs):
            raise ValueError("Transition manifest refers to unavailable runs")
        memory = EpisodicMemory(self.store)
        transitions, empty, seen, unlabelled = [], [], set(), {}
        for episode_id, run_ids in episodes.items():
            previous = None
            for run_id in run_ids:
                # Use the existing forged-outcome and receipt verification unchanged.
                experiences = await memory.read(run_id)
                events = await self.store.call("read_events", run_id)
                if not experiences:
                    empty.append(run_id)
                labelled = {e.observation.receipt for e in experiences}
                for event in events:
                    if event["kind"] != "execution_intent" or event["data"]["receipt"] in labelled:
                        continue
                    receipt = event["data"]["receipt"]
                    execution = await self.store.call("execution_record", receipt)
                    if execution["run_id"] != run_id or execution["status"] not in {
                        "pending",
                        "aborted",
                        "complete",
                    }:
                        raise ValueError("Unlabelled execution has an invalid receipt binding")
                    unlabelled[receipt] = execution["status"]
                for experience in experiences:
                    before, after, action = (
                        experience.input_state,
                        experience.observation,
                        experience.action,
                    )
                    receipt = after.receipt
                    outcome_seq = int(experience.reference.rsplit(":", 1)[1])
                    outcome = next(e for e in events if e["seq"] == outcome_seq)
                    node_id = outcome["data"]["node_id"]
                    intents = [
                        e
                        for e in events
                        if e["kind"] == "execution_intent"
                        and e["data"].get("receipt") == receipt
                        and e["data"].get("node_id") == node_id
                        and e["seq"] < outcome_seq
                    ]
                    if len(intents) != 1:
                        raise ValueError("Transition requires one earlier execution intent")
                    intent = intents[0]
                    decisions = [
                        e
                        for e in events
                        if e["kind"] == "decision"
                        and e["data"].get("decision") == "execute"
                        and e["data"].get("node_id") == node_id
                        and e["data"].get("action") == action.model_dump()
                        and not e["data"].get("direct", False)
                        and e["seq"] < intent["seq"]
                    ]
                    authorizations = [
                        e
                        for e in events
                        if e["kind"] == "authorization"
                        and e["data"].get("state_id") == before.id
                        and e["data"].get("action_hash") == action.fingerprint
                        and e["data"].get("decision") == "execute"
                        and decisions
                        and e["seq"] < decisions[-1]["seq"]
                    ]
                    observed = [
                        e
                        for e in events
                        if e["kind"] == "observed"
                        and e["data"].get("state") == before.model_dump()
                        and authorizations
                        and e["seq"] < authorizations[-1]["seq"]
                    ]
                    elapsed = (_timestamp(after.state) - _timestamp(before)).total_seconds()
                    if (
                        not observed
                        or before.kind != "observed"
                        or after.state.domain != before.domain
                        or action.state_id != before.id
                        or elapsed < 0
                    ):
                        raise ValueError("Transition requires aligned authoritative observations")
                    if previous is not None and (
                        previous.domain != before.domain
                        or _timestamp(before) < _timestamp(previous)
                    ):
                        raise ValueError("Episode transition continuity/order mismatch")
                    if receipt in seen:
                        continue
                    seen.add(receipt)
                    transition = Transition(
                        episode_id=episode_id,
                        run_id=run_id,
                        before=before,
                        action=action,
                        after=after,
                        receipt=receipt,
                        before_reference=f"{run_id}:{observed[-1]['seq']}",
                        authorization_reference=f"{run_id}:{authorizations[-1]['seq']}",
                        intent_reference=f"{run_id}:{intent['seq']}",
                        outcome_reference=experience.reference,
                        observed_elapsed_seconds=elapsed,
                        declared_action_duration=action.duration,
                        continuous_from_previous=(
                            None if previous is None else previous.payload == before.payload
                        ),
                    )
                    transitions.append(transition.model_copy(deep=True))
                    previous = after.state
        if await self.store.call("run_heads", runs) != heads:
            raise ValueError("Transition authority changed during validation; retry snapshot")
        payload = dict(
            episodes=episodes,
            transitions=transitions,
            runs_without_outcomes=empty,
            unlabelled_receipts=unlabelled,
        )
        snapshot = TransitionSnapshot(dataset_hash="", **payload)
        snapshot.dataset_hash = identity(snapshot.model_dump(exclude={"dataset_hash"}))
        return snapshot


def require_disjoint(training: TransitionSnapshot, evaluation: TransitionSnapshot) -> None:
    """Reject reuse of an episode, run or receipt across the experimental split."""
    training.verify_identity()
    evaluation.verify_identity()
    if (
        set(training.episodes) & set(evaluation.episodes)
        or {r for runs in training.episodes.values() for r in runs}
        & {r for runs in evaluation.episodes.values() for r in runs}
        or {t.receipt for t in training.transitions} & {t.receipt for t in evaluation.transitions}
    ):
        raise ValueError("Training and evaluation must be episode/run/receipt disjoint")
