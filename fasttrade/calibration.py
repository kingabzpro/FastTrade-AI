"""Post-hoc calibration of decision-model probabilities against realized outcomes.

Research tooling: it fits mappings from a frozen backtest's recorded decisions,
never from live data, and its outputs stay research labels, not advice.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

SCHEMA = 'fasttrade-calibration-1'
EPS = 1e-6
TEMPERATURE_BOUNDS = (0.05, 20.0)


def brier(probabilities, outcomes):
    p, y = np.asarray(probabilities, float), np.asarray(outcomes, float)
    return float(np.mean((p - y) ** 2))


def ece(probabilities, outcomes, bins=10):
    p, y = np.asarray(probabilities, float), np.asarray(outcomes, float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.searchsorted(edges, p, side='right') - 1, 0, bins - 1)
    total = 0.0
    for b in range(bins):
        mask = index == b
        if mask.any():
            total += mask.mean() * abs(p[mask].mean() - y[mask].mean())
    return float(total)


def reliability_curve(probabilities, outcomes, bins=10):
    p, y = np.asarray(probabilities, float), np.asarray(outcomes, float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    index = np.clip(np.searchsorted(edges, p, side='right') - 1, 0, bins - 1)
    curve = []
    for b in range(bins):
        mask = index == b
        if mask.any():
            curve.append({'bin': [round(float(edges[b]), 4), round(float(edges[b + 1]), 4)], 'count': int(mask.sum()), 'mean_probability': round(float(p[mask].mean()), 4), 'outcome_rate': round(float(y[mask].mean()), 4)})
    return curve


def _logit(p):
    p = np.clip(np.asarray(p, float), EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sigmoid(z):
    return 1 / (1 + np.exp(-z))


def apply_temperature(probabilities, temperature):
    return _sigmoid(_logit(probabilities) / temperature)


def _nll(temperature, z, y):
    q = np.clip(_sigmoid(z / temperature), EPS, 1 - EPS)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def fit_temperature(probabilities, outcomes):
    z, y = _logit(probabilities), np.asarray(outcomes, float)
    low, high = math.log(TEMPERATURE_BOUNDS[0]), math.log(TEMPERATURE_BOUNDS[1])
    ratio = (math.sqrt(5) - 1) / 2
    a, b = low, high
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = _nll(math.exp(c), z, y), _nll(math.exp(d), z, y)
    for _ in range(100):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = _nll(math.exp(c), z, y)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = _nll(math.exp(d), z, y)
        if b - a < 1e-6:
            break
    return math.exp((a + b) / 2)


def fit_isotonic(probabilities, outcomes):
    """Pool-adjacent-violators on outcomes sorted by probability.

    Returns blocks of [min_probability, max_probability, mean_outcome]; the
    means are nondecreasing by construction.
    """
    order = np.argsort(np.asarray(probabilities, float))
    x, y = np.asarray(probabilities, float)[order], np.asarray(outcomes, float)[order]
    blocks = [[float(xi), float(xi), float(yi), 1] for xi, yi in zip(x, y)]
    index = 0
    while index < len(blocks) - 1:
        if blocks[index][2] > blocks[index + 1][2]:
            left, right = blocks[index], blocks[index + 1]
            weight = left[3] + right[3]
            merged = [left[0], right[1], (left[2] * left[3] + right[2] * right[3]) / weight, weight]
            blocks[index:index + 2] = [merged]
            if index > 0:
                index -= 1
        else:
            index += 1
    return [[round(b[0], 6), round(b[1], 6), round(b[2], 6)] for b in blocks]


def apply_isotonic(probabilities, blocks):
    p = np.asarray(probabilities, float)
    lows = np.asarray([b[0] for b in blocks])
    values = np.asarray([b[2] for b in blocks])
    index = np.searchsorted(lows, p, side='right') - 1
    index = np.clip(index, 0, len(blocks) - 1)
    return values[index]


def conformal_quantile(probabilities, outcomes, alpha):
    """Split-conformal quantile of |outcome - probability| residuals."""
    if not 0 < alpha < 1:
        raise ValueError('Alpha must be between 0 and 1')
    residuals = np.sort(np.abs(np.asarray(outcomes, float) - np.asarray(probabilities, float)))
    n = len(residuals)
    rank = math.ceil((n + 1) * (1 - alpha))
    if rank > n:
        return float('inf')
    return float(residuals[rank - 1])


def conformal_interval(probability, quantile):
    return [max(0.0, probability - quantile), min(1.0, probability + quantile)]


def interval_excludes_half(interval):
    return interval[0] > 0.5 or interval[1] < 0.5


def build_ledger(report, data_dir):
    """Pair each recorded decision's P(BUY) with the realized move to the next
    execution open for that symbol, from the frozen dataset CSVs. The final
    review per symbol has no next execution and is excluded."""
    data_dir = str(data_dir)
    decisions = report.get('decisions') or []
    if not decisions or any('probabilities' not in d for d in decisions):
        raise ValueError('Report decisions must include probabilities')
    frames = {}
    rows, excluded = [], 0
    by_symbol = {}
    for decision in decisions:
        by_symbol.setdefault(decision['symbol'], []).append(decision)
    for symbol, group in by_symbol.items():
        if not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', symbol):
            raise ValueError(f'Invalid symbol in report: {symbol}')
        group.sort(key=lambda d: d['decision_time'])
        if symbol not in frames:
            frame = pd.read_csv(f'{data_dir}/{symbol}.csv')
            frames[symbol] = frame.assign(timestamp=pd.to_datetime(frame.timestamp, utc=True)).set_index('timestamp').sort_index()
        for index, decision in enumerate(group[:-1]):
            entry = _open_at(frames[symbol], decision['execution_time'])
            exit_ = _open_at(frames[symbol], group[index + 1]['execution_time'])
            if entry is None or exit_ is None:
                excluded += 1
                continue
            rows.append({'symbol': symbol, 'decision_time': decision['decision_time'], 'execution_time': decision['execution_time'], 'action': decision['action'], 'p_buy': float(decision['probabilities']['BUY']), 'outcome_up': bool(exit_ > entry)})
    return rows, {'last_reviews_without_outcome': len(by_symbol), 'excluded_missing_price_bars': excluded}


def _open_at(frame, iso_time):
    later = frame.loc[frame.index >= pd.Timestamp(iso_time), 'open']
    return float(later.iloc[0]) if len(later) else None


def split_temporal(rows, train_frac):
    if not 0 < train_frac < 1:
        raise ValueError('Train fraction must be between 0 and 1')
    rows = sorted(rows, key=lambda r: r['decision_time'])
    train_n = max(1, min(len(rows) - 1, int(len(rows) * train_frac)))
    return rows[:train_n], rows[train_n:]


def run_calibration(args):
    with open(args.report, encoding='utf-8') as handle:
        report = json.load(handle)
    rows, ledger_info = build_ledger(report, args.data_dir)
    if len(rows) < 8:
        raise ValueError('Not enough decisions with outcomes to calibrate; need at least 8')
    train, test = split_temporal(rows, args.train_frac)

    def values(group):
        return np.array([r['p_buy'] for r in group]), np.array([1.0 if r['outcome_up'] else 0.0 for r in group])

    train_p, train_y = values(train)
    test_p, test_y = values(test)
    temperature = fit_temperature(train_p, train_y)
    blocks = fit_isotonic(train_p, train_y)
    train_calibrated = apply_temperature(train_p, temperature)
    quantile = conformal_quantile(train_calibrated, train_y, args.alpha)
    intervals = [conformal_interval(p, quantile) for p in apply_temperature(test_p, temperature)]
    at_bound = temperature >= TEMPERATURE_BOUNDS[1] * 0.999 or temperature <= TEMPERATURE_BOUNDS[0] * 1.001

    def metrics(p, y):
        calibrated = apply_temperature(p, temperature)
        return {'raw': {'ece': round(ece(p, y), 4), 'brier': round(brier(p, y), 4)}, 'temperature': {'ece': round(ece(calibrated, y), 4), 'brier': round(brier(calibrated, y), 4)}, 'isotonic': {'ece': round(ece(apply_isotonic(p, blocks), y), 4), 'brier': round(brier(apply_isotonic(p, blocks), y), 4)}}

    with open(args.report, 'rb') as handle:
        report_sha = hashlib.sha256(handle.read()).hexdigest()
    artifact = {
        'schema': SCHEMA,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_report_sha256': report_sha,
        'model': report.get('model_resolved'),
        'method': 'temperature',
        'temperature': round(temperature, 6),
        'at_bound': at_bound,
        'fit': {'target': 'p_buy_vs_next_execution_open_up', 'horizon': '60m', 'note': 'Fitted on weekly backtest decisions; applying it to other horizons is an unlabeled extrapolation'},
        'train_window': {'start': train[0]['decision_time'], 'end': train[-1]['decision_time'], 'n': len(train)},
        'test_window': {'start': test[0]['decision_time'], 'end': test[-1]['decision_time'], 'n': len(test)},
        'isotonic_blocks': blocks,
        'conformal': {'alpha': args.alpha, 'quantile': (None if math.isinf(quantile) else round(quantile, 6)), 'applies_to': 'BUY decisions only'},
    }
    summary = {
        'notice': 'Research only; calibration is fitted on a retrospective backtest and is not advice.',
        'ledger': {'decisions': len(rows), **ledger_info, 'up_rate': round(float(np.concatenate([train_y, test_y]).mean()), 4)},
        'train': artifact['train_window'],
        'test': artifact['test_window'],
        'temperature': round(temperature, 6),
        'metrics_train': metrics(train_p, train_y),
        'metrics_test': metrics(test_p, test_y),
        'reliability_test_before': reliability_curve(test_p, test_y, bins=5),
        'reliability_test_after': reliability_curve(apply_temperature(test_p, temperature), test_y, bins=5),
        'conformal': {'alpha': args.alpha, 'quantile': artifact['conformal']['quantile'], 'test_interval_coverage': round(float(np.mean([y >= lo and y <= hi for (lo, hi), y in zip(intervals, test_y)])), 4), 'test_eligible_rate': round(float(np.mean([interval_excludes_half(i) for i in intervals])), 4) if intervals else None},
        'artifact': artifact,
    }
    return summary


def load_artifact(path):
    with open(path, encoding='utf-8') as handle:
        artifact = json.load(handle)
    if artifact.get('schema') != SCHEMA or artifact.get('method') != 'temperature' or not isinstance(artifact.get('temperature'), (int, float)) or not math.isfinite(artifact['temperature']) or artifact['temperature'] <= 0:
        raise ValueError('Unsupported calibration artifact')
    return artifact
