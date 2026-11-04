from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from fasttrade.overfitting import cscv, evaluate_folds, purged_splits


def test_purging_and_embargo_remove_overlapping_label_windows():
    starts = pd.date_range('2026-01-02', periods=12, freq='7D', tz='UTC')
    ends = starts + pd.Timedelta(days=10)
    splits = purged_splits(starts, ends, groups=6, test_groups=1, embargo=timedelta(days=7))
    first = splits[0]
    assert first['test'] == [0, 1]
    assert 2 in first['purged']
    assert 3 in first['embargoed']
    assert 4 in first['train']
    for split in splits:
        for train in split['train']:
            for test in split['test']:
                assert not (starts[train] < ends[test] and ends[train] > starts[test])
                assert not (ends[test] <= starts[train] < ends[test] + pd.Timedelta(days=7))


def test_purged_splits_reject_naive_or_unordered_dates():
    with pytest.raises(ValueError):
        purged_splits(['2026-01-01', '2026-01-08'], ['2026-01-05', '2026-01-12'], groups=2, test_groups=1)
    with pytest.raises(ValueError):
        purged_splits(['2026-01-08T00:00Z', '2026-01-01T00:00Z'], ['2026-01-09T00:00Z', '2026-01-02T00:00Z'], groups=2, test_groups=1)


def test_embargo_ends_at_the_boundary():
    starts = pd.to_datetime(['2026-01-01T00:00Z', '2026-01-08T00:00Z', '2026-01-16T00:00Z', '2026-01-23T00:00Z'])
    ends = starts + pd.Timedelta(days=1)
    split = purged_splits(starts, ends, groups=2, test_groups=1)[0]
    assert split['test'] == [0, 1]
    assert split['train'] == [2, 3]


def test_cscv_reports_full_fold_distribution_and_drops_incomplete_tail():
    data = np.column_stack((np.arange(25, dtype=float), -np.arange(25, dtype=float)))
    result = cscv(data, blocks=6)
    assert result['observations_used'] == 24 and result['excluded_tail'] == 1
    assert len(result['folds']) == 20
    assert result['pbo'] == 0
    assert all(fold['winner'] == 0 and fold['logit'] > 0 for fold in result['folds'])


def test_cscv_does_not_choose_arbitrarily_between_tied_training_winners():
    data = np.ones((12, 2))
    result = cscv(data, blocks=6)
    assert result['pbo'] is None
    assert all(fold['winner'] is None for fold in result['folds'])


def test_purged_fold_distribution_selects_using_training_only():
    starts = pd.date_range('2026-01-02', periods=18, freq='7D', tz='UTC')
    splits = purged_splits(starts, starts + pd.Timedelta(days=10), groups=6, test_groups=2)
    data = np.column_stack((np.full(18, .01), np.full(18, -.01)))
    distribution = evaluate_folds(data, splits)
    assert len(distribution) == 15
    assert all(item['winner'] == 0 and item['test_mean_excess_return'] == pytest.approx(.01) for item in distribution)
