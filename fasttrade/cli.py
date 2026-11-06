import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from dotenv import load_dotenv
from .market import alpaca, yahoo, cached_market, validate_candles, INTERVALS
from .indicators import features, cached_features, rule_signal
from .decisions import predict, provider_config
from .risk import evaluate


def parser():
    p = argparse.ArgumentParser(prog='ft', description='Research-only signals; no orders are placed.')
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('analyze', help='Analyze completed candles')
    add_analysis_arguments(a)
    batch = sub.add_parser('analyze-batch', help='Analyze multiple tickers concurrently (signal only)', description='Each ticker may incur a paid model request. Results are signal only; no orders are placed.')
    batch.add_argument('symbols', nargs='+', help='US stock ticker symbols (up to 50)')
    batch.add_argument('--timeframe', choices=INTERVALS, default='5m')
    batch.add_argument('--source', choices=('alpaca', 'yahoo'), default='alpaca', help='Market data provider (default: alpaca)')
    batch.add_argument('--decision-provider', choices=('jev', 'liquid', 'vllm', 'subq', 'baseten'), default='jev')
    batch.add_argument('--concurrency', type=int, choices=range(1, 6), default=3, metavar='1-5')
    b = sub.add_parser('benchmark', help='Run a weekly historical model benchmark')
    b.add_argument('--start', required=True, help='First decision date (YYYY-MM-DD)')
    b.add_argument('--end', required=True, help='Last valuation date (YYYY-MM-DD, inclusive)')
    b.add_argument('--symbols', default='AAPL,MSFT,NVDA', help='Comma-separated symbols (default: AAPL,MSFT,NVDA)')
    b.add_argument('--model', default=None, help='SystemOne model; defaults to SYSTEMONE_MODEL')
    t = sub.add_parser('backtest', help='Backtest against a validated frozen dataset')
    t.add_argument('--data-dir', required=True, help='Directory containing manifest, sessions, and raw hourly CSVs')
    t.add_argument('--start', required=True, help='First date, YYYY-MM-DD')
    t.add_argument('--end', required=True, help='Last date, YYYY-MM-DD')
    t.add_argument('--symbols', required=True, help='Comma-separated US ticker symbols')
    t.add_argument('--model', default=None, help='SystemOne model; defaults to SYSTEMONE_MODEL')
    t.add_argument('--fee-bps', type=float, default=5, help='Commission in basis points per side (default 5)')
    t.add_argument('--slippage-bps', type=float, default=10, help='Slippage in basis points per side (default 10)')
    t.add_argument('--forecast', action='store_true', help='Include an unvalidated local next-bar forecast')
    t.add_argument('--sizing-vol-target', type=float, help='Opt in to a risk-checked annual volatility-target sizing comparison')
    for name in ('max-position', 'max-exposure', 'max-daily-loss', 'min-confidence'):
        t.add_argument('--sizing-' + name, type=float)
    t.add_argument('--sizing-max-trades', type=int)
    t.add_argument('--sizing-max-drawdown', type=float, help='Opt in to a simulated drawdown exposure throttle, as a fraction in (0, 1]')
    t.add_argument('--output', required=True, help='New JSON report path')
    c = sub.add_parser('calibrate', help='Fit decision-probability calibration from a frozen backtest report (research only)')
    c.add_argument('--report', required=True, help='Backtest report JSON with per-decision probabilities')
    c.add_argument('--data-dir', required=True, help='Frozen dataset directory used by that backtest')
    c.add_argument('--train-frac', type=float, default=0.75, help='Chronological fraction used for fitting (default 0.75)')
    c.add_argument('--alpha', type=float, default=0.1, help='Conformal miscoverage level (default 0.1)')
    c.add_argument('--fee-bps', type=float, default=5, help='Commission in basis points per side, for the return gate (default 5)')
    c.add_argument('--slippage-bps', type=float, default=10, help='Slippage in basis points per side, for the return gate (default 10)')
    c.add_argument('--output', help='New calibration artifact JSON path for analyze --calibration')
    r = sub.add_parser('build-regimes', help='Build an offline historical regime corpus')
    r.add_argument('--report', required=True, help='Backtest report JSON')
    r.add_argument('--data-dir', required=True, help='Matching frozen dataset directory')
    r.add_argument('--output', required=True, help='New corpus JSON path')
    return p


