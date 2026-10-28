import pandas as pd
import pytest
from fasttrade import cli


def test_weekly_benchmark_uses_prior_candles_and_next_open(monkeypatch):
    from fasttrade import benchmark

    times = pd.date_range('2026-01-01 14:30', periods=400, freq='h', tz='UTC')
    frame = pd.DataFrame({'timestamp': times, 'open': [100.0] * len(times), 'high': [101.0] * len(times),
                          'low': [99.0] * len(times), 'close': [100.0] * len(times), 'volume': [100] * len(times)})
    frame.loc[70:, ['open', 'close']] = 101.0
    frame.loc[200:, ['open', 'close']] = 99.0
    frame.loc[300:, 'close'] = 98.0
    frame.loc[300:, 'low'] = 98.0
    states = []
    actions = iter(['BUY', 'SELL'])
    attempts = []
    monkeypatch.setattr(benchmark, 'download', lambda *args: frame.copy())
    def predict(state, url, model, key):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('Invalid SystemOne response')
        states.append(state)
        return {'action': next(actions), 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': model, 'latency_us': 1000}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['benchmark', '--start', '2026-01-01', '--end', '2026-01-14', '--symbols', 'AAPL', '--model', 'test'])
    report = benchmark.run(args)
    assert report['model_requested'] == 'test'
    assert report['decisions']
    assert len(attempts) == len(report['decisions']) + 1
    for state, row in zip(states, report['decisions']):
        assert pd.Timestamp(state['evaluation_time']) <= pd.Timestamp(row['execution_time'])
        assert pd.Timestamp(state['candle_timestamp']) < pd.Timestamp(row['execution_time'])
    assert states[0]['features']['price'] == 101.0
    assert states[1]['features']['price'] == 99.0
    assert report['ending_balance'] == pytest.approx(10000 * 99 / 101, abs=.01)
    assert report['directional_decisions'] == 2
    assert report['directional_accuracy_pct'] == 50.0


def test_benchmark_rejects_invalid_period():
    from fasttrade import benchmark
    args = cli.parser().parse_args(['benchmark', '--start', '2026-02-01', '--end', '2026-01-01'])
    with pytest.raises(ValueError, match='Start'):
        benchmark.run(args)
