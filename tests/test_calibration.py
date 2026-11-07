import json
import math

import numpy as np
import pandas as pd
import pytest

from fasttrade import calibration
from fasttrade.calibration import (apply_isotonic, apply_temperature, brier, build_ledger, certify_edge, conformal_interval, conformal_quantile, ece, fit_isotonic, fit_temperature, interval_excludes_half, load_artifact, reliability_curve, run_calibration, split_temporal, stationary_bootstrap_means)


def edge_row(index, action, move, spy_move):
    decision = pd.Timestamp('2026-01-02', tz='UTC') + pd.Timedelta(weeks=index)
    entry, exit_ = decision + pd.Timedelta(days=3), decision + pd.Timedelta(days=10)
    return {'decision_time': decision.isoformat(), 'execution_time': entry.isoformat(),
            'next_decision_time': (decision + pd.Timedelta(weeks=1)).isoformat(),
            'exit_time': exit_.isoformat(), 'outcome_available_at': (exit_ + pd.Timedelta(hours=1)).isoformat(),
            'action': action, 'move': move, 'spy_move': spy_move}


def test_brier_and_ece_known_values():
    assert brier([0.0, 1.0], [0, 1]) == 0.0
    assert brier([0.8, 0.8], [0, 1]) == pytest.approx(0.34)
    assert ece([0.8, 0.8], [0, 1]) == pytest.approx(0.3)
    assert ece([0.5, 0.5], [0, 1]) == pytest.approx(0.0)
    assert ece([1.0, 1.0], [0, 1]) == pytest.approx(0.5)


def test_temperature_fit_reduces_nll_when_overconfident():
    rng = np.random.default_rng(7)
    p = np.full(400, 0.9)
    y = rng.random(400) < 0.6
    temperature = fit_temperature(p, y.astype(float))
    raw = apply_temperature(p, 1.0)
    calibrated = apply_temperature(p, temperature)
    q = np.clip(calibrated, calibration.EPS, 1 - calibration.EPS)
    q_raw = np.clip(raw, calibration.EPS, 1 - calibration.EPS)
    nll_after = -np.mean(y * np.log(q) + (1 - y) * np.log(1 - q))
    nll_before = -np.mean(y * np.log(q_raw) + (1 - y) * np.log(1 - q_raw))
    assert temperature > 1.0
    assert nll_after < nll_before


def test_temperature_fit_near_identity_on_calibrated_data():
    rng = np.random.default_rng(11)
    p = rng.uniform(0.05, 0.95, 5000)
    y = (rng.random(5000) < p).astype(float)
    assert 0.7 < fit_temperature(p, y) < 1.4


def test_isotonic_blocks_are_monotone():
    blocks = fit_isotonic([0.1, 0.2, 0.3, 0.4], [1.0, 0.0, 0.0, 1.0])
    means = [b[2] for b in blocks]
    assert means == sorted(means)
    applied = apply_isotonic([0.05, 0.15, 0.25, 0.35, 0.5], blocks)
    assert applied.tolist() == sorted(applied.tolist())
    assert all(0 <= v <= 1 for v in applied)


def test_conformal_quantile_rank_and_coverage():
    rng = np.random.default_rng(3)
    train_p, train_y = rng.uniform(0.1, 0.9, 1000), (rng.random(1000) < 0.5).astype(float)
    test_p, test_y = rng.uniform(0.1, 0.9, 500), (rng.random(500) < 0.5).astype(float)
    q = conformal_quantile(train_p, train_y, 0.1)
    residuals = np.sort(np.abs(train_y - train_p))
    assert q == pytest.approx(residuals[math.ceil(1001 * 0.9) - 1])
    covered = [lo <= y <= hi for (lo, hi), y in zip([conformal_interval(p, q) for p in test_p], test_y)]
    assert np.mean(covered) >= 0.85


def test_stationary_bootstrap_matches_sample_mean():
    rng = np.random.default_rng(5)
    values = rng.normal(0.01, 0.03, 60)
    means = stationary_bootstrap_means(values, draws=500, seed=1)
    assert len(means) == 500
    assert abs(means.mean() - values.mean()) < 0.002
    assert stationary_bootstrap_means(values, draws=10, seed=2).tolist() == stationary_bootstrap_means(values, draws=10, seed=2).tolist()


