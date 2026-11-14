from collections import OrderedDict

import httpx
import pytest

from fasttrade import market, paper


@pytest.fixture(autouse=True)
def isolated_caches(monkeypatch):
    monkeypatch.setattr(market, '_cap_cache', OrderedDict())


def test_overview_sums_account_and_positions(monkeypatch):
    def get(url, **kwargs):
        assert kwargs['headers']['APCA-API-KEY-ID'] == 'key' and kwargs['headers']['APCA-API-SECRET-KEY'] == 'secret'
        assert url.endswith('/v2/account') or url.endswith('/v2/positions')
        if url.endswith('/account'):
            return httpx.Response(200, json={'cash': '950.50', 'equity': '1075.25', 'buying_power': '2000'}, request=httpx.Request('GET', url))
        return httpx.Response(200, json=[
            {'symbol': 'NVDA', 'qty': '2', 'avg_entry_price': '100', 'current_price': '62.375', 'market_value': '124.75', 'unrealized_pl': '-75.25'},
            {'symbol': 'BAD'},
        ], request=httpx.Request('GET', url))
    monkeypatch.setattr(paper, 'pooled_get', get)
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    result = paper.overview()
    assert result['cash'] == 950.5 and result['equity'] == 1075.25 and result['buying_power'] == 2000
    assert result['positions'] == [{'symbol': 'NVDA', 'quantity': 2.0, 'avg_price': 100.0, 'price': 62.375, 'value': 124.75, 'unrealized': -75.25}]


def test_overview_maps_errors_without_leaking_secrets(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'private-key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'private-secret')
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: httpx.Response(403, text='private-secret', request=httpx.Request('GET', url)))
    with pytest.raises(ValueError, match='rejected the paper trading credentials') as error:
        paper.overview()
    assert 'private-secret' not in str(error.value)
    monkeypatch.delenv('ALPACA_API_KEY', raising=False)
    with pytest.raises(ValueError, match='credentials are missing'):
        paper.overview()


def test_last_price_reads_latest_trade(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    seen = {}
    def get(url, **kwargs):
        seen['url'], seen['params'] = url, kwargs.get('params')
        return httpx.Response(200, json={'trades': {'AAPL': {'t': '2026-01-02T15:00:00Z', 'p': '142.85', 's': 100}}}, request=httpx.Request('GET', url))
    monkeypatch.setattr(paper, 'pooled_get', get)
    assert paper.last_price('aapl') == {'symbol': 'AAPL', 'price': 142.85}
    assert seen['url'].endswith('/v2/stocks/trades/latest') and seen['params']['symbols'] == 'AAPL'
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: httpx.Response(200, json={'trades': {}}, request=httpx.Request('GET', url)))
    with pytest.raises(ValueError, match='No Alpaca trade price'):
        paper.last_price('AAPL')


