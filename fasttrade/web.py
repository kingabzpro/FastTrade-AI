"""Local-only browser interface for the existing signal engine."""
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .cli import parser, run as run_analysis
from .market import INTERVALS

ROOT = Path(__file__).resolve().parent
PORT = 8765
app = FastAPI(title='FastTrade AI API', version='0.1.0', description='Local signal-only market research API. It never places orders.')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    symbol: str = Field(min_length=1, max_length=20, pattern=r'^[A-Za-z0-9.\^=\-]+$', description='US stock ticker symbol')
    timeframe: Literal['1m', '2m', '5m', '15m', '30m', '60m'] = '5m'
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


class DecisionResponse(BaseModel):
    action: Literal['BUY', 'HOLD', 'SELL']
    probabilities: dict[str, float]
    confidence: float
    model: str
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


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        return JSONResponse({'error': 'Local access only'}, status_code=403)
    origin = request.headers.get('origin')
    if origin:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get('host'):
            return JSONResponse({'error': 'Origin not allowed'}, status_code=403)
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@app.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/health', tags=['System'])
def health():
    """Report local server availability, without checking market or model connectivity."""
    return {'status': 'ok', 'mode': 'signal-only'}


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
