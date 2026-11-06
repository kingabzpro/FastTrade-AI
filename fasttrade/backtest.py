"""Chronological, long-only simulation on a checksummed raw-price dataset."""
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
from dotenv import load_dotenv

from . import cli
from .calibration import stationary_bootstrap_means
from .market import validate_candles
from .impact import impact_bps, liquidity


def replay_costs(sessions, frames, symbols, decisions, dividends, start, end, initial, fee, slip, multiplier, *, impact=False):
    fee, slip = fee * multiplier, slip * multiplier
    if fee >= 1 or (not impact and slip >= 1):
        return {'multiplier': multiplier, 'ending_balance': None, 'spy_ending_balance': None, 'reason': 'scaled per-side costs must be below 100%'}
    cash = {symbol: initial / len(symbols) for symbol in symbols}
    shares = {symbol: 0.0 for symbol in symbols}
    receivables = {symbol: 0.0 for symbol in [*symbols, 'SPY']}
    executions = {}
    for decision in decisions:
        try:
            decision_time = pd.Timestamp(decision['decision_time'])
            execution_time = pd.Timestamp(decision['execution_time'])
            if pd.isna(decision_time) or pd.isna(execution_time) or decision_time.tzinfo is None or execution_time.tzinfo is None or decision_time >= execution_time:
                raise ValueError('Recorded decision must precede execution with timezone-aware timestamps')
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid recorded decision or execution time') from exc
        executions.setdefault(execution_time, []).append(decision)
    active_sessions = [session for session in sessions.itertuples() if start <= session.date <= end]
    if any(not any(session.open <= execution < session.close for session in active_sessions) for execution in executions):
        raise ValueError('Recorded execution is outside a replay session')
    spy_shares = 0.0
    impact_trades = []

    def fill(symbol, action, price, cutoff, amount):
        if not impact:
            return (amount / (price * (1 + slip) * (1 + fee)), slip) if action == 'BUY' else (amount, slip)
        symbol_dividends = {day: event[0] for (name, day), event in dividends.items() if name == symbol}
        info = liquidity(sessions, frames[symbol], pd.Timestamp(cutoff), dividends=symbol_dividends)
        if info is None:
            raise ValueError(f'Insufficient prior liquidity history for {symbol} at {cutoff}')
        def rate(quantity):
            return impact_bps(quantity, info['daily_volatility'], info['daily_volume']) * multiplier / 10000
        if action == 'BUY':
            low, high = 0.0, amount / (price * (1 + fee))
            for _ in range(50):
                middle = (low + high) / 2
                if middle * price * (1 + rate(middle)) * (1 + fee) <= amount:
                    low = middle
                else:
                    high = middle
            quantity = low
        else:
            quantity = amount
        slippage = rate(quantity)
        impact_trades.append({'symbol': symbol, 'action': action, 'decision_time': cutoff,
                              'quantity': quantity, 'impact_bps': slippage * 10000, **info})
        return quantity, slippage

    for session in sessions.itertuples():
        if not start <= session.date <= end:
            continue
        for symbol in [*symbols, 'SPY']:
            event = dividends.get((symbol, session.date))
            if event:
                receivables[symbol] += (spy_shares if symbol == 'SPY' else shares[symbol]) * event[0]
        execution_times = [t for t in executions if session.open <= t < session.close]
        if len(execution_times) > 1:
            raise ValueError('Multiple execution times in one session are not supported')
        execution_time = execution_times[0] if execution_times else None
        actions = executions.get(execution_time, [])
        if actions and not spy_shares:
            try:
                spy_shares, _ = fill('SPY', 'BUY', float(frames['SPY'].loc[execution_time, 'open']), actions[0]['decision_time'], initial)
            except ValueError as exc:
                return {'multiplier': multiplier, 'ending_balance': None, 'spy_ending_balance': None, 'reason': str(exc)}
        for decision in actions:
            symbol, action = decision['symbol'], decision['action']
            price = float(frames[symbol].loc[execution_time, 'open'])
            if action == 'BUY' and cash[symbol] > 0:
                try:
                    shares[symbol], _ = fill(symbol, action, price, decision['decision_time'], cash[symbol])
                except ValueError as exc:
                    return {'multiplier': multiplier, 'ending_balance': None, 'spy_ending_balance': None, 'reason': str(exc)}
                cash[symbol] = 0.0
            elif action == 'SELL' and shares[symbol] > 0:
                try:
                    _, execution_slip = fill(symbol, action, price, decision['decision_time'], shares[symbol])
                except ValueError as exc:
                    return {'multiplier': multiplier, 'ending_balance': None, 'spy_ending_balance': None, 'reason': str(exc)}
                cash[symbol] = shares[symbol] * price * (1 - execution_slip) * (1 - fee)
                shares[symbol] = 0.0
        last_bar = pd.date_range(session.open, session.close, freq='h', inclusive='left')[-1]
    ending = sum(cash[s] + shares[s] * float(frames[s].loc[last_bar, 'close']) + receivables[s] for s in symbols)
    spy_ending = spy_shares * float(frames['SPY'].loc[last_bar, 'close']) + receivables['SPY']
    result = {'multiplier': multiplier, 'fee_bps': fee * 10000, 'slippage_bps': slip * 10000,
            'ending_balance': round(ending, 2), 'return_pct': round((ending / initial - 1) * 100, 2),
            'spy_ending_balance': round(spy_ending, 2), 'spy_return_pct': round((spy_ending / initial - 1) * 100, 2)}
    if impact:
        result.pop('slippage_bps')
        result['trades'] = impact_trades
    return result