def test_submit_order_posts_market_order_and_reports_fill(monkeypatch):
    posted = {}
    urls = []
    def post(url, **kwargs):
        posted['url'], posted['json'] = url, kwargs['json']
        return httpx.Response(200, json={'id': 'order-1', 'status': 'accepted', 'filled_qty': '1', 'filled_avg_price': None}, request=httpx.Request('POST', url))
    def get(url, **kwargs):
        urls.append(url)
        return httpx.Response(200, json={'is_open': True, 'next_open': '2026-11-16T09:30:00-05:00'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(paper, 'pooled_post', post)
    monkeypatch.setattr(paper, 'pooled_get', get)
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    result = paper.submit_order('nvda', 'BUY', 1.5)
    assert posted['json'] == {'symbol': 'NVDA', 'qty': '1.5', 'side': 'buy', 'type': 'market', 'time_in_force': 'day'}
    assert urls[-1].endswith('/v2/clock')
    assert result == {'symbol': 'NVDA', 'side': 'BUY', 'quantity': 1.5, 'order_id': 'order-1', 'status': 'accepted', 'filled_quantity': 1.0, 'filled_avg_price': None, 'pending': False, 'next_open': '2026-11-16T09:30:00-05:00'}
    with pytest.raises(ValueError, match='side must be BUY or SELL'):
        paper.submit_order('NVDA', 'HOLD', 1)
    with pytest.raises(ValueError, match='quantity must be positive'):
        paper.submit_order('NVDA', 'BUY', 0)


def test_submit_order_flags_pending_when_market_is_closed(monkeypatch):
    def post(url, **kwargs):
        return httpx.Response(200, json={'id': 'order-2', 'status': 'accepted', 'filled_qty': '0', 'filled_avg_price': None}, request=httpx.Request('POST', url))
    def get(url, **kwargs):
        assert url.endswith('/v2/clock')
        return httpx.Response(200, json={'is_open': False, 'next_open': '2026-11-16T09:30:00-05:00'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(paper, 'pooled_post', post)
    monkeypatch.setattr(paper, 'pooled_get', get)
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    result = paper.submit_order('AAPL', 'BUY', 1)
    assert result['pending'] is True and result['next_open'] == '2026-11-16T09:30:00-05:00'
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: (_ for _ in ()).throw(httpx.ConnectError('clock unavailable')))
    degraded = paper.submit_order('AAPL', 'BUY', 1)
    assert degraded['pending'] is False and degraded['next_open'] is None


def test_submit_order_reports_rejection_reason(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    monkeypatch.setattr(paper, 'pooled_post', lambda url, **kwargs: httpx.Response(403, json={'message': 'insufficient buying power'}, request=httpx.Request('POST', url)))
    with pytest.raises(ValueError, match='insufficient buying power'):
        paper.submit_order('NVDA', 'BUY', 100)


def test_movers_picks_top_gainer_with_required_cap(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: httpx.Response(200, json={'gainers': [
        {'symbol': 'SMALL', 'price': 12.0, 'percent_change': 9.1, 'change': 1.0},
        {'symbol': 'MYSTERY', 'price': 30.0, 'percent_change': 6.2, 'change': 1.5},
        {'symbol': 'BIGCO', 'price': 220.0, 'percent_change': 3.2, 'change': 6.8},
    ]}, request=httpx.Request('GET', url)))
    def cap(symbol):
        if symbol == 'SMALL':
            return 5e8
        if symbol == 'MYSTERY':
            raise ValueError('cap unavailable')
        return 3.4e12
    monkeypatch.setattr(paper.market, 'market_cap', cap)
    result = paper.movers()
    assert result['pick']['symbol'] == 'BIGCO' and result['pick']['market_cap'] == 3.4e12
    assert [entry['symbol'] for entry in result['considered']] == ['SMALL', 'MYSTERY', 'BIGCO']
    assert result['considered'][1]['market_cap'] is None


def test_movers_without_qualifying_cap_reports_none(monkeypatch):
    monkeypatch.setenv('ALPACA_API_KEY', 'key')
    monkeypatch.setenv('ALPACA_SECRET_KEY', 'secret')
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: httpx.Response(200, json={'gainers': [
        {'symbol': 'SMALL', 'price': 12.0, 'percent_change': 9.1, 'change': 1.0},
    ]}, request=httpx.Request('GET', url)))
    monkeypatch.setattr(paper.market, 'market_cap', lambda symbol: 5e8)
    result = paper.movers()
    assert result['pick'] is None and result['considered'][0]['market_cap'] == 5e8
    monkeypatch.setattr(paper, 'pooled_get', lambda url, **kwargs: httpx.Response(200, json={'gainers': []}, request=httpx.Request('GET', url)))
    assert paper.movers()['pick'] is None


def test_market_cap_caches_per_symbol(monkeypatch):
    calls = []
    def info(operation):
        calls.append(1)
        return {'marketCap': 3.4e12}
    monkeypatch.setattr(market, '_yahoo_request', info)
    assert market.market_cap('nvda') == 3.4e12
    assert market.market_cap('NVDA') == 3.4e12
    assert len(calls) == 1
    monkeypatch.setattr(market, '_yahoo_request', lambda operation: {})
    with pytest.raises(ValueError, match='Market cap unavailable'):
        market.market_cap('OTHER')
