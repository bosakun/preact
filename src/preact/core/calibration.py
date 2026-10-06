from __future__ import annotations

import json
import math

from .store import Store


def context_key(domain, task, horizon, success_metric, risk_metric, truth_source):
    return json.dumps(
        {
            "version": 1,
            "domain": domain,
            "task": task,
            "horizon": horizon,
            "success_metric": success_metric,
            "risk_metric": risk_metric,
            "truth_source": truth_source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def pool_scope(context):
    """Pool task families only when claim, horizon and observation authority agree.

    Legacy colon contexts remain readable but never cross into the versioned pool.
    """
    if context.startswith("{"):
        data = json.loads(context)
        return tuple(
            data[key]
            for key in [
                "version",
                "domain",
                "horizon",
                "success_metric",
                "risk_metric",
                "truth_source",
            ]
        )
    parts = context.split(":", 4)
    return ("legacy", parts[0], *parts[2:])


class Calibration:
    def __init__(self, store: Store):
        self.store = store

    def snapshot(self, context: str) -> dict:
        rows = self.store.error_rows()
        engines = sorted({r["engine"] for r in rows})
        result = {}
        for engine in engines:
            specific = [r for r in rows if r["engine"] == engine and r["context"] == context]
            pooled = [
                r
                for r in rows
                if r["engine"] == engine and pool_scope(r["context"]) == pool_scope(context)
            ]
            selected = specific if len(specific) >= 8 else pooled
            n = len(selected)
            squared = sum((r["value"] - r["label"]) ** 2 for r in selected)
            # Shrink sparse contexts toward a conservative Brier prior.
            brier = (squared + 2 * 0.25) / (n + 2)
            bias = sum(r["label"] - r["value"] for r in selected) / (n + 8)
            recent = selected[-20:]
            drift = bool(
                len(recent) >= 5
                and sum((r["value"] - r["label"]) ** 2 for r in recent) / len(recent) > 0.3
            )
            slope, intercept = 1.0, 0.0
            if n >= 30 and len({r["label"] for r in selected}) > 1:
                from scipy.optimize import minimize

                def objective(params):
                    a, b = params
                    loss = 2 * ((a - 1) ** 2 + b**2)
                    for row in selected:
                        p = min(0.999, max(0.001, row["value"]))
                        z = a * math.log(p / (1 - p)) + b
                        loss += max(z, 0) + math.log1p(math.exp(-abs(z))) - row["label"] * z
                    return loss

                fit = minimize(objective, [1.0, 0.0], bounds=[(0.1, 4), (-4, 4)])
                if fit.success:
                    slope, intercept = map(float, fit.x)
            residuals = sorted(abs(r["label"] - r["value"]) for r in selected)
            risk_rows = [r for r in selected if r["risk_value"] is not None]
            risk_n = len(risk_rows)
            risk_brier = (
                sum((r["risk_value"] - r["risk_label"]) ** 2 for r in risk_rows) + 0.5
            ) / (risk_n + 2)
            risk_recent = risk_rows[-20:]
            risk_drift = bool(
                len(risk_recent) >= 5
                and sum((r["risk_value"] - r["risk_label"]) ** 2 for r in risk_recent)
                / len(risk_recent)
                > 0.3
            )
            false_safe = sum(r["risk_label"] for r in risk_rows if r["risk_value"] <= 0.05)
            safe_claims = sum(r["risk_value"] <= 0.05 for r in risk_rows)
            from scipy.stats import beta

            # Bayesian reliability summary, not a safety certificate or a gate override.
            false_safe_interval = list(
                map(float, beta.ppf([0.025, 0.975], false_safe + 1, safe_claims - false_safe + 1))
            )
            result[engine] = {
                "n": n,
                "context_n": len(specific),
                "brier": brier,
                "weight": max(0.1, 1 - max(brier, risk_brier)),
                "bias": bias,
                "slope": slope,
                "intercept": intercept,
                "drift": drift,
                "risk_n": risk_n,
                "risk_brier": risk_brier,
                "risk_drift": risk_drift,
                "false_safe_count": false_safe,
                "safe_claims": safe_claims,
                "false_safe_beta_95": false_safe_interval,
                "residual_95": residuals[min(n - 1, math.ceil(0.95 * n) - 1)] if n else 1,
            }
        return result

    @staticmethod
    def calibrate(p: float, stats: dict | None) -> float:
        if not stats:
            return p
        if stats["n"] < 30:
            return min(1.0, max(0.0, p + stats["bias"]))
        p = min(0.999, max(0.001, p))
        z = stats["slope"] * math.log(p / (1 - p)) + stats["intercept"]
        return 1 / (1 + math.exp(-z))
