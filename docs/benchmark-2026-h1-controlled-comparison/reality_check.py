"""Compare the two recorded controlled variants; no model requests are made."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fasttrade.significance import reality_check
from fasttrade.overfitting import cscv, evaluate_folds, purged_splits


def run(folder):
    folder = Path(folder)
    manifest_bytes = (folder / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    symbols = manifest['symbols']
    frames = {}
    for symbol in [*symbols, 'SPY']:
        path = folder / f'{symbol}.csv.gz'
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256'][symbol]:
            raise ValueError(f'Snapshot checksum failed for {symbol}')
        frame = pd.read_csv(path)
        frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
        frames[symbol] = frame.set_index('timestamp')
    reports = [json.loads((folder / f'{name}-report.json').read_text(encoding='utf-8')) for name in ('baseline', 'new')]
    cutoff_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if any(report['snapshot_manifest_sha256'] != cutoff_hash or report['symbols'] != symbols
           or report['comparison_mode'] != name or report['model_requested'] != manifest['model']
           for report, name in zip(reports, ('baseline', 'new'))):
        raise ValueError('Reports do not match the frozen snapshot')
    matrices, shared_times, shared_decisions, spy_values = [], None, None, None
    for report in reports:
        grouped = {}
        for row in report['decisions']:
            when = pd.Timestamp(row['execution_time'])
            group = grouped.setdefault(when, {})
            if row['symbol'] not in symbols or row['symbol'] in group:
                raise ValueError('Duplicate or unexpected decision')
            group[row['symbol']] = row
        times = sorted(grouped)
        if len(times) < 3 or any(t.tzinfo is None or set(grouped[t]) != set(symbols) for t in times):
            raise ValueError('Each review must cover the full symbol universe')
        weeks = [t.tz_convert('America/New_York').date() - pd.Timedelta(days=t.tz_convert('America/New_York').weekday()) for t in times]
        if any((b - a).days != 7 for a, b in zip(weeks, weeks[1:])):
            raise ValueError('Execution weeks must be consecutive')
        if shared_times is not None and times != shared_times:
            raise ValueError('Variants must share execution times')
        decision_times = []
        for when in times:
            cohort = {pd.Timestamp(row['decision_time']) for row in grouped[when].values()}
            if len(cohort) != 1:
                raise ValueError('Each review must share one decision time')
            decision = next(iter(cohort))
            if decision.tzinfo is None or decision >= when:
                raise ValueError('Decision must precede execution')
            decision_times.append(decision)
        if shared_decisions is not None and decision_times != shared_decisions:
            raise ValueError('Variants must share decision times')
        shared_times = times
        shared_decisions = decision_times
        cash = {symbol: report['initial_balance'] / len(symbols) for symbol in symbols}
        shares = {symbol: 0.0 for symbol in symbols}
        values = []
        for when in times:
            group = grouped[when]
            prices = {symbol: float(frames[symbol].loc[when, 'open']) for symbol in symbols}
            values.append(sum(cash[symbol] + shares[symbol] * prices[symbol] for symbol in symbols))
            for symbol in symbols:
                row, price = group[symbol], prices[symbol]
                if not np.isclose(row['execution_price'], price, rtol=1e-12, atol=1e-12):
                    raise ValueError('Decision prices do not match the snapshot')
                if row['action'] == 'BUY' and cash[symbol] > 0:
                    shares[symbol], cash[symbol] = cash[symbol] / price, 0.0
                elif row['action'] == 'SELL' and shares[symbol] > 0:
                    cash[symbol], shares[symbol] = shares[symbol] * price, 0.0
                elif row['action'] not in ('BUY', 'SELL', 'HOLD'):
                    raise ValueError('Invalid recorded action')
                if not np.isclose(row['cash'], cash[symbol]) or not np.isclose(row['shares'], shares[symbol]):
                    raise ValueError('Recorded portfolio state does not match replay')
        valuation = pd.Timestamp(report['valuation_time']) - pd.Timedelta(hours=1)
        ending = sum(cash[symbol] + shares[symbol] * float(frames[symbol].loc[valuation, 'close']) for symbol in symbols)
        if abs(ending - report['ending_balance']) > .01:
            raise ValueError('Final portfolio does not match the report')
        if spy_values is None:
            spy_values = np.array([float(frames['SPY'].loc[t, 'open']) for t in times])
        if (not np.isfinite(spy_values).all() or (spy_values <= 0).any()
                or abs(report['initial_balance'] * float(frames['SPY'].loc[valuation, 'close']) / spy_values[0] - report['spy_ending_balance']) > .01):
            raise ValueError('SPY does not match the report')
        matrices.append(np.diff(values) / values[:-1] - np.diff(spy_values) / spy_values[:-1])
    matrix = np.column_stack(matrices)
    result = reality_check(matrix)
    result['variants'] = ['baseline', 'new']
    result['final_partial_period_excluded'] = True
    starts = shared_decisions[:-1]
    ends = [time + pd.Timedelta(hours=1) for time in shared_times[1:]]
    splits = purged_splits(starts, ends, groups=6, test_groups=2)
    result['overfitting'] = {'cscv': cscv(matrix), 'purged_folds': {'groups': 6, 'test_groups': 2, 'embargo_days': 7,
                              'label_intervals': [{'start': a.isoformat(), 'end': b.isoformat()} for a, b in zip(starts, ends)],
                              'folds': evaluate_folds(matrix, splits)}}
    return result


if __name__ == '__main__':
    print(json.dumps(run(Path(__file__).resolve().parent), indent=2))
