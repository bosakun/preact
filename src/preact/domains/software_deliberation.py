"""Owned checkout workflow and bounded adapters over trusted native patches."""

from dataclasses import asdict, dataclass

from preact.core.evidence import bind_claims, supports
from preact.core.models import (
    Action,
    ClaimInstance,
    Continuation,
    PredictionRequest,
    SequenceConditioning,
    SequenceTransition,
    identity,
)
from preact.core.registry import Registry
from preact.domains.software import BASE, PREPARE, REPAIR, SAFE, SoftwareWorld
from preact.engines.deliberation_usage import usage_record
from preact.engines.local import LocalHeuristic
from preact.engines.software_sequence import SoftwareSequenceHeuristic


class WorkflowSoftwareWorld(SoftwareWorld):
    """Declared staged integration fixture; only existing allowlisted Python executes."""

    def __init__(self, seed=0):
        super().__init__(seed)
        self.task = self.task.model_copy(
            update={
                "id": "owned-validator-workflow-v1",
                "title": "Integrate reusable checkout validation",
                "goal": "Reach the integrated reusable validator through declared patch stages",
            }
        )
        self.payload["workflow_progress"] = 0.0

    @staticmethod
    def options(state):
        code = state.payload["files"]["checkout.py"]
        return {
            BASE: [REPAIR, PREPARE],
            REPAIR: [PREPARE],
            PREPARE: [SAFE, REPAIR],
            SAFE: [REPAIR],
        }[code]

    async def propose(self, state, width):
        return [
            Action(
                name={
                    REPAIR: "Immediate repair",
                    PREPARE: "Prepare validator",
                    SAFE: "Integrate validator",
                }[code],
                kind="patch",
                state_id=state.id,
                payload={
                    "files": {"checkout.py": code},
                    "stage": {REPAIR: "repaired", PREPARE: "prepared", SAFE: "integrated"}[code],
                },
            )
            for code in self.options(state)[:width]
        ]

    def validate(self, state, action):
        super().validate(state, action)
        if action.payload["files"]["checkout.py"] not in self.options(state):
            raise ValueError("Patch is outside the declared staged workflow")

    def materialize(self, state, action):
        result = super().materialize(state, action)
        payload = dict(result.payload)
        payload["workflow_progress"] = {REPAIR: 0.6, PREPARE: 0.25, SAFE: 1.0}[
            payload["files"]["checkout.py"]
        ]
        # State.create revalidates content identity; never edit an authoritative State.
        from preact.core.models import State

        return State.create(
            "software",
            payload,
            "patch-materialization",
            kind="hypothetical",
            parent_id=state.id,
            uncertainty=state.uncertainty,
        )

    def complete(self, state):
        return state.payload.get("workflow_progress", 0) >= 1


