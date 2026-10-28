import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from .market import yahoo, validate_candles, INTERVALS
from .indicators import features
from .decisions import predict
from .risk import evaluate


def parser():
    p = argparse.ArgumentParser(prog='fasttrade', description='Research-only signals; no orders are placed.')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ('analyze', 'tui'):
        a = sub.add_parser(command, help='Analyze completed candles' if command == 'analyze' else 'Open the interactive dashboard')
        add_analysis_arguments(a, optional_symbol=command == 'tui')
    return p


def add_analysis_arguments(a, optional_symbol=False):
    a.add_argument('symbol', nargs='?' if optional_symbol else None, default='NVDA' if optional_symbol else None)
    a.add_argument('--timeframe', choices=INTERVALS, default='5m')
    a.add_argument('--csv', help='CSV with timezone-aware candle start timestamps')
    a.add_argument('--as-of', help='Timezone-aware evaluation time; defaults to now')
    a.add_argument('--session-timezone', help='Exchange timezone; defaults to America/New_York for CSV')
    a.add_argument('--max-age-seconds', type=float, help='Maximum age since candle close; defaults to two intervals')
    a.add_argument('--spread', type=float)
    a.add_argument('--liquidity', type=float)
    for name in ('position', 'equity', 'exposure', 'daily-loss', 'quantity', 'max-position', 'max-exposure', 'max-daily-loss', 'min-confidence'):
        a.add_argument('--' + name, type=float)
    for name in ('trade-count', 'max-trades'):
        a.add_argument('--' + name, type=int)


def run(args):
    started = time.perf_counter_ns()
    load_dotenv()
    as_of = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp(datetime.now(timezone.utc))
    if pd.isna(as_of) or as_of.tzinfo is None:
        raise ValueError('Evaluation time must include a timezone')
    max_age = args.max_age_seconds if args.max_age_seconds is not None else 2 * INTERVALS[args.timeframe]
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError('Maximum age must be a nonnegative finite number')
    if args.csv:
        frame, details, news = pd.read_csv(args.csv), {}, []
    else:
        frame, details, news = yahoo(args.symbol, args.timeframe, as_of)
        if args.as_of:
            # Current company details do not have historical availability timestamps.
            details = {k: v for k, v in details.items() if k == 'exchangeTimezoneName'}
    stale_preview = False
    try:
        frame = validate_candles(frame, args.timeframe, as_of, max_age)
    except ValueError as exc:
        if str(exc) != 'Latest completed candle is stale' or not getattr(args, 'allow_stale_preview', False):
            raise
        frame = validate_candles(frame, args.timeframe, as_of, float('inf'))
        as_of = frame.timestamp.iloc[-1] + pd.Timedelta(seconds=INTERVALS[args.timeframe])
        details = {k: v for k, v in details.items() if k == 'exchangeTimezoneName'}
        news = []
        stale_preview = True
    zone = args.session_timezone or details.get('exchangeTimezoneName') or 'America/New_York'
    indicator_start = time.perf_counter_ns()
    snapshot = features(frame, zone)
    indicator_ms = (time.perf_counter_ns() - indicator_start) / 1e6
    for name in ('spread', 'liquidity'):
        val = getattr(args, name)
        if val is not None and (not math.isfinite(val) or val < 0):
            raise ValueError(f'{name} must be nonnegative and finite')
        snapshot[name] = val
    state = {'symbol': args.symbol.upper(), 'timeframe': args.timeframe, 'candle_timestamp': frame.timestamp.iloc[-1].isoformat(), 'evaluation_time': as_of.isoformat(), 'features': snapshot, 'ticker_details': details, 'headlines': news}
    key = os.getenv('TYPESAFE_API_KEY')
    url = getattr(args, 'systemone_url', None) or os.getenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
    if not key and url.startswith('https://api.typesafe.ai/'):
        raise ValueError('TYPESAFE_API_KEY is missing')
    decision = predict(state, url, getattr(args, 'systemone_model', None) or os.getenv('SYSTEMONE_MODEL', 'jev-latest'), key)
    risk_args = {name: getattr(args, name) for name in ('position', 'equity', 'exposure', 'daily_loss', 'trade_count', 'quantity', 'max_position', 'max_exposure', 'max_daily_loss', 'max_trades', 'min_confidence')}
    risk_start = time.perf_counter_ns()
    reasons = evaluate(decision['action'], decision['confidence'], snapshot['price'], **risk_args)
    if stale_preview:
        reasons.append('Stale market data: research preview only')
    interpretations = [f"Price {'above' if snapshot['price'] > snapshot['vwap'] else 'at or below'} session VWAP", f"MACD {'above' if snapshot['macd'] > snapshot['macd_signal'] else 'at or below'} its signal", f"Volume {'above' if snapshot['volume_ratio'] > 1 else 'at or below'} 20-bar average"]
    risk_ms = (time.perf_counter_ns() - risk_start) / 1e6
    result = {'notice': 'Research only; model preferences are not probabilities of profitable trades.', 'market': state, 'decision': decision, 'indicator_interpretations': interpretations, 'risk_rejections': reasons, 'actionable_signal': decision['action'] if not reasons and decision['action'] != 'HOLD' else 'HOLD', 'stale_preview': stale_preview}
    if getattr(args, 'include_chart', False):
        chart = frame.copy()
        for n in (9, 21, 50):
            chart[f'ema_{n}'] = chart.close.ewm(span=n, adjust=False, min_periods=n).mean()
        session = chart.timestamp.dt.tz_convert(zone).dt.date
        typical_volume = ((chart.high + chart.low + chart.close) / 3) * chart.volume
        chart['vwap'] = typical_volume.groupby(session).cumsum() / chart.volume.groupby(session).cumsum().replace(0, float('nan'))
        chart = chart.tail(200).copy()
        chart['timestamp'] = chart.timestamp.map(lambda t: t.isoformat())
        result['candles'] = chart.astype(object).where(pd.notna(chart), None).to_dict('records')
        result['portfolio'] = risk_args
        result['source'] = 'CSV' if args.csv else 'Yahoo Finance'
    result['timings_ms'] = {'indicators': indicator_ms, 'model': decision['latency_us'] / 1000, 'risk': risk_ms, 'total': (time.perf_counter_ns() - started) / 1e6}
    return result


def dashboard():
    from .web import serve
    serve()
    return 0


def main():
    args = parser().parse_args()
    if args.command == 'tui':
        from .tui import FastTradeApp
        FastTradeApp(args).run()
        return 0
    try:
        print(json.dumps(run(args), indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'actionable_signal': 'HOLD', 'error': str(exc)}), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
