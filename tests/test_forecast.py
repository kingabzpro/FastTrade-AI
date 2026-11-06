import pytest


@pytest.mark.parametrize(('previous', 'current', 'median', 'expected'), [
    (100, 110, 115, 'aligned'),
    (100, 110, 105, 'opposed'),
    (110, 100, 105, 'opposed'),
    (100, 100, 105, 'flat'),
    (100, 110, 110, 'flat'),
])
def test_disagreement_compares_next_bar_forecast_with_last_price_move(previous, current, median, expected):
    from fasttrade.forecast import disagreement

    result = disagreement(previous, current, median)
    assert result['direction'] == expected
    assert result['status'] == 'unvalidated'
    assert result['observed_return'] == pytest.approx(current / previous - 1)
    assert result['forecast_return'] == pytest.approx(median / current - 1)


@pytest.mark.parametrize('prices', [(0, 1, 1), (1, float('nan'), 1), (1, 1, -1), (1e-300, 1e300, 1)])
def test_disagreement_rejects_invalid_or_nonfinite_returns(prices):
    from fasttrade.forecast import disagreement

    with pytest.raises(ValueError):
        disagreement(*prices)


def test_forecast_uses_fixed_recent_window_and_labels_unvalidated(monkeypatch):
    from fasttrade import forecast

    seen = []
    monkeypatch.setattr(forecast, '_predict', lambda values: seen.append(values) or [98.0, 101.0, 104.0])
    result = forecast.next_bar(list(range(1, 61)))
    assert seen == [list(range(11, 61))]
    assert result['quantiles'] == {'p10': 98.0, 'p50': 101.0, 'p90': 104.0}
    assert result['context_length'] == 50
    assert result['horizon_bars'] == 1
    assert result['status'] == 'unvalidated'


@pytest.mark.parametrize('quantiles', [[1.0, 0.5, 2.0], [1.0, float('nan'), 2.0], [-1.0, 1.0, 2.0]])
def test_forecast_rejects_invalid_quantiles(monkeypatch, quantiles):
    from fasttrade import forecast

    monkeypatch.setattr(forecast, '_predict', lambda values: quantiles)
    with pytest.raises(ValueError, match='forecast'):
        forecast.next_bar([1.0] * 50)
