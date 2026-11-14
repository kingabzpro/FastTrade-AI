import httpx
import json
import pandas as pd
import pytest
from fasttrade import decisions, market, cli
from fasttrade.calibration import SCHEMA as CALIBRATION_SCHEMA
from test_calibration import synthetic_report_and_data
from test_core import candles


def answer():
    return {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}}}


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
    monkeypatch.setattr(decisions, 'pooled_post', post)
    assert decisions.predict({}, 'https://example.com', 'jev', 'key')['action'] == 'BUY'
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(decisions, 'pooled_post', timeout)
    with pytest.raises(ValueError, match='request failed'):
        decisions.predict({}, 'https://example.com', 'jev', 'key')


def test_model_retries_explicit_rate_limit_but_not_timeout(monkeypatch):
    from fasttrade import resilience
    resilience._circuits.clear()
    delays, calls, now = [], [], [0.0]
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(resilience, 'sleep', sleep)
    monkeypatch.setattr(resilience.random, 'uniform', lambda *args: 0)
    def post(url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={'Retry-After': '2'}, request=httpx.Request('POST', url))
        data = answer()
        data['usage'] = {'input_tokens': 10, 'output_tokens': 3}
        return httpx.Response(200, json=data, request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions, 'pooled_post', post)
    decision = decisions.predict({}, 'https://example.com', 'jev', 'key')
    assert decision['action'] == 'BUY'
    assert decision['usage']['input_tokens'] == 10
    assert decision['usage']['output_tokens'] is None
    assert decision['usage']['output_tokens_last_attempt'] == 3
    assert len(calls) == 2 and delays == [2]
    def timeout(url, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('unknown outcome')
    monkeypatch.setattr(decisions, 'pooled_post', timeout)
    with pytest.raises(ValueError, match='timeout'):
        decisions.predict({}, 'https://example.com', 'jev', 'key')
    assert len(calls) == 3


def test_csv_cli_missing_risk_is_signal_only(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z'])
    result = cli.run(args)
    assert result['decision']['action'] == 'BUY'
    assert result['rule_comparison']['signal'] in ('BUY', 'SELL', None)
    assert result['rule_comparison']['agreement'] in ('agree', 'disagree', 'no_signal')
    assert result['actionable_signal'] is None
    assert result['risk_rejections'] == []


def test_cli_selects_yahoo_and_validates_candles(monkeypatch):
    monkeypatch.setattr(cli, 'alpaca', lambda *args: pytest.fail('Alpaca must not be called'))
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (candles(), {}, []))
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo', '--as-of', '2026-01-02T19:30:00Z'])
    assert cli.run(args)['source'] == 'Yahoo Finance'
    bad = candles().iloc[-10:]
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (bad, {}, []))
    with pytest.raises(ValueError, match='50'):
        cli.run(args)
    def fail(*args):
        raise ValueError('Market data provider failed')
    monkeypatch.setattr(cli, 'yahoo', fail)
    with pytest.raises(ValueError, match='provider failed'):
        cli.run(args)


def test_repeated_historical_analysis_reuses_market_snapshot(monkeypatch):
    calls = []
    def provider(*args, **kwargs):
        calls.append(args)
        return candles(), {}, []
    monkeypatch.setattr(cli, 'yahoo', provider)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo', '--as-of', '2026-01-02T19:30:00Z'])
    cli.run(args)
    cli.run(args)
    assert len(calls) == 1


def test_historical_analysis_ignores_current_news_and_later_mutations(monkeypatch):
    item = {'title': 'Original', 'source': 'News', 'published': '2026-01-01T00:00:00Z'}
    states = []
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (candles(), {'exchangeTimezoneName': 'America/New_York', 'shortName': 'Current'}, [item.copy()]))
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo', '--as-of', '2026-01-02T19:30Z'])
    market._market_cache.clear()
    cli.run(args)
    item['title'] = 'Later revised headline'
    market._market_cache.clear()
    cli.run(args)
    assert states[0] == states[1]
    assert states[0]['headlines'] == []
    assert states[0]['ticker_details'] == {'exchangeTimezoneName': 'America/New_York'}


