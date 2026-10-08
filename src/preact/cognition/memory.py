from preact.core.models import Action, Observation, State
from preact.core.store import Store

from .models import Experience


class EpisodicMemory:
    """An index into the execution ledger, not a second source of ground truth.

    A caller may restore the index with completed run IDs from an episode manifest.
    Records are reread and receipt checked on retrieval; no imagined branch is indexed.
    """

    def __init__(self, store: Store):
        self.store = store
        self.run_ids: list[str] = []

    async def read(self, run_id: str) -> list[Experience]:
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
            execution = await self.store.call("execution_record", receipt)
            node = nodes[data["node_id"]]
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

    async def remember(self, run_id: str) -> None:
        records = await self.read(run_id)
        if records and run_id not in self.run_ids:
            self.run_ids.append(run_id)

    async def retrieve(self, domain: str, limit: int = 12) -> list[Experience]:
        if limit <= 0:
            return []
        records = []
        for run_id in reversed(self.run_ids):
            records.extend(reversed(await self.read(run_id)))
            selected = [r for r in records if r.input_state.domain == domain]
            if len(selected) >= limit:
                break
        return list(reversed([r for r in records if r.input_state.domain == domain][:limit]))
