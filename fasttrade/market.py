import math
import pandas as pd

INTERVALS = {'1m': 60, '2m': 120, '5m': 300, '15m': 900, '30m': 1800, '60m': 3600, '90m': 5400}


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


def yahoo(symbol, timeframe, as_of):
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    try:
        frame = ticker.history(period='7d' if timeframe == '1m' else '1mo', interval=timeframe, auto_adjust=False, prepost=False, raise_errors=True)
    except Exception as exc:
        raise ValueError('Market data provider failed') from exc
    if frame.empty:
        raise ValueError('Market data provider returned no candles')
    frame = frame.reset_index().rename(columns={'Datetime':'timestamp', 'Date':'timestamp', 'Open':'open', 'High':'high', 'Low':'low', 'Close':'close', 'Volume':'volume'})
    try:
        info = ticker.info
        details = {k: info.get(k) for k in ('shortName', 'currency', 'exchange', 'exchangeTimezoneName') if info.get(k) is not None}
    except Exception:
        details = {}
    try:
        news = headlines(ticker.news, as_of)
    except Exception:
        news = []
    return frame, details, news
