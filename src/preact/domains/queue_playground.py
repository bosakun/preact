"""Operator-owned exhibition fixtures around the existing Queue cognitive pipeline.

Only constructor configuration and declared preparation/evaluation sequences live
here. The World alone generates/reads its private service schedule. No UI payload
or scenario name enters a prediction adapter.
"""

from preact.cognition import CognitiveAgent, Goal, WorldPlanner
from preact.cognition.lifecycle import LearningCoordinator
from preact.core.models import Policy
from preact.core.registry import Registry
from preact.domains.information_queue import InformationQueueWorld
from preact.domains.queue_learning import QueueLearningBackend
from preact.domains.queue_service_features import QueueServiceAdapter
from preact.engines.information_queue import InformationBoundVerifier
from preact.engines.queue_continuous import ContinuousQueueAdaptation
from preact.learning import TabularDynamicsTrainer, TransitionDataset
from preact.learning.recovery import EvaluationBranch, EvaluationCase

PRESETS = {
    "stable_high": (True, False, "安定・高能力"),
    "stable_low": (False, False, "安定・低能力"),
    "high_to_low": (True, True, "処理能力低下 High → Low"),
    "low_to_high": (False, True, "処理能力向上 Low → High"),
}
ROUNDS = 56


def queue_world(scenario, seed):
    high, shift, _ = PRESETS[scenario]
    return InformationQueueWorld(
        seed=seed,
        ticks=ROUNDS + 3,
        target=999,
        noise=0,
        high_first=high,
        shift_tick=8 if shift else ROUNDS + 3,
    )


def verification_registry(world):
    return Registry(
        [InformationBoundVerifier(world.task), InformationBoundVerifier(world.task, future=True)]
    )


class _DeclaredCollection:
    """A declared preparation sequence, not a learned or simulated decision engine."""

    def __init__(self, sequence, start):
        self.sequence, self.start = tuple(sequence), start

    async def propose(self, state, width):
        amount = self.sequence[state.payload["tick"] - self.start]
        return [
            InformationQueueWorld.probe(state)
            if amount == "probe"
            else InformationQueueWorld.action(state, amount)
        ][:width]


async def collect(world, sequence, store, artifacts, policy=None):
    generator = _DeclaredCollection(sequence, (await world.observe()).payload["tick"])
    agent = CognitiveAgent(
        store,
        artifacts,
        verification_registry(world),
        WorldPlanner(world, generator),
        [Goal(name="Declared preparation", metric="delivered", target=999)],
        (policy or Policy(calibration=False)).model_copy(
            update={
                "search": False,
                "width": 1,
                "max_nodes": 1,
                "max_calls": min(32, policy.max_calls if policy else 32),
            }
        ),
    )
    result = await agent.run(world, len(sequence))
    if result.unsafe or sum(r["steps"] for r in result.rounds) != len(sequence):
        raise ValueError("Incomplete formal Queue preparation")
    return result.run_ids


async def independent_evaluation(recovery, candidate, generation, *, high, policy=None):
    """The existing frozen recovery workload/seed design, using separate actual Worlds.

    Conditions are declared by the operator, never discovered from the source
    executor. Candidate quality/coverage/promotion thresholds remain in recovery.
    """
    cases = []
    for seed in (301, 302, 303, 304):
        for label, prefix in (("pending", [3]), ("queue_pending", [3, 3])):
            worlds, initials, prefixes = [], [], []
            for _ in (3, 1, 0):
                world = InformationQueueWorld(
                    seed=seed,
                    ticks=128,
                    target=999,
                    capacity=12,
                    noise=0,
                    high_first=False,
                    shift_tick=len(prefix) if high else 128,
                )
                initials.append(await world.observe())
                prefixes.append(
                    await collect(world, prefix, recovery.store, recovery.artifacts, policy)
                )
                worlds.append(world)
            state = await worlds[0].observe()
            actions = [InformationQueueWorld.action(state, n) for n in (3, 1, 0)]
            forecast = await recovery.forecast_candidate(candidate, worlds[0].task, state, actions)
            branches = []
            for index, amount in enumerate((3, 1, 0)):
                suffix = await collect(
                    worlds[index], [amount, 0, 0], recovery.store, recovery.artifacts, policy
                )
                branches.append(
                    EvaluationBranch(
                        episode_id=f"exhibit-evaluation:{generation}:{seed}:{label}:{amount}",
                        initial=initials[index],
                        runs=prefixes[index] + suffix,
                        final_observation=await worlds[index].observe(),
                    )
                )
            cases.append(EvaluationCase(forecast_artifact=forecast, branches=branches))
    return cases


async def prepare_queue(store, artifacts, scenario, seed, episode, policy=None):
    world = queue_world(scenario, seed)
    high, shift, _ = PRESETS[scenario]
    teacher = InformationQueueWorld(
        seed=701,
        ticks=32,
        target=999,
        noise=0,
        high_first=high,
        shift_tick=32,
    )
    training = {
        f"preparation:{episode}": await collect(teacher, ["probe"] * 16, store, artifacts, policy)
    }
    model = await TabularDynamicsTrainer().fit(
        TransitionDataset(store, training), QueueServiceAdapter()
    )
    lifecycle = await ContinuousQueueAdaptation.create(store, artifacts, episode)
    await lifecycle.start(
        model,
        training,
        world_episode_id=episode,
        task=world.task,
        anchor=await world.observe(),
    )

    async def evaluate(recovery, candidate, generation):
        return await independent_evaluation(
            recovery, candidate, generation, high=(not high if shift else high), policy=policy
        )

    backend = QueueLearningBackend(lifecycle, evaluate)
    learning = await LearningCoordinator.create(backend)
    return world, learning, training


def cognitive_runner(store, artifacts, world, learning, episode, observer, policy=None):
    policy = policy or Policy()
    width = min(3, policy.width, policy.max_nodes)
    return CognitiveAgent(
        store,
        artifacts,
        verification_registry(world),
        WorldPlanner(world),
        [Goal(name="Continuous throughput fixture", metric="delivered", target=999)],
        policy.model_copy(update={"search": False, "width": width, "max_nodes": width}),
        learning_coordinator=learning,
        episode_id=episode,
        round_observer=observer,
    )
