"""Receipt-derived service sufficient statistics, with explicitly censored labels."""

import math

from preact.core.models import Action, State
from preact.domains.information_queue import DOMAIN
from preact.domains.queue_features import QueueDynamicsAdapter
from preact.learning.dynamics import DynamicsCell
from preact.learning.transitions import Transition


class QueueServiceAdapter:
    specification = {
        "domain": DOMAIN,
        "adapter": "observed-binary-service",
        "version": "1",
        "service_values": [1, 3],
        "stationarity": "pooled historical marginal; iid ticks assumed at prediction",
        "censoring": "probe or available_work>=3; otherwise no service label",
    }
    feature_names = ("pooled_service",)
    target_names = ("informative", "high_and_informative", "probe", "ordinary")

    def features(self, state: State, action: Action) -> tuple[float, ...]:
        if state.schema_version != "1" or action.schema_version != "1":
            raise ValueError("Unsupported queue contract version")
        QueueDynamicsAdapter().features(state, action)
        payload = state.payload
        for key in ("tick", "queue", "delivered", "capacity", "episode_ticks"):
            if type(payload[key]) is not int or payload[key] < 0:
                raise ValueError("Unsupported queue numeric state")
        if payload["capacity"] < 1 or payload["tick"] >= payload["episode_ticks"]:
            raise ValueError("Unsupported queue time/capacity")
        for job in payload["pending"]:
            if (
                set(job) != {"due", "amount"}
                or type(job["due"]) is not int
                or type(job["amount"]) is not int
                or job["due"] <= payload["tick"]
                or job["amount"] < 1
            ):
                raise ValueError("Unsupported pending work")
        for key in ("probe_cost", "holding_cost", "submission_cost"):
            if not math.isfinite(payload[key]) or payload[key] < 0:
                raise ValueError("Unsupported queue costs")
        return (1.0,)

    def targets(self, row: Transition) -> tuple[float, ...]:
        # Reuse the existing visible arrival/conservation teacher validation.
        self.features(row.before, row.action)
        _, processed = QueueDynamicsAdapter().targets(row)
        before, after = row.before.payload, row.after.state.payload
        arrived = sum(j["amount"] for j in before["pending"] if j["due"] <= after["tick"])
        available = before["queue"] + arrived
        metrics = row.after.metrics
        if metrics.get("available_work") != available or metrics.get("processed") != processed:
            raise ValueError("Service teacher metrics disagree with actual state")
        probe = row.action.kind == "probe_service"
        if not probe and any(k in metrics for k in ("measured_service", "measurement_tick")):
            raise ValueError("Ordinary action cannot contain a service measurement")
        if probe:
            service = metrics.get("measured_service")
            if (
                service not in (1, 3)
                or metrics.get("measurement_tick") != before["tick"]
                or processed != min(available, service)
            ):
                raise ValueError("Misaligned or invalid executed probe measurement")
        else:
            service = processed if available >= 3 else None
            if service is not None and service not in (1, 3):
                raise ValueError("Uncensored service violates binary dynamics")
        successful = row.after.checks.get("action_success") is True and (
            not probe or row.after.checks.get("probe_success") is True
        )
        informative = successful and service is not None
        # A zero mask means missing ability information, NEVER a low-service label.
        return (
            float(informative),
            float(informative and service == 3),
            float(informative and probe),
            float(informative and not probe),
        )

    def decode(
        self, state: State, action: Action, cell: DynamicsCell
    ) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
        self.features(state, action)
        informative, high, probe, ordinary = (round(cell.count * x) for x in cell.mean)
        if (
            any(
                abs(cell.count * x - n) > 1e-8
                for x, n in zip(cell.mean, (informative, high, probe, ordinary))
            )
            or not 0 <= high <= informative <= cell.count
            or probe < 0
            or ordinary < 0
            or probe + ordinary != informative
        ):
            raise ValueError("Invalid masked service statistics")
        statistics = {
            "training_count": cell.count,
            "informative_count": informative,
            "probe_count": probe,
            "ordinary_count": ordinary,
        }
        if informative:
            statistics["p_high"] = high / informative
        return statistics, {}
