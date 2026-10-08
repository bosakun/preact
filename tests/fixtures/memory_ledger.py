"""Controlled actual queue executions for receipt/index tests, not Gate validation."""

from preact.core.store import Store
from preact.domains.cognitive_queue import CognitiveQueueWorld


async def append_execution(store: Store, world: CognitiveQueueWorld, run_id: str) -> dict:
    state = await world.observe()
    action = world.action(state, 1)
    tick = state.payload["tick"]
    node_id = f"executed-{tick}"
    for index, amount in enumerate((1, 3, 0)):
        candidate = action if index == 0 else world.action(state, amount)
        node = {
            "id": node_id if index == 0 else f"unexecuted-{tick}-{index}",
            "state": state.model_dump(),
            "action": candidate.model_dump(),
        }
        store.append(run_id, "node", {"node": node})
        for horizon in (1, 3):
            store.append(
                run_id,
                "prediction",
                {
                    "node_id": node["id"],
                    "prediction": {"id": f"{node['id']}-h{horizon}"},
                },
            )
        store.append(run_id, "node_updated", {"node": node})
    receipt = store.intent(run_id, state.id, action.fingerprint)
    store.append(run_id, "execution_intent", {"node_id": node_id, "receipt": receipt})
    observation = await world.execute(action, receipt)
    store.complete_execution(receipt, observation.model_dump())
    outcome = {"node_id": node_id, "observation": observation.model_dump()}
    store.append(run_id, "outcome", outcome)
    return outcome
