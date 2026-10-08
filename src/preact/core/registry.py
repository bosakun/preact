import asyncio
import time

from pydantic import ValidationError

from .evidence import supports
from .interfaces import EngineFailure, FutureEngine
from .models import Continuation, EvidenceKind, Prediction, PredictionRequest, identity


class Registry:
    def __init__(self, engines: list[FutureEngine]):
        self.engines = engines
        self.cache = {}
        self.accepted = {}
        self.accepted_requests = {}
        self.declarations = {id(e): e.capabilities.model_copy(deep=True) for e in engines}
        identifiers = [e.engine_id for e in self.declarations.values()]
        if any(c.evidence == EvidenceKind.OBSERVATION for c in self.declarations.values()):
            raise EngineFailure("FutureEngine cannot declare authoritative observation")
        if len(set(identifiers)) != len(identifiers):
            raise EngineFailure("Registered engine identifiers must be unique")

    def eligible(self, domain: str):
        return sorted(
            [e for e in self.engines if domain in self.declarations[id(e)].domains],
            key=lambda e: (self.declarations[id(e)].tier, self.declarations[id(e)].engine_id),
        )

    async def predict(self, engine: FutureEngine, request: PredictionRequest):
        cap = self.declarations.setdefault(id(engine), engine.capabilities.model_copy(deep=True))
        if engine.capabilities != cap:
            raise EngineFailure("Engine changed its registered capabilities")
        if request.state.domain not in cap.domains or request.horizon > cap.max_horizon:
            raise EngineFailure("Unsupported domain or horizon")
        if request.actions[0].state_id != request.state.id:
            raise EngineFailure("Action is not conditioned on the requested state")
        if cap.evidence == EvidenceKind.OBSERVATION:
            raise EngineFailure("Observation authority belongs to Domain Adapter")
        terminal_input = request.state
        if len(request.actions) > 1:
            if Continuation.ACTION_SEQUENCE not in cap.continuations or not request.claims:
                raise EngineFailure("Explicit sequence capability, claims and lineage required")
            if (
                request.conditioning is None
                or len(request.conditioning.transitions) != len(request.actions) - 1
            ):
                raise EngineFailure("Multiple actions require proven ordered lineage")
            for action, transition in zip(request.actions[:-1], request.conditioning.transitions):
                source = self.accepted.get(transition.prediction_id)
                if (
                    source is None
                    or source.state_id != terminal_input.id
                    or source.action_ids != [action.id]
                    or source.horizon != 1
                ):
                    raise EngineFailure(
                        "Sequence transition needs accepted aligned single-action evidence"
                    )
                source_request = self.accepted_requests[transition.prediction_id]
                if (
                    not source_request.claims
                    or source_request.actions[0].fingerprint != action.fingerprint
                ):
                    raise EngineFailure(
                        "Sequence source needs explicit compatible context and action binding"
                    )
                for claim in request.claims:
                    if any(
                        c.task_context != claim.task_context or c.conditions != claim.conditions
                        for c in source_request.claims
                    ):
                        raise EngineFailure("Sequence source conditions are incompatible")
                candidates = [source.successor] + [o.state for o in source.outcomes]
                if (
                    action.state_id != terminal_input.id
                    or transition.action_id != action.id
                    or transition.input_state_id != terminal_input.id
                    or transition.successor not in candidates
                ):
                    raise EngineFailure("Invalid sequence transition binding")
                terminal_input = transition.successor
            if request.actions[-1].state_id != terminal_input.id:
                raise EngineFailure("Final action is not bound to its predecessor state")
        elif request.conditioning is not None:
            raise EngineFailure("Single-action request cannot add hidden interventions")
        if request.horizon > 1 and not request.claims:
            raise EngineFailure("Multi-horizon forecasts require explicit continuation claims")
        for claim in request.claims:
            if (
                claim.state_id != request.state.id
                or claim.action_ids != tuple(a.id for a in request.actions)
                or claim.action_fingerprints != tuple(a.fingerprint for a in request.actions)
                or claim.horizon != request.horizon
                or not supports(cap, claim)
            ):
                raise EngineFailure("Unsupported or incorrectly bound claim")
            if claim.horizon > 1 and claim.continuation == Continuation.ENVIRONMENT_ONLY:
                dynamics = claim.conditions.get("dynamics")
                if (
                    not isinstance(dynamics, dict)
                    or not all(dynamics.get(k) for k in ("name", "version", "unit"))
                    or dynamics.get("agent_interventions") != "none"
                ):
                    raise EngineFailure(
                        "Environmental continuation requires no-intervention domain dynamics definition"
                    )
        before = identity(request.model_dump())
        key = identity({"request": request.model_dump(), "engine": cap.model_dump()})
        if key in self.cache:
            return self.cache[key].model_copy(deep=True), True
        started = time.monotonic()
        isolated = request.model_copy(deep=True)
        result = await asyncio.wait_for(engine.predict(isolated), request.deadline_seconds)
        if identity(isolated.model_dump()) != before:
            raise EngineFailure("Engine mutated prediction input")
        try:
            result = Prediction.model_validate(result.model_dump(warnings=False))
        except ValidationError as error:
            raise EngineFailure("Engine returned an invalid prediction contract") from error
        if (
            result.state_id != request.state.id
            or result.action_ids != [a.id for a in request.actions]
            or result.engine_id != cap.engine_id
            or result.engine_version != cap.version
            or result.evidence != cap.evidence
            or result.family != cap.family
            or result.horizon != request.horizon
        ):
            raise EngineFailure("Engine violated prediction identity/evidence contract")
        if (result.success.measured or result.risk.measured) and (
            result.evidence not in {EvidenceKind.EXECUTABLE, EvidenceKind.SIMULATION}
            or not set(cap.roles).intersection({"simulator", "verifier"})
        ):
            raise EngineFailure("Engine cannot promote predictions into measurements")
        if result.requested_claims is not None and result.requested_claims != request.claims:
            raise EngineFailure("Engine changed the declared request claim scope")
        result.requested_claims = (
            [c.model_copy(deep=True) for c in request.claims] if request.claims else None
        )
        if result.conditioning != request.conditioning:
            raise EngineFailure("Engine changed sequence conditioning")
        seen_claims = set()
        for finding in result.claim_results:
            if finding.claim not in request.claims or finding.claim.key in seen_claims:
                raise EngineFailure("Engine returned unsolicited or duplicate claim instance")
            seen_claims.add(finding.claim.key)
            if (
                (finding.estimate.measured or finding.check is not None)
                and result.evidence in {EvidenceKind.EXECUTABLE, EvidenceKind.SIMULATION}
                and not set(cap.roles).intersection({"simulator", "verifier"})
            ):
                raise EngineFailure("Engine role does not support claim verification")
            if finding.estimate.measured and result.evidence not in {
                EvidenceKind.EXECUTABLE,
                EvidenceKind.SIMULATION,
            }:
                raise EngineFailure("Unmeasured evidence cannot declare measurement")
        if (
            result.successor or result.outcomes or result.future_states
        ) and not cap.produces_successor:
            raise EngineFailure("Engine did not declare successor materialization")
        previous = request.state
        if result.future_states:
            if len(request.actions) != 1 or len(result.future_states) != request.horizon:
                raise EngineFailure("Environmental trace must cover the requested horizon")
            for state in result.future_states:
                if (
                    state.kind != "hypothetical"
                    or state.domain != previous.domain
                    or state.parent_id != previous.id
                ):
                    raise EngineFailure("Invalid environmental successor lineage")
                previous = state
            if result.successor is not None or result.outcomes:
                raise EngineFailure(
                    "Environmental trace cannot masquerade as an immediate successor"
                )
        if result.sample_count > min(request.sample_budget, cap.max_samples):
            raise EngineFailure("Engine exceeded its bounded sample budget")
        if not set(result.refines_engine_ids).issubset(cap.refines_engine_ids):
            raise EngineFailure("Engine claimed an undeclared evidence refinement")
        if result.successor is not None and result.outcomes:
            raise EngineFailure("Declare either a successor or a stochastic distribution")
        if len({o.label for o in result.outcomes}) != len(result.outcomes):
            raise EngineFailure("Stochastic outcome labels must be unique")
        if result.successor and (
            result.successor.kind != "hypothetical"
            or result.successor.parent_id != terminal_input.id
            or result.successor.domain != request.state.domain
        ):
            raise EngineFailure("Invalid hypothetical successor lineage")
        for outcome in result.outcomes:
            if (
                outcome.state.kind != "hypothetical"
                or outcome.state.parent_id != terminal_input.id
                or outcome.state.domain != request.state.domain
            ):
                raise EngineFailure("Invalid stochastic successor lineage")
        if result.outcomes and all(o.probability.value is not None for o in result.outcomes):
            if abs(sum(o.probability.value for o in result.outcomes) - 1) > 1e-6:
                raise EngineFailure("Stochastic outcome weights must sum to one")
        if result.id in self.accepted:
            raise EngineFailure("Engine reused a prediction source identity for a new request")
        result.latency_ms = (time.monotonic() - started) * 1000
        self.cache[key] = result.model_copy(deep=True)
        self.accepted[result.id] = result.model_copy(deep=True)
        self.accepted_requests[result.id] = request.model_copy(deep=True)
        return result, False
