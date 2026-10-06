import asyncio
import time

from pydantic import ValidationError

from .interfaces import EngineFailure, FutureEngine
from .models import Prediction, PredictionRequest, identity


class Registry:
    def __init__(self, engines: list[FutureEngine]):
        self.engines = engines
        self.cache = {}
        self.declarations = {id(e): e.capabilities.model_copy(deep=True) for e in engines}
        identifiers = [e.engine_id for e in self.declarations.values()]
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
            or result.successor.parent_id != request.state.id
            or result.successor.domain != request.state.domain
        ):
            raise EngineFailure("Invalid hypothetical successor lineage")
        for outcome in result.outcomes:
            if (
                outcome.state.kind != "hypothetical"
                or outcome.state.parent_id != request.state.id
                or outcome.state.domain != request.state.domain
            ):
                raise EngineFailure("Invalid stochastic successor lineage")
        if result.outcomes and all(o.probability.value is not None for o in result.outcomes):
            if abs(sum(o.probability.value for o in result.outcomes) - 1) > 1e-6:
                raise EngineFailure("Stochastic outcome weights must sum to one")
        result.latency_ms = (time.monotonic() - started) * 1000
        self.cache[key] = result.model_copy(deep=True)
        return result, False
