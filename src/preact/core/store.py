from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    case,
    create_engine,
    insert,
    select,
    update,
)

from .io import durable_io
from .models import RunEvent, now, uid


class Store:
    """Append-only ordered events; SQL transactions support SQLite and PostgreSQL."""

    def __init__(self, url: str = "sqlite:///.preact/preact.db"):
        if url.startswith("sqlite:///") and not url.endswith(":memory:"):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"connect_args": {"check_same_thread": False}} if url.startswith("sqlite") else {}
        if url.startswith("postgresql"):
            kwargs = {
                "connect_args": {
                    "connect_timeout": 5,
                    "options": "-c statement_timeout=5000 -c lock_timeout=1000 "
                    "-c idle_in_transaction_session_timeout=10000",
                },
                "pool_timeout": 5,
            }
        if url.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
        self.db = create_engine(url, pool_pre_ping=True, **kwargs)
        self.lock = threading.RLock()
        meta = MetaData()
        self.runs = Table(
            "runs",
            meta,
            Column("id", String, primary_key=True),
            Column("created", String),
            Column("status", String),
            Column("config", JSON),
            Column("result", JSON),
            Column("next_seq", Integer, nullable=False, default=0),
        )
        self.events = Table(
            "events",
            meta,
            Column("run_id", String, primary_key=True),
            Column("seq", Integer, primary_key=True),
            Column("kind", String),
            Column("timestamp", String),
            Column("data", JSON),
        )
        self.errors = Table(
            "prediction_errors",
            meta,
            Column("id", String, primary_key=True),
            Column("prediction_id", String, unique=True),
            Column("engine", String),
            Column("context", String),
            Column("value", Float),
            Column("label", Integer),
            Column("risk_value", Float),
            Column("risk_label", Integer),
            Column("source", String),
            Column("timestamp", String),
        )
        self.executions = Table(
            "executions",
            meta,
            Column("id", String, primary_key=True),
            Column("run_id", String),
            Column("state_id", String),
            Column("action_hash", String),
            Column("status", String),
            Column("receipt", JSON),
        )
        self.jobs = Table(
            "jobs",
            meta,
            Column("id", String, primary_key=True),
            Column("run_id", String),
            Column("status", String),
            Column("owner", String),
            Column("lease_until", Float),
            Column("payload", JSON),
            Column("error", Text),
        )
        meta.create_all(self.db)

    async def call(self, method, *args, **kwargs):
        """Async facade; retain synchronous API for CLI tools and transactions."""
        operation = getattr(self, method) if isinstance(method, str) else method

        def invoke():
            if self.db.dialect.name == "sqlite":
                # StaticPool shares an in-memory connection across API/runtime threads.
                with self.lock:
                    return operation(*args, **kwargs)
            return operation(*args, **kwargs)

        return await durable_io(invoke)

    def create_run(self, config: dict, run_id: str | None = None) -> str:
        run_id = run_id or uid()
        with self.db.begin() as conn:
            conn.execute(
                insert(self.runs).values(
                    id=run_id, created=now(), status="queued", config=config, result={}, next_seq=0
                )
            )
        return run_id

    def healthy(self):
        with self.db.connect() as conn:
            conn.execute(select(1)).scalar_one()

    def append(self, run_id: str, kind: str, data: dict) -> RunEvent:
        with self.lock, self.db.begin() as conn:
            seq = conn.execute(
                update(self.runs)
                .where(self.runs.c.id == run_id)
                .values(next_seq=self.runs.c.next_seq + 1)
                .returning(self.runs.c.next_seq)
            ).scalar_one()
            event = RunEvent(run_id=run_id, seq=seq, kind=kind, data=data)
            conn.execute(insert(self.events).values(**event.model_dump(exclude={"schema_version"})))
        return event

    def set_status(self, run_id: str, status: str, result: dict | None = None):
        values = {"status": status}
        if result is not None:
            values["result"] = result
        with self.db.begin() as conn:
            conn.execute(update(self.runs).where(self.runs.c.id == run_id).values(**values))

    def get_run(self, run_id: str) -> dict | None:
        with self.db.connect() as conn:
            row = conn.execute(select(self.runs).where(self.runs.c.id == run_id)).mappings().first()
            return dict(row) if row else None

    def list_runs(self) -> list[dict]:
        with self.db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(self.runs).order_by(self.runs.c.created.desc()).limit(100)
                ).mappings()
            ]

    def read_events(self, run_id: str, after: int = 0) -> list[dict]:
        with self.db.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    select(self.events)
                    .where((self.events.c.run_id == run_id) & (self.events.c.seq > after))
                    .order_by(self.events.c.seq)
                ).mappings()
            ]

    def intent(self, run_id: str, state_id: str, action_hash: str) -> str:
        receipt = hashlib.sha256(f"{run_id}:{state_id}:{action_hash}".encode()).hexdigest()
        with self.db.begin() as conn:
            existing = (
                conn.execute(select(self.executions).where(self.executions.c.id == receipt))
                .mappings()
                .first()
            )
            if existing:
                raise RuntimeError("Execution already has an intent; reconcile instead of retrying")
            conn.execute(
                insert(self.executions).values(
                    id=receipt,
                    run_id=run_id,
                    state_id=state_id,
                    action_hash=action_hash,
                    status="pending",
                    receipt={},
                )
            )
        return receipt

    def abort_execution(self, receipt: str, reason: str):
        """Only before dispatch: this records known non-execution, never an outcome."""
        with self.db.begin() as conn:
            changed = conn.execute(
                update(self.executions)
                .where(self.executions.c.id == receipt, self.executions.c.status == "pending")
                .values(status="aborted", receipt={"not_dispatched": True, "reason": reason})
                .returning(self.executions.c.id)
            ).scalar_one_or_none()
            if changed is None:
                raise RuntimeError("Execution is not pending; reconcile before changing it")

    def complete_execution(self, receipt: str, observation: dict):
        with self.db.begin() as conn:
            changed = conn.execute(
                update(self.executions)
                .where(self.executions.c.id == receipt, self.executions.c.status == "pending")
                .values(status="complete", receipt=observation)
                .returning(self.executions.c.id)
            ).scalar_one_or_none()
            if changed is None:
                raise RuntimeError("Only pending execution can receive an outcome")

    def execution_record(self, receipt: str) -> dict:
        """Read the durable binding; pending/aborted entries are not observations."""
        with self.db.connect() as conn:
            row = (
                conn.execute(select(self.executions).where(self.executions.c.id == receipt))
                .mappings()
                .one()
            )
            return dict(row)

    def pending_execution(self, run_id: str) -> bool:
        with self.db.connect() as conn:
            return (
                conn.execute(
                    select(self.executions.c.id)
                    .where(
                        (self.executions.c.run_id == run_id)
                        & (self.executions.c.status == "pending")
                    )
                    .limit(1)
                ).first()
                is not None
            )

    def record_error(
        self,
        prediction_id: str,
        engine: str,
        context: str,
        value: float,
        label: bool,
        risk_value: float | None,
        risk_label: bool,
        source: str = "execution",
    ) -> bool:
        if self.db.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as conflict_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as conflict_insert
        with self.lock, self.db.begin() as conn:
            inserted = conn.execute(
                conflict_insert(self.errors)
                .values(
                    id=uid(),
                    prediction_id=prediction_id,
                    engine=engine,
                    context=context,
                    value=value,
                    label=int(label),
                    risk_value=risk_value,
                    risk_label=int(risk_label),
                    source=source,
                    timestamp=now(),
                )
                .on_conflict_do_nothing(index_elements=["prediction_id"])
                .returning(self.errors.c.id)
            )
            return inserted.scalar_one_or_none() is not None

    def error_rows(self, engine: str | None = None, context: str | None = None) -> list[dict]:
        query = select(self.errors).where(self.errors.c.source == "execution")
        if engine:
            query = query.where(self.errors.c.engine == engine)
        if context:
            query = query.where(self.errors.c.context == context)
        with self.db.connect() as conn:
            return [
                dict(r) for r in conn.execute(query.order_by(self.errors.c.timestamp)).mappings()
            ]

    def enqueue(self, request: dict, run_id: str = "") -> str:
        job_id = uid()
        with self.db.begin() as conn:
            conn.execute(
                insert(self.jobs).values(
                    id=job_id,
                    run_id=run_id,
                    status="queued",
                    owner="",
                    lease_until=0.0,
                    payload={"request": request},
                    error="",
                )
            )
        return job_id

    def job(self, job_id: str):
        with self.db.connect() as conn:
            row = conn.execute(select(self.jobs).where(self.jobs.c.id == job_id)).mappings().first()
            return dict(row) if row else None

    def lease(self, owner: str, seconds: float = 60):
        clock = time.time()
        eligible = (self.jobs.c.status == "queued") | (
            (self.jobs.c.status == "running") & (self.jobs.c.lease_until < clock)
        )
        with self.lock, self.db.begin() as conn:
            # An abandoned cancellation is not a completed cleanup and must
            # never re-enter the executable prediction queue on lease expiry.
            conn.execute(
                update(self.jobs)
                .where((self.jobs.c.status == "cancelling") & (self.jobs.c.lease_until < clock))
                .values(status="interrupted", error="CancellationUnconfirmed")
            )
            query = select(self.jobs).where(eligible).limit(1)
            if self.db.dialect.name == "postgresql":
                query = query.with_for_update(skip_locked=True)
            row = conn.execute(query).mappings().first()
            if not row:
                return None
            changed = conn.execute(
                update(self.jobs)
                .where((self.jobs.c.id == row["id"]) & eligible)
                .values(status="running", owner=owner, lease_until=clock + seconds)
            )
            return dict(row) if changed.rowcount == 1 else None

    def finish_job(
        self,
        job_id: str,
        owner: str,
        prediction: dict | None,
        error: str = "",
        cleanup: dict | None = None,
    ):
        with self.db.begin() as conn:
            changed = conn.execute(
                update(self.jobs)
                .where(
                    (self.jobs.c.id == job_id)
                    & (self.jobs.c.owner == owner)
                    & (self.jobs.c.status == "running")
                )
                .values(
                    status="failed" if error else "complete",
                    payload={
                        "prediction": prediction,
                        "cleanup": cleanup,
                        "operation_finished": True,
                        "terminal_state_confirmed": (
                            cleanup.get("terminal_state_confirmed") is True
                            if cleanup is not None
                            else not bool(error)
                        ),
                    },
                    error=error,
                    lease_until=0,
                )
            )
            return changed.rowcount == 1

    def cancel_job(self, job_id: str):
        with self.db.begin() as conn:
            conn.execute(
                update(self.jobs)
                .where((self.jobs.c.id == job_id) & self.jobs.c.status.in_(["queued", "running"]))
                .values(
                    status=case((self.jobs.c.status == "queued", "cancelled"), else_="cancelling"),
                    lease_until=case(
                        (self.jobs.c.status == "queued", 0), else_=self.jobs.c.lease_until
                    ),
                )
            )

    def finish_cancelled_job(self, job_id: str, owner: str, cleanup: dict | None):
        """Only the claiming consumer may publish its drained cancellation evidence."""
        with self.db.begin() as conn:
            changed = conn.execute(
                update(self.jobs)
                .where(
                    (self.jobs.c.id == job_id)
                    & (self.jobs.c.owner == owner)
                    & (self.jobs.c.status == "cancelling")
                )
                .values(
                    status="cancelled",
                    lease_until=0,
                    error="CancelledError",
                    payload={
                        "prediction": None,
                        "cleanup": cleanup,
                        "operation_finished": True,
                        "terminal_state_confirmed": (
                            cleanup is not None and cleanup.get("terminal_state_confirmed") is True
                        ),
                    },
                )
            )
            return changed.rowcount == 1

    def recover(self):
        """An interrupted run must never be automatically re-executed."""
        with self.db.begin() as conn:
            conn.execute(
                update(self.runs)
                .where(self.runs.c.status.in_(["running", "queued"]))
                .values(
                    status="interrupted", result={"reason": "Restart; reconcile execution ledger"}
                )
            )


class Artifacts:
    def __init__(self, root: str = ".preact/artifacts"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, media: str = "application/json") -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = self.root / digest
        if not target.exists():
            temp = self.root / f".{uid()}"
            temp.write_bytes(data)
            os.replace(temp, target)
        return digest

    def json(self, value: dict) -> str:
        return self.put(json.dumps(value, sort_keys=True).encode())

    def read(self, digest: str) -> bytes:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid artifact digest")
        data = (self.root / digest).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Artifact integrity failure")
        return data
