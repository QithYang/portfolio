# Context: Offline retrieval evaluation metrics for docs/retrieval-evaluation.md (nDCG@5, paired bootstrap 95% CI, 2-fold CV confidence gate, quadratic weighted kappa).
# Design: Pure Python 3.10+ standard library; all inputs are passed in memory with no logs or personal data.
# Note: confidence_gate_2fold restricts candidate threshold generation to each training fold.
"""Offline retrieval evaluation. All inputs are supplied in memory."""

from collections import Counter
from math import inf, isfinite, log2
from random import Random
from statistics import mean


def ndcg_at_5(ranked_ids, grades):
    """Use grades 0, 1 or 2. Return None when ideal DCG is zero."""
    def dcg(values):
        return sum((2**grade - 1) / log2(rank + 2)
                   for rank, grade in enumerate(values))

    top = list(ranked_ids[:5])
    if len(set(top)) != len(top):
        raise ValueError("The top five must contain distinct IDs.")
    if any(grade not in (0, 1, 2) for grade in grades.values()):
        raise ValueError("Relevance grades must be 0, 1 or 2.")

    ideal = dcg(sorted(grades.values(), reverse=True)[:5])
    return dcg([grades.get(item, 0) for item in top]) / ideal if ideal else None


def _percentile_95(draws):
    draws = sorted(draws)
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def paired_bootstrap_95(treatment, baseline, *, groups=None,
                        resamples=10_000, seed=0):
    """Return mean(treatment - baseline), lower bound, upper bound."""
    delta = [a - b for a, b in zip(treatment, baseline, strict=True)]
    if not delta or resamples < 1 or any(not isfinite(d) for d in delta):
        raise ValueError("Provide finite pairs and a positive resample count.")

    if groups is None:
        units = [[d] for d in delta]
    else:
        by_group = {}
        for group, d in zip(groups, delta, strict=True):
            by_group.setdefault(group, []).append(d)
        units = list(by_group.values())

    rng = Random(seed)
    draws = (
        mean(d for _ in units for d in rng.choice(units))
        for _ in range(resamples)
    )
    return (mean(delta), *_percentile_95(draws))


def confidence_gate_2fold(signal, rerank, hybrid, *, seed=20261004):
    """Return held-out nDCG, thresholds and embedding-call rate.

    Supply one row per query in identical order. Set both nDCG scores
    to None when the query has zero ideal DCG.
    """
    rows = list(zip(signal, rerank, hybrid, strict=True))
    if len(rows) < 2 or any(not isfinite(score) for score, _, _ in rows):
        raise ValueError("Provide at least two queries with finite signals.")
    for _, fast, full in rows:
        if (fast is None) != (full is None):
            raise ValueError("nDCG eligibility must match between methods.")
        if fast is not None and not (0 <= fast <= 1 and 0 <= full <= 1):
            raise ValueError("nDCG scores must be in [0, 1].")

    def fit(train):
        eligible = [rows[i] for i in train if rows[i][2] is not None]
        if not eligible:
            return inf

        candidates = sorted({rows[i][0] for i in train}) + [inf]
        full_sum = sum(full for _, _, full in eligible)
        best, best_savings = inf, -1
        for threshold in candidates:
            total = sum(fast if score >= threshold else full
                        for score, fast, full in eligible)
            savings = sum(rows[i][0] >= threshold for i in train)
            if total >= full_sum - 1e-9 and savings > best_savings:
                best, best_savings = threshold, savings
        return best

    order = list(range(len(rows)))
    Random(seed).shuffle(order)
    middle = len(order) // 2
    fold_a, fold_b = order[:middle], order[middle:]
    thresholds = [inf] * len(rows)
    for train, test in ((fold_a, fold_b), (fold_b, fold_a)):
        threshold = fit(train)
        for i in test:
            thresholds[i] = threshold

    skip = [score >= threshold
            for (score, _, _), threshold in zip(rows, thresholds)]
    held_out = [fast if skipped else full
                for (_, fast, full), skipped in zip(rows, skip)]
    return held_out, thresholds, 1 - mean(skip)


def weighted_kappa(pairs):
    """Quadratic weighted Cohen's kappa for paired grades 0, 1 and 2."""
    pairs = list(pairs)
    if not pairs or any(a not in (0, 1, 2) or b not in (0, 1, 2)
                        for a, b in pairs):
        raise ValueError("Provide paired grades in {0, 1, 2}.")

    left = Counter(a for a, _ in pairs)
    right = Counter(b for _, b in pairs)
    n = len(pairs)
    observed = mean((a - b)**2 for a, b in pairs)
    expected = sum(left[a] * right[b] * (a - b)**2
                   for a in range(3) for b in range(3)) / n**2
    return 1 - observed / expected if expected else None


def weighted_kappa_ci_95(pairs, *, resamples=10_000, seed=0):
    """Bootstrap annotation pairs. Return kappa and its 95% CI."""
    pairs = list(pairs)
    estimate = weighted_kappa(pairs)
    if estimate is None or resamples < 1:
        raise ValueError("Kappa needs nonzero expected disagreement and resamples.")

    rng = Random(seed)
    # The archived CI used zero for resamples with zero expected disagreement.
    draws = (weighted_kappa(rng.choice(pairs) for _ in pairs) or 0.0
             for _ in range(resamples))
    return (estimate, *_percentile_95(draws))
