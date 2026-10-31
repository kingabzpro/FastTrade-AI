import pandas as pd
import pytest
from fasttrade import decisions
from fasttrade.market import validate_candles, headlines
from fasttrade.indicators import features
from fasttrade.decisions import normalize, predict_samples
from fasttrade.risk import evaluate


def candles():
    ts = pd.date_range('2026-01-02 14:30', periods=60, freq='5min', tz='UTC')
    return pd.DataFrame({'timestamp': ts, 'open': range(100, 160), 'high': range(101, 161), 'low': range(99, 159), 'close': range(100, 160), 'volume': [100] * 60})


def test_features_and_validation():
    frame = validate_candles(candles(), '5m', pd.Timestamp('2026-01-02 19:30', tz='UTC'), 300)
    result = features(frame, 'America/New_York')
    assert result['ema_9'] > result['ema_21'] > result['ema_50']
    assert result['rsi_14'] == 100
    assert result['atr_14'] == 2
    assert result['volume_ratio'] == 1


def test_original_model_features_do_not_include_added_context():
    result = features(candles(), 'America/New_York')
    assert 'volatility_30_bars' not in result
    assert 'distance_from_high_50_bars' not in result


def test_stale_and_incomplete_rejected():
    with pytest.raises(ValueError, match='stale'):
        validate_candles(candles(), '5m', pd.Timestamp('2026-01-03', tz='UTC'), 300)
    with pytest.raises(ValueError, match='completed'):
        validate_candles(candles().iloc[:1], '5m', pd.Timestamp('2026-01-02 14:31', tz='UTC'), 300)


def test_headlines_exclude_future_and_undated():
    items = [{'title':'old','providerPublishTime':100,'publisher':'a'}, {'title':'future','providerPublishTime':300,'publisher':'b'}, {'title':'undated'}]
    assert [x['title'] for x in headlines(items, pd.Timestamp(200, unit='s', tz='UTC'))] == ['old']


def test_invalid_probabilities_rejected():
    with pytest.raises(ValueError):
        normalize({'model':'jev','answers':{'action':{'type':'choice','choice':'BUY','confidence':.5,'probabilities':{'BUY':.9,'HOLD':.9,'SELL':0}}}}, 1)


def test_model_usage_is_recorded_only_when_valid():
    data = {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}}, 'usage': {'input_tokens': 500, 'output_tokens': 5}}
    assert normalize(data, 1000)['usage'] == {'input_tokens': 500, 'output_tokens': 5}
    data['usage']['input_tokens'] = -1
    assert normalize(data, 1000)['usage'] is None


def test_risk_rejects_oversize_and_short():
    args = dict(position=0, equity=1000, exposure=0, daily_loss=0, trade_count=0, quantity=10, max_position=500, max_exposure=800, max_daily_loss=100, max_trades=5, min_confidence=.5)
    assert evaluate('BUY', .8, 100, **args)
    assert evaluate('SELL', .8, 100, **args)


def test_predict_samples_majority_dispersion_and_usage(monkeypatch):
    sequence = [('BUY', .7, .2, .1, .8), ('BUY', .6, .3, .1, .7), ('HOLD', .3, .5, .2, .6), ('BUY', .65, .25, .1, .75), ('HOLD', .4, .45, .15, .55)]
    calls = []
    def fake_predict(state, url, model, key):
        action, b, h, s, confidence = sequence[len(calls)]
        calls.append(action)
        return {'action': action, 'probabilities': {'BUY': b, 'HOLD': h, 'SELL': s}, 'confidence': confidence, 'model': 'jev', 'latency_us': 1000, 'usage': {'input_tokens': 100, 'output_tokens': 10}}
    monkeypatch.setattr(decisions, 'predict', fake_predict)
    result = predict_samples({}, 'u', 'jev', 'k', 5)
    assert calls == ['BUY', 'BUY', 'HOLD', 'BUY', 'HOLD']
    assert result['action'] == 'BUY'
    assert result['probabilities']['BUY'] == pytest.approx((.7 + .6 + .3 + .65 + .4) / 5)
    assert result['confidence'] == pytest.approx((.8 + .7 + .6 + .75 + .55) / 5)
    assert result['dispersion'] == pytest.approx((1 - 3 / 5) * 5 / 4)
    assert result['effective_confidence'] == pytest.approx(result['confidence'] * (1 - result['dispersion']))
    assert result['sample_count'] == 5
    assert len(result['samples']) == 5
    assert result['usage'] == {'input_tokens': 500, 'output_tokens': 50}
    assert result['usage_complete'] is True
    assert result['latency_us'] == 5000


def test_predict_samples_retries_invalid_response(monkeypatch):
    calls = []
    def flaky(state, url, model, key):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError('Invalid SystemOne response')
        usage = {'input_tokens': 100, 'output_tokens': 10} if len(calls) == 1 else None
        return {'action': 'HOLD', 'probabilities': {'BUY': .2, 'HOLD': .6, 'SELL': .2}, 'confidence': .6, 'model': 'jev', 'latency_us': 100, 'usage': usage}
    monkeypatch.setattr(decisions, 'predict', flaky)
    result = predict_samples({}, 'u', 'jev', 'k', 2)
    assert len(calls) == 3
    assert result['sample_count'] == 2
    assert result['usage_complete'] is False
    assert result['usage'] == {'input_tokens': 100, 'output_tokens': None}


def test_predict_samples_unanimous_has_zero_dispersion(monkeypatch):
    def same(state, url, model, key):
        return {'action': 'SELL', 'probabilities': {'BUY': .1, 'HOLD': .2, 'SELL': .7}, 'confidence': .9, 'model': 'jev', 'latency_us': 50, 'usage': {'input_tokens': 1, 'output_tokens': 1}}
    monkeypatch.setattr(decisions, 'predict', same)
    result = predict_samples({}, 'u', 'jev', 'k', 3)
    assert result['action'] == 'SELL'
    assert result['dispersion'] == 0
    assert result['effective_confidence'] == pytest.approx(.9)


def test_predict_samples_votes_run_concurrently(monkeypatch):
    import threading
    barrier = threading.Barrier(3, timeout=10)
    def concurrent_predict(state, url, model, key):
        barrier.wait()
        return {'action': 'BUY', 'probabilities': {'BUY': .6, 'HOLD': .3, 'SELL': .1}, 'confidence': .7, 'model': 'jev', 'latency_us': 100, 'usage': {'input_tokens': 1, 'output_tokens': 1}}
    monkeypatch.setattr(decisions, 'predict', concurrent_predict)
    result = predict_samples({}, 'u', 'jev', 'k', 3)
    assert result['sample_count'] == 3
    assert result['request_attempts'] == 3
