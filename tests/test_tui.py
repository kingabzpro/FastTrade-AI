import asyncio

from fasttrade.cli import parser


def test_dashboard_waits_for_user_before_requesting_analysis(monkeypatch):
    from fasttrade.tui import FastTradeApp
    calls = []
    monkeypatch.setattr('fasttrade.tui.run_analysis', lambda args: calls.append(args.symbol))
    async def check():
        app = FastTradeApp(parser().parse_args(['tui']))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert calls == []
            assert not app.query_one('#loading').display
            assert not app.query_one('#probabilities').display
    asyncio.run(check())


def test_ft_defaults_to_dashboard(monkeypatch):
    from fasttrade.cli import dashboard
    seen = []
    monkeypatch.setattr('fasttrade.web.serve', lambda: seen.append('web'))
    monkeypatch.setattr('sys.argv', ['ft'])
    assert dashboard() == 0
    assert seen == ['web']


def test_tui_displays_decision_and_risk(monkeypatch):
    from fasttrade.tui import FastTradeApp
    result = {
        'market': {'symbol': 'NVDA', 'timeframe': '5m', 'candle_timestamp': '2026-01-02T21:00:00Z',
                   'features': {'price': 142.85, 'rsi_14': 58, 'macd': .42, 'vwap_delta': .008,
                                'volume_ratio': 1.65, 'atr_14': 1.25}, 'headlines': []},
        'decision': {'action': 'BUY', 'probabilities': {'BUY': .69, 'HOLD': .24, 'SELL': .07},
                     'model': 'jev', 'latency_us': 1000},
        'indicator_interpretations': ['Price above VWAP'],
        'risk_rejections': ['Position limit exceeded'], 'actionable_signal': 'HOLD',
        'stale_preview': True,
    }
    calls = []
    def analyze(args):
        calls.append(args.symbol)
        if len(calls) > 1:
            raise ValueError('Market data provider failed')
        return result
    monkeypatch.setattr('fasttrade.tui.run_analysis', analyze)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui', 'NVDA']))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            status = str(app.query_one('#status').render())
            assert '02 Jan 2026' in status and '21:00 UTC' in status
            assert 'Candle start' in status and 'jev' in status and '1 ms' in status
            assert 'STALE DATA' in status and 'HOLD ONLY' in status
            assert app.query_one('#status').region.height >= 2
            assert '142.85' in str(app.query_one('#summary').render())
            assert '69%' in str(app.query_one('#probabilities').render())
            assert 'Position limit exceeded' in str(app.query_one('#risk').render())
            assert 'HOLD' in str(app.query_one('#risk').render())
            for name in ('summary', 'indicators', 'evidence', 'risk', 'probabilities'):
                assert app.query_one('#' + name).region.bottom <= app.query_one('#content').region.bottom
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert 'Market data unavailable' in str(app.query_one('#status').render())
            assert '142.85' not in str(app.query_one('#summary').render())
            assert 'HOLD' in str(app.query_one('#risk').render())
    asyncio.run(check())


def test_loading_animation_hides_old_data(monkeypatch):
    import threading
    from fasttrade.tui import FastTradeApp
    release = threading.Event()
    def analyze(args):
        release.wait(timeout=5)
        raise ValueError('Market data provider failed')
    monkeypatch.setattr('fasttrade.tui.run_analysis', analyze)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui']))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.click('#refresh')
            await pilot.pause()
            assert app.query_one('#loading').display
            assert not app.query_one('#probabilities').display
            release.set()
            await app.workers.wait_for_complete()
            assert not app.query_one('#loading').display
    asyncio.run(check())


def test_tui_clears_old_result_on_error(monkeypatch):
    from fasttrade.tui import FastTradeApp
    def fail(args):
        raise ValueError('Market data provider failed')
    monkeypatch.setattr('fasttrade.tui.run_analysis', fail)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui', 'NVDA']))
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert 'Market data unavailable' in str(app.query_one('#status').render())
            assert 'HOLD' in str(app.query_one('#risk').render())
    asyncio.run(check())


def test_edit_ticker_and_timeframe_then_analyze(monkeypatch):
    from textual.widgets import Input, Select
    from fasttrade.tui import FastTradeApp
    calls = []
    def analyze(args):
        calls.append((args.symbol, args.timeframe, args.allow_stale_preview))
        raise ValueError('Market data provider failed')
    monkeypatch.setattr('fasttrade.tui.run_analysis', analyze)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui']))
        async with app.run_test(size=(90, 35)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.click('#symbol')
            await pilot.press('ctrl+a', 'a', 'a', 'p', 'l')
            app.query_one('#timeframe', Select).value = '1m'
            await pilot.press('f5')
            await app.workers.wait_for_complete()
            assert calls == [('NVDA', '5m', True), ('AAPL', '1m', True)]
            assert not app.query_one('#refresh').disabled
    asyncio.run(check())


def test_risk_settings_can_be_edited_in_dashboard(monkeypatch):
    from textual.widgets import Input
    from fasttrade.tui import FastTradeApp
    calls = []
    def analyze(args):
        calls.append(args.position)
        raise ValueError('Market data provider failed')
    monkeypatch.setattr('fasttrade.tui.run_analysis', analyze)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui']))
        async with app.run_test(size=(100, 45)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.click('#risk-settings')
            app.screen.query_one('#risk-position', Input).value = '3'
            await pilot.click('#save-risk')
            await app.workers.wait_for_complete()
            assert calls == [None, 3.0]
    asyncio.run(check())


def test_dashboard_fits_and_hides_unavailable_data(monkeypatch):
    from fasttrade.tui import FastTradeApp
    result = {
        'market': {'symbol': 'NVDA', 'timeframe': '5m', 'candle_timestamp': '2026-01-02T21:00:00Z',
                   'features': {'price': 142.85, 'rsi_14': 58, 'macd': .42, 'vwap_delta': .008,
                                'volume_ratio': 1.65, 'atr_14': 1.25}, 'headlines': []},
        'decision': {'action': 'HOLD', 'probabilities': {}, 'model': 'jev', 'latency_us': 1000},
        'indicator_interpretations': ['Price above VWAP'], 'risk_rejections': [], 'actionable_signal': 'HOLD',
    }
    monkeypatch.setattr('fasttrade.tui.run_analysis', lambda args: result)
    async def check():
        app = FastTradeApp(parser().parse_args(['tui']))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.query_one('#probabilities').display
            assert not app.query_one('#news').display
            assert app.query_one('#risk').region.bottom <= app.query_one('#content').region.bottom
            assert not app.query_one('#loading').display
    asyncio.run(check())
