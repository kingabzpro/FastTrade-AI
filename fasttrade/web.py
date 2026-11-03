"""Local-only browser interface for the existing signal engine."""
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import time, timedelta
import math
import os
from threading import Lock
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .cli import parser, run as run_analysis
from .decisions import predict
from .indicators import features, rule_signal
from .market import INTERVALS, alpaca, alpaca_news, validate_candles

ROOT = Path(__file__).resolve().parent
PORT = 8765


@asynccontextmanager
async def lifespan(app):
    from .batch_api import _recover
    await run_in_threadpool(_recover, AnalysisRequest, analyze)
    yield


app = FastAPI(title='FastTrade AI API', version='0.1.0', description='Local signal-only market research API. It never places orders.', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
_spy_cache = None
_spy_lock = Lock()
_spy_signals_cache = None
_spy_signals_lock = Lock()


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    symbol: str = Field(min_length=1, max_length=20, pattern=r'^[A-Za-z0-9.\^=\-]+$', description='US stock ticker symbol')
    timeframe: Literal['1m', '2m', '5m', '15m', '30m', '60m'] = '5m'
    source: Literal['alpaca', 'yahoo'] = 'alpaca'
    decision_provider: Literal['jev', 'liquid', 'vllm', 'subq', 'baseten'] = 'jev'
    sample: bool = Field(default=False, description='Use synthetic five-minute candles; portfolio actions are blocked')
    position: float | None = Field(default=None, ge=0)
    equity: float | None = Field(default=None, gt=0)
    exposure: float | None = Field(default=None, ge=0)
    daily_loss: float | None = Field(default=None, ge=0)
    trade_count: int | None = Field(default=None, ge=0)
    quantity: float | None = Field(default=None, gt=0)
    max_position: float | None = Field(default=None, ge=0)
    max_exposure: float | None = Field(default=None, ge=0)
    max_daily_loss: float | None = Field(default=None, ge=0)
    max_trades: int | None = Field(default=None, ge=0)
    min_confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator('timeframe')
    @classmethod
    def valid_timeframe(cls, value):
        if value not in INTERVALS:
            raise ValueError('Unsupported timeframe')
        return value


class SpySignalsRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')


class DecisionResponse(BaseModel):
    action: Literal['BUY', 'HOLD', 'SELL']
    probabilities: dict[str, float]
    confidence: float
    model: str
    provider: str = 'jev'
    latency_us: int
    usage: dict[str, int | None] | None = None


class RuleComparison(BaseModel):
    signal: Literal['BUY', 'SELL'] | None
    agreement: Literal['agree', 'disagree', 'no_signal']


class AnalysisResponse(BaseModel):
    market: dict[str, Any] = Field(description='Symbol, timeframe, candle timestamp, feature snapshot, ticker details, and headlines')
    model_signal: Literal['BUY', 'HOLD', 'SELL'] = Field(description='Model preference; not a risk-checked action')
    decision: DecisionResponse
    rule_comparison: RuleComparison = Field(description='Unvalidated indicator-based comparison; agreement does not establish accuracy')
    indicator_interpretations: list[str]
    risk_rejections: list[str]
    actionable_signal: Literal['BUY', 'HOLD', 'SELL'] | None = Field(description='Risk-checked signal, or null when portfolio and risk inputs are incomplete')
    stale_preview: bool
    candles: list[dict[str, Any]] = Field(description='Up to 200 completed OHLCV candles with EMA and VWAP values')
    portfolio: dict[str, float | int | None]
    source: str
    timings_ms: dict[str, float]


def spy_overview(timeframe, as_of):
    result = {'symbol': 'SPY', 'timeframe': timeframe, 'price': None, 'change_pct': None, 'change_candle_timestamp': None,
              'candle_timestamp': None, 'stale': False, 'news': [], 'news_status': 'unavailable'}
    try:
        frame, _, _ = alpaca('SPY', timeframe, as_of)
        try:
            frame = validate_candles(frame, timeframe, as_of, 2 * INTERVALS[timeframe])
        except ValueError as exc:
            if str(exc) != 'Latest completed candle is stale':
                raise
            frame = validate_candles(frame, timeframe, as_of, float('inf'))
            result['stale'] = True
        last_time = frame.timestamp.iloc[-1]
        result.update(price=float(frame.close.iloc[-1]), candle_timestamp=last_time.isoformat())
        prices = dict(zip(frame.timestamp, frame.close))
        for timestamp in reversed(frame.timestamp.tolist()):
            previous = prices.get(timestamp - timedelta(minutes=10))
            if previous is not None:
                result.update(change_pct=(float(prices[timestamp]) / float(previous) - 1) * 100,
                              change_candle_timestamp=timestamp.isoformat())
                break
    except (ValueError, TypeError, KeyError, IndexError):
        pass
    try:
        result['news'] = alpaca_news('SPY', as_of)
        result['news_status'] = 'available'
    except ValueError:
        pass
    return result


def outside_regular_hours(now):
    local = now.tz_convert('America/New_York')
    # shortcut: holidays and early closes are not modeled; use an exchange calendar for exact sessions.
    return local.weekday() >= 5 or not time(9, 30) <= local.time() < time(16)


def build_spy_signals(previous=None):
    import pandas as pd

    now = pd.Timestamp.now(tz='UTC')
    if (previous and previous.get('deterministic') in ('BUY', 'HOLD', 'SELL')
            and previous.get('model') in ('BUY', 'HOLD', 'SELL') and outside_regular_hours(now)):
        return previous.copy()
    result = {'deterministic': None, 'model': None, 'stale': False,
              'candle_timestamp': None, 'market_error': False, 'model_error': False}
    try:
        frame, details, news = alpaca('SPY', '10m', now)
        try:
            frame = validate_candles(frame, '10m', now, 2 * INTERVALS['10m'])
        except ValueError as exc:
            if str(exc) != 'Latest completed candle is stale':
                raise
            frame = validate_candles(frame, '10m', now, float('inf'))
            result['stale'] = True
            now = frame.timestamp.iloc[-1] + timedelta(seconds=INTERVALS['10m'])
            details = {k: v for k, v in details.items() if k == 'exchangeTimezoneName'}
            news = []
        if (previous and previous.get('candle_timestamp') == frame.timestamp.iloc[-1].isoformat()
                and previous.get('deterministic') in ('BUY', 'HOLD', 'SELL')
                and previous.get('model') in ('BUY', 'HOLD', 'SELL')):
            return previous.copy()
        snapshot = features(frame, details.get('exchangeTimezoneName') or 'America/New_York')
        values = [snapshot[k] for k in ('ema_9', 'ema_21', 'macd', 'macd_signal')]
        result['deterministic'] = (rule_signal(snapshot) or 'HOLD') if all(math.isfinite(v) for v in values) else None
        result['candle_timestamp'] = frame.timestamp.iloc[-1].isoformat()
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        result.update(market_error=True, model_error=True)
        return result
    state = {'symbol': 'SPY', 'timeframe': '10m', 'candle_timestamp': result['candle_timestamp'],
             'evaluation_time': now.isoformat(), 'features': snapshot, 'ticker_details': details, 'headlines': news}
    url = os.getenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
    key = os.getenv('TYPESAFE_API_KEY')
    try:
        if not key and url.startswith('https://api.typesafe.ai/'):
            raise ValueError('Missing model key')
        result['model'] = predict(state, url, os.getenv('SYSTEMONE_MODEL', 'jev-latest'), key)['action']
    except (ValueError, TypeError, KeyError, OverflowError):
        result['model_error'] = True
    return result


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        response = JSONResponse({'error': 'Local access only'}, status_code=403)
    elif origin := request.headers.get('origin'):
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get('host'):
            response = JSONResponse({'error': 'Origin not allowed'}, status_code=403)
        else:
            response = await call_next(request)
    else:
        response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health', tags=['System'])
def health():
    """Report local server availability, without checking market or model connectivity."""
    return {'status': 'ok', 'mode': 'signal-only'}


@app.get('/api/providers', tags=['System'])
def configured_providers():
    """List configured providers without exposing their credentials."""
    from dotenv import load_dotenv
    from .decisions import PROVIDERS

    load_dotenv()
    market = (['alpaca'] if os.getenv('ALPACA_API_KEY') and os.getenv('ALPACA_SECRET_KEY') else []) + ['yahoo']
    url = os.getenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
    decision = (['jev'] if os.getenv('TYPESAFE_API_KEY') or (url and not url.startswith('https://api.typesafe.ai/')) else [])
    decision += [name for name, (key, _, _) in PROVIDERS.items() if os.getenv(key)]
    return {'market': market, 'decision': decision}


@app.get('/api/market-overview', tags=['Market'])
def market_overview():
    """Return cached SPY context without making a model request."""
    import pandas as pd
    from dotenv import load_dotenv

    global _spy_cache
    with _spy_lock:
        now = pd.Timestamp.now(tz='UTC')
        if _spy_cache is None or monotonic() - _spy_cache[0] >= 600:
            load_dotenv()
            result = spy_overview('5m', now)
            if result.get('price') is not None or result.get('news_status') == 'available':
                _spy_cache = (monotonic(), result)
            else:
                _spy_cache = None
        else:
            result = _spy_cache[1]
        result = result.copy()
        if result.get('candle_timestamp'):
            close_time = pd.Timestamp(result['candle_timestamp']) + pd.Timedelta(seconds=INTERVALS['5m'])
            result['stale'] = (now - close_time).total_seconds() > 2 * INTERVALS['5m']
        return result


@app.post('/api/spy-signals', tags=['Analysis'])
def spy_signals(body: SpySignalsRequest):
    """Run one SPY model request per 10-minute cache window, without portfolio inputs."""
    import pandas as pd
    from dotenv import load_dotenv

    global _spy_signals_cache
    with _spy_signals_lock:
        if _spy_signals_cache is None or monotonic() - _spy_signals_cache[0] >= 600:
            load_dotenv()
            result = build_spy_signals(_spy_signals_cache[1] if _spy_signals_cache else None)
            _spy_signals_cache = (monotonic(), result)
        result = _spy_signals_cache[1].copy()
        if result.get('candle_timestamp'):
            close_time = pd.Timestamp(result['candle_timestamp']) + pd.Timedelta(seconds=INTERVALS['10m'])
            result['stale'] = (pd.Timestamp.now(tz='UTC') - close_time).total_seconds() > 2 * INTERVALS['10m']
        return result


@app.post('/api/analyze', tags=['Analysis'], responses={200: {'model': AnalysisResponse, 'description': 'Model preference and risk-checked signal'}, 503: {'description': 'Market or model unavailable; no actionable signal'}})
async def analyze(body: AnalysisRequest):
    """Analyze completed candles; incomplete risk inputs produce signal-only results."""
    args = parser().parse_args(['analyze', body.symbol.upper(), '--timeframe', body.timeframe])
    for key, value in body.model_dump(exclude={'symbol', 'timeframe', 'sample'}).items():
        setattr(args, key, value)
    args.include_chart = True
    args.allow_stale_preview = True
    if body.sample:
        if body.timeframe != '5m':
            return JSONResponse({'error': 'The sample contains 5-minute candles only.', 'actionable_signal': None}, status_code=422)
        args.csv = str(ROOT.parent / 'data' / 'candles.csv')
        args.as_of = '2026-01-02T21:00:00Z'
    errors = {
        'Alpaca credentials are missing': 'Add ALPACA_API_KEY and ALPACA_SECRET_KEY to .env before analyzing.',
        'Alpaca credentials or data access rejected': 'Alpaca rejected the credentials or data access.',
        'Alpaca rate limit reached': 'Alpaca rate limit reached. Try again later.',
        'Alpaca market data timed out': 'Alpaca market data timed out. Check your connection or try again later.',
        'Alpaca market data request failed': 'Alpaca market data is unavailable. Check the symbol and connection.',
        'Alpaca returned no candles': 'Alpaca returned no candles for this symbol and timeframe.',
        'Unsupported Alpaca timeframe': 'This timeframe is not supported by Alpaca.',
        'Invalid Alpaca stock symbol': 'Enter a valid US stock ticker for Alpaca.',
        'Market data provider failed': 'Market data is unavailable. Check the symbol, timeframe, and connection.',
        'Market data provider timed out': 'Yahoo Finance timed out. Check your connection or try again later.',
        'Market data provider returned no candles': 'No candles returned. Check the symbol and timeframe.',
        'At least 50 completed candles required': 'Not enough candles. Try another timeframe.',
        'No completed candles': 'No completed candles are available.',
        'SystemOne request failed': 'Model request failed. Check your key, endpoint, and connection.',
        'SystemOne request failed: timeout': 'Model request timed out. Check the endpoint and retry.',
        'Invalid SystemOne response': 'The model returned an invalid response.',
        **{f'{key} is missing': f'Add {key} to .env before analyzing.' for key in ('LIQUID_API_KEY', 'VLLM_SR_API_KEY', 'SUBQ_API_KEY', 'BASETEN_API_KEY')},
        'TYPESAFE_API_KEY is missing': 'Add TYPESAFE_API_KEY to .env before analyzing.',
    }
    try:
        result = await run_in_threadpool(run_analysis, args)
        if body.sample:
            result['source'] = 'Synthetic sample CSV'
            result['risk_rejections'].append('Synthetic sample data: research preview only')
            if result['actionable_signal'] is not None:
                result['actionable_signal'] = 'HOLD'
        return result
    except ValueError as exc:
        return JSONResponse({'error': errors.get(str(exc), 'Analysis failed. Check data and settings.'), 'actionable_signal': None}, status_code=503)
    except Exception:
        return JSONResponse({'error': 'Analysis failed. Check data and settings.', 'actionable_signal': None}, status_code=503)


from .batch_api import make_batch_router
app.include_router(make_batch_router(AnalysisRequest, analyze))


def serve():
    import os
    import socket
    import threading
    import webbrowser
    from dotenv import load_dotenv
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    import uvicorn

    url = f'http://127.0.0.1:{PORT}/'
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', PORT))
        sock.listen(128)
    except OSError:
        sock.close()
        raise SystemExit(f'Port {PORT} is already in use. Close the existing server and try again.')
    load_dotenv()
    console = Console()
    info = Table.grid(padding=(0, 2))
    info.add_column(style='#aab7db', no_wrap=True)
    info.add_column()
    for label, value, style in (
        ('Dashboard', url, 'bold #dce5ff'),
        ('API docs', url + 'docs', '#dce5ff'),
        ('Model', os.getenv('SYSTEMONE_MODEL', 'jev-latest'), '#b7c4ff'),
        ('Market data', 'Alpaca IEX', '#b7c4ff'),
    ):
        info.add_row(label, Text(value, style=style))
    console.print()
    console.print(Panel(Group(
        Text.from_markup('[bold #b7c4ff]FT[/bold #b7c4ff]  [bold white]FASTTRADE AI[/bold white]'),
        Text('Research workspace  ·  Signal only', style='#aab7db'),
        Text(''),
        Text('YOUR WORKSPACE', style='bold #8799dc'),
        info,
        Text(''),
        Text.from_markup('[bold #b7c4ff]Ctrl+C[/bold #b7c4ff]  [#aab7db]to stop  ·  Providers checked on analysis[/#aab7db]'),
    ), box=box.ROUNDED, border_style='#7889d2', width=78, padding=(1, 2)))
    console.print()
    opener = threading.Timer(1.0, lambda: webbrowser.open(url))
    opener.daemon = True
    opener.start()
    try:
        uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=PORT, log_level='warning')).run(sockets=[sock])
    finally:
        opener.cancel()
        sock.close()