def test_live_yahoo_analysis_still_includes_current_headlines(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return pd.Timestamp('2026-01-02T19:30Z')
    headline = {'title': 'Current', 'source': 'News', 'published': '2026-01-02T19:00:00Z'}
    states = []
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (candles(), {}, [headline]))
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo'])
    cli.run(args)
    assert states[0]['headlines'] == [headline]


def test_opt_in_regime_evidence_reaches_model_only_after_availability(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    corpus = {'created_at': '2026-01-01T00:00:00Z', 'timeframe': '5m', 'records': [
        {'source_id': 'past', 'symbol': 'TEST', 'decision_time': '2025-12-01T00:00:00Z',
         'available_at': '2025-12-01T00:00:00Z', 'outcome_available_at': '2025-12-08T00:00:00Z', 'outcome_return': .1,
         'features': {'return_1': 0, 'return_3': 0, 'return_5': 0, 'rsi_14': 50, 'vwap_delta': 0, 'volume_ratio': 1}},
        {'source_id': 'future', 'symbol': 'TEST', 'decision_time': '2025-12-29T00:00:00Z',
         'available_at': '2025-12-29T00:00:00Z', 'outcome_available_at': '2026-01-05T00:00:00Z', 'outcome_return': .9, 'features': {}}]}
    artifact = tmp_path / 'regimes.json'
    artifact.write_text(json.dumps(corpus), encoding='utf-8')
    seen = []
    monkeypatch.setattr(cli, 'predict', lambda state, *args: seen.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30Z', '--regime-data', str(artifact)])
    result = cli.run(args)
    assert [item['source_id'] for item in seen[0]['regime_evidence']] == ['past']
    assert result['market']['regime_evidence'] == seen[0]['regime_evidence']


def test_regime_timeframe_mismatch_fails_before_model_request(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'regimes.json'
    artifact.write_text(json.dumps({'created_at': '2026-01-01T00:00:00Z', 'timeframe': '60m', 'records': []}), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30Z', '--regime-data', str(artifact)])
    with pytest.raises(ValueError, match='timeframe'):
        cli.run(args)


def test_future_regime_corpus_does_not_change_historical_model_state(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'regimes.json'
    artifact.write_text(json.dumps({'created_at': '2026-01-03T00:00:00Z', 'timeframe': '5m', 'records': []}), encoding='utf-8')
    states = []
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    common = ['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30Z']
    cli.run(cli.parser().parse_args(common))
    cli.run(cli.parser().parse_args([*common, '--regime-data', str(artifact)]))
    assert states[0] == states[1]


def test_regime_context_cannot_use_unmatched_calibration(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30Z', '--regime-data', 'regimes.json', '--calibration', 'artifact.json'])
    with pytest.raises(ValueError, match='calibration'):
        cli.run(args)


def test_opt_in_forecast_receives_only_completed_candles(monkeypatch, tmp_path):
    from fasttrade import forecast

    frame = candles()
    future = frame.iloc[-1].copy()
    future['timestamp'] += pd.Timedelta(minutes=5)
    future[['open', 'high', 'low', 'close']] = [1000, 1001, 999, 1000]
    path = tmp_path / 'candles.csv'
    pd.concat([frame, future.to_frame().T], ignore_index=True).to_csv(path, index=False)
    seen = []
    monkeypatch.setattr(forecast, '_predict', lambda values: seen.append(values) or [157, 159, 161])
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30Z', '--forecast'])
    result = cli.run(args)
    assert seen == [list(range(110, 160))]
    assert result['market']['forecast']['quantiles']['p50'] == 159
    assert result['market']['forecast']['status'] == 'unvalidated'
    assert result['forecast_disagreement']['direction'] == 'flat'
    assert 'forecast_disagreement' not in result['market']


def test_forecast_cannot_use_unmatched_calibration(monkeypatch):
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    args = cli.parser().parse_args(['analyze', 'TEST', '--forecast', '--calibration', 'artifact.json'])
    with pytest.raises(ValueError, match='calibration'):
        cli.run(args)


def test_live_analyses_reuse_snapshot_with_distinct_evaluation_times(monkeypatch):
    times = iter([pd.Timestamp('2026-01-02T19:31:00Z'), pd.Timestamp('2026-01-02T19:31:10Z')])
    class Clock:
        @staticmethod
        def now(tz):
            return next(times)
    calls, states = [], []
    def provider(*args, **kwargs):
        calls.append(args)
        return candles(), {}, []
    def predict(state, *args):
        states.append(state)
        return decisions.normalize(answer(), 1)
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr(cli, 'alpaca', provider)
    monkeypatch.setattr(market, 'alpaca', provider)
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'alpaca'])
    cli.run(args)
    cli.run(args)
    assert len(calls) == 1
    assert states[0]['evaluation_time'] == '2026-01-02T19:31:00+00:00'
    assert states[1]['evaluation_time'] == '2026-01-02T19:31:10+00:00'
    assert states[0]['market_snapshot_cutoff'] == '2026-01-02T19:31:00+00:00'
    assert states[1]['market_snapshot_cutoff'] == '2026-01-02T19:31:00+00:00'


def test_cached_candles_still_obey_each_request_freshness_limit(monkeypatch):
    calls = []
    def provider(*args):
        calls.append(args)
        return candles(), {}, []
    monkeypatch.setattr(cli, 'yahoo', provider)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo', '--as-of', '2026-01-02T19:31:00Z', '--max-age-seconds', '120'])
    cli.run(args)
    args.max_age_seconds = 0
    with pytest.raises(ValueError, match='stale'):
        cli.run(args)
    assert len(calls) == 2


def test_stale_rejected_snapshot_does_not_hide_later_fresh_fetch(monkeypatch):
    calls = []
    def provider(*args):
        calls.append(args)
        frame = candles()
        if len(calls) > 1:
            frame['timestamp'] += pd.Timedelta(minutes=1)
        return frame, {}, []
    monkeypatch.setattr(cli, 'yahoo', provider)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo', '--as-of', '2026-01-02T19:31:00Z', '--max-age-seconds', '30'])
    with pytest.raises(ValueError, match='stale'):
        cli.run(args)
    cli.run(args)
    assert len(calls) == 2


def test_yahoo_rejects_unsupported_interval_without_request(monkeypatch):
    import yfinance
    monkeypatch.setattr(yfinance, 'Ticker', lambda *args: pytest.fail('request must not start'))
    with pytest.raises(ValueError, match='Yahoo Finance.*timeframe'):
        market.yahoo('SPY', '10m', pd.Timestamp('2026-01-02T19:30Z'))


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
    monkeypatch.setattr(cli, 'alpaca', lambda *args: (candles(), {'shortName': 'Current'}, [{'title': 'new', 'source': 'X', 'published': '2026-01-03T00:00:00+00:00'}]))
    states = []
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--as-of', '2026-01-03T00:00:00Z', '--position', '0', '--equity', '10000', '--exposure', '0', '--daily-loss', '0', '--trade-count', '0', '--quantity', '1', '--max-position', '1000', '--max-exposure', '5000', '--max-daily-loss', '200', '--max-trades', '5', '--min-confidence', '0'])
    args.allow_stale_preview = True
    result = cli.run(args)
    assert result['actionable_signal'] == 'HOLD'
    assert any('stale' in reason.lower() for reason in result['risk_rejections'])


def test_live_stale_preview_does_not_send_future_snapshot_cutoff(monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return pd.Timestamp('2026-01-03T00:00:00Z')
    states = []
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr(cli, 'yahoo', lambda *args: (candles(), {}, []))
    monkeypatch.setattr(cli, 'predict', lambda state, *args: states.append(state) or decisions.normalize(answer(), 1))
    args = cli.parser().parse_args(['analyze', 'TEST', '--source', 'yahoo'])
    args.allow_stale_preview = True
    result = cli.run(args)
    assert result['stale_preview'] is True
    assert 'market_snapshot_cutoff' not in states[0]
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


CALIBRATION_ARTIFACT = {'schema': CALIBRATION_SCHEMA, 'created_at': '2025-12-31T00:00:00+00:00', 'model': 'jev', 'method': 'temperature', 'temperature': 3.0, 'fit': {'target': 'p_buy_vs_next_execution_open_up', 'horizon': '5m', 'review_horizon': 'weekly-next-execution'}, 'train_window': {'start': '2025-09-28T20:30:00+00:00', 'end': '2025-10-12T20:30:00+00:00', 'outcome_available_through': '2025-10-15T00:00:00+00:00', 'n': 75}, 'test_window': {'start': '2025-12-01T00:00:00+00:00', 'end': '2025-12-23T00:00:00+00:00', 'outcome_available_through': '2025-12-30T00:00:00+00:00'}, 'conformal': {'alpha': 0.1, 'quantile': 0.2, 'applies_to': 'BUY decisions only'}, 'edge': {'certified': True, 'method': 'stationary-review-block-v1', 'periods': 10, 'n': 12, 'mean': 0.01, 'ci': [0.002, 0.02], 'round_trip_cost': 0.003, 'scope': {'symbol_review_times': [['TEST', 'Fri 14:30']]}}}


def full_risk_args():
    return ['--position', '0', '--equity', '1000', '--exposure', '0', '--daily-loss', '0', '--trade-count', '0', '--quantity', '1', '--max-position', '500', '--max-exposure', '800', '--max-daily-loss', '100', '--max-trades', '5', '--min-confidence', '0.6']


def analyze_args(csv, *extra):
    return cli.parser().parse_args(['analyze', 'TEST', '--csv', str(csv), '--as-of', '2026-01-02T19:30:00Z', *extra])


def test_example_inputs_pass_and_block_through_the_risk_gate(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    values = ['--position', '0', '--equity', '10000', '--exposure', '0', '--daily-loss', '0',
              '--trade-count', '0', '--quantity', '1', '--max-position', '1000', '--max-exposure', '5000',
              '--max-daily-loss', '200', '--max-trades', '5', '--min-confidence', '0.5']
    passed = cli.run(analyze_args(path, *values))
    assert passed['actionable_signal'] == 'BUY'
    blocked = cli.run(analyze_args(path, *values, '--daily-loss', '200'))
    assert blocked['actionable_signal'] == 'HOLD'
    assert 'Daily loss limit reached' in blocked['risk_rejections']


def test_calibration_gating_uses_calibrated_confidence(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(CALIBRATION_ARTIFACT), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    result = cli.run(analyze_args(path, '--calibration', str(artifact), *full_risk_args()))
    # Raw P(BUY)=0.7; temperature 3 maps it to sigmoid(logit(0.7)/3) ~= 0.5701, below the 0.6 threshold.
    assert result['decision']['calibrated_confidence'] == pytest.approx(0.570143, abs=1e-4)
    assert result['decision']['calibration']['fit_horizon'] == '5m'
    assert 'Confidence below threshold' in result['risk_rejections']
    assert result['actionable_signal'] == 'HOLD'
    uncalibrated = cli.run(analyze_args(path, *full_risk_args()))
    assert uncalibrated['risk_rejections'] == []
    assert uncalibrated['actionable_signal'] == 'BUY'


def test_uncertified_edge_blocks_actionable(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, edge={'certified': False, 'n': 12, 'mean': -0.004, 'ci': [-0.02, 0.01], 'round_trip_cost': 0.003})
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    result = cli.run(analyze_args(path, '--calibration', str(path_artifact), '--require-certified-edge', *full_risk_args()))
    assert result['edge_certified'] is False
    assert 'Model edge is not certified by the calibration artifact' in result['risk_rejections']
    assert result['actionable_signal'] == 'HOLD'


def test_uncertified_edge_reason_surfaces(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, edge={'certified': None, 'reason': 'fewer than 5 BUY decisions in the window', 'n': 2})
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(path_artifact), '--require-certified-edge', '--edge-horizon', 'weekly-next-execution', *risk_args))
    assert 'fewer than 5 BUY decisions' in result['risk_rejections'][0]
    assert result['actionable_signal'] == 'HOLD'


def test_certified_edge_allows_actionable(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(CALIBRATION_ARTIFACT), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(artifact), '--require-certified-edge', '--edge-horizon', 'weekly-next-execution', *risk_args))
    assert result['edge_certified'] is True
    assert result['risk_rejections'] == []
    assert result['actionable_signal'] == 'BUY'
    # Per-trade numbers ride along as diagnostics.
    assert result['conformal_interval'] == [0.370143, 0.770143]


def test_edge_gate_requires_an_explicit_matching_holding_horizon(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(CALIBRATION_ARTIFACT), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(artifact), '--require-certified-edge', *risk_args))
    assert result['edge_certified'] is False
    assert result['actionable_signal'] == 'HOLD'


@pytest.mark.parametrize('scope', [{'symbol_review_times': [['OTHER', 'Fri 14:30']]},
                                   {'symbol_review_times': [['TEST', 'Fri 15:30']]},
                                   {'symbol_review_times': [['TEST', 'Mon 14:30'], ['OTHER', 'Fri 14:30']]},
                                   {'symbol_review_times': 'TEST'}])
def test_edge_gate_rejects_out_of_scope_symbol_or_review_time(monkeypatch, tmp_path, scope):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(dict(CALIBRATION_ARTIFACT, edge=dict(CALIBRATION_ARTIFACT['edge'], scope=scope))), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    result = cli.run(analyze_args(path, '--calibration', str(artifact), '--require-certified-edge', '--edge-horizon', 'weekly-next-execution', *full_risk_args()))
    assert result['edge_certified'] is False


@pytest.mark.parametrize('action', ['SELL', 'HOLD'])
def test_buy_up_calibration_does_not_calibrate_or_authorize_other_actions(monkeypatch, tmp_path, action):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = tmp_path / 'artifact.json'
    artifact.write_text(json.dumps(CALIBRATION_ARTIFACT), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': action, 'probabilities': {'BUY': .1, 'HOLD': .2, 'SELL': .7}, 'confidence': .8, 'model': 'jev', 'latency_us': 1})
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(artifact), *risk_args))
    assert 'calibrated_confidence' not in result['decision']
    assert any('only for BUY' in reason for reason in result['risk_rejections'])
    assert result['actionable_signal'] == 'HOLD'


def test_legacy_edge_certification_cannot_open_gate(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, edge={'certified': True, 'n': 10, 'ci': [0.01, 0.04]})
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(path_artifact), '--require-certified-edge', *risk_args))
    assert result['edge_certified'] is False
    assert result['actionable_signal'] == 'HOLD'


