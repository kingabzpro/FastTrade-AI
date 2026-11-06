"""Replay recorded Alpaca decisions under explicit illustrative risk limits."""
import hashlib
import json
import tempfile
import argparse
from pathlib import Path
from unittest.mock import patch

from fasttrade import backtest, cli


def replay(*, max_drawdown=None):
    root = Path(__file__).resolve().parent / 'backtest-2026-09-28-alpaca'
    report_bytes = (root / 'report.json').read_bytes()
    original = json.loads(report_bytes)
    rows = {(d['symbol'], d['decision_time']): d for d in original['decisions']}
    if len(rows) != len(original['decisions']):
        raise ValueError('Duplicate recorded decisions')

    def recorded(args):
        row = rows[(args.symbol, args.as_of)]
        return {'decision': dict(row, latency_us=row['latency_ms'] * 1000),
                'market': {'candle_timestamp': row['candle_timestamp']}}

    with tempfile.TemporaryDirectory(prefix='fasttrade-sizing-') as directory:
        arguments = ['backtest', '--data-dir', str(root), '--start', original['start'],
            '--end', original['end'], '--symbols', ','.join(original['symbols']), '--model', original['model_requested'],
            '--fee-bps', str(original['fee_bps']), '--slippage-bps', str(original['slippage_bps']),
            '--sizing-vol-target', '.1', '--sizing-max-position', '400', '--sizing-max-exposure', '10000',
            '--sizing-max-daily-loss', '10000', '--sizing-max-trades', '25', '--sizing-min-confidence', '0',
            '--output', str(Path(directory) / 'unused.json')]
        if max_drawdown is not None:
            arguments.extend(['--sizing-max-drawdown', str(max_drawdown)])
        args = cli.parser().parse_args(arguments)
        with patch.object(cli, 'run', recorded):
            result = backtest.run(args)
    for name in ('ending_balance', 'spy_ending_balance', 'total_fees', 'dataset_manifest_sha256'):
        if result[name] != original[name]:
            raise ValueError(f'Recorded backtest does not replay: {name}')
    return {'report_sha256': hashlib.sha256(report_bytes).hexdigest(),
            'dataset_manifest_sha256': result['dataset_manifest_sha256'],
            'original_ending_balance': result['ending_balance'], 'sizing_ablation': result['sizing_ablation']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--drawdown', type=float, help='Opt in to a drawdown exposure throttle')
    options = parser.parse_args()
    print(json.dumps(replay(max_drawdown=options.drawdown), indent=2))