def test_certify_edge_verdicts():
    good = [edge_row(i, 'BUY', 0.03 + .001 * i, 0.0) for i in range(10)]
    verdict = certify_edge(good, 0.003)
    assert verdict['certified'] is True
    assert verdict['n'] == 10 and verdict['periods'] == 10
    assert verdict['ci'][0] > 0
    bad = [edge_row(i, 'BUY', -0.02 - .001 * i, 0.0) for i in range(10)]
    assert certify_edge(bad, 0.003)['certified'] is False
    assert certify_edge(good[:3], 0.003)['certified'] is None
    assert 'BUY decisions' in certify_edge(good[:3], 0.003)['reason']


def test_edge_bootstrap_resamples_entire_review_periods():
    rows = [edge_row(i, 'BUY', .04 if i % 2 else -.02, 0) for i in range(12)]
    duplicated = [dict(row, symbol=f'S{j}') for row in rows for j in range(10)]
    one, many = certify_edge(rows, 0), certify_edge(list(reversed(duplicated)), 0)
    assert one['periods'] == many['periods'] == 12
    assert one['ci'] == many['ci']
    assert certify_edge(duplicated[:90], 0)['certified'] is None


def test_same_week_decision_times_are_one_bootstrap_period():
    start = pd.Timestamp('2026-01-02', tz='UTC')
    rows = [{'decision_time': (start + pd.Timedelta(weeks=i, hours=j)).isoformat(),
             'action': 'BUY', 'move': .02, 'spy_move': 0} for i in range(9) for j in range(3)]
    verdict = certify_edge(rows, 0)
    assert verdict['n'] == 27 and verdict['periods'] == 9
    assert verdict['certified'] is None


def test_edge_bootstrap_keeps_weeks_without_buy_decisions():
    rows = [edge_row(i, 'HOLD' if i == 6 else 'BUY', .03 if i % 2 else -.01, 0) for i in range(13)]
    verdict = certify_edge(rows, 0)
    assert verdict['periods'] == 12 and verdict['review_periods'] == 13
    without_hold = certify_edge([row for row in rows if row['action'] == 'BUY'], 0)
    assert without_hold['review_periods'] == 13
    assert without_hold['ci'] == verdict['ci']


def test_edge_certification_rejects_missing_or_misaligned_weekly_reviews():
    rows = [edge_row(i, 'BUY', .03, 0) for i in range(12)]
    rows[3]['next_decision_time'] = rows[4]['next_decision_time']
    assert certify_edge(rows, 0)['certified'] is None
    assert 'weekly' in certify_edge(rows, 0)['reason']
    rows = [edge_row(i, 'BUY', .03, 0) for i in range(12)]
    extra = dict(rows[3], execution_time=(pd.Timestamp(rows[3]['execution_time']) + pd.Timedelta(hours=1)).isoformat())
    assert certify_edge(rows + [extra], 0)['certified'] is None
    rows = [edge_row(i, 'BUY', .03, 0) for i in range(12)]
    rows[3]['exit_time'] = (pd.Timestamp(rows[4]['execution_time']) + pd.Timedelta(hours=1)).isoformat()
    rows[3]['outcome_available_at'] = (pd.Timestamp(rows[3]['exit_time']) + pd.Timedelta(hours=1)).isoformat()
    assert certify_edge(rows, 0)['certified'] is None


def test_conformal_quantile_validates_alpha():
    train_p, train_y = np.full(20, 0.5), np.array([0, 1] * 10)
    with pytest.raises(ValueError, match='[Aa]lpha'):
        conformal_quantile(train_p, train_y, 1.0)
    with pytest.raises(ValueError, match='[Aa]lpha'):
        conformal_quantile(train_p, train_y, 0.0)


def test_interval_eligibility():
    assert interval_excludes_half([0.6, 0.9])
    assert interval_excludes_half([0.3, 0.45])
    assert not interval_excludes_half([0.4, 0.6])
    assert not interval_excludes_half([0.5, 0.9])


