import httpx
import pandas as pd
import pytest

from fasttrade import market


def test_alpaca_news_filters_future_updates_and_reports_failures(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'test-key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'test-secret')
    now = pd.Timestamp('2026-01-02T15:00:00Z')
    def get(url, *, headers, params, timeout):
        assert url == 'https://data.alpaca.markets/v1beta1/news'
        assert params['symbols'] == 'SPY' and params['end'] == now.isoformat()
        return httpx.Response(200, json={'news': [
            {'headline': 'Market update', 'source': 'Example', 'created_at': '2026-01-02T14:00:00Z', 'updated_at': '2026-01-02T14:10:00Z'},
            {'headline': 'Future update', 'source': 'Example', 'created_at': '2026-01-02T14:00:00Z', 'updated_at': '2026-01-02T16:00:00Z'},
        ]}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)
    assert [item['title'] for item in market.alpaca_news('SPY', now)] == ['Market update']
    monkeypatch.setattr(httpx, 'get', lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ConnectError('unavailable')))
    with pytest.raises(ValueError, match='News unavailable'):
        market.alpaca_news('SPY', now)


@pytest.mark.parametrize(('timeframe', 'expected'), [('5m', '5Min'), ('10m', '10Min')])
def test_alpaca_normalizes_newest_first_bars_and_uses_iex(monkeypatch, timeframe, expected):
    monkeypatch.setenv('ALPACA_API_KEY', 'test-key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'test-secret')
    def get(url, *, headers, params, timeout):
        assert url == 'https://data.alpaca.markets/v2/stocks/NVDA/bars'
        assert headers == {'APCA-API-KEY-ID': 'test-key', 'APCA-API-SECRET-KEY': 'test-secret'}
        assert params['feed'] == 'iex' and params['timeframe'] == expected
        assert params['sort'] == 'desc' and params['adjustment'] == 'raw'
        return httpx.Response(200, json={'bars': [
            {'t':'2026-01-02T14:35:00Z','o':2,'h':3,'l':1,'c':2,'v':100},
            {'t':'2026-01-02T14:30:00Z','o':1,'h':2,'l':1,'c':2,'v':50},
        ], 'next_page_token': None}, request=httpx.Request('GET', url))
    monkeypatch.setattr(httpx, 'get', get)
    frame, details, news = market.alpaca('nvda', timeframe, pd.Timestamp('2026-01-02T15:00:00Z'))
    assert list(frame.columns) == ['timestamp', 'open', 'high', 'low', 'close', 'volume']
    assert list(frame.timestamp) == ['2026-01-02T14:30:00Z', '2026-01-02T14:35:00Z']
    assert list(frame.volume) == [50, 100]
    assert details['exchangeTimezoneName'] == 'America/New_York' and news == []


def test_alpaca_missing_keys_unsupported_timeframe_and_bad_symbol_fail_without_request(monkeypatch):
    monkeypatch.delenv('ALPACA_API_KEY', raising=False)
    monkeypatch.delenv('ALPACA_SECRET_KEY', raising=False)
    monkeypatch.setattr(httpx, 'get', lambda *a, **k: pytest.fail('unexpected request'))
    with pytest.raises(ValueError, match='credentials'):
        market.alpaca('NVDA', '5m', pd.Timestamp('2026-01-02T15:00:00Z'))
    monkeypatch.setenv('ALPACA_API_KEY', 'x')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'y')
    with pytest.raises(ValueError, match='timeframe'):
        market.alpaca('NVDA', '90m', pd.Timestamp('2026-01-02T15:00:00Z'))
    with pytest.raises(ValueError, match='symbol'):
        market.alpaca('../bad', '5m', pd.Timestamp('2026-01-02T15:00:00Z'))


def test_alpaca_auth_failure_does_not_expose_credentials(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'private-key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'private-secret')
    monkeypatch.setattr(httpx, 'get', lambda url, **kw: httpx.Response(403, text='private-secret', request=httpx.Request('GET', url)))
    with pytest.raises(ValueError, match='Alpaca credentials or data access') as error:
        market.alpaca('NVDA', '5m', pd.Timestamp('2026-01-02T15:00:00Z'))
    assert 'private-secret' not in str(error.value)
