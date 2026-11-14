import math
import os
import re
import copy
import hashlib
import json
import logging
import httpx
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import monotonic
import pandas as pd
from .resilience import CircuitOpen, pooled_get, request

INTERVALS = {'1m': 60, '2m': 120, '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '60m': 3600, '90m': 5400}
logger = logging.getLogger(__name__)
_market_cache = OrderedDict()
_market_cache_lock = Lock()


def validate_candles(frame, timeframe, as_of, max_age):
    if timeframe not in INTERVALS:
        raise ValueError('Unsupported timeframe')
    required = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    if not set(required).issubset(frame.columns):
        raise ValueError('Candles require timestamp, open, high, low, close and volume')
    frame = frame[required].copy()
    try:
        # Naive timestamps have no unambiguous session or freshness meaning.
        timestamps = [pd.Timestamp(x) for x in frame.timestamp]
        if any(pd.isna(x) or x.tzinfo is None for x in timestamps):
            raise ValueError('timestamps must include a timezone')
        frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
        for col in required[1:]:
            frame[col] = pd.to_numeric(frame[col], errors='raise')
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Invalid candle timestamp or number') from exc
    if frame.empty or not frame.timestamp.is_monotonic_increasing or frame.timestamp.duplicated().any():
        raise ValueError('Candles must be nonempty, ordered and unique')
    nums = frame[required[1:]]
    if not nums.map(math.isfinite).all().all() or (frame[['open','high','low','close']] <= 0).any().any() or (frame.volume < 0).any():
        raise ValueError('Invalid candle values')
    if ((frame.high < frame[['open','close','low']].max(axis=1)) | (frame.low > frame[['open','close','high']].min(axis=1))).any():
        raise ValueError('Invalid OHLC range')
    as_of = pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        raise ValueError('Evaluation time must include a timezone')
    frame = frame[frame.timestamp + pd.Timedelta(seconds=INTERVALS[timeframe]) <= as_of].copy()
    if frame.empty:
        raise ValueError('No completed candles')
    if (as_of - (frame.timestamp.iloc[-1] + pd.Timedelta(seconds=INTERVALS[timeframe]))).total_seconds() > max_age:
        raise ValueError('Latest completed candle is stale')
    if len(frame) < 50:
        raise ValueError('At least 50 completed candles required')
    return frame


def cached_market(provider, symbol, timeframe, as_of, *, max_age=float('inf'), live=False, report_cutoff=False):
    as_of = pd.Timestamp(as_of)
    if timeframe not in INTERVALS or pd.isna(as_of) or as_of.tzinfo is None:
        raise ValueError('Unsupported timeframe or evaluation time')
    # Alpaca aggregates from minute bars; Yahoo does not document this alignment.
    live = live and provider is alpaca
    credentials = (os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'))
    credential_hash = hashlib.sha256(json.dumps(credentials).encode()).digest()
    key = (provider, symbol.upper(), timeframe, as_of, credential_hash, live)
    with _market_cache_lock:
        now = monotonic()
        cache_key = key
        if live:
            candidates = ((k, value) for k, value in _market_cache.items()
                          if k[:3] == key[:3] and k[4:] == key[4:] and now < value[0]
                          and value[2] <= as_of < min(value[3], value[2] + pd.Timedelta(seconds=15)))
            cache_key, cached = max(candidates, key=lambda item: item[1][2], default=(key, None))
        else:
            cached = _market_cache.get(key)
        if cached and now < cached[0]:
            _market_cache.move_to_end(cache_key)
            snapshot = copy.deepcopy(cached[1])
        else:
            if cached and now >= cached[0]:
                _market_cache.pop(cache_key, None)
            snapshot = None
    if snapshot is not None:
        try:
            validate_candles(snapshot[0], timeframe, as_of, max_age)
            return (*snapshot, cached[2]) if report_cutoff else snapshot
        except ValueError:
            with _market_cache_lock:
                if _market_cache.get(cache_key) is cached:
                    _market_cache.pop(cache_key, None)
    result = provider(symbol, timeframe, as_of, credentials=credentials) if provider is alpaca else provider(symbol, timeframe, as_of)
    try:
        validated = validate_candles(result[0], timeframe, as_of, max_age)
    except ValueError as exc:
        if str(exc) == 'Latest completed candle is stale':
            return (*result, as_of) if report_cutoff else result
        raise
    interval = INTERVALS[timeframe]
    if live:
        stamps = pd.to_datetime(result[0].timestamp, utc=True)
        if not (stamps == stamps.dt.floor('min')).all():
            return (*result, as_of) if report_cutoff else result
    with _market_cache_lock:
        next_close = validated.timestamp.iloc[-1] + pd.Timedelta(seconds=2 * interval)
        if live:
            next_close = min(next_close, as_of.floor('min') + pd.Timedelta(minutes=1))
            closes = pd.to_datetime(result[0].timestamp, utc=True) + pd.Timedelta(seconds=interval)
            future_closes = closes[closes > as_of]
            if not future_closes.empty:
                next_close = min(next_close, future_closes.min())
        _market_cache[key] = ((now if live else monotonic()) + (min(interval, 15) if live else min(interval, 600)), copy.deepcopy(result), as_of, next_close)
        _market_cache.move_to_end(key)
        while len(_market_cache) > 128:
            _market_cache.popitem(last=False)
    return (*result, as_of) if report_cutoff else result


def headlines(items, as_of):
    result = []
    for item in items or []:
        content = item.get('content', item)
        title = content.get('title')
        published = content.get('pubDate') or content.get('providerPublishTime')
        if not title or not published:
            continue
        try:
            date = pd.Timestamp(published, unit='s', tz='UTC') if isinstance(published, (int, float)) else pd.Timestamp(published)
            if date.tzinfo is None or date > as_of:
                continue
            provider = content.get('provider') or content.get('publisher') or 'Unknown'
            if isinstance(provider, dict):
                provider = provider.get('displayName', 'Unknown')
            result.append({'title': str(title), 'source': str(provider), 'published': date.isoformat()})
        except (TypeError, ValueError, OverflowError):
            continue
    return result[:10]


def alpaca_news(symbol, as_of):
    headers = {'APCA-API-KEY-ID': os.getenv('ALPACA_API_KEY'), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET_KEY')}
    if not all(headers.values()):
        raise ValueError('News unavailable')
    try:
        response = request(pooled_get, 'https://data.alpaca.markets/v1beta1/news', service='alpaca-data', credentials=list(headers.values()), retry_transport=True, headers=headers, params={
            'symbols': symbol, 'start': (as_of - pd.Timedelta(days=7)).isoformat(),
            'end': as_of.isoformat(), 'sort': 'desc', 'limit': 10,
        }, timeout=5)
        response.raise_for_status()
        items = response.json()['news']
        if not isinstance(items, list):
            raise ValueError('Invalid news')
    except (httpx.HTTPError, CircuitOpen, ValueError, TypeError, KeyError):
        raise ValueError('News unavailable') from None
    result = []
    for item in items:
        try:
            title = item['headline']
            published = pd.Timestamp(item['created_at'])
            updated = pd.Timestamp(item['updated_at'])
            if not isinstance(title, str) or not title.strip() or published.tzinfo is None or updated.tzinfo is None or published > as_of or updated > as_of:
                continue
            result.append({'title': title, 'source': str(item.get('source') or 'Alpaca News'), 'published': published.isoformat()})
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            continue
    return result


def _yahoo_request(operation):
    from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError, Timeout
    from yfinance.exceptions import YFDataException, YFRateLimitError

    def attempt(url):
        try:
            result = operation()
        except YFRateLimitError:
            # yfinance does not expose Retry-After; this is our local cooldown.
            return httpx.Response(429, headers={'Retry-After': '30'})
        except Timeout as exc:
            raise httpx.ReadTimeout('Yahoo timed out') from exc
        except CurlConnectionError as exc:
            raise httpx.ConnectError('Yahoo connection failed') from exc
        except YFDataException as exc:
            return httpx.Response(503 if str(exc) == '*** YAHOO! FINANCE IS CURRENTLY DOWN! ***' else 400)
        except Exception as exc:
            logger.warning('Yahoo request failed: %s', exc)
            return httpx.Response(400)
        return httpx.Response(200, extensions={'yahoo_result': result})

    try:
        response = request(attempt, 'https://query1.finance.yahoo.com', service='yahoo', credentials=None, retry_transport=True)
    except httpx.TimeoutException as exc:
        raise ValueError('Market data provider timed out') from exc
    except CircuitOpen as exc:
        raise ValueError('Market data provider rate limited' if 'rate limit' in str(exc).lower() else 'Market data provider failed') from exc
    except httpx.TransportError as exc:
        raise ValueError('Market data provider failed') from exc
    if response.status_code == 429:
        raise ValueError('Market data provider rate limited')
    if response.status_code != 200:
        raise ValueError('Market data provider failed')
    return response.extensions['yahoo_result']


def yahoo(symbol, timeframe, as_of):
    if timeframe not in ('1m', '2m', '5m', '15m', '30m', '60m', '90m'):
        raise ValueError('Unsupported Yahoo Finance timeframe')
    import yfinance as yf
    ticker = None
    def history():
        nonlocal ticker
        if ticker is None:
            ticker = yf.Ticker(symbol)
        return ticker.history(period='7d' if timeframe == '1m' else '1mo', interval=timeframe, auto_adjust=False, prepost=False, raise_errors=True)
    frame = _yahoo_request(history)
    if frame.empty:
        raise ValueError('Market data provider returned no candles')
    frame = frame.reset_index().rename(columns={'Datetime':'timestamp', 'Date':'timestamp', 'Open':'open', 'High':'high', 'Low':'low', 'Close':'close', 'Volume':'volume'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        info_future = pool.submit(_yahoo_request, lambda: ticker.info)
        news_future = pool.submit(_yahoo_request, lambda: ticker.news)
        try:
            info = info_future.result()
            details = {k: info.get(k) for k in ('shortName', 'currency', 'exchange', 'exchangeTimezoneName') if info.get(k) is not None}
        except Exception as exc:
            logger.warning('Yahoo info unavailable: %s', exc)
            details = {}
        try:
            news = headlines(news_future.result(), as_of)
        except Exception as exc:
            logger.warning('Yahoo news unavailable: %s', exc)
            news = []
    return frame, details, news


def alpaca(symbol, timeframe, as_of, *, credentials=None):
    key, secret = credentials if credentials is not None else (os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY'))
    if not key or not secret:
        raise ValueError('Alpaca credentials are missing')
    symbol = symbol.upper()
    if not re.fullmatch(r'[A-Z0-9.\-]{1,20}', symbol):
        raise ValueError('Invalid Alpaca stock symbol')
    intervals = {'1m': '1Min', '2m': '2Min', '5m': '5Min', '10m': '10Min', '15m': '15Min', '30m': '30Min', '60m': '1Hour'}
    if timeframe not in intervals:
        raise ValueError('Unsupported Alpaca timeframe')
    headers = {'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}
    params = {'timeframe': intervals[timeframe], 'feed': 'iex', 'adjustment': 'raw', 'start': (as_of - pd.Timedelta(days=30)).isoformat(), 'end': as_of.isoformat(), 'sort': 'desc', 'limit': 1000}
    bars = []
    try:
        # Each page must be nonempty and add at least one bar, so 51 pages suffice for the 50-candle minimum.
        for _ in range(51):
            response = request(pooled_get, f'https://data.alpaca.markets/v2/stocks/{symbol}/bars', service='alpaca-data', credentials=[key, secret], retry_transport=True, headers=headers, params=params, timeout=15)
            if response.status_code in (401, 403):
                raise ValueError('Alpaca credentials or data access rejected')
            if response.status_code == 429:
                raise ValueError('Alpaca rate limit reached')
            response.raise_for_status()
            data = response.json()
            page = data.get('bars') or []
            if not isinstance(page, list):
                raise ValueError('Invalid Alpaca candle response')
            bars.extend(page)
            token = data.get('next_page_token')
            if len(bars) >= 51 or not token:
                break
            if not page or token == params.get('page_token'):
                raise ValueError('Invalid Alpaca candle response')
            params['page_token'] = token
    except httpx.TimeoutException as exc:
        raise ValueError('Alpaca market data timed out') from exc
    except (httpx.HTTPError, CircuitOpen, TypeError, AttributeError) as exc:
        raise ValueError('Alpaca market data request failed') from exc
    if not bars:
        raise ValueError('Alpaca returned no candles')
    frame = pd.DataFrame(list(reversed(bars))).rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
    return frame, {'exchangeTimezoneName': 'America/New_York', 'currency': 'USD'}, []
