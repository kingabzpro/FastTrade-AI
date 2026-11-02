from fastapi.testclient import TestClient


def test_startup_banner_shows_local_settings_without_secrets(monkeypatch, capsys):
    import socket
    import threading
    import uvicorn
    from unittest.mock import MagicMock
    from fasttrade import web

    monkeypatch.setenv('SYSTEMONE_MODEL', 'test-model')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'private-test-key')
    monkeypatch.setenv('NO_COLOR', '1')
    monkeypatch.setattr(socket, 'socket', MagicMock())
    monkeypatch.setattr(threading, 'Timer', MagicMock())
    monkeypatch.setattr(uvicorn, 'Server', MagicMock())
    web.serve()
    output = capsys.readouterr().out
    assert 'http://127.0.0.1:8765/' in output
    assert 'http://127.0.0.1:8765/docs' in output
    assert '/openapi.json' not in output and '/api/health' not in output
    assert 'test-model' in output and 'Alpaca IEX' in output
    assert 'synthetic' not in output.lower() and 'sample' not in output.lower()
    assert 'API key' not in output and 'private-test-key' not in output and '\x1b[' not in output


def test_local_api_exposes_health_and_schema_without_running_analysis(monkeypatch):
    from fasttrade import web
    monkeypatch.setattr(web, 'run_analysis', lambda args: (_ for _ in ()).throw(AssertionError('unexpected analysis')))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.get('/api/health').json() == {'status': 'ok', 'mode': 'signal-only'}
    schema = client.get('/openapi.json').json()
    assert '/api/analyze' in schema['paths'] and '/api/health' in schema['paths']
    assert schema['paths']['/api/analyze']['post']['responses']['200']['content']['application/json']['schema']
    assert client.get('/docs').status_code == 200


def test_brand_logo_is_served():
    from fasttrade.web import app
    client = TestClient(app, base_url='http://127.0.0.1:8765')
    response = client.get('/static/logo.svg')
    assert response.status_code == 200
    assert '<svg' in response.text
    page = client.get('/').text
    assert '/static/logo.svg' in page
    assert '/static/favicon.svg' in page
    icon = client.get('/static/favicon.svg')
    assert icon.status_code == 200 and '#1C3155' in icon.text
    assert '/static/favicon.ico' in page
    favicon = client.get('/static/favicon.ico')
    assert favicon.status_code == 200 and favicon.content[:4] == b'\x00\x00\x01\x00'