@pytest.mark.parametrize('change', [
    {'model': 'another-model'},
    {'fit': {'target': 'p_buy_vs_next_execution_open_up', 'horizon': '60m', 'review_horizon': 'weekly-next-execution'}},
    {'fit': {'target': 'another-target', 'horizon': '5m', 'review_horizon': 'weekly-next-execution'}},
    {'fit': {'target': 'p_buy_vs_next_execution_open_up', 'horizon': '5m', 'review_horizon': 'daily'}},
])
def test_edge_gate_rejects_mismatched_model_or_horizon(monkeypatch, tmp_path, change):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, **change)
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(path_artifact), '--require-certified-edge', '--edge-horizon', 'weekly-next-execution', *risk_args))
    assert result['edge_certified'] is False
    assert result['actionable_signal'] == 'HOLD'


def test_future_calibration_artifact_is_rejected_before_model_call(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, created_at='2026-01-03T00:00:00+00:00')
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('model must not be called'))
    with pytest.raises(ValueError, match='artifact.*evaluation'):
        cli.run(analyze_args(path, '--calibration', str(path_artifact)))


@pytest.mark.parametrize('change', [
    {'train_window': {'start': '2025-09-01T00:00:00+00:00', 'end': '2025-10-01T00:00:00+00:00', 'outcome_available_through': '2026-01-03T00:00:00+00:00'}},
    {'test_window': {'start': '2025-12-01T00:00:00+00:00', 'end': '2025-12-23T00:00:00+00:00', 'outcome_available_through': '2026-01-03T00:00:00+00:00'}},
    {'train_window': {'start': '2025-01-01T00:00:00+00:00'}},
    {'train_window': {'start': '2025-09-01T00:00:00+00:00', 'end': '2025-10-01T00:00:00+00:00', 'outcome_available_through': '2025-12-10T00:00:00+00:00'}},
])
def test_unavailable_calibration_outcomes_are_rejected_before_model_call(monkeypatch, tmp_path, change):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, **change)
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('model must not be called'))
    with pytest.raises(ValueError, match='artifact.*outcome'):
        cli.run(analyze_args(path, '--calibration', str(path_artifact)))