class SoftwarePredictionAdapter:
    """Finite explicit paths, using Registry's accepted ordered lineage."""

    def __init__(self, world, store, *, horizon=2, predictor=None):
        if not 1 <= horizon <= 3:
            raise ValueError("Software horizon must be 1..3")
        self.world, self.store, self.horizon = world, store, horizon
        self.predictor = predictor or LocalHeuristic(world)
        if type(self.predictor) is not LocalHeuristic or not isinstance(world, SoftwareWorld):
            raise ValueError("v1 Software adapter supports the trusted local heuristic only")
        if getattr(world, "proposal_calls", 0) or world.proposal_usage_complete is not True:
            raise ValueError("External candidate generation needs a separately accounted adapter")
        self.usage = []
        self.sequence = SoftwareSequenceHeuristic(world, self.predictor, self.usage)
        self.max_calls = 3 * (1 if horizon == 1 else 5 if horizon == 2 else 11)

    def signature(self):
        return identity(
            {
                "adapter": "software-registry-sequence/v1",
                "horizon": self.horizon,
                "max_calls": self.max_calls,
                "task": self.world.task.model_dump(),
                "predictor": self.predictor.capabilities.model_dump(),
                "sequence": self.sequence.capabilities.model_dump(),
            }
        )

    async def heads(self, runs):
        return await self.store.call("run_heads", list(dict.fromkeys(runs)))

    async def candidates(self, state, width):
        return await self.world.propose(state, width)

    async def check_basis(self, record):
        return None  # Immutable settings/capabilities are fenced by signature and State.

    async def forecast(self, state, actions, runs):
        registry = Registry([self.predictor, self.sequence])
        candidates = []
        for root in actions:
            request = PredictionRequest(
                state=state,
                actions=[root],
                claims=[
                    c
                    for c in bind_claims(self.world.task, state.id, [root])
                    if supports(self.predictor.capabilities, c)
                ],
            )
            p, _ = await registry.predict(self.predictor, request)
            self.usage.append(usage_record(p))
            records = [{"request": request.model_dump(), "prediction": p.model_dump()}]
            paths = []
            if p.successor is None or p.outcomes or p.future_states:
                candidates.append(
                    {
                        "action": root.model_dump(),
                        "fingerprint": root.fingerprint,
                        "status": "unknown",
                        "reason": "unsupported_successor",
                        "records": records,
                        "paths": [],
                    }
                )
                continue
            if self.horizon == 1:
                paths.append(
                    {
                        "actions": [root.model_dump()],
                        "states": [p.successor.model_dump()],
                        "prediction_ids": [p.id],
                        "weight": 1.0,
                    }
                )
            else:

                async def extend(current, chain, transitions, states, ids):
                    next_actions = await self.world.propose(current, 2)
                    if not next_actions:
                        return
                    for action in next_actions:
                        if len(chain) + 1 == self.horizon:
                            sequence_actions = [*chain, action]
                            claim = ClaimInstance(
                                definition=self.sequence.definition,
                                state_id=state.id,
                                task_context=identity(self.world.task.model_dump()),
                                action_ids=tuple(a.id for a in sequence_actions),
                                action_fingerprints=tuple(a.fingerprint for a in sequence_actions),
                                horizon=self.horizon,
                                continuation=Continuation.ACTION_SEQUENCE,
                            )
                            req = PredictionRequest(
                                state=state,
                                actions=sequence_actions,
                                horizon=self.horizon,
                                claims=[claim],
                                conditioning=SequenceConditioning(transitions=transitions),
                            )
                            pred, _ = await registry.predict(self.sequence, req)
                            self.usage.append(usage_record(pred))
                            records.append(
                                {"request": req.model_dump(), "prediction": pred.model_dump()}
                            )
                            paths.append(
                                {
                                    "actions": [a.model_dump() for a in sequence_actions],
                                    "states": [*states, pred.successor.model_dump()],
                                    "prediction_ids": [*ids, pred.id],
                                    "weight": None,
                                }
                            )
                        else:
                            req = PredictionRequest(
                                state=current,
                                actions=[action],
                                claims=[
                                    c
                                    for c in bind_claims(self.world.task, current.id, [action])
                                    if supports(self.predictor.capabilities, c)
                                ],
                            )
                            pred, _ = await registry.predict(self.predictor, req)
                            self.usage.append(usage_record(pred))
                            records.append(
                                {"request": req.model_dump(), "prediction": pred.model_dump()}
                            )
                            if pred.successor is None or pred.outcomes or pred.future_states:
                                raise ValueError("Cannot compose stochastic or missing successor")
                            transition = SequenceTransition(
                                action_id=action.id,
                                input_state_id=current.id,
                                successor=pred.successor,
                                prediction_id=pred.id,
                            )
                            await extend(
                                pred.successor,
                                [*chain, action],
                                [*transitions, transition],
                                [*states, pred.successor.model_dump()],
                                [*ids, pred.id],
                            )

                await extend(
                    p.successor,
                    [root],
                    [
                        SequenceTransition(
                            action_id=root.id,
                            input_state_id=state.id,
                            successor=p.successor,
                            prediction_id=p.id,
                        )
                    ],
                    [p.successor.model_dump()],
                    [p.id],
                )
            candidates.append(
                {
                    "action": root.model_dump(),
                    "fingerprint": root.fingerprint,
                    "status": "estimated" if paths else "unknown",
                    "reason": None if paths else "unsupported_continuation",
                    "records": records,
                    "paths": paths,
                }
            )
        return {
            "adapter": "software-registry-sequence/v1",
            "horizon": self.horizon,
            "continuation": str(
                Continuation.ACTION_SEQUENCE if self.horizon > 1 else Continuation.ENVIRONMENT_ONLY
            ),
            "model_group": self.signature(),
            "candidates": candidates,
            "assumptions": [
                "deterministic trusted template dynamics",
                "best bounded optional intervention; no safety certification",
            ],
        }


@dataclass(frozen=True)
class SoftwareGoalAdapter:
    action_cost: float = 0.05
    uncertainty_cost: float = 0.1

    def signature(self):
        return identity({"adapter": "software-goal-progress/v1", **asdict(self)})

    def evaluate(self, future, state, goals):
        if len(goals) != 1:
            raise ValueError("Software v1 requires one scalar Goal")
        goal = goals[0]
        options = []
        for path in future["paths"]:
            progress = max(float(s["payload"].get(goal.metric, 0)) for s in path["states"])
            uncertain = max(r["prediction"]["success"]["uncertainty"] for r in future["records"])
            value = (
                min(goal.target, progress)
                - self.action_cost * len(path["actions"])
                - self.uncertainty_cost * uncertain
            )
            options.append(
                {
                    "value": value,
                    "progress": progress,
                    "uncertainty": uncertain,
                    "actions": len(path["actions"]),
                    "prediction_ids": path["prediction_ids"],
                }
            )
        best = max(options, key=lambda p: p["value"])
        return {
            **best,
            "alternatives": options,
            "rule": "best_attained_goal_minus_cost/v1",
            "calibrated": False,
        }
