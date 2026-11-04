import pandas as pd
import pytest

from fasttrade import market
from fasttrade import indicators
from test_core import candles


def test_cache_reuses_only_exact_as_of_and_is_defensive(monkeypatch):
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(as_of)
        return candles(), {'exchangeTimezoneName': 'America/New_York'}, [{'title': 'one'}]

    first = market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'))
    first[0].loc[0, 'close'] = 999
    first[1]['exchangeTimezoneName'] = 'changed'
    first[2][0]['title'] = 'changed'
    second = market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'))
    assert len(calls) == 1
    assert second[0].loc[0, 'close'] != 999
    assert second[1]['exchangeTimezoneName'] == 'America/New_York'
    assert second[2][0]['title'] == 'one'
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:34Z'))
    assert len(calls) == 2


def test_cache_never_reuses_snapshot_for_a_different_as_of():
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(as_of)
        return candles(), {}, []

    later = pd.Timestamp('2026-01-02T19:34Z')
    earlier = pd.Timestamp('2026-01-02T19:31Z')
    market.cached_market(provider, 'TEST', '5m', later)
    market.cached_market(provider, 'TEST', '5m', earlier)
    market.cached_market(provider, 'TEST', '5m', earlier)
    market.cached_market(provider, 'TEST', '5m', later)
    assert calls == [later, earlier]