def test_per_trade_numbers_are_diagnostic_only(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    artifact = dict(CALIBRATION_ARTIFACT, conformal={'alpha': 0.1, 'quantile': 0.02, 'applies_to': 'BUY decisions only'}, returns={'blocks': [[0.0, 1.0, -0.05]], 'alpha': 0.1, 'quantile': 0.01, 'round_trip_cost': 0.003, 'round_trip_cost_bps': 30.0})
    path_artifact = tmp_path / 'artifact.json'
    path_artifact.write_text(json.dumps(artifact), encoding='utf-8')
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    risk_args = full_risk_args()[:-2] + ['--min-confidence', '0.5']
    result = cli.run(analyze_args(path, '--calibration', str(path_artifact), '--require-certified-edge', '--edge-horizon', 'weekly-next-execution', *risk_args))
    assert result['expected_return'] == -0.05
    assert result['return_interval_lower'] == -0.06
    assert 'Calibrated expected return does not clear round-trip costs' not in result['risk_rejections']
    assert result['actionable_signal'] == 'BUY'


def test_require_certified_edge_without_calibration_is_rejected(monkeypatch, tmp_path):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    with pytest.raises(ValueError, match='--require-certified-edge requires --calibration'):
        cli.run(analyze_args(path, '--require-certified-edge'))


def test_calibrate_command_writes_artifact_and_refuses_overwrite(monkeypatch, tmp_path):
    report_path = tmp_path / 'report.json'
    report_path.write_text(json.dumps(synthetic_report_and_data(tmp_path)), encoding='utf-8')
    artifact_path = tmp_path / 'artifact.json'
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'calibrate', '--report', str(report_path), '--data-dir', str(tmp_path), '--output', str(artifact_path)])
    assert cli.main() == 0
    written = json.loads(artifact_path.read_text(encoding='utf-8'))
    assert written['schema'] == CALIBRATION_SCHEMA
    assert written['method'] == 'temperature'
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'calibrate', '--report', str(report_path), '--data-dir', str(tmp_path), '--output', str(artifact_path)])
    assert cli.main() == 1


