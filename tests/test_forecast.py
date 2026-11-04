import pytest


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
