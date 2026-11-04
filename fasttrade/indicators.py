import math
from collections import OrderedDict
from threading import Lock
from time import monotonic
import pandas as pd

_feature_cache = OrderedDict()
_feature_cache_lock = Lock()


def cached_features(compute, frame, timezone, ttl):
    key = (compute, timezone, ttl, len(frame), frame.timestamp.iloc[0], frame.timestamp.iloc[-1])
    with _feature_cache_lock:
        cached = _feature_cache.get(key)
        if cached and monotonic() < cached[0] and frame.equals(cached[1]):
            _feature_cache.move_to_end(key)
            return cached[2].copy()
        _feature_cache.pop(key, None)
    result = compute(frame, timezone)
    with _feature_cache_lock:
        _feature_cache[key] = (monotonic() + min(ttl, 600), frame.copy(deep=True), result.copy())
        _feature_cache.move_to_end(key)
        while len(_feature_cache) > 128:
            _feature_cache.popitem(last=False)
    return result


def features(frame, timezone):
    close, high, low, volume = (frame[x] for x in ('close', 'high', 'low', 'volume'))
    ema = lambda n: close.ewm(span=n, adjust=False, min_periods=n).mean()
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean().iloc[-1]
    loss = (-change.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean().iloc[-1]
    rsi = 50.0 if gain == loss == 0 else (100.0 if loss == 0 else 100 - 100 / (1 + gain / loss))
    true_range = pd.concat([high-low, (high-close.shift()).abs(), (low-close.shift()).abs()], axis=1).max(axis=1)
    session = frame.timestamp.dt.tz_convert(timezone).dt.date
    today = frame[session == session.iloc[-1]]
    total_volume = today.volume.sum()
    if total_volume <= 0 or volume.tail(20).mean() <= 0:
        raise ValueError('Volume is insufficient to calculate VWAP or volume ratio')
    vwap = (((today.high + today.low + today.close) / 3) * today.volume).sum() / total_volume
    macd = ema(12) - ema(26)
    return {'price': float(close.iloc[-1]), 'ema_9': float(ema(9).iloc[-1]), 'ema_21': float(ema(21).iloc[-1]), 'ema_50': float(ema(50).iloc[-1]), 'rsi_14': float(rsi), 'macd': float(macd.iloc[-1]), 'macd_signal': float(macd.ewm(span=9, adjust=False, min_periods=9).mean().iloc[-1]), 'vwap': float(vwap), 'vwap_delta': float(close.iloc[-1] / vwap - 1), 'atr_14': float(true_range.ewm(alpha=1/14, adjust=False, min_periods=14).mean().iloc[-1]), 'volume_ratio': float(volume.iloc[-1] / volume.tail(20).mean()), **{f'return_{n}': float(close.iloc[-1] / close.iloc[-1-n] - 1) for n in (1,3,5)}, 'spread': None, 'liquidity': None}


def rule_signal(snapshot):
    """Unvalidated directional cross-check; mixed or tied indicators have no signal."""
    if not all(math.isfinite(snapshot[key]) for key in ('ema_9', 'ema_21', 'macd', 'macd_signal')):
        return None
    if snapshot['ema_9'] > snapshot['ema_21'] and snapshot['macd'] > snapshot['macd_signal']:
        return 'BUY'
    if snapshot['ema_9'] < snapshot['ema_21'] and snapshot['macd'] < snapshot['macd_signal']:
        return 'SELL'
    return None
