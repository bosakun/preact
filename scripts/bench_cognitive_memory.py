"""Compare receipt-backed retrieval with the Phase 1 full-reread algorithm.

Run from the checkout with python -m scripts.bench_cognitive_memory --output NEW_DIR.
Controlled fixtures execute queue actions and commit receipts through Store; they do
not claim Gate approval. Predictive branches contain reference-only test payloads.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import platform
import random
import statistics
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import event

from preact.cognition.memory import EpisodicMemory
from preact.core.models import identity
from preact.core.store import Store
from preact.domains.cognitive_queue import CognitiveQueueWorld
from tests.fixtures.memory_ledger import append_execution


class FullRereadMemory(EpisodicMemory):
    """Phase 1 retrieval/remember, sharing the unchanged authoritative read validator."""

    async def remember(self, run_id):
        records = await self.read(run_id)
        if records and run_id not in self.run_ids:
            self.run_ids.append(run_id)

    async def retrieve(self, domain, limit=12):
        if limit <= 0:
            return []
        records = []
        for run_id in reversed(self.run_ids):
            records.extend(reversed(await self.read(run_id)))
            selected = [r for r in records if r.input_state.domain == domain]
            if len(selected) >= limit:
                break
        return list(reversed([r for r in records if r.input_state.domain == domain][:limit]))


class MeasuredStore(Store):
    def __init__(self, url):
        super().__init__(url)
        self.calls = Counter()
        self.sql_calls = 0
        event.listen(self.db, "before_cursor_execute", self._statement)

    def _statement(self, *args):
        self.sql_calls += 1

    async def call(self, method, *args, **kwargs):
        self.calls[method] += 1
        return await super().call(method, *args, **kwargs)

    def reset_counts(self):
        self.calls.clear()
        self.sql_calls = 0


def summary(samples):
    ordered = sorted(s["wall_ms"] for s in samples)
    return {
        "iterations": len(samples),
        "wall_p50_ms": statistics.median(ordered),
        "wall_p95_ms": ordered[max(0, int(len(ordered) * 0.95 + 0.999999) - 1)],
        "cpu_p50_ms": statistics.median(s["cpu_ms"] for s in samples),
        "cpu_total_ms": sum(s["cpu_ms"] for s in samples),
        "sql_calls_per_retrieve": statistics.mean(s["sql_calls"] for s in samples),
        "store_calls_per_retrieve": {
            key: statistics.mean(s["store_calls"].get(key, 0) for s in samples)
            for key in sorted({key for s in samples for key in s["store_calls"]})
        },
        "peak_cached_experiences": max(s["cached_experiences"] for s in samples),
        "experience_equality": all(s["experience_equality"] for s in samples),
    }


async def case(directory, count, layout, protocol):
    store = MeasuredStore(f"sqlite:///{directory / 'ledger.db'}")
    world = CognitiveQueueWorld(ticks=count, target=count * 2, seed=protocol["seed"])
    run_ids = []
    last_outcome = None
    for index in range(count):
        if layout == "one_per_run" or not run_ids:
            run_ids.append(store.create_run({}, run_id=f"memory-bench-{index:06d}"))
        last_outcome = await append_execution(store, world, run_ids[-1])
    limit = protocol["retrieve_limit"]
    baseline = FullRereadMemory(store)
    baseline.run_ids = run_ids.copy()
    expected = await baseline.retrieve(world.task.domain, limit)
    indexed = EpisodicMemory(store)
    indexed.run_ids = run_ids.copy()
    assert await indexed.retrieve(world.task.domain, limit) == expected
    modes = {
        name: [] for name in ("full_reread", "indexed_cold", "indexed_warm", "one_run_invalidated")
    }
    iterations = protocol["iterations"]
    if layout == "single_run" and count >= protocol["large_run_threshold"]:
        iterations = protocol["large_run_iterations"]
    rng = random.Random(protocol["seed"] + count)
    for trial in range(max(iterations, protocol["warm_iterations"])):
        order = list(modes) if trial < iterations else ["indexed_warm"]
        rng.shuffle(order)
        for mode in order:
            memory = baseline if mode == "full_reread" else indexed
            if mode == "indexed_cold":
                memory = EpisodicMemory(store)
                memory.run_ids = run_ids.copy()
            elif mode == "one_run_invalidated":
                # Prime after any previous trial's mutation, outside the timed call.
                await indexed.retrieve(world.task.domain, limit)
                store.append(run_ids[-1], "memory_bench_diagnostic", {"trial": trial})
            store.reset_counts()
            cpu_start, wall_start = time.process_time(), time.perf_counter()
            result = await memory.retrieve(world.task.domain, limit)
            wall_ms = (time.perf_counter() - wall_start) * 1000
            cpu_ms = (time.process_time() - cpu_start) * 1000
            matches = result == expected
            if not matches:
                raise ValueError("Indexed retrieval differs from Phase 1 Experience contents")
            modes[mode].append(
                {
                    "wall_ms": wall_ms,
                    "cpu_ms": cpu_ms,
                    "sql_calls": store.sql_calls,
                    "store_calls": dict(store.calls),
                    "cached_experiences": 0 if memory is baseline else memory.cached_experiences,
                    "experience_equality": matches,
                }
            )
    forged = copy.deepcopy(last_outcome)
    forged["observation"]["metrics"]["processed"] = 999
    store.append(run_ids[-1], "outcome", forged)
    rejected = {}
    for name, memory in (("full_reread", baseline), ("indexed", indexed)):
        try:
            await memory.retrieve(world.task.domain, limit)
        except ValueError as error:
            if "committed" not in str(error):
                raise
            rejected[name] = True
        else:
            raise ValueError(f"{name} accepted an appended forged outcome")
    result = {
        "receipt_count": count,
        "layout": layout,
        "run_count": len(run_ids),
        "returned_references": [e.reference for e in expected],
        "experience_sha256": identity([e.model_dump() for e in expected]),
        "forged_outcome_rejected": rejected,
        "measurements": {mode: summary(samples) for mode, samples in modes.items()},
        "samples": modes,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    store.db.dispose()
    return result


async def benchmark(output, protocol_path):
    protocol = json.loads(protocol_path.read_text())
    if (
        not protocol["receipt_counts"]
        or min(protocol["receipt_counts"]) < 1
        or min(
            protocol["iterations"], protocol["warm_iterations"], protocol["large_run_iterations"]
        )
        < 1
        or protocol["retrieve_limit"] < 1
        or set(protocol["layouts"]) - {"single_run", "one_per_run"}
    ):
        raise ValueError("Invalid memory benchmark protocol")
    output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    sources = [
        "src/preact/cognition/memory.py",
        "src/preact/cognition/models.py",
        "src/preact/core/store.py",
        "src/preact/domains/cognitive_queue.py",
        "scripts/bench_cognitive_memory.py",
        "tests/fixtures/memory_ledger.py",
    ]
    report = {
        "protocol": protocol,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "source_sha256": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sources
        },
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "results": [],
    }
    for count in protocol["receipt_counts"]:
        for layout in protocol["layouts"]:
            directory = output / f"{layout}-{count}"
            directory.mkdir()
            result = await case(directory, count, layout, protocol)
            report["results"].append(result)
            (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            timings = {k: round(v["wall_p50_ms"], 3) for k, v in result["measurements"].items()}
            print(json.dumps({"count": count, "layout": layout, "p50_ms": timings}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--protocol", type=Path, default=Path("benchmarks/cognitive-memory-index-v1.json")
    )
    args = parser.parse_args()
    asyncio.run(benchmark(args.output, args.protocol))


if __name__ == "__main__":
    main()
