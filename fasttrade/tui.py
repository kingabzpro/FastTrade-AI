from copy import copy
from datetime import datetime, timezone
import math
import os
from urllib.parse import urlsplit

from rich.text import Text
from dotenv import load_dotenv
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, LoadingIndicator, OptionList, Select, Static

from .cli import run as run_analysis
from .market import INTERVALS
from .chart import render_chart


RISK_FIELDS = (
    ('position', 'Current position (shares)'), ('equity', 'Account equity'),
    ('exposure', 'Total long exposure'), ('daily_loss', 'Daily loss'),
    ('trade_count', 'Trades today'), ('quantity', 'Proposed shares'),
    ('max_position', 'Max position value'), ('max_exposure', 'Max exposure'),
    ('max_daily_loss', 'Max daily loss'), ('max_trades', 'Max trades'),
    ('min_confidence', 'Minimum confidence (0–1)'),
)


class TickerInput(Input):
    BINDINGS = [Binding('ctrl+a', 'select_all', show=False, priority=True)]


class HelpScreen(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close')]
    CSS = 'HelpScreen { align: center middle; background: #000000 70%; } HelpScreen Static { width: 64; max-width: 95%; height: auto; background: #182433; padding: 1 2; border: round #6185a3; }'
    def compose(self):
        yield Static('KEYBOARD HELP\n\n1 Overview   2 Chart   3 Backtest   4 Paper   5 Settings\nA Analyze / R refresh   / Symbol   F5 Analyze   Q or Ctrl+Q Quit\nChart: + / - zoom, left / right pan, O change overlay\nEsc closes this help. Shortcuts do not intercept typing.')


class HistoryScreen(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close')]
    CSS = '''
    HistoryScreen { align: center middle; background: #000000 70%; }
    #history-dialog { width: 92%; height: 90%; background: #182433; padding: 1; }
    #history-list { height: 8; }
    #history-detail { height: 1fr; overflow-y: auto; }
    '''
    def __init__(self, records):
        super().__init__()
        self.records = records
    def compose(self):
        import json
        with Vertical(id='history-dialog'):
            yield Static('SESSION HISTORY • select a signal; Esc closes • not persisted')
            yield OptionList(*[f"{r['market']['candle_timestamp']}  {r['market']['symbol']} {r['market']['timeframe']}  {r['decision']['action']} → {r['actionable_signal']}  {r['decision']['model']}" for r in self.records], id='history-list')
            yield Static(json.dumps({k: v for k, v in self.records[-1].items() if k != 'candles'}, indent=2) if self.records else 'No session signals.', id='history-detail')
    @on(OptionList.OptionSelected, '#history-list')
    def selected(self, event):
        import json
        record = self.records[event.option_index]
        self.query_one('#history-detail', Static).update(Text(json.dumps({k: v for k, v in record.items() if k != 'candles'}, indent=2)))


class RiskSettings(ModalScreen):
    BINDINGS = [('escape', 'dismiss', 'Close')]
    CSS = '''
    RiskSettings { align: center middle; background: #000000 70%; }
    #risk-dialog { width: 64; max-width: 95%; height: 90%; background: #182433; border: round #6185a3; padding: 1 2; }
    #risk-fields { height: 1fr; }
    .risk-row { height: 3; }
    .risk-row Label { width: 1fr; content-align: left middle; }
    .risk-row Input { width: 22; }
    #risk-actions { height: 3; }
    #risk-error { height: 2; color: red; }
    '''

    def __init__(self, args):
        super().__init__()
        self.args = args

    def compose(self):
        with Vertical(id='risk-dialog'):
            yield Static('RISK SETTINGS  •  Leave blank to keep the signal on HOLD')
            with VerticalScroll(id='risk-fields'):
                for name, label in RISK_FIELDS:
                    with Horizontal(classes='risk-row'):
                        yield Label(label)
                        value = getattr(self.args, name)
                        yield Input(value='' if value is None else str(value), placeholder='Required', id='risk-' + name.replace('_', '-'))
            yield Static('', id='risk-error')
            with Horizontal(id='risk-actions'):
                yield Button('Save & analyze', id='save-risk', variant='primary')
                yield Button('Cancel', id='cancel-risk')

    @on(Button.Pressed, '#cancel-risk')
    def cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, '#save-risk')
    def save(self):
        values = {}
        try:
            for name, _ in RISK_FIELDS:
                raw = self.query_one('#risk-' + name.replace('_', '-'), Input).value.strip()
                value = None if not raw else (int(raw) if name in ('trade_count', 'max_trades') else float(raw))
                if value is not None and (not math.isfinite(value) or value < 0 or (name == 'min_confidence' and value > 1)):
                    raise ValueError
                values[name] = value
        except ValueError:
            self.query_one('#risk-error', Static).update('Use nonnegative numbers; trade counts must be whole and confidence at most 1.')
            return
        self.dismiss(values)


class FastTradeApp(App):
    TITLE = 'FASTTRADE / SIGNAL DASHBOARD'
    BINDINGS = [('f5', 'refresh_data', 'Analyze'), ('a', 'refresh_data', 'Analyze'), ('r', 'refresh_data', 'Refresh'), ('ctrl+q', 'quit', 'Quit'), ('q', 'quit', 'Quit'), ('1', 'overview', 'Overview'), ('2', 'chart', 'Chart'), ('3', 'backtest', 'Backtest'), ('4', 'paper', 'Paper'), ('5', 'settings', 'Settings'), ('slash', 'focus_symbol', 'Symbol'), ('question_mark', 'help_screen', 'Help'), ('h', 'history', 'History'), ('plus', 'zoom_in', 'Zoom in'), ('minus', 'zoom_out', 'Zoom out'), ('left', 'pan_left', 'Older'), ('right', 'pan_right', 'Newer'), ('o', 'overlay', 'Overlay')]
    CSS = '''
    Screen { background: #101722; color: #e7edf3; }
    #controls { height: 3; padding: 0 1; }
    #navigation { height: 1; padding: 0 1; }
    #navigation Button { min-width: 0; width: 1fr; height: 1; border: none; padding: 0; }
    #symbol { width: 1fr; min-width: 12; }
    #timeframe { width: 16; }
    #refresh { width: 16; }
    #risk-settings { width: 16; }
    #status-row { height: auto; min-height: 2; padding: 0 1; }
    #status { width: 1fr; height: auto; color: #a6bdd3; }
    #loading { width: 6; height: 1; display: none; color: #48c9e5; }
    #content { height: 1fr; padding: 0 1; }
    #chart-view { height: 1fr; padding: 0 2; display: none; }
    #settings-view { height: 1fr; padding: 0 2; display: none; }
    #settings-view Input { width: 100%; }
    #view-message { height: 1fr; padding: 1 2; display: none; color: #e4b15c; }
    .column { width: 1fr; height: 1fr; }
    .section { height: auto; margin: 0 1 0 0; padding: 0 1; }
    #summary { background: #182a37; }
    #risk { background: #30291c; }
    '''

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.loading = False
        self.view = 'overview'
        self.history = []
        self.current_result = None
        self.chart_window = 30
        self.chart_offset = 0
        self.chart_overlay = 'ema_9'

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id='navigation'):
            for number, label in enumerate(('Overview', 'Chart', 'Backtest', 'Paper', 'Settings'), 1):
                yield Button(f'{number} {label}', id='nav-' + str(number), variant='primary' if number == 1 else 'default')
        with Horizontal(id='controls'):
            yield TickerInput(value=self.args.symbol, placeholder='Ticker', id='symbol')
            yield Select([(v, v) for v in INTERVALS], value=self.args.timeframe, allow_blank=False, id='timeframe')
            yield Button('Analyze', id='refresh', variant='primary')
            yield Button('Risk settings', id='risk-settings')
        with Horizontal(id='status-row'):
            yield LoadingIndicator(id='loading')
            yield Static('Enter a ticker, choose a timeframe, then select Analyze (or press F5).', id='status')
        with Horizontal(id='content'):
            with VerticalScroll(classes='column'):
                yield Static('Waiting for data', id='summary', classes='section')
                yield Static('', id='overview-chart', classes='section')
                yield Static('', id='indicators', classes='section')
                yield Static('', id='evidence', classes='section')
            with VerticalScroll(classes='column'):
                yield Static('Actionable signal: HOLD', id='risk', classes='section')
                yield Static('', id='probabilities', classes='section')
                yield Static('', id='metrics', classes='section')
                yield Static('', id='news', classes='section')
                yield Button('Session history', id='history')
        with VerticalScroll(id='chart-view'):
            yield Static('Run an analysis to see completed candles.', id='chart-detail')
        with VerticalScroll(id='settings-view'):
            yield Static('MODEL SETTINGS • session only; credentials remain in .env')
            yield Label('SystemOne endpoint (only use a trusted provider)')
            yield Input(id='model-url')
            yield Label('Model')
            yield Input(id='model-name')
            yield Button('Apply settings', id='save-settings', variant='primary')
            yield Static('Risk: use Risk settings above. Market source: chosen at startup.\nProvider connection test and broker settings: unavailable.', id='settings-note')
        yield Static('', id='view-message')
        yield Footer()

    def on_mount(self):
        self.clear_result()
        load_dotenv()
        self.query_one('#model-url', Input).value = os.getenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
        self.query_one('#model-name', Input).value = os.getenv('SYSTEMONE_MODEL', 'jev-latest')
        self.query_one('#refresh', Button).focus()

    @on(Button.Pressed, '#nav-1')
    def nav_overview(self): self.action_overview()
    @on(Button.Pressed, '#nav-2')
    def nav_chart(self): self.action_chart()
    @on(Button.Pressed, '#nav-3')
    def nav_backtest(self): self.action_backtest()
    @on(Button.Pressed, '#nav-4')
    def nav_paper(self): self.action_paper()
    @on(Button.Pressed, '#nav-5')
    def nav_settings(self): self.action_settings()
    @on(Button.Pressed, '#history')
    def open_history(self): self.push_screen(HistoryScreen(self.history))
    def action_history(self): self.push_screen(HistoryScreen(self.history))

    @on(Button.Pressed, '#save-settings')
    def save_model_settings(self):
        url = self.query_one('#model-url', Input).value.strip()
        model = self.query_one('#model-name', Input).value.strip()
        try:
            parts = urlsplit(url)
            valid = parts.scheme in ('http', 'https') and parts.hostname and not parts.username and not parts.password and model
        except ValueError:
            valid = False
        if not valid:
            self.query_one('#settings-note', Static).update('Enter an HTTP(S) URL without credentials and a model name.')
            return
        self.args.systemone_url = url
        self.args.systemone_model = model
        self.clear_result()
        self.query_one('#status', Static).update('Settings applied for this session. Select Analyze to run.')
        self.query_one('#settings-note', Static).update('Applied for this session. Risk: use Risk settings above. Connection test: unavailable.')

    def set_view(self, name, message=''):
        self.view = name
        self.query_one('#content').display = name == 'overview'
        self.query_one('#chart-view').display = name == 'chart'
        self.query_one('#settings-view').display = name == 'settings'
        self.query_one('#view-message').display = name not in ('overview', 'chart', 'settings')
        self.query_one('#view-message', Static).update(Text(message))
        active = {'overview': 1, 'chart': 2, 'backtest': 3, 'paper': 4, 'settings': 5}[name]
        for i in range(1, 6):
            self.query_one('#nav-' + str(i), Button).variant = 'primary' if i == active else 'default'
        if name == 'chart': self.update_chart()

    def action_overview(self): self.set_view('overview')
    def action_chart(self): self.set_view('chart')
    def action_backtest(self): self.set_view('backtest', 'BACKTEST: unavailable. This installation has no backtesting engine or results.')
    def action_paper(self): self.set_view('paper', 'PAPER TRADING: unavailable. No broker is connected and orders cannot be placed.')
    def action_settings(self): self.set_view('settings', 'SETTINGS\nRisk limits: use the Risk settings button above.\nModel URL, model and API key: configure in .env; see README.\nBroker configuration and connection tests: unavailable.')
    def action_focus_symbol(self): self.query_one('#symbol', Input).focus()
    def action_help_screen(self): self.push_screen(HelpScreen())

    def update_chart(self):
        candles = self.current_result.get('candles', []) if self.current_result else []
        interval = INTERVALS[self.current_result['market']['timeframe']] if self.current_result else 300
        marker = self.current_result['decision']['action'] if candles else None
        chart = render_chart(candles, interval, self.chart_window, self.chart_offset, self.chart_overlay, marker)
        if candles:
            chart.append('\n+/- zoom  ←/→ pan  O overlay  |  Completed candles, UTC', style='dim')
        self.query_one('#chart-detail', Static).update(chart)
        self.query_one('#overview-chart').display = bool(candles)
        self.query_one('#overview-chart', Static).update(render_chart(candles, interval, 18, 0, self.chart_overlay, compact=True))

    def action_zoom_in(self):
        if self.view == 'chart': self.chart_window = max(10, self.chart_window - 10); self.update_chart()
    def action_zoom_out(self):
        if self.view == 'chart': self.chart_window = min(50, self.chart_window + 10); self.update_chart()
    def action_pan_left(self):
        if self.view == 'chart': self.chart_offset = min(max(0, len(self.current_result.get('candles', [])) - 1) if self.current_result else 0, self.chart_offset + 10); self.update_chart()
    def action_pan_right(self):
        if self.view == 'chart': self.chart_offset = max(0, self.chart_offset - 10); self.update_chart()
    def action_overlay(self):
        if self.view == 'chart':
            options = ('ema_9', 'ema_21', 'ema_50', 'vwap', None)
            self.chart_overlay = options[(options.index(self.chart_overlay) + 1) % len(options)]
            self.update_chart()

    @on(Button.Pressed, '#refresh')
    def refresh_pressed(self):
        self.action_refresh_data()

    @on(Input.Submitted, '#symbol')
    def symbol_submitted(self):
        self.action_refresh_data()

    @on(Button.Pressed, '#risk-settings')
    def open_risk_settings(self):
        def saved(values):
            if values is not None:
                for name, value in values.items():
                    setattr(self.args, name, value)
                self.action_refresh_data()
        self.push_screen(RiskSettings(self.args), saved)

    def action_refresh_data(self):
        if self.loading:
            return
        symbol = self.query_one('#symbol', Input).value.strip().upper()
        if not symbol:
            self.show_error('Enter a ticker symbol.')
            return
        args = copy(self.args)
        args.symbol = symbol
        args.timeframe = str(self.query_one('#timeframe', Select).value)
        args.allow_stale_preview = True
        args.include_chart = True
        self.loading = True
        self.query_one('#refresh', Button).disabled = True
        self.query_one('#risk-settings', Button).disabled = True
        self.query_one('#symbol', Input).disabled = True
        self.query_one('#timeframe', Select).disabled = True
        self.clear_result()
        self.query_one('#loading').display = True
        self.query_one('#status', Static).update('ANALYZING • Fetching completed candles and requesting a model decision...')
        self.fetch(args)

    @work(thread=True)
    def fetch(self, args):
        try:
            result = run_analysis(args)
        except ValueError as exc:
            safe_errors = {'Market data provider failed': 'Market data unavailable. Check the symbol, interval, and network; retry.',
                           'Market data provider returned no candles': 'No candles returned. Check the symbol and timeframe; retry.',
                           'Latest completed candle is stale': 'Candles are stale. Retry when fresher data is available.',
                           'No completed candles': 'No completed candles. Check the selected time and timeframe.',
                           'At least 50 completed candles required': 'Not enough candles. Choose a shorter interval or a larger dataset.',
                           'SystemOne request failed': 'SystemOne request failed. Check the endpoint, key, and network; retry.',
                           'SystemOne request failed: timeout': 'SystemOne timed out. Check the endpoint and retry.',
                           'Invalid SystemOne response': 'Invalid SystemOne response. Check the model and provider compatibility.',
                           'TYPESAFE_API_KEY is missing': 'TYPESAFE_API_KEY is missing. Add it to .env, then retry.'}
            message = safe_errors.get(str(exc), 'Analysis failed. Check market data and settings.')
            self.app.call_from_thread(self.show_error, message)
            return
        except Exception:
            # Do not expose provider exceptions that might contain credentials.
            self.app.call_from_thread(self.show_error, 'Analysis failed. Check market data, settings, and model connection.')
            return
        self.app.call_from_thread(self.show_result, result, args.csv is not None)

    def clear_result(self):
        self.query_one('#summary', Static).update('No current decision')
        self.current_result = None
        self.query_one('#chart-detail', Static).update('No current chart. Run an analysis to load completed candles.')
        for section in ('indicators', 'probabilities', 'evidence', 'news', 'overview-chart', 'metrics'):
            self.query_one('#' + section).display = False
        self.query_one('#risk', Static).update('Actionable signal: HOLD\nNo current analysis')

    def finish_loading(self):
        self.loading = False
        self.query_one('#loading').display = False
        self.query_one('#refresh', Button).disabled = False
        self.query_one('#risk-settings', Button).disabled = False
        self.query_one('#symbol', Input).disabled = False
        self.query_one('#timeframe', Select).disabled = False

    def show_error(self, message):
        self.clear_result()
        self.query_one('#status', Static).update(Text('ERROR • ' + message, style='bold red'))
        self.finish_loading()

    def show_result(self, result, csv):
        self.current_result = result
        self.history.append(result)
        self.history = self.history[-100:]
        self.chart_offset = 0
        market = result['market']
        data = market['features']
        decision = result['decision']
        mode = 'STALE DATA / RESEARCH PREVIEW • HOLD ONLY' if result.get('stale_preview') else ('CSV / HISTORICAL' if csv else 'YAHOO FINANCE')
        timestamp = datetime.fromisoformat(market['candle_timestamp'].replace('Z', '+00:00')).astimezone(timezone.utc)
        candle_time = timestamp.strftime('%d %b %Y, %H:%M UTC')
        status = Text()
        status.append(f'{mode} • RESEARCH ONLY\n', style='bold yellow' if result.get('stale_preview') else 'bold cyan')
        status.append(f"Candle start: {candle_time} | Model: {decision['model']} | Request: {decision['latency_us'] / 1000:.0f} ms")
        self.query_one('#status', Static).update(status)
        summary = Text(f"{market['symbol']}  ·  {market['timeframe']}\n", style='bold cyan')
        summary.append(f"Price  {data['price']:.2f}    Model preference  ")
        summary.append(decision['action'], style={'BUY': 'bold green', 'HOLD': 'bold white', 'SELL': 'bold red'}.get(decision['action'], 'bold'))
        self.query_one('#summary', Static).update(summary)
        self.query_one('#indicators').display = True
        indicators = Text('TECHNICAL ANALYSIS\n', style='bold cyan')
        indicators.append(f"RSI  {data['rsi_14']:.1f}    MACD  {data['macd']:.3f}\nVWAP  {data['vwap_delta']:+.2%}    Volume  {data['volume_ratio']:.2f}x\nATR  {data['atr_14']:.3f}", style='white')
        self.query_one('#indicators', Static).update(indicators)
        probabilities = Text('MODEL PREFERENCES (not profit odds)\n', style='bold cyan')
        scores = decision.get('probabilities') or {}
        for action, color in (('BUY', 'green'), ('HOLD', 'white'), ('SELL', 'red')):
            value = scores.get(action)
            if value is None:
                continue
            probabilities.append(f'{action:5} ', style=color)
            probabilities.append('━' * round(value * 15), style=color)
            probabilities.append(f' {value:.0%}\n')
        self.query_one('#probabilities').display = bool(scores)
        self.query_one('#probabilities', Static).update(probabilities)
        self.query_one('#evidence').display = bool(result['indicator_interpretations'])
        evidence = Text('INDICATOR EVIDENCE\n', style='bold cyan')
        evidence.append('\n'.join('• ' + item for item in result['indicator_interpretations']), style='white')
        self.query_one('#evidence', Static).update(evidence)
        reasons = result['risk_rejections']
        risk = Text('RISK CHECK  ·  Actionable: ', style='bold yellow')
        risk.append(result['actionable_signal'] + '\n', style='bold yellow' if result['actionable_signal'] == 'HOLD' else 'bold green')
        if reasons and decision['action'] != 'HOLD': risk.append('BLOCKED — proposed action rejected\n', style='bold yellow')
        risk.append('\n'.join('• ' + reason for reason in reasons) if reasons else 'No risk rejections', style='white')
        position = result.get('portfolio', {}).get('position')
        risk.append(f"\nPosition: {position:g} shares" if position is not None else '\nPosition: unavailable', style='dim')
        risk.append('\nMode: signal only • no orders', style='dim')
        self.query_one('#risk', Static).update(risk)
        news = market['headlines']
        self.query_one('#news').display = bool(news)
        news_text = Text('RECENT NEWS\n', style='bold cyan')
        news_text.append('\n'.join(f"• {item['title']} — {item['source']}  |  {item['published']}" for item in news), style='white')
        self.query_one('#news', Static).update(news_text)
        metrics = result.get('timings_ms')
        self.query_one('#metrics').display = bool(metrics)
        if metrics:
            self.query_one('#metrics', Static).update(Text('LATENCY (ms)\nIndicators {:.1f}  Model {:.1f}\nRisk {:.1f}  Total {:.1f}'.format(metrics['indicators'], metrics['model'], metrics['risk'], metrics['total'])))
        self.update_chart()
        self.finish_loading()
