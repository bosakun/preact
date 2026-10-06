from preact.core.calibration import Calibration, context_key
from preact.core.decision import evaluate, gate
from preact.core.models import Decision, Policy, PredictionRequest
from preact.core.store import Store
from preact.domains.software import SoftwareWorld
from preact.engines.local import LocalVerifier


async def test_risk_errors_change_gate_even_when_success_predictions_were_correct():
    world = SoftwareWorld()
    state = await world.observe()
    action = (await world.propose(state, 2))[1]
    p = await LocalVerifier(world).predict(PredictionRequest(state=state, actions=[action]))
    store = Store("sqlite:///:memory:")
    for index in range(8):
        store.record_error(str(index), "local-tests@1", "software:repair:h1", 1, True, 0, True)
    stats = Calibration(store).snapshot("software:repair:h1")
    assert not stats["local-tests@1"]["drift"]
    assert stats["local-tests@1"]["risk_drift"]
    assert stats["local-tests@1"]["false_safe_count"] == 8
    assert (
        gate(
            evaluate([p], world.task, stats), state.id, action.fingerprint, Policy(), True
        ).decision
        == Decision.VERIFY
    )


def test_sparse_trust_never_pools_different_horizons_claims_or_truth_authorities():
    store = Store("sqlite:///:memory:")
    base = dict(
        domain="software",
        task="repair",
        horizon=1,
        success_metric="postconditions/v1",
        risk_metric="unsafe/v1",
        truth_source="sandbox-authority/v1",
    )
    context = context_key(**base)
    for index in range(4):
        store.record_error(str(index), "engine@1", context, 0.9, False, 0.1, True)
    calibration = Calibration(store)
    assert calibration.snapshot(context_key(**{**base, "task": "other"}))["engine@1"]["n"] == 4
    for key, changed in [
        ("domain", "physical"),
        ("horizon", 3),
        ("success_metric", "task_completion/v1"),
        ("risk_metric", "collision/v2"),
        ("truth_source", "local-simulation/v1"),
    ]:
        assert calibration.snapshot(context_key(**{**base, key: changed}))["engine@1"]["n"] == 0
    assert calibration.snapshot("software:repair:h1")["engine@1"]["n"] == 0
