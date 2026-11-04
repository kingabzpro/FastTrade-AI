"""Weekly, long-only historical simulation of the existing signal engine."""
import os
import re
import sys
import tempfile
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
from dotenv import load_dotenv

from . import cli
from .market import validate_candles
from .calibration import stationary_bootstrap_means


def download(symbol, start, end):
    import yfinance as yf
    try:
        frame = yf.Ticker(symbol).history(start=start.isoformat(), end=end.isoformat(), interval='60m', auto_adjust=True, prepost=False, raise_errors=True)
    except Exception as exc:
        raise ValueError(f'Could not download hourly data for {symbol}') from exc
    if frame.empty:
        raise ValueError(f'No hourly data for {symbol}; Yahoo limits historical intraday data')
    return frame.reset_index().rename(columns={'Datetime': 'timestamp', 'Date': 'timestamp', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'})


def run(args):
    try:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    except ValueError as exc:
        raise ValueError('Start and end must be dates in YYYY-MM-DD format') from exc
    if start >= end:
        raise ValueError('Start must be before end')
    symbols = [s.strip().upper() for s in args.symbols.split(',')]
    if not symbols or any(not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', s) or s == 'SPY' for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError('Provide unique comma-separated symbols; SPY is reserved for the benchmark')
    load_dotenv()
    model = args.model or os.getenv('SYSTEMONE_MODEL', 'jev-latest')
    end_exclusive = pd.Timestamp(end + timedelta(days=1), tz='America/New_York').tz_convert('UTC')
    cutoff = min(end_exclusive, pd.Timestamp.now(tz='UTC'))
    frames = {}
    for symbol in [*symbols, 'SPY']:
        frame = download(symbol, start - timedelta(days=30), end + timedelta(days=1))
        frames[symbol] = validate_candles(frame, '60m', cutoff, float('inf')).set_index('timestamp')
        if frames[symbol].index[0].tz_convert('America/New_York').date() > start:
            raise ValueError(f'Hourly history for {symbol} does not cover the requested start date')
    common = frames['SPY'].index
    for symbol in symbols:
        common = common.intersection(frames[symbol].index)
    common = common.sort_values()
    local = common.tz_convert('America/New_York')
    eligible = [i for i, time in enumerate(local) if start <= time.date() <= end]
    expected = frames['SPY'].index[(frames['SPY'].index.tz_convert('America/New_York').date >= start) & (frames['SPY'].index.tz_convert('America/New_York').date <= end)]
    if not common.intersection(expected).equals(expected):
        raise ValueError('Hourly candles are missing for at least one symbol during the requested period')
    weeks = {}
    for i in eligible:
        weeks[local[i].isocalendar()[:2]] = i
    schedule = [i for i in weeks.values() if i + 1 < len(common) and common[i + 1] >= common[i] + pd.Timedelta(hours=1)]
    if not schedule:
        raise ValueError('No weekly decisions with a later execution candle in the requested range')
    for i in schedule:
        if any(len(frame.loc[:common[i]]) < 50 for frame in frames.values()):
            raise ValueError('At least 50 earlier hourly candles are required for each symbol and SPY')
    first_open = common[schedule[0] + 1]
    final = common[-1]
    initial = 10000.0
    cash = {s: initial / len(symbols) for s in symbols}
    shares = {s: 0.0 for s in symbols}
    decisions = []
    portfolio_values, spy_values = [], []
    with tempfile.TemporaryDirectory(prefix='fasttrade-benchmark-') as directory:
        paths = {}
        for symbol in symbols:
            paths[symbol] = Path(directory) / f'{symbol}.csv'
            frames[symbol].reset_index().to_csv(paths[symbol], index=False)
        for week, i in enumerate(schedule, 1):
            decision_time = common[i] + pd.Timedelta(hours=1)
            execution_time = common[i + 1]
            portfolio_values.append(sum(cash[s] + shares[s] * float(frames[s].loc[execution_time, 'open']) for s in symbols))
            spy_values.append(float(frames['SPY'].loc[execution_time, 'open']))
            print(f'Benchmark week {week}/{len(schedule)}: {decision_time.date()}', file=sys.stderr, flush=True)
            for symbol in symbols:
                analysis = cli.parser().parse_args(['analyze', symbol, '--timeframe', '60m', '--csv', str(paths[symbol]), '--as-of', decision_time.isoformat()])
                analysis.systemone_model = model
                for attempt in range(3):
                    try:
                        result = cli.run(analysis)
                        break
                    except ValueError as exc:
                        if str(exc) != 'Invalid SystemOne response' or attempt == 2:
                            raise
                        print(f'Retrying invalid response for {symbol} ({attempt + 1}/2)', file=sys.stderr, flush=True)
                decision = result['decision']
                price = float(frames[symbol].loc[execution_time, 'open'])
                if decision['action'] == 'BUY' and cash[symbol] > 0:
                    shares[symbol], cash[symbol] = cash[symbol] / price, 0.0
                elif decision['action'] == 'SELL' and shares[symbol] > 0:
                    cash[symbol], shares[symbol] = shares[symbol] * price, 0.0
                decisions.append({'symbol': symbol, 'decision_time': decision_time.isoformat(), 'execution_time': execution_time.isoformat(), 'action': decision['action'], 'probabilities': decision['probabilities'], 'confidence': decision['confidence'], 'model': decision['model'], 'latency_ms': decision['latency_us'] / 1000, 'execution_price': price, 'cash': cash[symbol], 'shares': shares[symbol]})
    ending = sum(cash[s] + shares[s] * float(frames[s].loc[final, 'close']) for s in symbols)
    spy_open = float(frames['SPY'].loc[first_open, 'open'])
    spy_close = float(frames['SPY'].loc[final, 'close'])
    portfolio_values.append(ending)
    spy_values.append(spy_close)
    excess = np.diff(portfolio_values) / portfolio_values[:-1] - np.diff(spy_values) / spy_values[:-1]
    significance = {'method': 'stationary bootstrap', 'observations': len(excess),
                    'mean_weekly_excess_return': float(excess.mean()), 'confidence_interval': None,
                    'block_length': max(2, math.ceil(len(excess) ** (1 / 3))), 'draws': 2000,
                    'seed': 0, 'confidence_level': 0.95}
    if len(excess) >= 2:
        significance['confidence_interval'] = np.percentile(stationary_bootstrap_means(excess), [2.5, 97.5]).tolist()
    directional = []
    for symbol in symbols:
        rows = [row for row in decisions if row['symbol'] == symbol]
        for index, row in enumerate(rows):
            next_price = rows[index + 1]['execution_price'] if index + 1 < len(rows) else float(frames[symbol].loc[final, 'close'])
            row['next_valuation_price'] = next_price
            if row['action'] in ('BUY', 'SELL'):
                row['directional_correct'] = next_price > row['execution_price'] if row['action'] == 'BUY' else next_price < row['execution_price']
                directional.append(row['directional_correct'])
    return {'model_requested': model, 'start': args.start, 'end': args.end, 'symbols': symbols, 'initial_balance': initial, 'first_execution_time': first_open.isoformat(), 'valuation_time': (final + pd.Timedelta(hours=1)).isoformat(), 'ending_balance': round(ending, 2), 'return_pct': round((ending / initial - 1) * 100, 2), 'spy_ending_balance': round(initial * spy_close / spy_open, 2), 'spy_return_pct': round((spy_close / spy_open - 1) * 100, 2), 'directional_decisions': len(directional), 'directional_accuracy_pct': round(100 * sum(directional) / len(directional), 2) if directional else None, 'significance': significance, 'assumptions': 'Adjusted hourly Yahoo prices; weekly model preferences; next common hourly open; equal independent allocations; fractional shares; no fees, slippage, taxes, or risk checks. Historical model knowledge and revised data can bias results.', 'decisions': decisions}