def test_reliability_curve_bins():
    curve = reliability_curve([0.05, 0.15, 0.85], [0, 0, 1], bins=2)
    assert curve[0]['bin'] == [0.0, 0.5]
    assert curve[0]['count'] == 2
    assert curve[-1]['outcome_rate'] == 1.0


def test_build_ledger_pairs_probabilities_with_next_open(tmp_path):
    times = pd.date_range('2026-01-05 14:30', periods=4, freq='7D', tz='UTC')
    frame = pd.DataFrame({'timestamp': times, 'open': [100.0, 110.0, 90.0, 95.0], 'high': [101, 111, 91, 96], 'low': [99, 109, 89, 94], 'close': [105, 100, 92, 95], 'volume': [10] * 4})
    frame.to_csv(tmp_path / 'TEST.csv', index=False)
    spy = pd.DataFrame({'timestamp': times, 'open': [100.0, 100.5, 100.2, 100.8], 'high': [100.6, 101.1, 100.8, 101.4], 'low': [99.4, 99.9, 99.6, 100.2], 'close': [100.3, 100.8, 100.5, 101.0], 'volume': [10] * 4})
    spy.to_csv(tmp_path / 'SPY.csv', index=False)
    decisions = [{'symbol': 'TEST', 'decision_time': (t - pd.Timedelta(days=3)).isoformat(), 'execution_time': t.isoformat(), 'action': 'BUY', 'probabilities': {'BUY': p, 'HOLD': 0.1, 'SELL': 0.1}} for t, p in zip(times, (0.8, 0.7, 0.3, 0.9))]
    rows, info = build_ledger({'decisions': decisions}, tmp_path)
    assert info == {'last_reviews_without_outcome': 1, 'excluded_missing_price_bars': 0}
    assert len(rows) == 3
    assert [r['outcome_up'] for r in rows] == [True, False, True]
    assert [r['p_buy'] for r in rows] == [0.8, 0.7, 0.3]
    assert rows[0]['move'] == pytest.approx(0.10)
    assert rows[1]['move'] == pytest.approx(90.0 / 110.0 - 1)
    assert rows[2]['move'] == pytest.approx(95.0 / 90.0 - 1)
    assert rows[0]['spy_move'] == pytest.approx(0.005)
    assert rows[1]['spy_move'] == pytest.approx(100.2 / 100.5 - 1)
    assert rows[0]['exit_time'] == times[1].isoformat()
    assert pd.Timestamp(rows[0]['outcome_available_at']) == times[1] + pd.Timedelta(hours=1)


def test_build_ledger_does_not_substitute_a_later_bar_for_a_missing_execution(tmp_path):
    times = pd.date_range('2026-01-05 14:30', periods=3, freq='7D', tz='UTC')
    available = [times[0], times[1] + pd.Timedelta(hours=1), times[2]]
    for symbol in ('TEST', 'SPY'):
        pd.DataFrame({'timestamp': available, 'open': [100, 101, 102]}).to_csv(tmp_path / f'{symbol}.csv', index=False)
    decisions = [{'symbol': 'TEST', 'decision_time': (t - pd.Timedelta(days=1)).isoformat(), 'execution_time': t.isoformat(),
                  'action': 'BUY', 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}} for t in times]
    rows, info = build_ledger({'decisions': decisions}, tmp_path)
    assert rows == []
    assert info['excluded_missing_price_bars'] == 2


def test_build_ledger_requires_spy(tmp_path):
    times = pd.date_range('2026-01-05 14:30', periods=3, freq='7D', tz='UTC')
    frame = pd.DataFrame({'timestamp': times, 'open': [100.0, 101.0, 102.0], 'high': [101, 102, 103], 'low': [99, 100, 101], 'close': [100.5, 101.5, 102.5], 'volume': [10] * 3})
    frame.to_csv(tmp_path / 'TEST.csv', index=False)
    decisions = [{'symbol': 'TEST', 'decision_time': (t - pd.Timedelta(days=3)).isoformat(), 'execution_time': t.isoformat(), 'action': 'BUY', 'probabilities': {'BUY': 0.6, 'HOLD': 0.2, 'SELL': 0.2}} for t in times]
    with pytest.raises(ValueError, match='SPY'):
        build_ledger({'decisions': decisions}, tmp_path)


