"""Actual gated episodes for learning tests; no private service inspection."""

from preact.cognition import CognitiveAgent, Goal
from preact.cognition.models import Belief
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.core.store import Artifacts, Store
from preact.domains.information_queue import InformationQueueWorld
from preact.engines.information_queue import InformationBoundVerifier
from preact.learning import TransitionDataset


class ScheduledPlanner:
    def infer(self, state, experience):
        return Belief(observed=state, unknown=["current_service"])

    def propose(self, belief, goals, width):
        state = belief.observed
        amounts = (3, 1, 0)
        index = state.payload["tick"] % len(amounts)
        return [InformationQueueWorld.action(state, amounts[index])][:width]


async def collect(
    tmp_path, *, store=None, world=None, rounds=12, episode="episode", engines=None, policy=None
):
    world = world or InformationQueueWorld(
        ticks=rounds, target=999, capacity=12, high_first=False, shift_tick=rounds, noise=0
    )
    store = store or Store("sqlite:///" + str(tmp_path / "ledger.db"))
    agent = CognitiveAgent(
        store,
        Artifacts(str(tmp_path / "artifacts")),
        Registry(
            engines
            if engines is not None
            else [
                InformationBoundVerifier(world.task),
                InformationBoundVerifier(world.task, future=True),
            ]
        ),
        ScheduledPlanner(),
        [Goal(name="Deliver", metric="delivered", target=999)],
        policy or Policy(search=False, calibration=False, width=1, max_nodes=1, max_calls=8),
    )
    result = await agent.run(world, rounds)
    return world, agent, result, TransitionDataset(store, {episode: result.run_ids})
