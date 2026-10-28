import hashlib, json, os, sys
from datetime import date, timedelta
from pathlib import Path
import pandas as pd
from fasttrade import benchmark, cli
from fasttrade.market import validate_candles

folder = Path(sys.argv[1])
mode = sys.argv[2]
start, end = date(2026, 1, 2), date(2026, 6, 30)
original = pd.read_csv('docs/benchmark-2026-h1.csv')
symbols = sorted(original.symbol.unique().tolist())
if mode == 'freeze':
    manifest = {}
    frames = {}
    cutoff = pd.Timestamp(end + timedelta(days=1), tz='America/New_York').tz_convert('UTC')
    for number, symbol in enumerate([*symbols, 'SPY'], 1):
        frame = benchmark.download(symbol, start - timedelta(days=30), end + timedelta(days=1))
        checked = validate_candles(frame, '60m', cutoff, float('inf')).set_index('timestamp')
        if checked.index[0].tz_convert('America/New_York').date() > start:
            raise ValueError(f'Insufficient historical data for {symbol}')
        path = folder / f'{symbol}.csv.gz'
        frame.to_csv(path, index=False, compression='gzip')
        manifest[symbol] = hashlib.sha256(path.read_bytes()).hexdigest()
        frames[symbol] = checked
        print(f'Frozen {number}/26: {symbol}', flush=True)
    common = frames['SPY'].index
    for symbol in symbols:
        common = common.intersection(frames[symbol].index)
    common = common.sort_values()
    spy = frames['SPY'].index
    local = spy.tz_convert('America/New_York').date
    expected = spy[(local >= start) & (local <= end)]
    if not common.intersection(expected).equals(expected):
        raise ValueError('The snapshot has missing hourly candles')
    (folder / 'manifest.json').write_text(json.dumps({'start': str(start), 'end': str(end), 'model': 'jev-1.14.0', 'source': 'Yahoo Finance adjusted hourly OHLCV', 'symbols': symbols, 'sha256': manifest}, indent=2), encoding='utf-8')
    print('Snapshot complete.', flush=True)
    sys.exit(0)

manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
for symbol, expected in manifest['sha256'].items():
    if hashlib.sha256((folder / f'{symbol}.csv.gz').read_bytes()).hexdigest() != expected:
        raise ValueError(f'Snapshot checksum failed for {symbol}')
benchmark.download = lambda symbol, *_: pd.read_csv(folder / f'{symbol}.csv.gz')
if mode == 'baseline':
    original_features = cli.features
    def old_features(frame, zone):
        features = original_features(frame, zone)
        features.pop('volatility_30_bars', None)
        features.pop('distance_from_high_50_bars', None)
        return features
    cli.features = old_features
elif mode == 'new':
    original_features = cli.features
    def new_features(frame, zone):
        features = original_features(frame, zone)
        features['volatility_30_bars'] = float(frame.close.pct_change().tail(30).std(ddof=0))
        features['distance_from_high_50_bars'] = float(frame.close.iloc[-1] / frame.high.tail(50).max() - 1)
        return features
    cli.features = new_features
else:
    raise ValueError('Unknown comparison mode')
checkpoint = folder / f'{mode}-checkpoint.jsonl'
cache = {}
if checkpoint.exists():
    with checkpoint.open(encoding='utf-8') as f:
        for line in f:
            record = json.loads(line)
            cache[record['state_sha256']] = record['decision']
original_predict = cli.predict
def checkpointed_predict(state, url, model, key):
    state_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    if state_hash in cache:
        return cache[state_hash]
    decision = original_predict(state, url, model, key)
    if decision['model'] != 'jev-1.14.0':
        raise ValueError('Provider returned an unexpected model version')
    with checkpoint.open('a', encoding='utf-8') as f:
        f.write(json.dumps({'state_sha256': state_hash, 'decision': decision}, allow_nan=False) + '\n')
    cache[state_hash] = decision
    return decision
cli.predict = checkpointed_predict
args = cli.parser().parse_args(['benchmark', '--start', str(start), '--end', str(end), '--symbols', ','.join(symbols), '--model', 'jev-1.14.0'])
report = benchmark.run(args)
report['comparison_mode'] = mode
report['snapshot_manifest_sha256'] = hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest()
(folder / f'{mode}-report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
print(f'{mode} complete: {len(report["decisions"])} decisions', flush=True)
