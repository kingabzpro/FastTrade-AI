"""Research statistics for a declared, aligned family of return series."""
import math

import numpy as np


def reality_check(excess_returns, draws=2000, seed=0):
    """White's centered stationary-bootstrap reality check for mean excess returns.

    Rows are the same chronological periods across all declared variants.
    Resample rows jointly to preserve cross-variant dependence.
    """
    values = np.asarray(excess_returns, dtype=float)
    if values.ndim != 2 or values.shape[0] < 2 or values.shape[1] < 1 or not np.isfinite(values).all():
        raise ValueError('Provide at least two aligned finite periods and one variant')
    if type(draws) is not int or draws < 1:
        raise ValueError('Draws must be a positive integer')
    periods, variants = values.shape
    block = max(2, math.ceil(periods ** (1 / 3)))
    rng = np.random.default_rng(seed)
    means = values.mean(axis=0)
    observed = float(means.max())
    bootstrapped = np.empty((draws, variants))
    for draw in range(draws):
        indices = np.empty(periods, dtype=int)
        position = int(rng.integers(periods))
        for i in range(periods):
            indices[i] = position
            position = int(rng.integers(periods)) if rng.random() < 1 / block else (position + 1) % periods
        bootstrapped[draw] = values[indices].mean(axis=0)
    null_max = (bootstrapped - means).max(axis=1)
    return {'method': 'White reality check, stationary bootstrap', 'observations': periods,
            'variants_tested': variants, 'mean_excess_returns': means.tolist(),
            'confidence_intervals': np.percentile(bootstrapped, [2.5, 97.5], axis=0).T.tolist(),
            'p_value': float((1 + np.count_nonzero(null_max >= observed)) / (draws + 1)),
            'block_length': block, 'draws': draws, 'seed': seed}
