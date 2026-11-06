"""Overfitting diagnostics on aligned, already-recorded research returns."""
from datetime import timedelta
from itertools import combinations
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

EULER_MASCHERONI = 0.5772156649015329


def purged_splits(starts, ends, *, groups=6, test_groups=2, embargo=timedelta(days=7)):
    if type(groups) is not int or type(test_groups) is not int or not 2 <= groups <= 12 or not 1 <= test_groups < groups:
        raise ValueError('Invalid fold configuration')
    try:
        start = [pd.Timestamp(value) for value in starts]
        end = [pd.Timestamp(value) for value in ends]
        gap = pd.Timedelta(embargo)
        if (len(start) != len(end) or len(start) < groups or pd.isna(gap) or gap < pd.Timedelta(0)
                or any(pd.isna(a) or pd.isna(b) or a.tzinfo is None or b.tzinfo is None or a >= b for a, b in zip(start, end))
                or any(a >= b for a, b in zip(start, start[1:]))):
            raise ValueError('Invalid label intervals')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid label intervals') from exc
    blocks = np.array_split(np.arange(len(start)), groups)
    results = []
    for selected in combinations(range(groups), test_groups):
        test = sorted(int(index) for block in selected for index in blocks[block])
        test_set = set(test)
        train, purged, embargoed = [], [], []
        for i in range(len(start)):
            if i in test_set:
                continue
            if any(start[i] < end[j] and end[i] > start[j] for j in test):
                purged.append(i)
            elif any(end[j] <= start[i] < end[j] + gap for j in test):
                embargoed.append(i)
            else:
                train.append(i)
        results.append({'test_groups': list(selected), 'train': train, 'test': test, 'purged': purged, 'embargoed': embargoed})
    return results


