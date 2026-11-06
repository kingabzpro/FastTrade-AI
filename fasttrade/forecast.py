"""Optional, unvalidated next-bar price forecast from a pinned local model."""
import math
from functools import lru_cache

MODEL = 'amazon/chronos-bolt-tiny'
REVISION = 'a0e552de83495b5c28c14c71c374f3e33280b340'
CONTEXT_LENGTH = 50


def disagreement(previous_close, current_close, median_forecast):
    values = [float(previous_close), float(current_close), float(median_forecast)]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError('Disagreement requires finite positive prices')
    observed = values[1] / values[0] - 1
    forecast = values[2] / values[1] - 1
    if not math.isfinite(observed) or not math.isfinite(forecast):
        raise ValueError('Disagreement requires finite returns')
    direction = 'flat' if observed == 0 or forecast == 0 else 'aligned' if (observed > 0) == (forecast > 0) else 'opposed'
    return {'status': 'unvalidated', 'direction': direction, 'observed_return': observed, 'forecast_return': forecast}


@lru_cache(maxsize=1)
def _pipeline():
    try:
        from chronos import BaseChronosPipeline
    except ImportError as exc:
        raise ValueError('Forecast requires the optional forecast dependencies') from exc
    return BaseChronosPipeline.from_pretrained(MODEL, revision=REVISION, device_map='cpu')


def _predict(values):
    pipeline = _pipeline()
    import torch
    quantiles, _ = pipeline.predict_quantiles(
        torch.tensor(values, dtype=torch.float32), prediction_length=1,
        quantile_levels=[0.1, 0.5, 0.9],
    )
    return quantiles[0, 0].tolist()


def next_bar(closes):
    values = list(closes)[-CONTEXT_LENGTH:]
    if len(values) != CONTEXT_LENGTH or any(not math.isfinite(float(x)) or float(x) <= 0 for x in values):
        raise ValueError('Forecast requires 50 finite positive closes')
    try:
        quantiles = [float(x) for x in _predict(values)]
        if len(quantiles) != 3 or any(not math.isfinite(x) or x <= 0 for x in quantiles) or quantiles != sorted(quantiles):
            raise ValueError('Invalid forecast quantiles')
    except (TypeError, OverflowError) as exc:
        raise ValueError('Invalid forecast quantiles') from exc
    return {'status': 'unvalidated', 'model': MODEL, 'revision': REVISION,
            'context_length': CONTEXT_LENGTH, 'horizon_bars': 1,
            'quantiles': dict(zip(('p10', 'p50', 'p90'), quantiles))}