def test_mobile_navigation_has_real_destinations():
    from html.parser import HTMLParser
    from fasttrade.web import app

    class NavLinks(HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_nav = False
            self.links = set()

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'nav' and values.get('aria-label') == 'Mobile navigation':
                self.in_nav = True
            elif tag == 'a' and self.in_nav:
                self.links.add(values.get('href'))

        def handle_endtag(self, tag):
            if tag == 'nav':
                self.in_nav = False

    page = TestClient(app, base_url='http://127.0.0.1:8765').get('/')
    parser = NavLinks()
    parser.feed(page.text)
    assert {'#workspace', '#chart-panel', '#history-panel'} <= parser.links


def test_page_does_not_run_analysis_on_open(monkeypatch):
    from fasttrade import web
    calls = []
    monkeypatch.setattr(web, 'run_analysis', lambda args: calls.append(args))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    page = client.get('/')
    assert page.status_code == 200
    assert 'FastTrade' in page.text
    assert calls == []


def test_analysis_uses_existing_engine_and_risk_inputs(monkeypatch):
    from fasttrade import web
    seen = []
    def analyze(args):
        seen.append(args)
        return {'actionable_signal': 'HOLD', 'risk_rejections': ['Stale data'], 'decision': {'action': 'BUY'}}
    monkeypatch.setattr(web, 'run_analysis', analyze)
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    response = client.post('/api/analyze', json={'symbol': 'aapl', 'timeframe': '5m', 'position': 2})
    assert response.status_code == 200
    assert response.json()['actionable_signal'] == 'HOLD'
    assert seen[0].symbol == 'AAPL' and seen[0].position == 2
    assert seen[0].command == 'analyze'
    assert seen[0].include_chart and seen[0].allow_stale_preview


def test_analysis_api_passes_provider_and_rejects_invalid_choice(monkeypatch):
    from fasttrade import web
    seen = []
    monkeypatch.setattr(web, 'run_analysis', lambda args: seen.append(args.source) or {'source': 'Yahoo Finance'})
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.post('/api/analyze', json={'symbol': 'SPY', 'source': 'yahoo'}).status_code == 200
    assert seen == ['yahoo']
    assert client.post('/api/analyze', json={'symbol': 'SPY', 'source': 'other'}).status_code == 422


def test_spy_overview_uses_completed_bars_and_reports_news_failure(monkeypatch):
    import pandas as pd
    import pytest
    from fasttrade import web
    now = pd.Timestamp('2026-01-02T15:00:00Z')
    frame = pd.DataFrame({'timestamp': pd.date_range(end=now - pd.Timedelta(minutes=5), periods=51, freq='5min'),
                          'open': 100., 'high': 103., 'low': 99., 'close': [100.] * 50 + [102.], 'volume': 10.})
    monkeypatch.setattr(web, 'alpaca', lambda symbol, timeframe, as_of: (frame, {}, []))
    monkeypatch.setattr(web, 'alpaca_news', lambda *args: (_ for _ in ()).throw(ValueError('News unavailable')))
    result = web.spy_overview('5m', now)
    assert result['price'] == 102 and result['change_pct'] == pytest.approx(2)
    assert result['stale'] is False and result['news_status'] == 'unavailable'


def test_spy_overview_marks_stale_bars_and_keeps_news_when_bars_fail(monkeypatch):
    import pandas as pd
    from fasttrade import web
    now = pd.Timestamp('2026-01-02T15:00:00Z')
    frame = pd.DataFrame({'timestamp': pd.date_range(end=now - pd.Timedelta(hours=1), periods=51, freq='5min'),
                          'open': 100., 'high': 103., 'low': 99., 'close': 100., 'volume': 10.})
    news = [{'title': 'SPY news', 'source': 'Example', 'published': now.isoformat()}]
    monkeypatch.setattr(web, 'alpaca', lambda *args: (frame, {}, []))
    monkeypatch.setattr(web, 'alpaca_news', lambda *args: news)
    assert web.spy_overview('5m', now)['stale'] is True
    monkeypatch.setattr(web, 'alpaca', lambda *args: (_ for _ in ()).throw(ValueError('unavailable')))
    result = web.spy_overview('5m', now)
    assert result['price'] is None and result['news'] == news and result['news_status'] == 'available'


def test_spy_change_uses_exactly_ten_minutes_and_rejects_gaps(monkeypatch):
    import pandas as pd
    import pytest
    from fasttrade import web
    now = pd.Timestamp('2026-01-03T15:00:00Z')
    times = pd.date_range(end=pd.Timestamp('2026-01-02T20:55:00Z'), periods=51, freq='5min')
    frame = pd.DataFrame({'timestamp': times, 'open': 100., 'high': 110., 'low': 99.,
                          'close': [100.] * 48 + [100., 105., 110.], 'volume': 10.})
    monkeypatch.setattr(web, 'alpaca', lambda *args: (frame, {}, []))
    monkeypatch.setattr(web, 'alpaca_news', lambda *args: [])
    result = web.spy_overview('5m', now)
    assert result['stale'] and result['change_pct'] == pytest.approx(10)
    frame.loc[48, 'timestamp'] -= pd.Timedelta(minutes=1)
    result = web.spy_overview('5m', now)
    assert result['change_pct'] == pytest.approx(5)
    assert pd.Timestamp(result['change_candle_timestamp']) == pd.Timestamp('2026-01-02T20:50:00Z')
    frame['timestamp'] = pd.date_range(end=pd.Timestamp('2026-01-02T20:55:00Z'), periods=51, freq='6min')
    assert web.spy_overview('5m', now)['change_pct'] is None


def test_spy_endpoint_caches_for_ten_minutes_without_model_calls(monkeypatch):
    from fasttrade import web
    calls = []
    clock = [1000.]
    monkeypatch.setattr(web, '_spy_cache', None)
    monkeypatch.setattr(web, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(web, 'run_analysis', lambda args: (_ for _ in ()).throw(AssertionError('unexpected model call')))
    monkeypatch.setattr(web, 'spy_overview', lambda timeframe, now: calls.append(timeframe) or {'symbol': 'SPY', 'timeframe': timeframe, 'price': 100, 'candle_timestamp': now.isoformat(), 'news_status': 'available'})
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.get('/api/market-overview').json()['symbol'] == 'SPY'
    clock[0] += 599
    assert client.get('/api/market-overview').json()['symbol'] == 'SPY'
    assert calls == ['5m']
    clock[0] += 1
    assert client.get('/api/market-overview').json()['symbol'] == 'SPY'
    assert calls == ['5m', '5m']


def test_spy_signals_cache_for_ten_minutes_and_use_post(monkeypatch):
    from fasttrade import web
    clock = [1000.]
    calls = []
    monkeypatch.setattr(web, '_spy_signals_cache', None)
    monkeypatch.setattr(web, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(web, 'build_spy_signals', lambda previous=None: calls.append('run') or {'deterministic': 'HOLD', 'model': 'BUY', 'stale': True})
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.get('/api/spy-signals').status_code == 405
    assert client.post('/api/spy-signals', json={}).json()['model'] == 'BUY'
    clock[0] += 599
    assert client.post('/api/spy-signals', json={}).json()['deterministic'] == 'HOLD'
    assert calls == ['run']
    clock[0] += 1
    assert client.post('/api/spy-signals', json={}).json()['model'] == 'BUY'
    assert calls == ['run', 'run']


def test_spy_signals_reject_cross_origin_and_non_json_requests(monkeypatch):
    from fasttrade import web
    monkeypatch.setattr(web, '_spy_signals_cache', None)
    monkeypatch.setattr(web, 'build_spy_signals', lambda: (_ for _ in ()).throw(AssertionError('unexpected model call')))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.post('/api/spy-signals', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.post('/api/spy-signals', content='{}', headers={'Content-Type': 'text/plain'}).status_code == 422


def test_concurrent_spy_signals_share_one_model_request(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from fasttrade import web
    started, release = Event(), Event()
    calls = []
    monkeypatch.setattr(web, '_spy_signals_cache', None)
    def build(previous=None):
        calls.append(1)
        started.set()
        assert release.wait(5)
        return {'deterministic': 'BUY', 'model': 'HOLD', 'stale': False}
    monkeypatch.setattr(web, 'build_spy_signals', build)
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, '/api/spy-signals', json={})
        assert started.wait(5)
        second = pool.submit(client.post, '/api/spy-signals', json={})
        release.set()
        assert first.result().json()['model'] == second.result().json()['model'] == 'HOLD'
    assert calls == [1]


def test_spy_closed_market_reuses_model_until_new_candle(monkeypatch):
    import pandas as pd
    from fasttrade import web
    now = pd.Timestamp.now(tz='UTC').floor('10min') - pd.Timedelta(hours=2)
    frame = pd.DataFrame({'timestamp': pd.date_range(end=now, periods=60, freq='10min'),
                          'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 10.})
    calls = []
    clock = [1000.]
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(web, '_spy_signals_cache', None)
    monkeypatch.setattr(web, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(web, 'outside_regular_hours', lambda now: False)
    monkeypatch.setattr(web, 'alpaca', lambda *args: (frame, {}, []))
    monkeypatch.setattr(web, 'predict', lambda state, *args: calls.append(state['candle_timestamp']) or {'action': 'BUY'})
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.post('/api/spy-signals', json={}).json()['model'] == 'BUY'
    clock[0] += 600
    assert client.post('/api/spy-signals', json={}).json()['model'] == 'BUY'
    assert len(calls) == 1
    frame.loc[59, 'timestamp'] += pd.Timedelta(minutes=10)
    clock[0] += 600
    assert client.post('/api/spy-signals', json={}).json()['model'] == 'BUY'
    assert len(calls) == 2


def test_spy_signals_remain_unchanged_outside_regular_hours(monkeypatch):
    from fasttrade import web
    previous = {'deterministic': 'HOLD', 'model': 'BUY', 'stale': False, 'candle_timestamp': '2026-01-02T20:50:00Z'}
    monkeypatch.setattr(web, 'outside_regular_hours', lambda now: True)
    monkeypatch.setattr(web, 'alpaca', lambda *args: (_ for _ in ()).throw(AssertionError('unexpected data request')))
    assert web.build_spy_signals(previous) == previous


def test_regular_hours_use_new_york_time():
    import pandas as pd
    from fasttrade.web import outside_regular_hours
    assert outside_regular_hours(pd.Timestamp('2026-11-02T14:29:00Z'))
    assert not outside_regular_hours(pd.Timestamp('2026-11-02T14:30:00Z'))
    assert outside_regular_hours(pd.Timestamp('2026-11-02T21:00:00Z'))
    assert outside_regular_hours(pd.Timestamp('2026-11-01T15:00:00Z'))


def test_spy_deterministic_hold_is_not_a_model_failure(monkeypatch):
    import pandas as pd
    from fasttrade import web
    now = pd.Timestamp.now(tz='UTC').floor('10min')
    frame = pd.DataFrame({'timestamp': pd.date_range(end=now - pd.Timedelta(minutes=10), periods=60, freq='10min'),
                          'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 10.})
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    monkeypatch.setattr(web, 'alpaca', lambda *args: (frame, {}, []))
    monkeypatch.setattr(web, 'predict', lambda *args: (_ for _ in ()).throw(ValueError('model error')))
    result = web.build_spy_signals()
    assert result['deterministic'] == 'HOLD'
    assert result['model'] is None
    assert result['model_error'] is True


def test_spy_signals_share_a_validated_snapshot_without_portfolio(monkeypatch):
    import pandas as pd
    from fasttrade import web
    now = pd.Timestamp.now(tz='UTC').floor('10min')
    frame = pd.DataFrame({'timestamp': pd.date_range(end=now, periods=61, freq='10min'),
                          'open': 100., 'high': 101., 'low': 99., 'close': [100.] * 60 + [101.], 'volume': 10.})
    seen = []
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-key')
    def bars(symbol, timeframe, as_of):
        assert symbol == 'SPY' and timeframe == '10m'
        return frame, {}, []
    monkeypatch.setattr(web, 'alpaca', bars)
    monkeypatch.setattr(web, 'predict', lambda state, *args: seen.append(state) or {'action': 'SELL'})
    result = web.build_spy_signals()
    assert result['deterministic'] == 'HOLD' and result['model'] == 'SELL'
    assert seen[0]['symbol'] == 'SPY' and seen[0]['timeframe'] == '10m'
    assert pd.Timestamp(seen[0]['candle_timestamp']) == now - pd.Timedelta(minutes=10)
    assert seen[0]['features']['price'] == 100
    assert 'portfolio' not in seen[0] and 'position' not in seen[0]


def test_local_api_rejects_external_origins_and_invalid_input(monkeypatch):
    from fasttrade import web
    monkeypatch.setattr(web, 'run_analysis', lambda args: (_ for _ in ()).throw(AssertionError('unexpected call')))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.post('/api/analyze', headers={'Origin': 'https://evil.example'}, json={'symbol': 'NVDA'}).status_code == 403
    assert client.post('/api/analyze', json={'symbol': '../bad'}).status_code == 422
    assert client.post('/api/analyze', json={'symbol': 'NVDA', 'timeframe': 'bad'}).status_code == 422
    assert client.post('/api/analyze', json={'symbol': 'NVDA', 'timeframe': '90m'}).status_code == 422
    assert client.post('/api/analyze', json={'symbol': 'NVDA', 'systemone_url': 'https://evil.example'}).status_code == 422


def test_security_headers_apply_to_normal_and_rejected_requests():
    from fasttrade.web import app
    client = TestClient(app, base_url='http://127.0.0.1:8765')
    responses = (client.get('/api/health'), client.get('/api/health', headers={'Origin': 'https://evil.example'}),
                 TestClient(app, base_url='http://example.com').get('/api/health'))
    for response in responses:
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert response.headers['x-frame-options'] == 'DENY'


def test_provider_error_has_safe_message(monkeypatch):
    from fasttrade import web
    def fail(args):
        raise ValueError('SystemOne request failed: timeout')
    monkeypatch.setattr(web, 'run_analysis', fail)
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    response = client.post('/api/analyze', json={'symbol': 'NVDA'})
    assert response.status_code == 503
    assert response.json()['actionable_signal'] is None
    assert 'timed out' in response.json()['error'].lower()


def test_sample_cannot_produce_actionable_trade(monkeypatch):
    from fasttrade import web
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'actionable_signal': 'BUY', 'risk_rejections': [], 'decision': {'action': 'BUY'}})
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    response = client.post('/api/analyze', json={'symbol': 'NVDA', 'sample': True})
    assert response.status_code == 200
    assert response.json()['actionable_signal'] == 'HOLD'
    assert 'sample' in response.json()['risk_rejections'][-1].lower()


def test_sample_end_to_end_with_mocked_model(monkeypatch):
    from fasttrade import cli, decisions, web
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize({'model': 'test', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .9, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}}}}, 1000))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    data = {'symbol': 'NVDA', 'sample': True, 'position': 0, 'equity': 10000, 'exposure': 0, 'daily_loss': 0, 'trade_count': 0, 'quantity': 1, 'max_position': 1000, 'max_exposure': 5000, 'max_daily_loss': 200, 'max_trades': 5, 'min_confidence': 0}
    response = client.post('/api/analyze', json=data)
    assert response.status_code == 200
    result = response.json()
    assert result['decision']['action'] == 'BUY' and result['actionable_signal'] == 'HOLD'
    assert len(result['candles']) == 78
    assert result['source'] == 'Synthetic sample CSV'


def test_sample_without_portfolio_is_signal_only(monkeypatch):
    from fasttrade import cli, decisions, web
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize({'model': 'test', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .9, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}}}}, 1000))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    result = client.post('/api/analyze', json={'symbol': 'NVDA', 'sample': True}).json()
    assert result['decision']['action'] == 'BUY'
    assert result['actionable_signal'] is None
    assert any('sample' in reason.lower() for reason in result['risk_rejections'])


def test_market_timeout_explains_yahoo_connectivity(monkeypatch):
    from fasttrade import web
    def fail(args):
        raise ValueError('Market data provider timed out')
    monkeypatch.setattr(web, 'run_analysis', fail)
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    response = client.post('/api/analyze', json={'symbol': 'NVDA'})
    assert response.status_code == 503
    assert 'Yahoo Finance' in response.json()['error']
    assert 'timed out' in response.json()['error']
    assert response.json()['actionable_signal'] is None
