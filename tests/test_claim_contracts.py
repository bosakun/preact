import pytest
from pydantic import ValidationError

from preact.core.evidence import bind_claims, supports, task_definitions
from preact.core.interfaces import EngineFailure
from preact.core.models import ClaimDefinition, Continuation, EvidenceKind, PredictionRequest
from preact.core.registry import Registry
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier


async def test_definitions_do_not_contain_episode_identity_but_instances_do():
    world = SoftwareWorld()
    state = await world.observe()
    actions = await world.propose(state, 3)
    a = bind_claims(world.task, state.id, [actions[0]])
    b = bind_claims(world.task, state.id, [actions[1]])
    assert a[0].definition == b[0].definition
    assert a[0].key != b[0].key
    assert "state_id" not in a[0].definition.model_dump()
    assert a[0].model_copy(update={"horizon": 3}).key != a[0].key
    assert a[0].model_copy(update={"conditions": {"dynamics": "v2"}}).key != a[0].key
    cap = LocalVerifier(world).capabilities
    assert supports(cap, a[0]) and supports(cap, b[0])
    changed = a[0].model_copy(
        update={"definition": a[0].definition.model_copy(update={"version": "v2"})}
    )
    assert not supports(cap, changed)


async def test_ambiguous_action_list_and_observation_engine_fail_closed():
    world = SoftwareWorld()
    state = await world.observe()
    actions = await world.propose(state, 3)
    engine = LocalVerifier(world)
    with pytest.raises(EngineFailure, match="lineage"):
        await Registry([engine]).predict(
            engine, PredictionRequest(state=state, actions=actions[:2])
        )
    engine.capabilities.evidence = EvidenceKind.OBSERVATION
    with pytest.raises(EngineFailure, match="observation"):
        Registry([engine])


def test_root_claim_cannot_hide_future_agent_interventions():
    with pytest.raises(ValidationError):
        ClaimDefinition(name="safety", version="v1", kind="check", scope="anything")
    assert Continuation.ENVIRONMENT_ONLY != Continuation.ACTION_SEQUENCE


async def test_legacy_checks_are_task_local_and_unknown_capabilities_do_not_cover_checks():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 1))[0]
    claim = bind_claims(world.task, state.id, [action])[-1]
    cap = LocalVerifier(world).capabilities.model_copy(
        update={"supported_claims": None, "verification_checks": None}
    )
    assert not supports(cap, claim)
    other = world.task.model_copy(update={"id": "different-task"})
    assert task_definitions(other)[-1] != claim.definition
