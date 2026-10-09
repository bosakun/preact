"""Public queue-v2 features and actual visible transition targets, not hidden service."""

from preact.core.models import Action, State
from preact.domains.information_queue import DOMAIN, PROVENANCE, action_amount
from preact.learning.dynamics import DynamicsCell
from preact.learning.transitions import Transition


class QueueDynamicsAdapter:
    specification = {"domain": DOMAIN, "adapter": "visible-queue-deltas", "version": "1"}
    feature_names = ("available_bucket", "arrivals", "amount", "probe")
    target_names = ("queue_delta", "delivered_delta")

    def features(self, state: State, action: Action) -> tuple[float, ...]:
        amount = action_amount(state, action)
        payload = state.payload
        if (
            state.provenance != PROVENANCE
            or payload["service_bounds"] != [1, 3]
            or payload["service_values"] != [1, 3]
            or payload["arrival_delay"] != 2
        ):
            raise ValueError("Unsupported queue dynamics schema/provenance")
        arrivals = sum(p["amount"] for p in payload["pending"] if p["due"] <= payload["tick"] + 1)
        available = payload["queue"] + arrivals
        return (min(available, 3), arrivals, amount, float(action.kind == "probe_service"))

    def targets(self, transition: Transition) -> tuple[float, ...]:
        before, after = transition.before.payload, transition.after.state.payload
        amount = action_amount(transition.before, transition.action)
        if transition.after.state.provenance != PROVENANCE or after["tick"] != before["tick"] + 1:
            raise ValueError("Queue teacher must cover exactly one actually observed tick")
        pending = [p for p in before["pending"] if p["due"] > after["tick"]]
        if amount:
            pending.append({"due": after["tick"] + 1, "amount": amount})
        arrivals = self.features(transition.before, transition.action)[1]
        processed = after["delivered"] - before["delivered"]
        if (
            after["pending"] != pending
            or after["queue"] != before["queue"] + arrivals - processed
            or not 0 <= processed <= min(before["queue"] + arrivals, 3)
            or any(
                after[key] != before[key]
                for key in before
                if key not in {"tick", "queue", "delivered", "pending"}
            )
        ):
            raise ValueError("Queue teacher violates the visible action/dynamics contract")
        return (after["queue"] - before["queue"], processed)

    def decode(
        self, state: State, action: Action, cell: DynamicsCell
    ) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
        _, arrivals, _, _ = self.features(state, action)
        qdelta, processed = cell.mean
        if abs(qdelta + processed - arrivals) > 1e-9 or not 0 <= processed <= min(
            state.payload["queue"] + arrivals, 3
        ):
            raise ValueError("Invalid learned queue transition")
        return (
            {
                "next_queue": state.payload["queue"] + qdelta,
                "next_delivered": state.payload["delivered"] + processed,
                "processed": processed,
            },
            {
                "next_queue": (
                    state.payload["queue"] + cell.minimum[0],
                    state.payload["queue"] + cell.maximum[0],
                ),
                "next_delivered": (
                    state.payload["delivered"] + cell.minimum[1],
                    state.payload["delivered"] + cell.maximum[1],
                ),
                "processed": (cell.minimum[1], cell.maximum[1]),
            },
        )