def add_analysis_arguments(a):
    a.add_argument('symbol')
    a.add_argument('--timeframe', choices=INTERVALS, default='5m')
    source = a.add_mutually_exclusive_group()
    source.add_argument('--csv', help='CSV with timezone-aware candle start timestamps')
    source.add_argument('--source', choices=('alpaca', 'yahoo'), default='alpaca', help='Market data provider (default: alpaca)')
    a.add_argument('--as-of', help='Timezone-aware evaluation time; defaults to now')
    a.add_argument('--session-timezone', help='Exchange timezone; defaults to America/New_York for CSV')
    a.add_argument('--max-age-seconds', type=float, help='Maximum age since candle close; defaults to two intervals')
    a.add_argument('--spread', type=float)
    a.add_argument('--liquidity', type=float)
    a.add_argument('--decision-provider', choices=('jev', 'liquid', 'vllm', 'subq', 'baseten'), default='jev')
    a.add_argument('--calibration', help='Calibration artifact JSON from ft calibrate; confidence is then reported and gated calibrated')
    a.add_argument('--require-certified-edge', action='store_true', help='Block actionable signals unless the artifact certifies aggregate net-of-cost edge on its test window')
    a.add_argument('--edge-horizon', choices=('weekly-next-execution',), help='Declare the holding horizon used by the edge gate')
    a.add_argument('--regime-data', help='Opt in to a matching historical regime corpus JSON')
    a.add_argument('--forecast', action='store_true', help='Include an unvalidated local next-bar forecast')
    for name in ('position', 'equity', 'exposure', 'daily-loss', 'quantity', 'max-position', 'max-exposure', 'max-daily-loss', 'min-confidence'):
        a.add_argument('--' + name, type=float)
    for name in ('trade-count', 'max-trades'):
        a.add_argument('--' + name, type=int)


