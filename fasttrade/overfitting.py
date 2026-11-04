"""Overfitting diagnostics on aligned, already-recorded research returns."""
from datetime import timedelta
from itertools import combinations
import math

import numpy as np
import pandas as pd


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