def test_live_cache_reuses_only_until_next_candle_can_close(monkeypatch):
    market._market_cache.clear()
    now = [0.0]
    monkeypatch.setattr(market, 'monotonic', lambda: now[0])
    calls = []
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        return candles(), {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31:10Z'), live=True)
    assert len(calls) == 1
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:32Z'), live=True)
    assert len(calls) == 2


def test_live_cache_does_not_reuse_future_snapshots_or_cross_historical_cutoffs(monkeypatch):
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        return candles(), {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    later = pd.Timestamp('2026-01-02T19:31:10Z')
    earlier = pd.Timestamp('2026-01-02T19:31Z')
    market.cached_market(provider, 'TEST', '5m', later, live=True)
    market.cached_market(provider, 'TEST', '5m', earlier, live=True)
    market.cached_market(provider, 'TEST', '5m', earlier)
    _, _, _, cutoff = market.cached_market(provider, 'TEST', '5m', later, live=True, report_cutoff=True)
    assert calls == [later, earlier, earlier]
    assert cutoff == later


def test_live_cache_expires_quickly(monkeypatch):
    market._market_cache.clear()
    now = [0.0]
    monkeypatch.setattr(market, 'monotonic', lambda: now[0])
    calls = []
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        return candles(), {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    now[0] = 16
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31:16Z'), live=True)
    assert len(calls) == 2


def test_slow_fetch_does_not_extend_live_snapshot_lifetime(monkeypatch):
    market._market_cache.clear()
    now = [0.0]
    monkeypatch.setattr(market, 'monotonic', lambda: now[0])
    calls = []
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        now[0] += 40
        return candles(), {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31:41Z'), live=True)
    assert len(calls) == 2


def test_live_cache_refetches_when_a_returned_partial_candle_completes(monkeypatch):
    market._market_cache.clear()
    calls = []
    frame = candles()
    partial = frame.iloc[[-1]].copy()
    partial['timestamp'] = pd.Timestamp('2026-01-02T19:27Z')
    frame = pd.concat([frame, partial], ignore_index=True)
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        return frame, {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:32Z'), live=True)
    assert len(calls) == 2


def test_live_yahoo_does_not_reuse_a_different_cutoff():
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(as_of)
        return candles(), {}, []
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31:10Z'), live=True)
    assert len(calls) == 2


def test_live_cache_checks_expiry_after_obtaining_lock(monkeypatch):
    market._market_cache.clear()
    now = [0.0]
    monkeypatch.setattr(market, 'monotonic', lambda: now[0])
    calls = []
    def provider(symbol, timeframe, as_of, **kwargs):
        calls.append(as_of)
        return candles(), {}, []
    monkeypatch.setattr(market, 'alpaca', provider)
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'), live=True)
    lock = market._market_cache_lock
    class DelayedLock:
        def __enter__(self):
            lock.acquire()
            now[0] = 16
        def __exit__(self, *args):
            lock.release()
    monkeypatch.setattr(market, '_market_cache_lock', DelayedLock())
    market.cached_market(provider, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31:10Z'), live=True)
    assert len(calls) == 2


def test_cache_expires_and_failed_fetches_are_not_cached(monkeypatch):
    market._market_cache.clear()
    now = [0.0]
    monkeypatch.setattr(market, 'monotonic', lambda: now[0])
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError('upstream unavailable')
        return candles(), {}, []

    as_of = pd.Timestamp('2026-01-02T19:31Z')
    with pytest.raises(ValueError, match='upstream'):
        market.cached_market(provider, 'TEST', '5m', as_of)
    market.cached_market(provider, 'TEST', '5m', as_of)
    now[0] = 301
    market.cached_market(provider, 'TEST', '5m', as_of)
    assert len(calls) == 3


def test_invalid_market_data_is_not_cached():
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(1)
        return (candles().iloc[-10:] if len(calls) == 1 else candles()), {}, []
    as_of = pd.Timestamp('2026-01-02T19:31Z')
    with pytest.raises(ValueError, match='50'):
        market.cached_market(provider, 'TEST', '5m', as_of)
    market.cached_market(provider, 'TEST', '5m', as_of)
    assert len(calls) == 2


def test_cache_does_not_cross_alpaca_credentials(monkeypatch):
    market._market_cache.clear()
    calls = []
    def provider(symbol, timeframe, as_of):
        calls.append(1)
        return candles(), {}, []
    as_of = pd.Timestamp('2026-01-02T19:31Z')
    monkeypatch.setenv('ALPACA_API_KEY', 'one')
    market.cached_market(provider, 'TEST', '5m', as_of)
    monkeypatch.setenv('ALPACA_API_KEY', 'two')
    market.cached_market(provider, 'TEST', '5m', as_of)
    assert len(calls) == 2


def test_cached_alpaca_fetch_uses_same_credential_snapshot_as_cache_key(monkeypatch):
    import hashlib
    from types import SimpleNamespace
    import httpx

    market._market_cache.clear()
    monkeypatch.setenv('ALPACA_API_KEY', 'key-a')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret-a')
    def change_credentials_after_snapshot(value):
        monkeypatch.setenv('ALPACA_API_KEY', 'key-b')
        monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret-b')
        return hashlib.sha256(value)
    monkeypatch.setattr(market, 'hashlib', SimpleNamespace(sha256=change_credentials_after_snapshot))
    seen = []
    frame = candles()
    bars = [{'t': row.timestamp.isoformat(), 'o': row.open, 'h': row.high, 'l': row.low,
             'c': row.close, 'v': row.volume} for row in reversed(list(frame.itertuples()))]
    def get(url, *, headers, **kwargs):
        seen.append(headers.copy())
        return httpx.Response(200, json={'bars': bars}, request=httpx.Request('GET', url))
    monkeypatch.setattr(market.httpx, 'get', get)
    market.cached_market(market.alpaca, 'TEST', '5m', pd.Timestamp('2026-01-02T19:31Z'))
    assert seen[0]['APCA-API-KEY-ID'] == 'key-a'
    assert seen[0]['APCA-API-SECRET-KEY'] == 'secret-a'


def test_indicator_cache_reuses_only_identical_validated_candles(monkeypatch):
    indicators._feature_cache.clear()
    calls = []
    def compute(frame, timezone):
        calls.append(1)
        return {'price': float(frame.close.iloc[-1]), 'spread': None}
    frame = candles()
    first = indicators.cached_features(compute, frame, 'America/New_York', 300)
    first['price'] = -1
    assert indicators.cached_features(compute, frame.copy(), 'America/New_York', 300)['price'] == 159
    changed = frame.copy()
    changed.loc[10, 'close'] += 1
    indicators.cached_features(compute, changed, 'America/New_York', 300)
    indicators.cached_features(compute, frame, 'UTC', 300)
    assert len(calls) == 3


def test_indicator_cache_does_not_reuse_longer_timeframe_entry(monkeypatch):
    indicators._feature_cache.clear()
    calls = []
    def compute(frame, timezone):
        calls.append(1)
        return {'price': 1}
    frame = candles()
    indicators.cached_features(compute, frame, 'UTC', 3600)
    indicators.cached_features(compute, frame, 'UTC', 60)
    assert len(calls) == 2