def run(args):
    started = time.perf_counter_ns()
    load_dotenv()
    if getattr(args, 'regime_data', None) and getattr(args, 'calibration', None):
        raise ValueError('Regime evidence cannot be combined with calibration')
    if getattr(args, 'forecast', False) and getattr(args, 'calibration', None):
        raise ValueError('Forecast evidence cannot be combined with calibration')
    as_of = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp(datetime.now(timezone.utc))
    if pd.isna(as_of) or as_of.tzinfo is None:
        raise ValueError('Evaluation time must include a timezone')
    max_age = args.max_age_seconds if args.max_age_seconds is not None else 2 * INTERVALS[args.timeframe]
    if not math.isfinite(max_age) or max_age < 0:
        raise ValueError('Maximum age must be a nonnegative finite number')
    if args.csv:
        frame, details, news = pd.read_csv(args.csv), {}, []
        snapshot_cutoff = as_of
    else:
        provider = yahoo if args.source == 'yahoo' else alpaca
        frame, details, news, snapshot_cutoff = cached_market(provider, args.symbol, args.timeframe, as_of, max_age=max_age, live=args.as_of is None, report_cutoff=True)
        if args.as_of:
            # Current provider metadata and headlines lack verifiable historical availability.
            details = {k: v for k, v in details.items() if k == 'exchangeTimezoneName'}
            news = []
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
    snapshot = cached_features(features, frame, zone, INTERVALS[args.timeframe])
    indicator_ms = (time.perf_counter_ns() - indicator_start) / 1e6
    for name in ('spread', 'liquidity'):
        val = getattr(args, name)
        if val is not None and (not math.isfinite(val) or val < 0):
            raise ValueError(f'{name} must be nonnegative and finite')
        snapshot[name] = val
    state = {'symbol': args.symbol.upper(), 'timeframe': args.timeframe, 'candle_timestamp': frame.timestamp.iloc[-1].isoformat(), 'evaluation_time': as_of.isoformat(), 'features': snapshot, 'ticker_details': details, 'headlines': news}
    if getattr(args, 'forecast', False):
        from .forecast import next_bar
        state['forecast'] = next_bar(frame.close)
    if not args.csv and args.as_of is None and not stale_preview:
        state['market_snapshot_cutoff'] = snapshot_cutoff.isoformat()
    if getattr(args, 'regime_data', None):
        from .regimes import retrieve
        with open(args.regime_data, encoding='utf-8') as handle:
            corpus = json.load(handle)
        evidence = retrieve(corpus, snapshot, as_of, timeframe=args.timeframe)
        if evidence is not None:
            state['regime_evidence'] = evidence
    provider = getattr(args, 'decision_provider', 'jev')
    if provider != 'jev' and getattr(args, 'calibration', None):
        raise ValueError('Calibration artifacts are only supported with Jev')
    key = os.getenv('TYPESAFE_API_KEY')
    url = getattr(args, 'systemone_url', None) or os.getenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
    if provider != 'jev':
        url, model, key = provider_config(provider)
    else:
        model = getattr(args, 'systemone_model', None) or os.getenv('SYSTEMONE_MODEL', 'jev-latest')
    if provider == 'jev' and not key and url.startswith('https://api.typesafe.ai/'):
        raise ValueError('TYPESAFE_API_KEY is missing')
    if getattr(args, 'require_certified_edge', False) and not getattr(args, 'calibration', None):
        raise ValueError('--require-certified-edge requires --calibration')
    calibration = None
    if getattr(args, 'calibration', None):
        from .calibration import apply_isotonic, apply_temperature, conformal_interval, load_artifact, valid_calibration_edge, validate_artifact_time
        calibration = load_artifact(args.calibration)
        validate_artifact_time(calibration, as_of)
    decision = predict(state, url, model, key)
    decision['provider'] = provider
    if calibration is not None and decision['action'] == 'BUY':
        calibrated = float(apply_temperature(decision['probabilities'][decision['action']], calibration['temperature']))
        decision['calibrated_confidence'] = round(calibrated, 6)
        decision['calibration'] = {'method': 'temperature', 'temperature': calibration['temperature'], 'at_bound': calibration.get('at_bound', False), 'fit_target': calibration['fit']['target'], 'fit_horizon': calibration['fit']['horizon'], 'train_end': calibration['train_window']['end'], 'note': 'Fitted on backtest outcomes; research label, not advice'}
    confidence = decision.get('calibrated_confidence', decision['confidence'])
    risk_args = {name: getattr(args, name) for name in ('position', 'equity', 'exposure', 'daily_loss', 'trade_count', 'quantity', 'max_position', 'max_exposure', 'max_daily_loss', 'max_trades', 'min_confidence')}
    risk_start = time.perf_counter_ns()
    risk_complete = all(value is not None for value in risk_args.values())
    reasons = evaluate(decision['action'], confidence, snapshot['price'], **risk_args) if any(value is not None for value in risk_args.values()) else []
    if calibration is not None and decision['action'] == 'BUY':
        # Per-trade direction and expected-return numbers are diagnostics; the
        # certifiable object is the aggregate net-of-cost edge on the test window.
        result_extra = {}
        quantile = calibration['conformal']['quantile']
        interval = conformal_interval(decision['calibrated_confidence'], quantile) if quantile is not None else [0.0, 1.0]
        result_extra['conformal_interval'] = [round(interval[0], 6), round(interval[1], 6)]
        returns_gate = calibration.get('returns')
        if returns_gate:
            expected = float(apply_isotonic(decision['probabilities']['BUY'], returns_gate['blocks']))
            lower = (expected - returns_gate['quantile']) if returns_gate['quantile'] is not None else None
            result_extra['expected_return'] = round(expected, 6)
            result_extra['return_interval_lower'] = (round(lower, 8) if lower is not None else None)
        if getattr(args, 'require_certified_edge', False):
            edge = calibration.get('edge') or {}
            result_extra['edge_certified'] = valid_calibration_edge(calibration, decision['model'], args.timeframe, as_of, getattr(args, 'edge_horizon', None), state['symbol'])
            if not result_extra['edge_certified']:
                suffix = f" ({edge['reason']})" if edge.get('reason') else ''
                reasons.append('Model edge is not certified by the calibration artifact' + suffix)
    else:
        result_extra = {}
        if calibration is not None:
            reasons.append('Calibration is only for BUY decisions')
            if getattr(args, 'require_certified_edge', False):
                result_extra['edge_certified'] = False
    if stale_preview:
        reasons.append('Stale market data: research preview only')
    rule = rule_signal(snapshot)
    comparison = {'signal': rule, 'agreement': 'no_signal' if rule is None else 'agree' if rule == decision['action'] else 'disagree'}
    interpretations = [f"Price {'above' if snapshot['price'] > snapshot['vwap'] else 'at or below'} session VWAP", f"MACD {'above' if snapshot['macd'] > snapshot['macd_signal'] else 'at or below'} its signal", f"Volume {'above' if snapshot['volume_ratio'] > 1 else 'at or below'} 20-bar average"]
    risk_ms = (time.perf_counter_ns() - risk_start) / 1e6
    result = {'market': state, 'model_signal': decision['action'], 'decision': decision, 'rule_comparison': comparison, 'indicator_interpretations': interpretations, 'risk_rejections': reasons, 'actionable_signal': (decision['action'] if not reasons else 'HOLD') if risk_complete else None, 'stale_preview': stale_preview, **result_extra}
    if getattr(args, 'forecast', False):
        from .forecast import disagreement
        result['forecast_disagreement'] = disagreement(frame.close.iloc[-2], frame.close.iloc[-1], state['forecast']['quantiles']['p50'])
    result['source'] = 'CSV' if args.csv else 'Yahoo Finance' if args.source == 'yahoo' else 'Alpaca IEX'
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
    result['timings_ms'] = {'indicators': indicator_ms, 'model': decision['latency_us'] / 1000, 'risk': risk_ms, 'total': (time.perf_counter_ns() - started) / 1e6}
    return result


