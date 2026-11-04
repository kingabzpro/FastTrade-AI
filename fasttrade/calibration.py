"""Post-hoc calibration of decision-model probabilities against realized outcomes.

Research tooling: it fits mappings from a frozen backtest's recorded decisions,
never from live data, and its outputs stay research labels, not advice.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

SCHEMA = 'fasttrade-calibration-1'
EDGE_METHOD = 'stationary-review-block-v1'
MIN_EDGE_PERIODS = 10
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
    residuals = np.abs(np.asarray(outcomes, float) - np.asarray(probabilities, float))
    return conformal_quantile_from_residuals(residuals, alpha)


def conformal_quantile_from_residuals(residuals, alpha):
    if not 0 < alpha < 1:
        raise ValueError('Alpha must be between 0 and 1')
    residuals = np.sort(np.abs(np.asarray(residuals, float)))
    n = len(residuals)
    rank = math.ceil((n + 1) * (1 - alpha))
    if rank > n:
        return float('inf')
    return float(residuals[rank - 1])


def stationary_bootstrap_means(values, draws=2000, seed=0, weights=None):
    """Politis-Romano stationary bootstrap: resample mean via geometric blocks.

    Block length is ceil(n^(1/3)) per the small-sample default. The caller
    supplies chronological observations; optional weights preserve group sizes.
    """
    values = np.asarray(values, float)
    n = len(values)
    if n == 0:
        raise ValueError('Nothing to bootstrap')
    weights = np.ones(n) if weights is None else np.asarray(weights, float)
    if weights.shape != values.shape or not np.isfinite(values).all() or not np.isfinite(weights).all() or (weights < 0).any() or not weights.any():
        raise ValueError('Invalid bootstrap observations or weights')
    block = max(2, math.ceil(n ** (1 / 3)))
    rng = np.random.default_rng(seed)
    probability = 1.0 / block
    means = np.empty(draws)
    for draw in range(draws):
        indices = np.empty(n, dtype=int)
        position = int(rng.integers(n))
        for i in range(n):
            indices[i] = position
            if rng.random() < probability:
                position = int(rng.integers(n))
            else:
                position = (position + 1) % n
        sampled_weights = weights[indices]
        if not sampled_weights.any():
            raise ValueError('Bootstrap sample contains no weighted observations')
        means[draw] = np.average(values[indices], weights=sampled_weights)
    return means


def certify_edge(rows, round_trip, min_decisions=5):
    """Aggregate verdict: bootstrap CI on the mean net excess return of BUY
    decisions (symbol move minus SPY move over the same interval, minus
    round-trip cost). Per-trade certainty does not exist in weekly equity
    markets; the mean edge is the certifiable object and its CI shrinks with
    the number of decisions."""
    buys = [r for r in rows if r['action'] == 'BUY']
    excess = np.array([r['move'] - r['spy_move'] - round_trip for r in buys])
    periods = {_review_period(row['decision_time']): [] for row in rows}
    for row, value in zip(buys, excess):
        periods.setdefault(_review_period(row['decision_time']), []).append(value)
    first = min(periods) if periods else None
    last = max(periods) if periods else None
    grouped = [periods.get(first + timedelta(weeks=i), []) for i in range((last - first).days // 7 + 1)] if periods else []
    buy_periods = sum(bool(group) for group in grouped)
    base = {'method': EDGE_METHOD, 'n': len(excess), 'periods': buy_periods, 'review_periods': len(grouped)}
    if len(excess) < min_decisions:
        return {**base, 'certified': None, 'reason': f'fewer than {min_decisions} BUY decisions in the window'}
    # shortcut: ten review periods are a screening minimum, not a power guarantee; use longer independent samples when available.
    if buy_periods < MIN_EDGE_PERIODS:
        return {**base, 'certified': None, 'reason': f'fewer than {MIN_EDGE_PERIODS} distinct BUY review periods in the window'}
    intervals = {}
    try:
        for row in buys:
            review = _review_period(row['decision_time'])
            decision, entry, next_decision, exit_, available = (_utc_timestamp(row[key]) for key in ('decision_time', 'execution_time', 'next_decision_time', 'exit_time', 'outcome_available_at'))
            if (_review_period(next_decision) != review + timedelta(weeks=1) or not decision < entry < next_decision < exit_
                    or available < exit_ + pd.Timedelta(hours=1) or intervals.setdefault(review, (entry, exit_)) != (entry, exit_)):
                raise ValueError('Misaligned weekly reviews')
        ordered = sorted(intervals)
        if any(intervals[left][1] > intervals[right][0] for left, right in zip(ordered, ordered[1:])):
            raise ValueError('Overlapping weekly intervals')
    except (KeyError, TypeError, ValueError, OverflowError):
        return {**base, 'certified': None, 'reason': 'missing or misaligned weekly review intervals'}
    try:
        means = stationary_bootstrap_means([np.mean(group) if group else 0 for group in grouped], weights=[len(group) for group in grouped])
    except ValueError as exc:
        if str(exc) != 'Bootstrap sample contains no weighted observations':
            raise
        return {**base, 'certified': None, 'reason': 'bootstrap resampling produced a period sample with no BUY decisions'}
    low, high = np.percentile(means, [2.5, 97.5])
    return {**base, 'certified': bool(low > 0), 'mean': round(float(excess.mean()), 6), 'ci': [round(float(low), 6), round(float(high), 6)], 'round_trip_cost': round(round_trip, 8), 'block_length': max(2, math.ceil(len(grouped) ** (1 / 3))), 'draws': 2000}


def valid_edge_certification(edge):
    if not isinstance(edge, dict) or edge.get('certified') is not True or edge.get('method') != EDGE_METHOD:
        return False
    periods, count, ci = edge.get('periods'), edge.get('n'), edge.get('ci')
    return (type(periods) is int and type(count) is int and count >= periods >= MIN_EDGE_PERIODS
            and isinstance(ci, list) and len(ci) == 2
            and all(type(value) in (int, float) and math.isfinite(value) for value in ci)
            and 0 < ci[0] <= ci[1])


def valid_calibration_edge(artifact, model, timeframe, as_of, review_horizon=None, symbol=None):
    if not valid_edge_certification(artifact.get('edge')) or artifact.get('model') != model:
        return False
    if (not isinstance(artifact.get('fit'), dict) or artifact['fit'].get('horizon') != timeframe
            or artifact['fit'].get('target') != 'p_buy_vs_next_execution_open_up'
            or review_horizon != 'weekly-next-execution' or artifact['fit'].get('review_horizon') != review_horizon):
        return False
    try:
        validate_artifact_time(artifact, as_of)
        local_time = _utc_timestamp(as_of).tz_convert('America/New_York')
        scope = artifact['edge']['scope']
        if not isinstance(scope['symbol_review_times'], list) or [symbol, local_time.strftime('%a %H:%M')] not in scope['symbol_review_times']:
            return False
        return True
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def validate_artifact_time(artifact, as_of):
    try:
        created = _utc_timestamp(artifact['created_at'])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Calibration artifact needs a timezone-aware creation time') from exc
    if created > _utc_timestamp(as_of):
        raise ValueError('Calibration artifact was created after the evaluation time')
    try:
        windows = [artifact[window] for window in ('train_window', 'test_window')]
        starts = [_utc_timestamp(window['start']) for window in windows]
        ends = [_utc_timestamp(window['end']) for window in windows]
        outcomes = [_utc_timestamp(window['outcome_available_through']) for window in windows]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Calibration artifact needs outcome availability times; regenerate it') from exc
    if (any(start > end or end >= outcome or outcome > created for start, end, outcome in zip(starts, ends, outcomes))
            or ends[0] >= starts[1] or outcomes[0] > starts[1]):
        raise ValueError('Calibration artifact outcome windows overlap or were unavailable at creation time')


def conformal_interval(probability, quantile):
    return [max(0.0, probability - quantile), min(1.0, probability + quantile)]


def interval_excludes_half(interval):
    return interval[0] > 0.5 or interval[1] < 0.5


def _utc_timestamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('Calibration timestamps must include timezones')
    return stamp.tz_convert('UTC')


def _review_period(value):
    day = _utc_timestamp(value).tz_convert('America/New_York').date()
    return day - timedelta(days=day.weekday())


def build_ledger(report, data_dir):
    """Pair each recorded decision's P(BUY) with the realized move to the next
    execution open for that symbol, from the frozen dataset CSVs. The final
    review per symbol has no next execution and is excluded. Each pair carries
    the binary direction (`outcome_up`) and the simple return (`move`)."""
    data_dir = str(data_dir)
    decisions = report.get('decisions') or []
    if not decisions or any('probabilities' not in d for d in decisions):
        raise ValueError('Report decisions must include probabilities')
    frames = {}
    rows, excluded = [], 0
    by_symbol = {}
    for decision in decisions:
        if _utc_timestamp(decision['decision_time']) >= _utc_timestamp(decision['execution_time']):
            raise ValueError('Decision must precede execution')
        by_symbol.setdefault(decision['symbol'], []).append(decision)
    spy_frame = None
    for symbol, group in by_symbol.items():
        if not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', symbol):
            raise ValueError(f'Invalid symbol in report: {symbol}')
        group.sort(key=lambda d: _utc_timestamp(d['decision_time']))
        executions = [_utc_timestamp(d['execution_time']) for d in group]
        if any(a >= b for a, b in zip(executions, executions[1:])):
            raise ValueError('Execution times must be unique and ordered per symbol')
        if symbol not in frames:
            frame = pd.read_csv(f'{data_dir}/{symbol}.csv')
            frames[symbol] = frame.assign(timestamp=pd.to_datetime(frame.timestamp, utc=True)).set_index('timestamp').sort_index()
        for index, decision in enumerate(group[:-1]):
            entry = _open_at(frames[symbol], decision['execution_time'])
            exit_ = _open_at(frames[symbol], group[index + 1]['execution_time'])
            if entry is None or exit_ is None:
                excluded += 1
                continue
            if spy_frame is None:
                try:
                    spy = pd.read_csv(f'{data_dir}/SPY.csv')
                except OSError as exc:
                    raise ValueError('Dataset must include SPY.csv for the excess-return edge') from exc
                spy_frame = spy.assign(timestamp=pd.to_datetime(spy.timestamp, utc=True)).set_index('timestamp').sort_index()
            spy_entry = _open_at(spy_frame, decision['execution_time'])
            spy_exit = _open_at(spy_frame, group[index + 1]['execution_time'])
            if spy_entry is None or spy_exit is None or spy_entry <= 0:
                excluded += 1
                continue
            exit_time = _utc_timestamp(group[index + 1]['execution_time'])
            rows.append({'symbol': symbol, 'decision_time': decision['decision_time'], 'execution_time': decision['execution_time'], 'next_decision_time': group[index + 1]['decision_time'], 'exit_time': exit_time.isoformat(), 'outcome_available_at': (exit_time + pd.Timedelta(hours=1)).isoformat(), 'action': decision['action'], 'p_buy': float(decision['probabilities']['BUY']), 'outcome_up': bool(exit_ > entry), 'move': float(exit_ / entry - 1), 'spy_move': float(spy_exit / spy_entry - 1)})
    return rows, {'last_reviews_without_outcome': len(by_symbol), 'excluded_missing_price_bars': excluded}


def _open_at(frame, iso_time):
    try:
        value = float(frame.loc[pd.Timestamp(iso_time), 'open'])
        return value if math.isfinite(value) and value > 0 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def split_temporal(rows, train_frac):
    if not 0 < train_frac < 1:
        raise ValueError('Train fraction must be between 0 and 1')
    rows = sorted(rows, key=lambda r: _utc_timestamp(r['decision_time']))
    reviews = sorted({_review_period(row['decision_time']) for row in rows})
    if len(reviews) < 2:
        raise ValueError('At least two distinct review periods are required')
    split = max(1, min(len(reviews) - 1, int(len(reviews) * train_frac)))
    test = [row for row in rows if _review_period(row['decision_time']) >= reviews[split]]
    cutoff = min(_utc_timestamp(row['decision_time']) for row in test)
    train = [row for row in rows if _review_period(row['decision_time']) < reviews[split] and _utc_timestamp(row['outcome_available_at']) <= cutoff]
    if not train or not test:
        raise ValueError('Not enough non-overlapping training and test outcomes')
    return train, test


def run_calibration(args):
    with open(args.report, encoding='utf-8') as handle:
        report = json.load(handle)
    rows, ledger_info = build_ledger(report, args.data_dir)
    if len(rows) < 8:
        raise ValueError('Not enough decisions with outcomes to calibrate; need at least 8')
    train, test = split_temporal(rows, args.train_frac)
    ledger_info['purged_overlapping_labels'] = len(rows) - len(train) - len(test)

    def values(group):
        return (np.array([r['p_buy'] for r in group]), np.array([1.0 if r['outcome_up'] else 0.0 for r in group]), np.array([r['move'] for r in group]))

    train_p, train_y, train_moves = values(train)
    test_p, test_y, test_moves = values(test)
    temperature = fit_temperature(train_p, train_y)
    blocks = fit_isotonic(train_p, train_y)
    train_calibrated = apply_temperature(train_p, temperature)
    quantile = conformal_quantile(train_calibrated, train_y, args.alpha)
    intervals = [conformal_interval(p, quantile) for p in apply_temperature(test_p, temperature)]
    at_bound = temperature >= TEMPERATURE_BOUNDS[1] * 0.999 or temperature <= TEMPERATURE_BOUNDS[0] * 1.001
    if args.fee_bps < 0 or args.slippage_bps < 0:
        raise ValueError('Fees and slippage must be nonnegative')
    round_trip = 2 * (args.fee_bps + args.slippage_bps) / 10000
    return_blocks = fit_isotonic(train_p, train_moves)
    return_residuals = np.abs(train_moves - apply_isotonic(train_p, return_blocks))
    return_quantile = conformal_quantile_from_residuals(return_residuals, args.alpha)
    test_expected = apply_isotonic(test_p, return_blocks)
    test_lower = test_expected - (return_quantile if not math.isinf(return_quantile) else float('inf'))
    certified = [lower > round_trip for lower in test_lower]
    chronological = sorted(rows, key=lambda r: r['decision_time'])
    edge = certify_edge(test, round_trip)
    edge_in_sample = certify_edge(chronological, round_trip)
    edge['definition'] = 'mean net excess return per BUY decision: symbol move minus SPY move over the same interval, minus round-trip cost'
    edge['window'] = 'test'
    edge['scope'] = {'symbol_review_times': [list(pair) for pair in sorted({(row['symbol'], _utc_timestamp(row['decision_time']).tz_convert('America/New_York').strftime('%a %H:%M')) for row in test if row['action'] == 'BUY'})]}
    edge['in_sample'] = edge_in_sample

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
        'fit': {'target': 'p_buy_vs_next_execution_open_up', 'horizon': '60m', 'review_horizon': 'weekly-next-execution', 'note': 'Fitted on weekly backtest decisions; applying it to other horizons is an unlabeled extrapolation'},
        'train_window': {'start': train[0]['decision_time'], 'end': train[-1]['decision_time'], 'outcome_available_through': max(_utc_timestamp(row['outcome_available_at']) for row in train).isoformat(), 'n': len(train)},
        'test_window': {'start': test[0]['decision_time'], 'end': test[-1]['decision_time'], 'outcome_available_through': max(_utc_timestamp(row['outcome_available_at']) for row in test).isoformat(), 'n': len(test)},
        'isotonic_blocks': blocks,
        'conformal': {'alpha': args.alpha, 'quantile': (None if math.isinf(quantile) else round(quantile, 6)), 'applies_to': 'BUY decisions only'},
        'returns': {'blocks': return_blocks, 'alpha': args.alpha, 'quantile': (None if math.isinf(return_quantile) else round(return_quantile, 8)), 'round_trip_cost': round(round_trip, 8), 'round_trip_cost_bps': round(round_trip * 1e4, 2), 'note': 'Diagnostic only: expected return maps raw P(BUY) through isotonic blocks fitted on realized next-open moves'},
        'edge': edge,
    }
    summary = {
        'notice': 'Research only; calibration is fitted on a retrospective backtest and is not advice.',
        'ledger': {'decisions': len(rows), **ledger_info, 'up_rate': round(sum(row['outcome_up'] for row in rows) / len(rows), 4)},
        'train': artifact['train_window'],
        'test': artifact['test_window'],
        'temperature': round(temperature, 6),
        'metrics_train': metrics(train_p, train_y),
        'metrics_test': metrics(test_p, test_y),
        'reliability_test_before': reliability_curve(test_p, test_y, bins=5),
        'reliability_test_after': reliability_curve(apply_temperature(test_p, temperature), test_y, bins=5),
        'conformal': {'alpha': args.alpha, 'quantile': artifact['conformal']['quantile'], 'test_interval_coverage': round(float(np.mean([y >= lo and y <= hi for (lo, hi), y in zip(intervals, test_y)])), 4), 'test_eligible_rate': round(float(np.mean([interval_excludes_half(i) for i in intervals])), 4) if intervals else None},
        'returns': {'round_trip_cost_bps': artifact['returns']['round_trip_cost_bps'], 'train_mean_move': round(float(train_moves.mean()), 6), 'test_mean_move': round(float(test_moves.mean()), 6), 'test_expected_mean_move': round(float(test_expected.mean()), 6), 'test_certified_rate': round(float(np.mean(certified)), 4) if len(certified) else None},
        'edge': {'certified': edge['certified'], 'reason': edge.get('reason'), 'n': edge['n'], 'mean': edge.get('mean'), 'ci': edge.get('ci'), 'in_sample': {'certified': edge_in_sample['certified'], 'n': edge_in_sample['n'], 'mean': edge_in_sample.get('mean'), 'ci': edge_in_sample.get('ci')}},
        'artifact': artifact,
    }
    return summary


def load_artifact(path):
    with open(path, encoding='utf-8') as handle:
        artifact = json.load(handle)
    if artifact.get('schema') != SCHEMA or artifact.get('method') != 'temperature' or not isinstance(artifact.get('temperature'), (int, float)) or not math.isfinite(artifact['temperature']) or artifact['temperature'] <= 0:
        raise ValueError('Unsupported calibration artifact')
    return artifact