def run(args):
    try:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    except ValueError as exc:
        raise ValueError('Dates must use YYYY-MM-DD') from exc
    if start >= end or end >= pd.Timestamp.now(tz='America/New_York').date():
        raise ValueError('Start must precede end, and end must be a completed past date')
    symbols = [s.strip().upper() for s in args.symbols.split(',')]
    if any(not re.fullmatch(r'[A-Z0-9.^-]{1,20}', s) or s == 'SPY' for s in symbols) or len(set(symbols)) != len(symbols):
        raise ValueError('Symbols must be unique US tickers; SPY is reserved for the baseline')
    if any(not math.isfinite(x) or not 0 <= x < 10000 for x in (args.fee_bps, args.slippage_bps)):
        raise ValueError('Costs must be finite basis points between 0 and 10000')
    if Path(args.output).exists():
        raise ValueError('Output file already exists')
    if not Path(args.output).parent.is_dir():
        raise ValueError('Output directory does not exist')

    root = Path(args.data_dir)
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get('price_basis') != 'unadjusted' or manifest.get('corporate_actions') not in ('none', 'cash_dividends_only') or not isinstance(manifest.get('sha256'), dict):
        raise ValueError('Manifest must attest unadjusted prices and supported corporate actions over the dataset')

    def read_snapshot(name):
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest.get('sha256', {}).get(name):
            raise ValueError(f'Data checksum mismatch: {name}')
        return pd.read_csv(io.BytesIO(data))

    sessions = read_snapshot('sessions.csv')
    if not {'open', 'close'} <= set(sessions.columns):
        raise ValueError('Sessions require open and close timestamps')
    try:
        for col in ('open', 'close'):
            times = [pd.Timestamp(value) for value in sessions[col]]
            if any(pd.isna(value) or value.tzinfo is None for value in times):
                raise ValueError('Session timestamps must include timezones')
            sessions[col] = pd.to_datetime(times, utc=True)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid session timestamps') from exc
    if sessions.empty or not sessions.open.is_monotonic_increasing or sessions.open.duplicated().any() or (sessions.close <= sessions.open).any():
        raise ValueError('Sessions must be ordered, unique and nonempty')
    sessions['date'] = sessions.open.dt.tz_convert('America/New_York').dt.date
    if sessions.date.duplicated().any() or sessions.date.iloc[0] > start or sessions.date.iloc[-1] <= end:
        raise ValueError('Session calendar must cover the start and include one session after the end date')
    if any(sessions.close.dt.tz_convert('America/New_York').dt.date != sessions.date):
        raise ValueError('Each session must open and close on the same New York date')
    if any(sessions.open.iloc[i] <= sessions.close.iloc[i-1] for i in range(1, len(sessions))):
        raise ValueError('Sessions must not overlap')
    dividends = {}
    if manifest['corporate_actions'] == 'cash_dividends_only':
        events = read_snapshot('dividends.csv')
        if not {'symbol', 'ex_date', 'pay_date', 'amount'} <= set(events.columns):
            raise ValueError('Dividends require symbol, ex_date, pay_date and amount')
        for event in events.itertuples():
            try:
                symbol = str(event.symbol).upper()
                ex_date, pay_date = date.fromisoformat(str(event.ex_date)), date.fromisoformat(str(event.pay_date))
                amount = float(event.amount)
            except (TypeError, ValueError) as exc:
                raise ValueError('Invalid dividend event') from exc
            if symbol not in [*symbols, 'SPY'] or ex_date not in set(sessions.date) or pay_date <= ex_date or not math.isfinite(amount) or amount <= 0 or (symbol, ex_date) in dividends:
                raise ValueError('Invalid or duplicate dividend event')
            if start <= ex_date <= end and pay_date <= end:
                raise ValueError('Dividend payment during the backtest is not supported')
            dividends[(symbol, ex_date)] = (amount, pay_date)
    cutoff = pd.Timestamp(end + timedelta(days=1), tz='America/New_York').tz_convert('UTC')
    frames = {}
    for symbol in [*symbols, 'SPY']:
        frame = read_snapshot(f'{symbol}.csv')
        frames[symbol] = validate_candles(frame, '60m', cutoff, float('inf')).set_index('timestamp')
    allowed = set()
    for session in sessions.itertuples():
        if session.date > end:
            break
        expected = pd.date_range(session.open, session.close, freq='h', inclusive='left')
        allowed.update(expected)
        for symbol, frame in frames.items():
            if session.date >= start and not expected.isin(frame.index).all():
                raise ValueError(f'Missing session candles for {symbol} on {session.date}')
            if session.date < start and expected[-1] >= frame.index[0] and not expected.isin(frame.index).all():
                raise ValueError(f'Missing warmup candles for {symbol} on {session.date}')
    for symbol, frame in frames.items():
        if not frame.index.isin(allowed).all():
            raise ValueError(f'Candles for {symbol} do not match the supplied session calendar')
    # The last session bar can be shorter than an hour; wait for its nominal hour to finish.
    weeks = {}
    for i, session in enumerate(sessions.itertuples()):
        weeks[session.date.isocalendar()[:2]] = i
    review = {i for i in weeks.values() if start <= sessions.date.iloc[i] <= end and i + 1 < len(sessions) and sessions.date.iloc[i+1] <= end}
    if not review:
        raise ValueError('No completed weekly review with an execution session in the date range')
    for i in review:
        last_bar = pd.date_range(sessions.open.iloc[i], sessions.close.iloc[i], freq='h', inclusive='left')[-1]
        decision_time = last_bar + pd.Timedelta(hours=1)
        if decision_time >= sessions.open.iloc[i+1]:
            raise ValueError('Execution must occur after the completed decision candle')
        if any(len(frame.loc[:last_bar]) < 50 for frame in frames.values()):
            raise ValueError('At least 50 completed warmup candles are required')
    load_dotenv()
    model = args.model or os.getenv('SYSTEMONE_MODEL', 'jev-latest')
    fee, slip = args.fee_bps / 10000, args.slippage_bps / 10000
    initial = 10000.0
    cash = {s: initial / len(symbols) for s in symbols}
    shares = {s: 0.0 for s in symbols}
    decisions, trades, curve = [], [], []
    pending = {}
    resolved_model = None
    retries = 0
    spy_shares = 0.0
    receivables = {s: 0.0 for s in [*symbols, 'SPY']}
    dividend_ledger = []
    first_execution = None
    execution_values = []
    with tempfile.TemporaryDirectory(prefix='fasttrade-backtest-') as directory:
        paths = {}
        for symbol in symbols:
            paths[symbol] = Path(directory) / f'{symbol}.csv'
            frames[symbol].reset_index().to_csv(paths[symbol], index=False)
        for i, session in enumerate(sessions.itertuples()):
            if not start <= session.date <= end:
                continue
            for symbol in [*symbols, 'SPY']:
                event = dividends.get((symbol, session.date))
                held = spy_shares if symbol == 'SPY' else shares[symbol]
                if event and held:
                    amount, pay_date = event
                    receivables[symbol] += held * amount
                    dividend_ledger.append({'symbol': symbol, 'ex_date': str(session.date), 'pay_date': str(pay_date), 'shares': held, 'amount_per_share': amount, 'receivable': held * amount})
            if pending:
                portfolio_open = sum(cash[s] + shares[s] * float(frames[s].loc[session.open, 'open']) + receivables[s] for s in symbols)
                spy_open = spy_shares * float(frames['SPY'].loc[session.open, 'open']) + receivables['SPY'] if first_execution is not None else initial
                execution_values.append((session.date, portfolio_open, spy_open))
                if first_execution is None:
                    first_execution = session.open
                    spy_price = float(frames['SPY'].loc[session.open, 'open']) * (1 + slip)
                    spy_shares = initial / (spy_price * (1 + fee))
                for symbol, action in pending.items():
                    open_price = float(frames[symbol].loc[session.open, 'open'])
                    if action == 'BUY' and cash[symbol] > 0:
                        price = open_price * (1 + slip)
                        quantity = cash[symbol] / (price * (1 + fee))
                        commission = quantity * price * fee
                        shares[symbol], cash[symbol] = quantity, 0.0
                    elif action == 'SELL' and shares[symbol] > 0:
                        price = open_price * (1 - slip)
                        quantity = shares[symbol]
                        commission = quantity * price * fee
                        cash[symbol], shares[symbol] = quantity * price - commission, 0.0
                    else:
                        continue
                    trades.append({'symbol': symbol, 'time': session.open.isoformat(), 'action': action, 'reference_open': open_price, 'fill_price': price, 'shares': quantity, 'fee': commission})
                pending = {}
            last_bar = pd.date_range(session.open, session.close, freq='h', inclusive='left')[-1]
            equity = sum(cash[s] + shares[s] * float(frames[s].loc[last_bar, 'close']) + receivables[s] for s in symbols)
            curve.append({'date': str(session.date), 'equity': equity})
            if i in review:
                decision_time = last_bar + pd.Timedelta(hours=1)
                print(f'Backtest review {session.date}: {len(symbols)} model decisions', file=sys.stderr, flush=True)
                for symbol in symbols:
                    analysis = cli.parser().parse_args(['analyze', symbol, '--timeframe', '60m', '--csv', str(paths[symbol]), '--as-of', decision_time.isoformat()])
                    analysis.systemone_model = model
                    analysis.forecast = getattr(args, 'forecast', False)
                    for attempt in range(3):
                        try:
                            result = cli.run(analysis)
                            break
                        except ValueError as exc:
                            if str(exc) != 'Invalid SystemOne response' or attempt == 2:
                                raise
                            retries += 1
                            print(f'Retrying invalid model response for {symbol} ({attempt + 1}/2)', file=sys.stderr, flush=True)
                    decision = result['decision']
                    if resolved_model is not None and decision['model'] != resolved_model:
                        raise ValueError('Model version changed during the backtest')
                    resolved_model = decision['model']
                    pending[symbol] = decision['action']
                    state_bytes = json.dumps(result['market'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
                    decisions.append({'symbol': symbol, 'decision_time': decision_time.isoformat(), 'execution_time': sessions.open.iloc[i+1].isoformat(), 'candle_timestamp': result['market']['candle_timestamp'], 'state_sha256': hashlib.sha256(state_bytes).hexdigest(), 'action': decision['action'], 'probabilities': decision['probabilities'], 'confidence': decision['confidence'], 'model': decision['model'], 'latency_ms': decision['latency_us'] / 1000, 'usage': decision.get('usage')})
                    if analysis.forecast:
                        decisions[-1]['forecast'] = result['market']['forecast']
    if not curve or first_execution is None:
        raise ValueError('No executable decisions in the requested period')
    ending = curve[-1]['equity']
    spy_ending = spy_shares * float(frames['SPY'].loc[last_bar, 'close']) + receivables['SPY']
    peak, drawdown = initial, 0.0
    for point in curve:
        peak = max(peak, point['equity'])
        drawdown = min(drawdown, point['equity'] / peak - 1)
    usage_complete = retries == 0 and all(d['usage'] is not None and d['usage']['output_tokens'] is not None for d in decisions)
    excess = [b[1] / a[1] - b[2] / a[2] for a, b in zip(execution_values, execution_values[1:])]
    consecutive = all((b[0] - timedelta(days=b[0].weekday()) - (a[0] - timedelta(days=a[0].weekday()))).days == 7 for a, b in zip(execution_values, execution_values[1:]))
    significance = {'method': 'stationary bootstrap', 'observations': len(excess), 'mean_weekly_excess_return': float(np.mean(excess)) if excess and consecutive else None,
                    'confidence_interval': None, 'p_value': None, 'variants_tested': None,
                    'block_length': max(2, math.ceil(len(excess) ** (1 / 3))) if len(excess) >= 2 and consecutive else None,
                    'draws': 2000, 'seed': 0, 'confidence_level': .95, 'final_partial_period_excluded': True,
                    'reason': 'execution periods are not consecutive calendar weeks' if not consecutive else 'fewer than two complete periods' if len(excess) < 2 else None}
    if len(excess) >= 2 and consecutive:
        significance['confidence_interval'] = np.percentile(stationary_bootstrap_means(excess), [2.5, 97.5]).tolist()
    sensitivity = {'method': 'fixed-decision flat-cost replay', 'scenarios': [replay_costs(sessions, frames, symbols, decisions, dividends, start, end, initial, fee, slip, factor) for factor in (.5, 1, 2)]}
    impact_sensitivity = {'method': 'fixed-decision square-root impact replay', 'scenarios': [replay_costs(sessions, frames, symbols, decisions, dividends, start, end, initial, fee, slip, factor, impact=True) for factor in (.5, 1, 2)]}
    return {'start': str(start), 'end': str(end), 'symbols': symbols, 'model_requested': model, 'model_resolved': resolved_model, 'model_retries': retries, 'mean_model_latency_ms': round(sum(d['latency_ms'] for d in decisions) / len(decisions), 2), 'input_tokens_successful': sum(d['usage']['input_tokens'] for d in decisions if d['usage'] is not None), 'usage_complete': usage_complete, 'dataset_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'fee_bps': args.fee_bps, 'slippage_bps': args.slippage_bps, 'initial_balance': initial, 'first_execution_time': first_execution.isoformat(), 'ending_balance': round(ending, 2), 'return_pct': round((ending / initial - 1) * 100, 2), 'spy_ending_balance': round(spy_ending, 2), 'spy_return_pct': round((spy_ending / initial - 1) * 100, 2), 'max_drawdown_pct': round(drawdown * 100, 2), 'total_fees': round(sum(t['fee'] for t in trades), 2), 'dividend_receivables': round(sum(receivables[s] for s in symbols), 2), 'spy_dividend_receivables': round(receivables['SPY'], 2), 'dividends': dividend_ledger, 'decisions': decisions, 'trades': trades, 'equity_curve': curve, 'significance': significance, 'cost_sensitivity': sensitivity, 'impact_sensitivity': impact_sensitivity, 'overfitting': {'pbo': None, 'fold_distribution': None, 'reason': 'requires aligned returns from multiple variants'}, 'limitations': 'The input manifest is an unverified attestation; corporate actions, model training knowledge, survivorship and hindsight selection are not verified. Only cash dividends payable after the test are supported; no split accounting. Primary fees and slippage are flat assumptions; the separate impact replay uses uncalibrated liquidity and cost assumptions and does not model spread or partial fills.'}
