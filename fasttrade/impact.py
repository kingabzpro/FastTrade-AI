"""Research-only square-root impact estimates from prior completed sessions."""
import math

import numpy as np
import pandas as pd


def impact_bps(quantity, daily_volatility, daily_volume):
    if not all(math.isfinite(x) for x in (quantity, daily_volatility, daily_volume)) or quantity <= 0 or daily_volatility < 0 or daily_volume <= 0:
        raise ValueError('Impact requires positive quantity and volume and finite volatility')
    return min(50.0, max(1.0, 10000 * daily_volatility * math.sqrt(quantity / daily_volume)))


def impact_fill(action, amount, price, info, fee, multiplier=1):
    if action not in ('BUY', 'SELL') or any(not math.isfinite(x) for x in (amount, price, fee, multiplier)) or amount <= 0 or price <= 0 or not 0 <= fee < 1 or multiplier <= 0 or 50 * multiplier >= 10000:
        raise ValueError('Invalid impact fill inputs')
    def rate(quantity):
        return impact_bps(quantity, info['daily_volatility'], info['daily_volume']) * multiplier / 10000
    if action == 'BUY':
        low, high = 0.0, amount / (price * (1 + fee))
        for _ in range(50):
            middle = (low + high) / 2
            if middle * price * (1 + rate(middle)) * (1 + fee) <= amount:
                low = middle
            else:
                high = middle
        quantity = low
    else:
        quantity = amount
    return quantity, rate(quantity)


def liquidity(sessions, frame, cutoff, *, dividends=None):
    dividends = dividends or {}
    history = []
    for session in reversed(list(sessions.itertuples())):
        if session.open >= cutoff:
            continue
        expected = pd.date_range(session.open, session.close, freq='h', inclusive='left')
        if expected[-1] + pd.Timedelta(hours=1) > cutoff:
            continue
        if not expected.isin(frame.index).all():
            history.append(None)
        else:
            bars = frame.loc[expected]
            close, volume = float(bars.close.iloc[-1]), float(bars.volume.sum())
            history.append((close, volume, float(dividends.get(session.date, 0)))
                           if math.isfinite(close) and close > 0 and math.isfinite(volume) and volume > 0 else None)
        if len(history) == 21:
            break
    if not history or history[0] is None:
        return None
    history.reverse()
    valid = [row for row in history if row is not None]
    returns = [(current[0] + current[2]) / previous[0] - 1 for previous, current in zip(history, history[1:])
               if previous is not None and current is not None]
    if len(valid) < 6 or len(returns) < 5:
        return None
    return {'sessions': len(valid), 'missing_sessions': len(history) - len(valid),
            'return_observations': len(returns), 'daily_volume': float(np.mean([row[1] for row in valid[-20:]])),
            'daily_volatility': float(np.std(returns, ddof=1))}
