"""Small exact-enough enumerative linear feasibility helpers.

The decision engine only needs to know whether a point exists in a bounded
weight polytope intersected by linear "candidate k is best" half-spaces.  The
polytope is tiny (one equality, non-negative bounded weights), so enumerating
basic solutions is simpler and more auditable than vendoring a full LP solver.
"""
from __future__ import annotations

from itertools import combinations
from typing import List, Optional, Sequence, Tuple

EPS = 1e-8


def solve_square(system: List[List[float]], rhs: List[float]) -> Optional[List[float]]:
    """Solve A x = b with partial-pivot Gaussian elimination."""
    n = len(system)
    if n == 0:
        return []
    if len(system[0]) != n or len(rhs) != n:
        return None

    a = [list(map(float, row)) + [float(rhs[i])] for i, row in enumerate(system)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-11:
            return None
        if pivot != col:
            a[col], a[pivot] = a[pivot], a[col]

        pivot_value = a[col][col]
        for j in range(col, n + 1):
            a[col][j] /= pivot_value

        for row in range(n):
            if row == col:
                continue
            factor = a[row][col]
            if abs(factor) < 1e-15:
                continue
            for j in range(col, n + 1):
                a[row][j] -= factor * a[col][j]

    x = [a[i][n] for i in range(n)]
    if any(v != v or v in (float("inf"), float("-inf")) for v in x):
        return None
    return x


def _satisfies(
    w: Sequence[float],
    lower: Sequence[float],
    upper: Sequence[float],
    inequalities: Sequence[Tuple[List[float], float]],
    tolerance: float = 2e-7,
) -> bool:
    if abs(sum(w) - 1.0) > tolerance:
        return False
    for value, lo, hi in zip(w, lower, upper):
        if value < lo - tolerance or value > hi + tolerance:
            return False
    for coeff, rhs in inequalities:
        if sum(c * x for c, x in zip(coeff, w)) < rhs - tolerance:
            return False
    return True


def feasible_weight_point(
    lower: Sequence[float],
    upper: Sequence[float],
    inequalities: Optional[Sequence[Tuple[List[float], float]]] = None,
    tolerance: float = 2e-7,
) -> Tuple[Optional[List[float]], Optional[str]]:
    """Return one feasible weight vector.

    Each inequality is ``coeff dot w >= rhs``.  A non-empty compact polytope has
    a vertex defined by the sum-to-one equality and ``n-1`` other independent
    active constraints; enumerate those candidate active equations.
    """
    inequalities = list(inequalities or [])
    n = len(lower)
    if n == 0:
        return None, "no_metrics"
    if len(upper) != n:
        return None, "bad_bounds"
    if any(lo < -EPS or hi > 1 + EPS or lo > hi + EPS for lo, hi in zip(lower, upper)):
        return None, "invalid_weight_range"
    if sum(lower) > 1 + tolerance:
        return None, "weight_lower_bounds_sum_above_one"
    if sum(upper) < 1 - tolerance:
        return None, "weight_upper_bounds_sum_below_one"

    # A feasible interior point is enough for callers that have no extra
    # inequalities.  More generally, keep the enumeration auditable.
    active_equations: List[Tuple[List[float], float, str]] = []
    for i in range(n):
        row = [0.0] * n
        row[i] = 1.0
        active_equations.append((row, float(lower[i]), f"w{i}=lower"))
        active_equations.append((row[:], float(upper[i]), f"w{i}=upper"))
    for idx, (coeff, rhs) in enumerate(inequalities):
        active_equations.append((list(coeff), float(rhs), f"ineq{idx}_active"))

    sum_row = [1.0] * n

    # First try a simple residual point. It often succeeds and avoids loops.
    residual = [0.0] * n
    remaining = 1.0
    for i in range(n - 1):
        residual[i] = lower[i]
        remaining -= lower[i]
    residual[n - 1] = remaining
    if lower[n - 1] - tolerance <= residual[n - 1] <= upper[n - 1] + tolerance:
        if _satisfies(residual, lower, upper, inequalities, tolerance):
            return [min(max(x, lower[i]), upper[i]) for i, x in enumerate(residual)], None

    if n == 1:
        w = [1.0]
        if _satisfies(w, lower, upper, inequalities, tolerance):
            return w, None
        return None, "no_feasible_witness"

    for chosen in combinations(range(len(active_equations)), n - 1):
        matrix = [sum_row[:]]
        rhs = [1.0]
        for ci in chosen:
            coeff, value, _ = active_equations[ci]
            matrix.append(list(coeff))
            rhs.append(value)
        candidate = solve_square(matrix, rhs)
        if candidate is None:
            continue
        if _satisfies(candidate, lower, upper, inequalities, tolerance):
            # Remove tiny floating excursions at active box bounds.
            cleaned = []
            for x, lo, hi in zip(candidate, lower, upper):
                if abs(x - lo) < 1e-7:
                    x = lo
                elif abs(x - hi) < 1e-7:
                    x = hi
                cleaned.append(x)
            if abs(sum(cleaned) - 1.0) < 1e-6:
                return cleaned, None

    return None, "no_feasible_witness"