def run_batch(args):
    symbols = [symbol.upper() for symbol in args.symbols]
    if len(symbols) > 50 or any(not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', symbol) for symbol in symbols):
        raise ValueError('Provide at most 50 valid ticker symbols')
    if len(set(symbols)) != len(symbols):
        raise ValueError('Duplicate ticker symbols are not allowed')
    load_dotenv()

    def analyze(symbol):
        analysis_args = parser().parse_args(['analyze', symbol, '--timeframe', args.timeframe, '--source', args.source, '--decision-provider', args.decision_provider])
        analysis_args.allow_stale_preview = True
        try:
            return {'symbol': symbol, 'result': run(analysis_args)}
        except (ValueError, OSError, KeyError) as exc:
            return {'symbol': symbol, 'error': str(exc), 'actionable_signal': None}
        except Exception:
            return {'symbol': symbol, 'error': 'Unexpected analysis failure', 'actionable_signal': None}

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(analyze, symbols))
    usage = [item.get('result', {}).get('decision', {}).get('usage') for item in results]
    summary = {'input_tokens_reported': sum(item['input_tokens'] for item in usage if item is not None),
               'output_tokens_reported': sum(item['output_tokens'] if item['output_tokens'] is not None else item.get('output_tokens_last_attempt', 0) or 0 for item in usage if item is not None),
               'complete': all(item is not None and item['output_tokens'] is not None for item in usage)}
    summary['status'] = ('Token information available' if summary['complete'] else
                         'Token information unavailable for some analyses' if any(item is not None for item in usage) else
                         'Token information unavailable')
    return {'results': results, 'usage': summary}


def dashboard():
    if len(sys.argv) > 1:
        return main()
    from .web import serve
    serve()
    return 0


def main():
    args = parser().parse_args()
    if args.command == 'analyze' and not args.csv and not args.as_of:
        args.allow_stale_preview = True
    try:
        if args.command == 'analyze-batch':
            result = run_batch(args)
            print(json.dumps(result, indent=2, allow_nan=False))
            return int(any('error' in item for item in result['results']))
        if args.command == 'benchmark':
            from .benchmark import run as run_benchmark
            result = run_benchmark(args)
        elif args.command == 'build-regimes':
            from .regimes import build_corpus
            corpus = build_corpus(args.report, args.data_dir)
            with open(args.output, 'x', encoding='utf-8') as output:
                json.dump(corpus, output, indent=2, allow_nan=False)
                output.write('\n')
            result = {'corpus': args.output, 'created_at': corpus['created_at'], 'records': len(corpus['records'])}
        elif args.command == 'calibrate':
            from .calibration import run_calibration
            result = run_calibration(args)
            if args.output:
                with open(args.output, 'x', encoding='utf-8') as output:
                    json.dump(result['artifact'], output, indent=2, allow_nan=False)
                    output.write('\n')
                result = {'artifact': args.output, **{key: result[key] for key in ('ledger', 'temperature', 'metrics_train', 'metrics_test', 'conformal')}}
        elif args.command == 'backtest':
            from .backtest import run as run_backtest
            result = run_backtest(args)
            with open(args.output, 'x', encoding='utf-8') as output:
                json.dump(result, output, indent=2, allow_nan=False)
                output.write('\n')
            result = {'report': args.output, **{key: result[key] for key in ('model_resolved', 'ending_balance', 'return_pct', 'spy_ending_balance', 'spy_return_pct', 'max_drawdown_pct', 'total_fees')}}
        else:
            result = run(args)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({'actionable_signal': None, 'error': str(exc)}), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
