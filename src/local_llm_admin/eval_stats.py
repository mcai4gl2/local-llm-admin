"""M10 statistics for eval scores. Pure: a percentile bootstrap over case scores gives a mean
interval, and a paired bootstrap over per-case differences decides whether one run is better
or worse than another. Seeded, so the same scores always give the same interval."""

from __future__ import annotations

import random
from dataclasses import dataclass

ROUND_DIGITS = 4
LOWER_Q = 0.025
UPPER_Q = 0.975
# Fewer shared cases than this cannot support a verdict, however tight the interval looks.
MIN_PAIRED_N = 10


@dataclass(frozen=True)
class Interval:
    mean: float
    lo: float
    hi: float
    n: int


@dataclass(frozen=True)
class Paired:
    n: int
    mean_diff: float
    lo: float
    hi: float
    wins: int
    losses: int
    ties: int
    verdict: str


def _bootstrap(values: list[float], *, iters: int, seed: int) -> tuple[float, float, float]:
    """Mean, and the percentile bootstrap interval of that mean, rounded for output."""
    if iters < 1:
        raise ValueError("iters must be at least 1")
    n = len(values)
    mean = sum(values) / n
    if n == 1 or min(values) == max(values):
        return round(mean, ROUND_DIGITS), round(mean, ROUND_DIGITS), round(mean, ROUND_DIGITS)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(iters))
    lo = means[int(LOWER_Q * iters)]
    hi = means[min(iters - 1, int(UPPER_Q * iters))]
    return round(mean, ROUND_DIGITS), round(lo, ROUND_DIGITS), round(hi, ROUND_DIGITS)


def mean_ci(scores: list[float], *, iters: int = 2000, seed: int = 0) -> Interval:
    if not scores:
        raise ValueError("no scores")
    mean, lo, hi = _bootstrap(list(scores), iters=iters, seed=seed)
    return Interval(mean=mean, lo=lo, hi=hi, n=len(scores))


def paired(
    a: dict[str, float], b: dict[str, float], *, iters: int = 2000, seed: int = 0
) -> Paired:
    shared = sorted(a.keys() & b.keys())
    if not shared:
        raise ValueError("no shared cases")
    diffs = [a[key] - b[key] for key in shared]
    mean, lo, hi = _bootstrap(diffs, iters=iters, seed=seed)
    if len(shared) < MIN_PAIRED_N:
        verdict = "no clear difference"
    elif lo > 0:
        verdict = "better"
    elif hi < 0:
        verdict = "worse"
    else:
        verdict = "no clear difference"
    return Paired(
        n=len(shared),
        mean_diff=mean,
        lo=lo,
        hi=hi,
        wins=sum(1 for d in diffs if d > 0),
        losses=sum(1 for d in diffs if d < 0),
        ties=sum(1 for d in diffs if d == 0),
        verdict=verdict,
    )
