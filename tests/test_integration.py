import httpx
import pandas as pd
import pytest
from fasttrade import decisions, market, cli
from test_core import candles


def answer():
    return {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}}}


def test_terminal_dashboard_command_is_not_available():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['tui'])


def test_ft_launches_dashboard_without_args_and_cli_with_args(monkeypatch):
    from fasttrade import web
    calls = []
    monkeypatch.setattr(web, 'serve', lambda: calls.append('dashboard'))
    monkeypatch.setattr(cli, 'main', lambda: calls.append('cli') or 0)
    monkeypatch.setattr(cli.sys, 'argv', ['ft'])
    assert cli.dashboard() == 0
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'benchmark', '--help'])
    assert cli.dashboard() == 0
    assert calls == ['dashboard', 'cli']


def test_decision_request_and_timeout(monkeypatch):
    def post(url, **kwargs):
        assert kwargs['json']['questions']['action']['type'] == 'choice'
        return httpx.Response(200, json=answer(), request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions.httpx, 'post', post)
    assert decisions.predict({}, 'https://example.com', 'jev', 'key')['action'] == 'BUY'
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(decisions.httpx, 'post', timeout)
    with pytest.raises(ValueError, match='request failed'):
        decisions.predict({}, 'https://example.com', 'jev', 'key')


def test_csv_cli_missing_risk_is_signal_only(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z'])
    result = cli.run(args)
    assert result['decision']['action'] == 'BUY'
    assert result['actionable_signal'] is None
    assert result['risk_rejections'] == []


def test_partial_portfolio_has_no_actionable_signal(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z', '--position', '1'])
    result = cli.run(args)
    assert result['actionable_signal'] is None
    assert result['risk_rejections']


def test_analysis_supplies_chart_and_timings_without_lookahead(monkeypatch, tmp_path):
    path = tmp_path / 'bars.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 2000))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:25:00Z'])
    args.include_chart = True
    result = cli.run(args)
    assert len(result['candles']) == 59
    assert result['candles'][-1]['timestamp'] == '2026-01-02T19:20:00+00:00'
    assert result['candles'][-1]['ema_9'] > 0
    assert result['timings_ms']['total'] >= result['timings_ms']['indicators'] >= 0
    assert result['timings_ms']['model'] == 2


def test_invalid_candles_and_vwap_session():
    from fasttrade.indicators import features
    frame = candles()
    frame.loc[3, 'high'] = 1
    with pytest.raises(ValueError, match='OHLC'):
        market.validate_candles(frame, '5m', pd.Timestamp('2026-01-02T19:30Z'), 300)
    with pytest.raises(ValueError, match='50'):
        market.validate_candles(candles().iloc[-10:], '5m', pd.Timestamp('2026-01-02T19:30Z'), 300)
    frame = candles()
    frame.loc[59, 'timestamp'] = pd.Timestamp('2026-01-03T14:30Z')
    assert features(frame, 'America/New_York')['vwap'] == pytest.approx(frame.close.iloc[-1])


def test_yahoo_news_failure_does_not_block(monkeypatch):
    import yfinance
    class Ticker:
        def history(self, **kwargs):
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        @property
        def info(self):
            return {'shortName': 'Example'}
        @property
        def news(self):
            raise RuntimeError('unavailable')
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    frame, details, news = market.yahoo('TEST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(frame) == 60 and details['shortName'] == 'Example' and news == []


def test_yfinance_quote_parser_has_no_inplace_deprecation():
    import warnings
    from yfinance.utils import parse_quotes

    data = {'timestamp': [2, 1], 'indicators': {'quote': [{
        'open': [1.0, 1.0], 'high': [2.0, 2.0], 'low': [0.5, 0.5],
        'close': [1.5, 1.5], 'volume': [10, 20],
    }]}}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        quotes = parse_quotes(data)
    assert quotes.index.is_monotonic_increasing
    assert not any('inplace keyword' in str(w.message) for w in caught)


def test_yahoo_market_failure(monkeypatch):
    import yfinance
    class Ticker:
        def history(self, **kwargs):
            raise RuntimeError('provider unavailable')
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    with pytest.raises(ValueError, match='provider failed'):
        market.yahoo('TEST', '5m', pd.Timestamp('2026-01-02T19:30Z'))


def test_yahoo_1m_requests_supported_history(monkeypatch):
    import yfinance
    seen = []
    class Ticker:
        def history(self, **kwargs):
            seen.append(kwargs['period'])
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        info = {}
        news = []
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    market.yahoo('TEST', '1m', pd.Timestamp('2026-01-02T19:30Z'))
    assert seen == ['7d']


def test_stale_preview_cannot_be_actionable(monkeypatch):
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (candles(), {'shortName': 'Current'}, [{'title': 'new', 'source': 'X', 'published': '2026-01-03T00:00:00+00:00'}]))
    states = []
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--as-of', '2026-01-03T00:00:00Z', '--position', '0', '--equity', '10000', '--exposure', '0', '--daily-loss', '0', '--trade-count', '0', '--quantity', '1', '--max-position', '1000', '--max-exposure', '5000', '--max-daily-loss', '200', '--max-trades', '5', '--min-confidence', '0'])
    args.allow_stale_preview = True
    result = cli.run(args)
    assert result['actionable_signal'] == 'HOLD'
    assert any('stale' in reason.lower() for reason in result['risk_rejections'])
    assert states[0]['evaluation_time'] == '2026-01-02T19:30:00+00:00'
    assert states[0]['headlines'] == []


def test_naive_timestamps_rejected():
    frame = candles()
    frame['timestamp'] = frame.timestamp.dt.tz_localize(None)
    with pytest.raises(ValueError, match='timestamp'):
        market.validate_candles(frame, '5m', pd.Timestamp('2026-01-02T19:30Z'), 300)


@pytest.mark.parametrize('overrides', [
    {'daily_loss': 100}, {'trade_count': 5}, {'confidence': .4},
    {'exposure': 750}, {'position': 5},
])
def test_risk_limits(overrides):
    from fasttrade.risk import evaluate
    args = dict(action='BUY', confidence=.8, price=100, position=0, equity=1000, exposure=0, daily_loss=0, trade_count=0, quantity=1, max_position=500, max_exposure=800, max_daily_loss=100, max_trades=5, min_confidence=.5)
    args.update(overrides)
    assert evaluate(**args)
