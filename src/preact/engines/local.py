from __future__ import annotations

import asyncio

from preact.core.models import Capabilities, Estimate, EvidenceKind, Prediction, State
from preact.domains.physical import rollout
from preact.domains.software import probe


def certain(value):
    return Estimate(
        value=float(value), lower=float(value), upper=float(value), uncertainty=0, measured=True
    )


class LocalHeuristic:
    """Explicit non-LLM hypothesis generator for local development, never sponsor evidence."""

    def __init__(self, world):
        self.world = world
        self.capabilities = Capabilities(
            engine_id="local-heuristic",
            version="1",
            family="local-heuristic",
            domains=[world.task.domain],
            evidence=EvidenceKind.INFERENCE,
            tier=0,
            produces_successor=True,
            applicability="Bundled tasks only; heuristic probability is uncalibrated",
        )

    async def predict(self, request):
        action = request.actions[0]
        successor = self.world.materialize(request.state, action)
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=request.state.id,
            action_ids=[action.id],
            evidence=EvidenceKind.INFERENCE,
            success=Estimate(value=0.92, lower=0.4, upper=0.99, uncertainty=0.4),
            risk=Estimate(value=0.03, lower=0, upper=0.5, uncertainty=0.5),
            successor=successor,
            assumptions=["Cheap template/kinematic hypothesis; no executable safety evidence"],
        )


class LocalVerifier:
    def __init__(self, world):
        self.world = world
        self.capabilities = Capabilities(
            engine_id="local-tests" if world.task.domain == "software" else "mujoco",
            version="1",
            family="execution" if world.task.domain == "software" else "mujoco-physics",
            domains=[world.task.domain],
            evidence=EvidenceKind.EXECUTABLE
            if world.task.domain == "software"
            else EvidenceKind.SIMULATION,
            tier=2,
            produces_successor=True,
            applicability="Trusted bundled source / deterministic Cartesian MuJoCo lab, not Isaac or hardware",
        )

    async def predict(self, request):
        action, state = request.actions[0], request.state
        self.world.validate(state, action)
        if hasattr(self.world, "verify_future"):
            payload, checks, metrics, raw = await self.world.verify_future(
                state, action, request.seed
            )
            successor = State.create(
                state.domain,
                payload,
                "trusted-release-verification",
                kind="hypothetical",
                parent_id=state.id,
            )
        elif state.domain == "software":
            successor = self.world.materialize(state, action)
            results = await probe(successor.payload["files"]["checkout.py"], request.seed)
            successor = State.create(
                "software",
                {**successor.payload, "goal_complete": all(results.values())},
                "local-probe-measurement",
                kind="hypothetical",
                parent_id=state.id,
            )
            checks = {
                "syntax": True,
                "regressions": all(
                    results[k]
                    for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
                ),
            }
            metrics = {
                "goal_progress": float(results["over_discount"]),
                "tests_passed": sum(results.values()),
            }
            raw = {"tests": results}
        else:
            payload, checks, metrics = await asyncio.to_thread(
                rollout, state.payload, action, request.seed
            )
            successor = State.create(
                "physical", payload, "mujoco-rollout", kind="hypothetical", parent_id=state.id
            )
            raw = {
                "trajectory": payload["trajectory"],
                "truth_scope": "deterministic local simulation",
            }
        safe = all(checks.values())
        return Prediction(
            engine_id=self.capabilities.engine_id,
            engine_version="1",
            family=self.capabilities.family,
            state_id=state.id,
            action_ids=[action.id],
            evidence=self.capabilities.evidence,
            success=certain(safe),
            risk=certain(not safe),
            mandatory_checks=checks,
            violations=[k for k, passed in checks.items() if not passed],
            successor=successor,
            metrics=metrics,
            vectors={"object_position": successor.payload["object"]}
            if state.domain == "physical"
            else {},
            raw=raw,
            assumptions=[
                "Deterministic reference measurements; model mismatch is outside this local evidence scope"
            ],
        )
