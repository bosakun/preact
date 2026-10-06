from preact.domains.evaluation import checkout_evaluator
from preact.domains.software import BASE, PREPARE, PROBE, SAFE, SHORTCUT, probe


async def test_actual_trusted_sources_run_randomized_invariants_and_goal_checks():
    for seed in [0, 47, 999]:
        safe = await probe(SAFE, seed)
        assert all(safe.values())
        shortcut = await probe(SHORTCUT, seed)
        assert not shortcut["protected_invariants"]
        assert not shortcut["protected_goal"]
        for code in [BASE, PREPARE]:
            result = await probe(code, seed)
            assert result["protected_invariants"]
            assert not result["protected_goal"]


def test_hidden_sample_reproducibility_and_diversity():
    assert checkout_evaluator(PROBE, 1) == checkout_evaluator(PROBE, 1)
    assert checkout_evaluator(PROBE, 1) != checkout_evaluator(PROBE, 2)
