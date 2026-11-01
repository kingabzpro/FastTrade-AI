import pandas as pd
import pytest
from fasttrade.market import validate_candles, headlines
from fasttrade.indicators import features
from fasttrade.decisions import normalize
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


@pytest.mark.parametrize(('ema9', 'ema21', 'macd', 'signal', 'expected'), [
    (11, 10, 2, 1, 'BUY'),
    (9, 10, 0, 1, 'SELL'),
    (11, 10, 0, 1, None),
    (10, 10, 2, 1, None),
    (11, 10, 1, 1, None),
    (float('nan'), 10, 2, 1, None),
    (float('inf'), 10, 2, 1, None),
])
def test_rule_signal_requires_agreement_between_trend_and_momentum(ema9, ema21, macd, signal, expected):
    from fasttrade.indicators import rule_signal
    assert rule_signal({'ema_9': ema9, 'ema_21': ema21, 'macd': macd, 'macd_signal': signal}) == expected
