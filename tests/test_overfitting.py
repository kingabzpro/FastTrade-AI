from datetime import timedelta
import math
from statistics import NormalDist

import numpy as np
import pandas as pd
import pytest

from fasttrade.overfitting import _sharpe_moments, cscv, deflated_sharpe, evaluate_folds, expected_max_sharpe, min_track_record_length, purged_splits


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


def test_expected_max_sharpe_matches_published_formula_and_grows_with_trials():
    normal = NormalDist()
    gamma = 0.5772156649015329
    for trials, variance in ((2, 0.5), (100, 1.0), (1000, 4.0)):
        expected = math.sqrt(variance) * ((1 - gamma) * normal.inv_cdf(1 - 1 / trials)
                                          + gamma * normal.inv_cdf(1 - 1 / (trials * math.e)))
        assert expected_max_sharpe(trials, variance) == pytest.approx(expected)
    assert expected_max_sharpe(1000, 1.0) > expected_max_sharpe(100, 1.0) > expected_max_sharpe(2, 1.0)
    with pytest.raises(ValueError):
        expected_max_sharpe(1, 1.0)
    with pytest.raises(ValueError):
        expected_max_sharpe(10, -1.0)


def test_deflated_sharpe_of_identical_normal_variants_reduces_to_psr_at_zero_threshold():
    rng = np.random.default_rng(7)
    series = rng.normal(0.002, 0.01, size=40)
    result = deflated_sharpe(np.column_stack((series, series)))
    assert result['trials'] == 2 and result['sharpe_variance'] == pytest.approx(0)
    assert result['expected_max_sharpe'] == 0
    sharpe, skew, kurtosis = _sharpe_moments(series)
    factor = 1 - skew * sharpe + (kurtosis - 1) / 4 * sharpe ** 2
    manual = NormalDist().cdf(sharpe * math.sqrt(len(series) - 1) / math.sqrt(factor))
    assert result['variants'][0]['deflated_sharpe'] == pytest.approx(manual)
    assert result['variants'][1]['deflated_sharpe'] == pytest.approx(manual)


def test_deflated_sharpe_penalises_more_trials_and_weaker_variants():
    rng = np.random.default_rng(3)
    strong = rng.normal(0.004, 0.01, size=30)
    weak = rng.normal(0.000, 0.01, size=30)
    pair = deflated_sharpe(np.column_stack((strong, weak)))
    solo = deflated_sharpe(np.column_stack((strong, strong + rng.normal(0, 1e-9, size=30))))
    best_pair = max(item['deflated_sharpe'] for item in pair['variants'])
    best_solo = max(item['deflated_sharpe'] for item in solo['variants'])
    assert best_pair < best_solo
    assert max(pair['variants'], key=lambda item: item['sharpe'])['deflated_sharpe'] > min(pair['variants'], key=lambda item: item['sharpe'])['deflated_sharpe']
    assert pair['expected_max_sharpe'] > 0
    with pytest.raises(ValueError):
        deflated_sharpe(strong.reshape(-1, 1))
    with pytest.raises(ValueError):
        deflated_sharpe(np.column_stack((strong[:2], weak[:2])))


def test_min_track_record_length_inverts_the_deflated_statistic():
    rng = np.random.default_rng(3)
    strong, weak = rng.normal(0.004, 0.01, size=30), rng.normal(0.000, 0.01, size=30)
    result = deflated_sharpe(np.column_stack((strong, weak)))
    item = max(result['variants'], key=lambda entry: entry['sharpe'])
    length, reason = min_track_record_length(item['sharpe'], item['skewness'], item['kurtosis'],
                                             result['expected_max_sharpe'], confidence=0.95)
    assert reason is None and length > 1
    factor = 1 - item['skewness'] * item['sharpe'] + (item['kurtosis'] - 1) / 4 * item['sharpe'] ** 2
    rebuilt = NormalDist().cdf((item['sharpe'] - result['expected_max_sharpe'])
                               * math.sqrt(math.ceil(length) - 1) / math.sqrt(factor))
    assert rebuilt >= 0.95
    below, blocked = min_track_record_length(0.0, item['skewness'], item['kurtosis'], 0.01)
    assert below is None and 'does not exceed' in blocked
    with pytest.raises(ValueError):
        min_track_record_length(0.1, 0.0, 3.0, -1.0, confidence=1.5)
