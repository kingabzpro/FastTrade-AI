import math


def evaluate(action, confidence, price, *, position, equity, exposure, daily_loss, trade_count, quantity, max_position, max_exposure, max_daily_loss, max_trades, min_confidence):
    values = [position, equity, exposure, daily_loss, trade_count, quantity, max_position, max_exposure, max_daily_loss, max_trades, min_confidence, price, confidence]
    if any(v is None or not math.isfinite(v) for v in values):
        return ['Missing or invalid portfolio or risk input']
    if min(position, exposure, daily_loss, trade_count, quantity, max_position, max_exposure, max_daily_loss, max_trades) < 0 or equity <= 0 or price <= 0 or quantity <= 0 or not 0 <= min_confidence <= 1:
        return ['Invalid portfolio or risk input']
    if action == 'HOLD':
        return []
    reasons = []
    if confidence < min_confidence: reasons.append('Confidence below threshold')
    if daily_loss >= max_daily_loss: reasons.append('Daily loss limit reached')
    if trade_count >= max_trades: reasons.append('Trade count limit reached')
    if action == 'SELL' and quantity > position: reasons.append('SELL exceeds existing long position')
    if action == 'BUY':
        if (position + quantity) * price > max_position: reasons.append('Position limit exceeded')
        if exposure + quantity * price > max_exposure or exposure + quantity * price > equity: reasons.append('Exposure limit exceeded')
    return reasons
