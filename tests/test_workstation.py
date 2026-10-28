import asyncio
import pandas as pd
from fasttrade import cli, decisions
from test_core import candles
from test_integration import answer


def test_analysis_supplies_chart_and_timings_without_lookahead(monkeypatch, tmp_path):
    frame = candles()
    path = tmp_path / 'bars.csv'
    frame.to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 2000))
    args = cli.parser().parse_args(['tui', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:25:00Z'])
    args.include_chart = True
    result = cli.run(args)
    assert len(result['candles']) == 59
    assert result['candles'][-1]['timestamp'] == '2026-01-02T19:20:00+00:00'
    assert result['candles'][-1]['ema_9'] > 0
    assert result['timings_ms']['total'] >= result['timings_ms']['indicators'] >= 0
    assert result['timings_ms']['model'] == 2


def test_chart_marks_gaps_and_renders_volume():
    from fasttrade.chart import render_chart
    bars = [{'timestamp': '2026-01-02T14:30:00+00:00', 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 50},
            {'timestamp': '2026-01-02T14:45:00+00:00', 'open': 11, 'high': 13, 'low': 10, 'close': 12, 'volume': 100}]
    chart = str(render_chart(bars, 300))
    assert 'GAP' in chart and 'VOL' in chart and '12.00' in chart


def test_navigation_and_unavailable_views(monkeypatch):
    from fasttrade.tui import FastTradeApp
    async def check():
        app = FastTradeApp(cli.parser().parse_args(['tui']))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press('2')
            assert app.view == 'chart'
            await pilot.press('3')
            assert 'unavailable' in str(app.query_one('#view-message').render()).lower()
            await pilot.press('4')
            assert 'unavailable' in str(app.query_one('#view-message').render()).lower()
            await pilot.press('1')
            assert app.view == 'overview'
    asyncio.run(check())


def test_typing_does_not_trigger_shortcuts():
    from textual.widgets import Input
    from fasttrade.tui import FastTradeApp
    async def check():
        app = FastTradeApp(cli.parser().parse_args(['tui']))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click('#symbol')
            await pilot.press('ctrl+a', 'a', 'r', 'q', '1')
            assert app.query_one('#symbol', Input).value == 'arq1'
            assert app.view == 'overview' and not app.loading
    asyncio.run(check())


def test_blocked_signal_history_and_chart(monkeypatch, tmp_path):
    from fasttrade.tui import FastTradeApp
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 2000))
    async def check():
        args = cli.parser().parse_args(['tui', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z'])
        app = FastTradeApp(args)
        async with app.run_test(size=(120, 35)) as pilot:
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert 'BLOCKED' in str(app.query_one('#risk').render())
            assert len(app.history) == 1
            await pilot.press('2')
            assert 'OHLC' in str(app.query_one('#chart-detail').render())
            await pilot.press('o')
            assert 'EMA_21' in str(app.query_one('#chart-detail').render())
            await pilot.press('h')
            assert 'TEST' in str(app.screen.query_one('#history-detail').render())
            await pilot.press('escape')
            assert app.view == 'chart'
    asyncio.run(check())


def test_settings_apply_endpoint_and_model_for_next_analysis(monkeypatch):
    from textual.widgets import Input
    from fasttrade.tui import FastTradeApp
    seen = []
    def analyze(args):
        seen.append((args.systemone_url, args.systemone_model))
        raise ValueError('Market data provider failed')
    monkeypatch.setattr('fasttrade.tui.run_analysis', analyze)
    async def check():
        app = FastTradeApp(cli.parser().parse_args(['tui']))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('5')
            app.query_one('#model-url', Input).value = 'http://localhost:11434/v1/systemone'
            app.query_one('#model-name', Input).value = 'd1'
            await pilot.click('#save-settings')
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            assert seen == [('http://localhost:11434/v1/systemone', 'd1')]
    asyncio.run(check())
