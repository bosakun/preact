"""One-sided exact binomial bounds for a stated simulation population."""

from scipy.stats import beta

from preact.core.models import Estimate


def binomial_estimate(positives: int, n: int, alpha: float = 0.05) -> Estimate:
    if n < 1 or not 0 <= positives <= n or not 0 < alpha < 1:
        raise ValueError("Invalid binomial observation counts/confidence")
    lower = float(beta.ppf(alpha, positives, n - positives + 1)) if positives else 0.0
    upper = float(beta.ppf(1 - alpha, positives + 1, n - positives)) if positives < n else 1.0
    return Estimate(
        value=positives / n,
        lower=lower,
        upper=upper,
        uncertainty=upper - lower,
        measured=True,
    )
