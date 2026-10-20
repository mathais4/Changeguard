"""Transparent additive scoring + an optional logistic fit of the weights on repo history."""
from __future__ import annotations

import math

DEFAULT_WEIGHTS = {
    "size": 0.20, "spread": 0.08, "defect_history": 0.25, "churn_recent": 0.10,
    "unfamiliarity": 0.12, "test_gap": 0.20, "sensitive": 0.08,
    "blast_radius": 0.20, "coverage_gap": 0.12,
}
# Signals computable from commit history alone, so they can be replayed and evaluated honestly.
REPLAY = ["size", "spread", "defect_history", "churn_recent", "unfamiliarity", "test_gap", "sensitive"]


def score(signals: dict, weights: dict, only=None):
    """0-100 weighted average of available signals. Returns (total, {signal: points})."""
    avail = {k: s.value for k, s in signals.items()
             if s.value is not None and (only is None or k in only)}
    tot = sum(weights.get(k, 0) for k in avail) or 1.0
    points = {k: 100 * weights.get(k, 0) * v / tot for k, v in avail.items()}
    return sum(points.values()), points


def fit_logistic(X, y, l2=0.01, lr=0.3):
    """Class-balanced logistic regression with non-negative weights (keeps signals interpretable)."""
    n, d = len(X), len(X[0])
    pos = sum(y)
    if pos == 0 or pos == n:
        raise ValueError("need both classes to fit")
    wp, wn = n / (2 * pos), n / (2 * (n - pos))
    iters = max(200, min(1500, int(3e6 / (n * d))))
    w, b = [0.0] * d, 0.0
    for _ in range(iters):
        gw, gb = [0.0] * d, 0.0
        for xi, yi in zip(X, y):
            z = b + sum(wj * xj for wj, xj in zip(w, xi))
            p = 1 / (1 + math.exp(-max(-30, min(30, z))))
            e = (p - yi) * (wp if yi else wn)
            gb += e
            for j in range(d):
                gw[j] += e * xi[j]
        b -= lr * gb / n
        for j in range(d):
            w[j] = max(0.0, w[j] - lr * (gw[j] / n + l2 * w[j]))
    return w, b
