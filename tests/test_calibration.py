import json
import math

import numpy as np
import pandas as pd
import pytest

from fasttrade import calibration
from fasttrade.calibration import (apply_isotonic, apply_temperature, brier, build_ledger, conformal_interval, conformal_quantile, ece, fit_isotonic, fit_temperature, interval_excludes_half, load_artifact, reliability_curve, run_calibration, split_temporal)


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
    decisions = [{'symbol': 'TEST', 'decision_time': (t + pd.Timedelta(hours=1)).isoformat(), 'execution_time': t.isoformat(), 'action': 'BUY', 'probabilities': {'BUY': p, 'HOLD': 0.1, 'SELL': 0.1}} for t, p in zip(times, (0.8, 0.7, 0.3, 0.9))]
    rows, info = build_ledger({'decisions': decisions}, tmp_path)
    assert info == {'last_reviews_without_outcome': 1, 'excluded_missing_price_bars': 0}
    assert len(rows) == 3
    assert [r['outcome_up'] for r in rows] == [True, False, True]
    assert [r['p_buy'] for r in rows] == [0.8, 0.7, 0.3]
    assert rows[0]['move'] == pytest.approx(0.10)
    assert rows[1]['move'] == pytest.approx(90.0 / 110.0 - 1)
    assert rows[2]['move'] == pytest.approx(95.0 / 90.0 - 1)


def test_build_ledger_requires_probabilities(tmp_path):
    with pytest.raises(ValueError, match='probabilities'):
        build_ledger({'decisions': [{'symbol': 'TEST', 'decision_time': '2026-01-05T15:30:00+00:00', 'execution_time': '2026-01-05T14:30:00+00:00', 'action': 'BUY'}]}, tmp_path)


def test_split_temporal_no_overlap():
    rows = [{'decision_time': f'2026-01-{day:02d}T15:30:00+00:00'} for day in range(1, 11)]
    train, test = split_temporal(rows, 0.7)
    assert len(train) == 7 and len(test) == 3
    assert max(r['decision_time'] for r in train) <= min(r['decision_time'] for r in test)
    with pytest.raises(ValueError, match='fraction'):
        split_temporal(rows, 1.0)


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
    for symbol, step in (('AAA', 1.0), ('BBB', -1.0)):
        times = pd.date_range('2026-01-05 14:30', periods=reviews, freq='7D', tz='UTC')
        opens = [100 + step * 5 * i for i in range(reviews)]
        pd.DataFrame({'timestamp': times, 'open': opens, 'high': [o + 1 for o in opens], 'low': [o - 1 for o in opens], 'close': opens, 'volume': [10] * reviews}).to_csv(tmp_path / f'{symbol}.csv', index=False)
        decisions = [{'symbol': symbol, 'decision_time': (t + pd.Timedelta(hours=1)).isoformat(), 'execution_time': t.isoformat(), 'action': 'BUY' if step > 0 else 'SELL', 'probabilities': {'BUY': 0.8, 'HOLD': 0.1, 'SELL': 0.1}} for t in times]
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
