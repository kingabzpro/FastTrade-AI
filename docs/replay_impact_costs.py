"""Replay the frozen flagship decisions without model requests."""
import hashlib
import json
import math
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from fasttrade import backtest, cli
from fasttrade.market import validate_candles


def h1_sessions(start, end):
    import exchange_calendars
    calendar = exchange_calendars.get_calendar('XNYS', start=start, end=end)
    schedule = calendar.schedule[['open', 'close']].copy()
    schedule['date'] = schedule.index.date
    return schedule.reset_index(drop=True)


def replay(*, slippage_model='flat'):
    root = Path(__file__).resolve().parent / 'backtest-2026-09-28'
    report_bytes = (root / 'report-measured.json').read_bytes()
    original = json.loads(report_bytes)
    rows = {(d['symbol'], d['decision_time']): d for d in original['decisions']}

    def recorded(args):
        row = rows[(args.symbol, args.as_of)]
        return {'decision': dict(row, latency_us=row['latency_ms'] * 1000, confidence=0),
                'market': {'candle_timestamp': row['candle_timestamp']}}

    with tempfile.TemporaryDirectory(prefix='fasttrade-impact-') as directory:
        args = cli.parser().parse_args(['backtest', '--slippage-model', slippage_model, '--data-dir', str(root), '--start', original['start'],
            '--end', original['end'], '--symbols', ','.join(original['symbols']), '--model', original['model_requested'],
            '--fee-bps', str(original['fee_bps']), '--slippage-bps', str(original['slippage_bps']),
            '--output', str(Path(directory) / 'unused.json')])
        with patch.object(cli, 'run', recorded):
            result = backtest.run(args)
    checks = ('ending_balance', 'spy_ending_balance', 'total_fees', 'dataset_manifest_sha256') if slippage_model == 'flat' else ('dataset_manifest_sha256',)
    for name in checks:
        if result[name] != original[name]:
            raise ValueError(f'Recorded backtest does not replay: {name}')
    if slippage_model == 'impact':
        scenario = result['impact_sensitivity']['scenarios'][1]
        if result['ending_balance'] != scenario['ending_balance'] or result['spy_ending_balance'] != scenario['spy_ending_balance']:
            raise ValueError('Primary impact and replay do not match')
    return {'report_sha256': hashlib.sha256(report_bytes).hexdigest(),
            'dataset_manifest_sha256': result['dataset_manifest_sha256'],
            'flat_1x_ending_balance': original['ending_balance'],
            'primary_ending_balance': result['ending_balance'], 'primary_return_pct': result['return_pct'],
            'primary_spy_return_pct': result['spy_return_pct'], 'slippage_model': slippage_model,
            'impact_sensitivity': result['impact_sensitivity']}


def replay_h1():
    root = Path(__file__).resolve().parent / 'benchmark-2026-h1-controlled-comparison'
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    report_bytes = (root / 'new-report.json').read_bytes()
    report = json.loads(report_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if report['snapshot_manifest_sha256'] != manifest_hash or report['symbols'] != manifest['symbols']:
        raise ValueError('Report and snapshot manifest do not match')
    start, end = date.fromisoformat(report['start']), date.fromisoformat(report['end'])
    cutoff = pd.Timestamp(end + pd.Timedelta(days=1), tz='America/New_York').tz_convert('UTC')
    frames = {}
    for symbol in [*report['symbols'], 'SPY']:
        path = root / f'{symbol}.csv.gz'
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256'][symbol]:
            raise ValueError(f'Snapshot checksum failed for {symbol}')
        frames[symbol] = validate_candles(pd.read_csv(path), '60m', cutoff, float('inf')).set_index('timestamp')
    first_date = str(frames['SPY'].index[0].tz_convert('America/New_York').date())
    sessions = h1_sessions(first_date, str(end))
    for row in report['decisions']:
        price = float(frames[row['symbol']].loc[pd.Timestamp(row['execution_time']), 'open'])
        if not math.isclose(price, row['execution_price'], rel_tol=1e-12):
            raise ValueError('Recorded execution price does not match the snapshot')
    common = (sessions, frames, report['symbols'], report['decisions'], {}, start, end, report['initial_balance'])
    zero = backtest.replay_costs(*common, 0, 0, 1)
    if zero['ending_balance'] != report['ending_balance'] or zero['spy_ending_balance'] != report['spy_ending_balance']:
        raise ValueError('H1 no-cost replay does not match the recorded report')
    scenarios = [backtest.replay_costs(*common, .0005, .001, factor, impact=True) for factor in (.5, 1, 2)]
    return {'report_sha256': hashlib.sha256(report_bytes).hexdigest(), 'dataset_manifest_sha256': manifest_hash,
            'no_cost_matches_report': True,
            'impact_sensitivity': {'method': 'fixed-decision square-root impact replay', 'scenarios': scenarios}}


if __name__ == '__main__':
    if sys.argv[1:] not in ([], ['--h1'], ['--primary-impact']):
        raise SystemExit('Usage: replay_impact_costs.py [--h1 | --primary-impact]')
    result = replay_h1() if sys.argv[1:] == ['--h1'] else replay(slippage_model='impact' if sys.argv[1:] else 'flat')
    print(json.dumps(result, indent=2))
