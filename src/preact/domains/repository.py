"""Trusted release-workflow lab with actual SQLite, configuration and build evidence.

These bounded first-party operations may execute locally. Generated repository code
and SQL still require a remote sandbox; this adapter is not a sandbox substitute.
"""

import ast
import base64
import copy
import json
import sqlite3
import tempfile
from pathlib import Path

from preact.core.models import Action, Observation, State, Task, identity
from preact.domains.software import ALLOWED, BASE, REPAIR, SHORTCUT, probe

MIGRATIONS = {
    "safe": "ALTER TABLE orders ADD COLUMN discount REAL NOT NULL DEFAULT 0 CHECK(discount >= 0);",
    "nullable": "ALTER TABLE orders ADD COLUMN discount REAL;",
    "destructive": "DROP TABLE orders;",
}


class RepositoryWorld:
    def __init__(self, seed=0):
        self.task = Task(
            id="repository-release",
            domain="software",
            seed=seed,
            max_steps=6,
            title="Release a checkout repair with a compatible database migration",
            goal="Preserve orders/validation, repair discounts, migrate safely, configure v2 and build",
            required_checks=[
                "syntax",
                "regressions",
                "data_preservation",
                "schema_integrity",
                "configuration",
            ],
        )
        self.expected_rows = [(1, 10 + seed % 7), (2, 20), (3, 30)]
        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE orders(id INTEGER PRIMARY KEY, total REAL NOT NULL CHECK(total >= 0))"
        )
        db.executemany("INSERT INTO orders VALUES (?, ?)", self.expected_rows)
        db.commit()
        self.payload = {
            "files": {
                "checkout.py": BASE,
                "config.json": json.dumps({"strict_validation": True, "schema_version": 1}),
            },
            "database_b64": base64.b64encode(db.serialize()).decode(),
            "stage": "initial",
            "goal_progress": 0.0,
            "goal_complete": False,
            "schema_version": 1,
            "build_digest": None,
        }
        db.close()
        self.receipts = {}

    async def observe(self):
        return State.create("software", copy.deepcopy(self.payload), "trusted-release-repository")

    async def propose(self, state, width):
        p = state.payload
        config = json.loads(p["files"]["config.json"])
        if p["files"]["checkout.py"] != REPAIR:
            options = [
                ("Quick clamp without validation", "patch", {"source": SHORTCUT}),
                ("Preserve validation and repair discounts", "patch", {"source": REPAIR}),
            ]
        elif p["schema_version"] == 1:
            options = [
                (
                    "Recreate orders to simplify migration",
                    "migration",
                    {"migration": "destructive"},
                ),
                ("Add a nullable discount field", "migration", {"migration": "nullable"}),
                (
                    "Add constrained discount with a safe default",
                    "migration",
                    {"migration": "safe"},
                ),
            ]
        elif config["schema_version"] != 2:
            options = [
                (
                    "Disable strict validation for compatibility",
                    "configuration",
                    {"strict_validation": False, "schema_version": 2},
                ),
                (
                    "Enable the compatible v2 configuration",
                    "configuration",
                    {"strict_validation": True, "schema_version": 2},
                ),
            ]
        else:
            options = [("Compile and verify the release", "command", {"command": "build"})]
        return [
            Action(
                name=name,
                kind=kind,
                state_id=state.id,
                payload=params,
                rationale="Evaluate code, data, schema and configuration together before release",
            )
            for name, kind, params in options[:width]
        ]

    def validate(self, state, action):
        if state.domain != "software" or action.state_id != state.id:
            raise ValueError("Invalid release state/action")
        if set(state.payload["files"]) != {"checkout.py", "config.json"}:
            raise ValueError("Release source tree is outside the bounded fixture")
        if state.payload["files"]["checkout.py"] not in ALLOWED:
            raise ValueError("Generated source requires a remote sandbox")
        p = action.payload
        valid = (
            action.kind == "patch"
            and set(p) == {"source"}
            and p["source"] in ALLOWED
            or action.kind == "migration"
            and set(p) == {"migration"}
            and p["migration"] in MIGRATIONS
            or action.kind == "configuration"
            and set(p) == {"strict_validation", "schema_version"}
            and type(p["strict_validation"]) is bool
            and p["schema_version"] == 2
            or action.kind == "command"
            and p == {"command": "build"}
        )
        if not valid:
            raise ValueError("Command/configuration/migration is not allowlisted")

    def materialize(self, state, action):
        self.validate(state, action)
        p = copy.deepcopy(state.payload)
        p["goal_complete"], p["build_digest"] = False, None
        if action.kind == "patch":
            p["files"]["checkout.py"] = action.payload["source"]
        elif action.kind == "configuration":
            p["files"]["config.json"] = json.dumps(action.payload, sort_keys=True)
        p["stage"] = action.kind
        return State.create(
            "software", p, "release-materialization", kind="hypothetical", parent_id=state.id
        )

    async def verify_future(self, state, action, seed):
        p = copy.deepcopy(self.materialize(state, action).payload)
        raw_db = base64.b64decode(p["database_b64"], validate=True)
        if len(raw_db) > 65536 or not raw_db.startswith(b"SQLite format 3\x00"):
            raise ValueError("Invalid bounded database snapshot")
        db = sqlite3.connect(":memory:")
        try:
            db.deserialize(raw_db)
            if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]:
                raise ValueError("Unexpected executable database triggers")
            if action.kind == "migration":
                db.executescript(MIGRATIONS[action.payload["migration"]])
            code, config = p["files"]["checkout.py"], json.loads(p["files"]["config.json"])
            tests = await probe(code, seed)
            try:
                rows = db.execute("SELECT id,total FROM orders ORDER BY id").fetchall()
            except sqlite3.OperationalError:
                rows = []
            columns = {row[1]: row for row in db.execute("PRAGMA table_info(orders)").fetchall()}
            migrated = "discount" in columns
            schema_ok = bool(columns)
            if migrated:
                schema_ok &= bool(columns["discount"][3] and columns["discount"][4] is not None)
                try:
                    db.execute("INSERT INTO orders(id,total,discount) VALUES (999,1,-1)")
                    schema_ok = False
                except sqlite3.IntegrityError:
                    pass
                finally:
                    db.rollback()
            ast.parse(code)
            checks = {
                "syntax": True,
                "regressions": all(
                    tests[k] for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
                ),
                "data_preservation": rows == self.expected_rows,
                "schema_integrity": bool(schema_ok),
                "configuration": config["strict_validation"] is True
                and config["schema_version"] <= (2 if migrated else 1),
            }
            p["schema_version"] = 2 if migrated else 1
            p["database_b64"] = base64.b64encode(db.serialize()).decode()
            digest = identity({"files": p["files"], "database_dump": list(db.iterdump())})
            if action.kind == "command":
                with tempfile.TemporaryDirectory(prefix="preact-build-") as folder:
                    path = Path(folder) / "checkout.py"
                    path.write_text(code)
                    # Actual compilation produces a bytecode artifact from trusted source.
                    import py_compile

                    bytecode = py_compile.compile(
                        str(path),
                        dfile="/repository/checkout.py",
                        doraise=True,
                        invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
                    )
                    import hashlib

                    p["build_bytecode_hash"] = hashlib.sha256(
                        Path(bytecode).read_bytes()
                    ).hexdigest()
                    p["build_digest"] = digest
            progress = (
                float(all(tests.values()))
                + float(migrated)
                + float(config["schema_version"] == 2)
                + float(p["build_digest"] == digest)
            ) / 4
            p["goal_progress"] = progress
            p["goal_complete"] = bool(all(checks.values()) and progress == 1)
            return (
                p,
                checks,
                {"goal_progress": progress, "orders_preserved": float(len(rows))},
                {
                    "tests": tests,
                    "columns": list(columns),
                    "build_digest": p["build_digest"],
                    "migration_sql": MIGRATIONS.get(action.payload.get("migration")),
                    "bytecode_hash": p.get("build_bytecode_hash"),
                    "scope": "Actual trusted Python, SQLite migration and bytecode compilation; not cloud sandbox evidence",
                },
            )
        finally:
            db.close()

    def complete(self, state):
        return bool(state.payload.get("goal_complete"))

    async def execute(self, action, receipt):
        if receipt in self.receipts:
            return self.receipts[receipt]
        state = await self.observe()
        p, checks, metrics, _ = await self.verify_future(state, action, self.task.seed)
        self.payload = p
        result = Observation(
            state=await self.observe(),
            success=self.complete(await self.observe()),
            unsafe=not all(checks.values()),
            checks={**checks, "action_success": all(checks.values())},
            metrics=metrics,
            receipt=receipt,
            cost_usd=0,
        )
        self.receipts[receipt] = result
        return result