def cscv(returns, blocks=6):
    values = np.asarray(returns, dtype=float)
    if (type(blocks) is not int or blocks < 2 or blocks > 12 or blocks % 2
            or values.ndim != 2 or values.shape[0] < blocks or values.shape[1] < 2 or not np.isfinite(values).all()):
        raise ValueError('CSCV requires aligned finite returns, at least two variants and an even block count')
    count = len(values) // blocks * blocks
    groups = np.split(np.arange(count), blocks)
    folds, logits = [], []
    for chosen in combinations(range(blocks), blocks // 2):
        train = np.concatenate([groups[index] for index in chosen])
        test = np.concatenate([groups[index] for index in range(blocks) if index not in chosen])
        train_means, test_means = values[train].mean(axis=0), values[test].mean(axis=0)
        winners = np.flatnonzero(np.isclose(train_means, train_means.max(), rtol=1e-12, atol=1e-15))
        if len(winners) != 1:
            folds.append({'train_blocks': list(chosen), 'winner': None, 'logit': None, 'reason': 'training tie'})
            continue
        winner = int(winners[0])
        ties = np.isclose(test_means, test_means[winner], rtol=1e-12, atol=1e-15)
        rank = float(np.count_nonzero((test_means < test_means[winner]) & ~ties) + (np.count_nonzero(ties) + 1) / 2)
        relative = rank / (values.shape[1] + 1)
        logit = math.log(relative / (1 - relative))
        logits.append(logit)
        folds.append({'train_blocks': list(chosen), 'winner': winner, 'out_of_sample_rank': rank, 'logit': logit})
    return {'method': 'CSCV on fixed return streams', 'metric': 'mean excess return', 'blocks': blocks,
            'variants_tested': values.shape[1], 'observations_used': count, 'excluded_tail': len(values) - count,
            'pbo': float(sum(value <= 0 for value in logits) / len(logits)) if logits else None,
            'valid_folds': len(logits), 'folds': folds}


def evaluate_folds(returns, splits):
    values = np.asarray(returns, dtype=float)
    if values.ndim != 2 or values.shape[1] < 2 or not np.isfinite(values).all():
        raise ValueError('Fold evaluation requires aligned finite variant returns')
    result = []
    for split in splits:
        train, test = split['train'], split['test']
        if (any(type(index) is not int or not 0 <= index < len(values) for index in [*train, *test])
                or len(set(train)) != len(train) or len(set(test)) != len(test) or set(train) & set(test)):
            raise ValueError('Invalid fold indices')
        fold = {**split, 'winner': None, 'test_mean_excess_return': None}
        if not train or not test:
            fold['reason'] = 'empty training or test set after purging'
        else:
            means = values[train].mean(axis=0)
            winners = np.flatnonzero(np.isclose(means, means.max(), rtol=1e-12, atol=1e-15))
            if len(winners) != 1:
                fold['reason'] = 'training tie'
            else:
                fold['winner'] = int(winners[0])
                fold['test_mean_excess_return'] = float(values[test, fold['winner']].mean())
        result.append(fold)
    return result


def expected_max_sharpe(trials, sharpe_variance):
    """Expected maximum per-period Sharpe ratio under zero skill (Bailey & Lopez de Prado 2014, Eq. 1).

    `sharpe_variance` is the sample variance of the trials' per-period Sharpe estimates
    in the same units as the Sharpe being deflated.
    """
    if type(trials) is not int or trials < 2:
        raise ValueError('Trials must be an integer of at least two')
    if not math.isfinite(sharpe_variance) or sharpe_variance < 0:
        raise ValueError('Sharpe variance must be finite and non-negative')
    normal = NormalDist()
    max_z = ((1 - EULER_MASCHERONI) * normal.inv_cdf(1 - 1 / trials)
             + EULER_MASCHERONI * normal.inv_cdf(1 - 1 / (trials * math.e)))
    return math.sqrt(sharpe_variance) * max_z


def _sharpe_moments(values):
    """Per-period Sharpe with population skewness and non-excess kurtosis of the returns."""
    count = len(values)
    mean, spread = float(values.mean()), float(values.std(ddof=1))
    if not math.isfinite(spread) or spread <= 0:
        raise ValueError('Sharpe requires returns with a positive finite standard deviation')
    centered = values - mean
    second = float((centered ** 2).mean())
    skew = float((centered ** 3).mean() / second ** 1.5) if second > 0 else 0.0
    kurtosis = float((centered ** 4).mean() / second ** 2) if second > 0 else 3.0
    return mean / spread, skew, kurtosis


def _sharpe_variance_factor(sharpe, skew, kurtosis):
    """Variance of the Sharpe estimator's normal approximation (Lo 2002)."""
    factor = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError('Sharpe outside the normal-approximation support of its returns distribution')
    return factor


def deflated_sharpe(returns):
    """Deflated Sharpe ratios for a declared family of aligned per-period excess-return columns.

    Deflates each variant's observed Sharpe against the expected maximum Sharpe under zero
    skill given the number of trials, the cross-trial Sharpe variance, sample length, and
    the returns' skewness and kurtosis (Bailey & Lopez de Prado 2014). Rows are periods,
    columns are variants; at least two variants and four periods are required.
    """
    values = np.asarray(returns, dtype=float)
    if values.ndim != 2 or values.shape[0] < 4 or values.shape[1] < 2 or not np.isfinite(values).all():
        raise ValueError('Deflated Sharpe requires aligned finite returns over at least four periods and two variants')
    periods, variants = values.shape
    estimates = [_sharpe_moments(values[:, index]) for index in range(variants)]
    sharpes = [item[0] for item in estimates]
    centered = np.asarray(sharpes) - sum(sharpes) / variants
    variance = float((centered ** 2).sum() / (variants - 1))
    threshold = expected_max_sharpe(variants, variance)
    normal = NormalDist()
    variants_report = []
    for sharpe, skew, kurtosis in estimates:
        factor = _sharpe_variance_factor(sharpe, skew, kurtosis)
        statistic = (sharpe - threshold) * math.sqrt(periods - 1) / math.sqrt(factor)
        variants_report.append({'sharpe': sharpe, 'skewness': skew, 'kurtosis': kurtosis,
                                'deflated_sharpe': float(normal.cdf(statistic))})
    return {'method': 'Deflated Sharpe ratio (Bailey & Lopez de Prado 2014)', 'periods': periods,
            'trials': variants, 'sharpe_variance': variance, 'expected_max_sharpe': threshold,
            'note': 'Trials cover only the declared recorded variants; unrecorded historical trials are not counted, so this deflation is a lower bound.',
            'variants': variants_report}


def min_track_record_length(sharpe, skew, kurtosis, threshold_sharpe, confidence=0.95):
    """Sample length in periods needed for the observed Sharpe to clear `threshold_sharpe` at `confidence`.

    Inverts the deflated-Sharpe statistic (Bailey & Lopez de Prado 2014): the smallest T with
    (sharpe - threshold) * sqrt(T - 1) / sqrt(variance factor) >= Normal(confidence) quantile.
    Returns None with a reason when the observed Sharpe does not exceed the threshold, since
    no sample length can make it meaningful under this criterion.
    """
    if not 0 < confidence < 1:
        raise ValueError('Confidence must lie strictly between zero and one')
    factor = _sharpe_variance_factor(sharpe, skew, kurtosis)
    if sharpe <= threshold_sharpe:
        return None, 'observed Sharpe does not exceed the expected maximum under zero skill'
    z = NormalDist().inv_cdf(confidence)
    return 1 + (z * math.sqrt(factor) / (sharpe - threshold_sharpe)) ** 2, None
