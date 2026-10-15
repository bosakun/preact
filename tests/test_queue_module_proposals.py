"""Contract/authority checks with an explicitly isolated test reasoner."""

import json

import pytest

from preact.core.interfaces import EngineFailure
from preact.core.models import Capabilities, EvidenceKind, Policy
from preact.core.store import Artifacts, Store
from preact.domains.queue_playground import cognitive_runner, prepare_queue


class TestReasoner:
    __test__ = False

    def __init__(self, ranking):
        self.ranking, self.prompts = ranking, []
        self.capabilities = Capabilities(
            engine_id="isolated-test-reasoner",
            version="1",
            family="test-only",
            domains=["cognitive_queue_v2"],
            evidence=EvidenceKind.INFERENCE,
            tier=0,
            applicability="Isolated test fixture; never real model inference",
        )

    async def json_call(self, prompt, schema, seed, deadline):
        self.prompts.append(json.loads(prompt))
        return {"ranking": self.ranking}, {
            "request_id": "isolated-test-call",
            "cost_usd": 0,
            "cost_known": True,
            "external_api_calls": 0,
        }


async def run_composed(tmp_path, reasoner):
    store = Store("sqlite:///" + str(tmp_path / "ledger.db"))
    artifacts = Artifacts(str(tmp_path / "artifacts"))
    policy = Policy(calibration=False)
    world, learning, _ = await prepare_queue(
        store, artifacts, "stable_high", 11, "module-test", policy, reasoner=reasoner
    )
    runner = cognitive_runner(store, artifacts, world, learning, "module-test", None, policy)
    return store, runner, world, learning


async def test_llm_proposals_change_formal_candidate_set_without_execution_authority(tmp_path):
    reasoner = TestReasoner([2, 1, 0])
    store, runner, world, _ = await run_composed(tmp_path, reasoner)
    result = await runner.run(world, 9)
    assert len(reasoner.prompts) == 1  # eight native readiness rounds make no LLM call
    prompt = reasoner.prompts[0]
    assert prompt["operation"] == "rank_actions"
    assert set(prompt["state"]["payload"]) == {
        "tick",
        "queue",
        "pending",
        "delivered",
        "capacity",
        "service_bounds",
        "arrival_delay",
        "episode_ticks",
        "target",
        "probe_cost",
        "holding_cost",
        "submission_cost",
        "service_values",
    }
    assert "high_first" not in json.dumps(prompt) and "shift_tick" not in json.dumps(prompt)
    events = store.read_events(result.run_ids[-1])
    trace = next(e["data"] for e in events if e["kind"] == "deliberation")
    proposal = trace["simulation"]["proposal_module"]
    assert proposal["called"] and len(proposal["input_candidates"]) == 3
    assert len(proposal["output_fingerprints"]) == 2
    assert proposal["structured_output"] == {"ranking": [2, 1, 0]}
    assert [c["action"]["payload"]["amount"] for c in trace["simulation"]["candidates"]] == [0, 1]
    chosen = next(e["data"]["action"] for e in events if e["kind"] == "decision")
    assert chosen["payload"]["amount"] == 1  # numeric planning still chooses among LLM proposals
    kinds = [e["kind"] for e in events]
    assert kinds.index("authorization") < kinds.index("execution_intent") < kinds.index("outcome")
    usage = [e["data"] for e in events if e["kind"] == "agent_usage"]
    assert any(u.get("request_id") == "isolated-test-call" for u in usage)
    assert not result.unsafe


@pytest.mark.parametrize("ranking", [[99], [True]])
async def test_invalid_llm_output_does_not_fall_back_or_execute_another_action(tmp_path, ranking):
    reasoner = TestReasoner(ranking)
    store, runner, world, learning = await run_composed(tmp_path, reasoner)
    with pytest.raises(EngineFailure, match="Invalid proposer ranking"):
        await runner.run(world, 9)
    assert len(reasoner.prompts) == 1
    runs = [
        e["data"]["run"]
        for e in store.read_events(learning.journal_run)
        if e["kind"] == "learning_round_open"
    ]
    last = store.read_events(runs[-1])
    assert not any(e["kind"] in {"outcome", "execution_intent"} for e in last)
    assert sum(e["kind"] == "outcome" for r in runs for e in store.read_events(r)) == 8
