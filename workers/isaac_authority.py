"""Transactional Isaac authority; SDK execution stays separate from persistence."""

import asyncio

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import insert, select, update

from preact.core.models import Action, Observation, State, now, uid
from preact.domains.physical import PhysicalWorld
from preact.engines.isaac import Isaac, isaac_step
from preact.service.worker import worker_app


class EpisodeRequest(BaseModel):
    seed: int = Field(default=0, ge=0, le=100000)


class Execution(BaseModel):
    action: Action
    receipt: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")


def authority_app(store, token, step=isaac_step):
    app = worker_app(Isaac(), store, token)
    lane = asyncio.Lock()

    def create(seed, state):
        episode = uid()
        with store.db.begin() as conn:
            conn.execute(
                insert(store.runs).values(
                    id=episode,
                    created=now(),
                    status="episode",
                    config={"episode": True, "seed": seed},
                    result=state,
                    next_seq=0,
                )
            )
        return episode

    def reserve(episode, action, receipt):
        with store.db.begin() as conn:
            query = select(store.runs).where(store.runs.c.id == episode)
            if store.db.dialect.name == "postgresql":
                query = query.with_for_update()
            row = conn.execute(query).mappings().first()
            if not row or not row["config"].get("episode"):
                raise HTTPException(404, "Episode not found")
            prior = (
                conn.execute(select(store.executions).where(store.executions.c.id == receipt))
                .mappings()
                .first()
            )
            if prior:
                if prior["run_id"] != episode or prior["action_hash"] != action.fingerprint:
                    raise HTTPException(409, "Receipt belongs to a different action")
                if prior["status"] == "complete":
                    return None, prior["receipt"]
                raise HTTPException(409, "Uncertain execution; reconcile instead of retrying")
            if row["status"] != "episode":
                raise HTTPException(409, "Episode has unresolved execution; reconcile first")
            state = State.model_validate(row["result"])
            if action.state_id != state.id:
                raise HTTPException(409, "Stale action state")
            try:
                PhysicalWorld().validate(state, action)
            except ValueError:
                raise HTTPException(422, "Invalid physical action") from None
            # Cross-process reservation. Revision also fences an unchanged SDK state.
            changed = conn.execute(
                update(store.runs)
                .where(
                    store.runs.c.id == episode,
                    store.runs.c.status == "episode",
                    store.runs.c.next_seq == row["next_seq"],
                )
                .values(status="executing", next_seq=row["next_seq"] + 1)
                .returning(store.runs.c.id)
            ).scalar_one_or_none()
            if changed is None:
                raise HTTPException(409, "Episode changed before reservation")
            conn.execute(
                insert(store.executions).values(
                    id=receipt,
                    run_id=episode,
                    state_id=state.id,
                    action_hash=action.fingerprint,
                    status="pending",
                    receipt={},
                )
            )
        return {"state": state.model_dump(), "seed": row["config"]["seed"]}, None

    def complete(episode, action, receipt, observation):
        with store.db.begin() as conn:
            changed = conn.execute(
                update(store.executions)
                .where(
                    store.executions.c.id == receipt,
                    store.executions.c.run_id == episode,
                    store.executions.c.action_hash == action.fingerprint,
                    store.executions.c.status == "pending",
                )
                .values(status="complete", receipt=observation.model_dump())
                .returning(store.executions.c.id)
            ).scalar_one_or_none()
            if changed is None:
                raise HTTPException(409, "Execution receipt changed; reconcile first")
            changed = conn.execute(
                update(store.runs)
                .where(store.runs.c.id == episode, store.runs.c.status == "executing")
                .values(status="episode", result=observation.state.model_dump())
                .returning(store.runs.c.id)
            ).scalar_one_or_none()
            if changed is None:
                raise HTTPException(409, "Episode changed; reconcile first")

    @app.post("/episodes")
    async def create_episode(request: EpisodeRequest):
        state = await PhysicalWorld(request.seed).observe()
        async with lane:
            result = await step({"state": state.model_dump(), "seed": request.seed}, authority=True)
        observed = State.model_validate(result["state"])
        if observed.kind != "observed" or observed.domain != "physical":
            raise HTTPException(502, "SDK did not return a physical observation")
        return {"id": await store.call(create, request.seed, observed.model_dump())}

    @app.get("/episodes/{episode}")
    async def observe(episode: str):
        row = await store.call("get_run", episode)
        if not row or not row["config"].get("episode"):
            raise HTTPException(404, "Episode not found")
        if row["status"] != "episode":
            raise HTTPException(409, "Episode has unresolved execution; reconcile first")
        return row["result"]

    @app.post("/episodes/{episode}/actions")
    async def execute(episode: str, request: Execution):
        async with lane:
            bundle, prior = await store.call(reserve, episode, request.action, request.receipt)
            if prior is not None:
                return prior
            bundle["actions"] = [request.action.model_dump()]
            result = await step(bundle, authority=True)
            checks = result["checks"]
            observed = State.model_validate(result["state"])
            if observed.kind != "observed" or observed.domain != "physical":
                raise HTTPException(502, "SDK did not return a physical observation")
            observation = Observation(
                state=observed,
                success=observed.payload["delivered"],
                unsafe=not checks["clearance"],
                checks={**checks, "action_success": all(checks.values())},
                metrics=result["metrics"],
                vectors={"object_position": observed.payload["object"]},
                artifacts={"observed_video": observed.payload["camera_video"]}
                if observed.payload.get("camera_video")
                else {},
                receipt=request.receipt,
            )
            # Failed/cancelled SDK steps retain intent. Only a physical observation
            # atomically publishes the completed receipt and authoritative state.
            await store.call(complete, episode, request.action, request.receipt, observation)
            return observation.model_dump()

    return app
