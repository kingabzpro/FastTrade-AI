import math
import os
import re
import httpx
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

INTERVALS = {'1m': 60, '2m': 120, '5m': 300, '10m': 600, '15m': 900, '30m': 1800, '60m': 3600, '90m': 5400}


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
        response = httpx.get('https://data.alpaca.markets/v1beta1/news', headers=headers, params={
            'symbols': symbol, 'start': (as_of - pd.Timedelta(days=7)).isoformat(),
            'end': as_of.isoformat(), 'sort': 'desc', 'limit': 10,
        }, timeout=5)
        response.raise_for_status()
        items = response.json()['news']
        if not isinstance(items, list):
            raise ValueError('Invalid news')
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
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


def yahoo(symbol, timeframe, as_of):
    if timeframe not in ('1m', '2m', '5m', '15m', '30m', '60m', '90m'):
        raise ValueError('Unsupported Yahoo Finance timeframe')
    import yfinance as yf
    from curl_cffi.requests.exceptions import Timeout
    ticker = yf.Ticker(symbol)
    try:
        frame = ticker.history(period='7d' if timeframe == '1m' else '1mo', interval=timeframe, auto_adjust=False, prepost=False, raise_errors=True)
    except Timeout as exc:
        raise ValueError('Market data provider timed out') from exc
    except Exception as exc:
        raise ValueError('Market data provider failed') from exc
    if frame.empty:
        raise ValueError('Market data provider returned no candles')
    frame = frame.reset_index().rename(columns={'Datetime':'timestamp', 'Date':'timestamp', 'Open':'open', 'High':'high', 'Low':'low', 'Close':'close', 'Volume':'volume'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        info_future = pool.submit(lambda: ticker.info)
        news_future = pool.submit(lambda: ticker.news)
        try:
            info = info_future.result()
            details = {k: info.get(k) for k in ('shortName', 'currency', 'exchange', 'exchangeTimezoneName') if info.get(k) is not None}
        except Exception:
            details = {}
        try:
            news = headlines(news_future.result(), as_of)
        except Exception:
            news = []
    return frame, details, news


def alpaca(symbol, timeframe, as_of):
    key, secret = os.getenv('ALPACA_API_KEY'), os.getenv('ALPACA_SECRET_KEY')
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
        while True:
            response = httpx.get(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars', headers=headers, params=params, timeout=15)
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
    except (httpx.HTTPError, TypeError, AttributeError) as exc:
        raise ValueError('Alpaca market data request failed') from exc
    if not bars:
        raise ValueError('Alpaca returned no candles')
    frame = pd.DataFrame(list(reversed(bars))).rename(columns={'t': 'timestamp', 'o': 'open', 'h': 'high', 'l': 'low', 'c': 'close', 'v': 'volume'})
    return frame, {'exchangeTimezoneName': 'America/New_York', 'currency': 'USD'}, []
