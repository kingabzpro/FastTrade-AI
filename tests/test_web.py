from fastapi.testclient import TestClient


def test_web_launcher_uses_new_port():
    from fasttrade.web import PORT
    assert PORT == 8765


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
    assert 'test-model' in output and 'Yahoo Finance' in output
    assert 'API key' not in output and 'private-test-key' not in output and '\x1b[' not in output


def test_startup_banner_uses_colors_on_a_terminal(monkeypatch, capsys):
    import socket
    import threading
    import uvicorn
    import rich.console
    from unittest.mock import MagicMock
    from fasttrade import web

    console = rich.console.Console
    monkeypatch.delenv('NO_COLOR', raising=False)
    monkeypatch.setattr(rich.console, 'Console', lambda: console(force_terminal=True, color_system='standard'))
    monkeypatch.setattr(socket, 'socket', MagicMock())
    monkeypatch.setattr(threading, 'Timer', MagicMock())
    monkeypatch.setattr(uvicorn, 'Server', MagicMock())
    web.serve()
    assert '\x1b[' in capsys.readouterr().out


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


def test_local_api_rejects_external_origins_and_invalid_input(monkeypatch):
    from fasttrade import web
    monkeypatch.setattr(web, 'run_analysis', lambda args: (_ for _ in ()).throw(AssertionError('unexpected call')))
    client = TestClient(web.app, base_url='http://127.0.0.1:8765')
    assert client.post('/api/analyze', headers={'Origin': 'https://evil.example'}, json={'symbol': 'NVDA'}).status_code == 403
    assert client.post('/api/analyze', json={'symbol': '../bad'}).status_code == 422
    assert client.post('/api/analyze', json={'symbol': 'NVDA', 'timeframe': 'bad'}).status_code == 422
    assert client.post('/api/analyze', json={'symbol': 'NVDA', 'systemone_url': 'https://evil.example'}).status_code == 422


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