@pytest.mark.parametrize(('rule', 'agreement'), [('BUY', 'agree'), ('SELL', 'disagree'), (None, 'no_signal')])
def test_rule_comparison_does_not_change_risk_gate(monkeypatch, tmp_path, rule, agreement):
    path = tmp_path / 'candles.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    monkeypatch.setattr(cli, 'rule_signal', lambda snapshot: rule)
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z', *full_risk_args(), '--min-confidence', '0.9'])
    result = cli.run(args)
    assert result['rule_comparison'] == {'signal': rule, 'agreement': agreement}
    assert result['decision']['action'] == 'BUY'
    assert result['actionable_signal'] == 'HOLD'
    assert 'Confidence below threshold' in result['risk_rejections']


def test_yahoo_timeout_reports_provider_connectivity(monkeypatch):
    import yfinance
    from curl_cffi.requests.exceptions import Timeout
    class Ticker:
        def history(self, **kwargs):
            raise Timeout('network details')
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    with pytest.raises(ValueError, match='Market data provider timed out'):
        market.yahoo('NVDA', '5m', pd.Timestamp('2026-01-02T19:30Z'))


def test_yahoo_fetches_details_and_headlines_concurrently(monkeypatch):
    import threading
    import yfinance
    barrier = threading.Barrier(2, timeout=3)
    class Ticker:
        def history(self, **kwargs):
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        @property
        def info(self):
            barrier.wait()
            return {'shortName': 'Example', 'exchangeTimezoneName': 'America/New_York'}
        @property
        def news(self):
            barrier.wait()
            return [{'title': 'Earlier', 'providerPublishTime': 1767380400, 'publisher': 'Source'}]
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    frame, details, news = market.yahoo('TEST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(frame) == 60
    assert details['shortName'] == 'Example'
    assert [item['title'] for item in news] == ['Earlier']