def test_build_ledger_requires_probabilities(tmp_path):
    with pytest.raises(ValueError, match='probabilities'):
        build_ledger({'decisions': [{'symbol': 'TEST', 'decision_time': '2026-01-05T15:30:00+00:00', 'execution_time': '2026-01-05T14:30:00+00:00', 'action': 'BUY'}]}, tmp_path)


def test_build_ledger_rejects_execution_before_decision(tmp_path):
    pd.DataFrame({'timestamp': ['2026-01-05T14:30:00+00:00'], 'open': [100]}).to_csv(tmp_path / 'TEST.csv', index=False)
    decisions = [{'symbol': 'TEST', 'decision_time': '2026-01-05T15:30:00+00:00',
                  'execution_time': '2026-01-05T14:30:00+00:00', 'action': 'BUY',
                  'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}}]
    with pytest.raises(ValueError, match='execution'):
        build_ledger({'decisions': decisions}, tmp_path)


def test_split_temporal_no_overlap():
    times = pd.date_range('2026-01-02 15:30', periods=10, freq='7D', tz='UTC')
    rows = [{'decision_time': when.isoformat(), 'exit_time': (when + pd.Timedelta(hours=1)).isoformat(),
             'outcome_available_at': (when + pd.Timedelta(hours=2)).isoformat()} for when in times]
    train, test = split_temporal(rows, 0.7)
    assert len(train) == 7 and len(test) == 3
    assert max(r['decision_time'] for r in train) <= min(r['decision_time'] for r in test)
    with pytest.raises(ValueError, match='fraction'):
        split_temporal(rows, 1.0)


def test_split_temporal_keeps_reviews_together_and_purges_unavailable_outcomes():
    dates = pd.date_range('2026-01-02', periods=4, freq='7D', tz='UTC')
    rows = [{'symbol': symbol, 'decision_time': (when + pd.Timedelta(hours=index)).isoformat(), 'exit_time': (when + pd.Timedelta(days=10)).isoformat(),
             'outcome_available_at': (when + pd.Timedelta(days=10, hours=1)).isoformat()}
            for when in dates for index, symbol in enumerate(('A', 'B', 'C'))]
    train, test = split_temporal(rows, .6)
    assert {pd.Timestamp(row['decision_time']).date() for row in train} == {dates[0].date()}
    assert {pd.Timestamp(row['decision_time']).date() for row in test} == {dates[2].date(), dates[3].date()}
    assert len(train) == 3 and len(test) == 6
    assert max(pd.Timestamp(r['outcome_available_at']) for r in train) <= min(pd.Timestamp(r['decision_time']) for r in test)


def test_split_purges_label_whose_hourly_exit_bar_is_not_complete():
    dates = pd.date_range('2026-01-02 15:30', periods=3, freq='7D', tz='UTC')
    rows = [
        {'decision_time': dates[0].isoformat(), 'exit_time': (dates[1] - pd.Timedelta(hours=2)).isoformat(),
         'outcome_available_at': (dates[1] - pd.Timedelta(hours=1)).isoformat()},
        {'decision_time': dates[1].isoformat(), 'exit_time': (dates[2] - pd.Timedelta(minutes=30)).isoformat(),
         'outcome_available_at': (dates[2] + pd.Timedelta(minutes=30)).isoformat()},
        {'decision_time': dates[2].isoformat(), 'exit_time': (dates[2] + pd.Timedelta(days=7)).isoformat(),
         'outcome_available_at': (dates[2] + pd.Timedelta(days=7, hours=1)).isoformat()},
    ]
    train, test = split_temporal(rows, .75)
    assert [row['decision_time'] for row in train] == [dates[0].isoformat()]
    assert [row['decision_time'] for row in test] == [dates[2].isoformat()]


def test_load_artifact_validation(tmp_path):
    artifact = {'schema': calibration.SCHEMA, 'method': 'temperature', 'temperature': 1.5, 'fit': {'target': 't', 'horizon': '60m'}, 'train_window': {'start': 'a', 'end': 'b'}, 'conformal': {'quantile': 0.1}}
    path = tmp_path / 'artifact.json'
    path.write_text(json.dumps(artifact), encoding='utf-8')
    assert load_artifact(path)['temperature'] == 1.5
    path.write_text(json.dumps({**artifact, 'schema': 'other'}), encoding='utf-8')
    with pytest.raises(ValueError, match='artifact'):
        load_artifact(path)
    path.write_text(json.dumps({**artifact, 'temperature': 0}), encoding='utf-8')
    with pytest.raises(ValueError, match='artifact'):
        load_artifact(path)


def synthetic_report_and_data(tmp_path, reviews=10):
    symbols, up, down = ['AAA', 'BBB'], [], []
    times = pd.date_range('2026-01-05 14:30', periods=reviews, freq='7D', tz='UTC')
    spy_opens = [100 + 0.2 * i for i in range(reviews)]
    pd.DataFrame({'timestamp': times, 'open': spy_opens, 'high': [o + 1 for o in spy_opens], 'low': [o - 1 for o in spy_opens], 'close': spy_opens, 'volume': [10] * reviews}).to_csv(tmp_path / 'SPY.csv', index=False)
    for symbol, step in (('AAA', 1.0), ('BBB', -1.0)):
        times = pd.date_range('2026-01-05 14:30', periods=reviews, freq='7D', tz='UTC')
        opens = [100 + step * 5 * i for i in range(reviews)]
        pd.DataFrame({'timestamp': times, 'open': opens, 'high': [o + 1 for o in opens], 'low': [o - 1 for o in opens], 'close': opens, 'volume': [10] * reviews}).to_csv(tmp_path / f'{symbol}.csv', index=False)
        decisions = [{'symbol': symbol, 'decision_time': (t - pd.Timedelta(days=3)).isoformat(), 'execution_time': t.isoformat(), 'action': 'BUY' if step > 0 else 'SELL', 'probabilities': {'BUY': 0.8, 'HOLD': 0.1, 'SELL': 0.1}} for t in times]
        (up if step > 0 else down).extend(decisions)
    report = {'model_resolved': 'jev-test', 'decisions': up + down}
    return report


def test_run_calibration_end_to_end(tmp_path):
    report = synthetic_report_and_data(tmp_path)
    report_path = tmp_path / 'report.json'
    report_path.write_text(json.dumps(report), encoding='utf-8')

    class Args:
        report, data_dir, train_frac, alpha = str(report_path), str(tmp_path), 0.75, 0.1
        fee_bps, slippage_bps = 5.0, 10.0

    summary = run_calibration(Args())
    assert summary['ledger']['decisions'] == 18
    assert summary['ledger']['last_reviews_without_outcome'] == 2
    assert summary['temperature'] > 1.0
    assert summary['train']['end'] <= summary['test']['start']
    assert set(summary['metrics_test']) == {'raw', 'temperature', 'isotonic'}
    assert summary['reliability_test_before'] and summary['reliability_test_after']
    assert summary['artifact']['schema'] == calibration.SCHEMA
    assert summary['artifact']['source_report_sha256']
    assert isinstance(summary['artifact']['at_bound'], bool)
    assert summary['artifact']['returns']['round_trip_cost_bps'] == 30.0
    assert 0 <= summary['returns']['test_certified_rate'] <= 1
    assert summary['edge']['certified'] is None
    assert 'BUY decisions' in summary['edge']['reason']
    assert summary['edge']['in_sample']['certified'] is None
    assert summary['artifact']['edge']['in_sample']['n'] == 9
    assert summary['artifact']['edge']['in_sample']['periods'] == 9


def test_calibration_ledger_rate_includes_purged_rows(historical_docs):
    from types import SimpleNamespace
    args = SimpleNamespace(report='docs/backtest-2026-09-28/report-measured.json', data_dir='docs/backtest-2026-09-28',
                           train_frac=.75, alpha=.1, fee_bps=5, slippage_bps=10)
    summary = run_calibration(args)
    assert summary['ledger']['decisions'] == 75
    assert summary['ledger']['purged_overlapping_labels'] == 25
    assert summary['ledger']['up_rate'] == pytest.approx(53 / 75, abs=.0001)
