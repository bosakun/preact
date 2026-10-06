"""Server-owned standalone release evaluator uploaded only to execution workers."""

import ast
import base64
import hashlib
import json
import py_compile
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def evaluate(request, protected):
    payload, action = request["payload"], request["action"]
    database = base64.b64decode(payload["database_b64"], validate=True)
    if len(database) > 65536 or not database.startswith(b"SQLite format 3\x00"):
        raise ValueError("Invalid bounded database snapshot")
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(database)
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]:
            raise ValueError("Unexpected executable database triggers")
        if action["kind"] == "migration":
            db.executescript(protected["migrations"][action["payload"]["migration"]])
        code, config = payload["files"]["checkout.py"], json.loads(payload["files"]["config.json"])
        ast.parse(code)
        with tempfile.TemporaryDirectory(prefix="preact-release-") as folder:
            path = Path(folder) / "checkout.py"
            path.write_text(code)
            evaluator = Path(folder) / "protected.py"
            evaluator.write_text(protected["checkout_probe"])
            result = subprocess.run(
                [sys.executable, "-I", str(evaluator), str(path)],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            tests = json.loads(result.stdout)
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
            checks = {
                "syntax": True,
                "regressions": all(
                    tests[k] for k in ["ordinary", "zero", "negative_total", "protected_invariants"]
                ),
                "data_preservation": [list(row) for row in rows] == protected["expected_rows"],
                "schema_integrity": bool(schema_ok),
                "configuration": config["strict_validation"] is True
                and config["schema_version"] <= (2 if migrated else 1),
            }
            payload["schema_version"] = 2 if migrated else 1
            payload["database_b64"] = base64.b64encode(db.serialize()).decode()
            digest = hashlib.sha256(
                json.dumps(
                    {"files": payload["files"], "database_dump": list(db.iterdump())},
                    sort_keys=True,
                    allow_nan=False,
                ).encode()
            ).hexdigest()
            if action["kind"] == "command":
                bytecode = py_compile.compile(
                    str(path),
                    dfile="/repository/checkout.py",
                    doraise=True,
                    invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
                )
                payload["build_bytecode_hash"] = hashlib.sha256(
                    Path(bytecode).read_bytes()
                ).hexdigest()
                payload["build_digest"] = digest
            progress = (
                float(all(tests.values()))
                + float(migrated)
                + float(config["schema_version"] == 2)
                + float(payload["build_digest"] == digest)
            ) / 4
            payload["goal_progress"] = progress
            payload["goal_complete"] = bool(all(checks.values()) and progress == 1)
            return {
                "payload": payload,
                "checks": checks,
                "metrics": {"goal_progress": progress, "orders_preserved": float(len(rows))},
                "evidence": {
                    "tests": tests,
                    "columns": list(columns),
                    "build_digest": payload["build_digest"],
                    "bytecode_hash": payload.get("build_bytecode_hash"),
                },
            }
    finally:
        db.close()


if __name__ == "__main__":
    print(
        json.dumps(
            evaluate(
                json.loads(Path(sys.argv[1]).read_text()), json.loads(Path(sys.argv[2]).read_text())
            )
        )
    )
