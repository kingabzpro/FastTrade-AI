"""Risk-checked position sizing for research replays."""
import math
import hashlib
import json

import pandas as pd

from .impact import liquidity
from .risk import evaluate


def volatility_weight(target, volatility):
    if not math.isfinite(target) or target <= 0 or not math.isfinite(volatility) or volatility < 0:
        raise ValueError('Volatility target and volatility must be finite and nonnegative, with a positive target')
    return min(1.0, target / volatility) if volatility else 1.0


def drawdown_factor(peak, equity, max_drawdown):
    if (not all(math.isfinite(x) for x in (peak, equity, max_drawdown)) or
        peak <= 0 or equity < 0 or not 0 < max_drawdown <= 1):
        raise ValueError('Drawdown inputs must have a positive peak and a limit in (0, 1]')
    return max(0.0, 1 - max(0.0, 1 - equity / peak) / max_drawdown)


def validate_limits(limits):
    try:
        if (not isinstance(limits, dict) or
            any(not math.isfinite(limits[k]) or limits[k] < 0 for k in ('max_position', 'max_exposure', 'max_daily_loss')) or
            type(limits['max_trades']) is not int or limits['max_trades'] < 0 or
            not math.isfinite(limits['min_confidence']) or not 0 <= limits['min_confidence'] <= 1):
            raise ValueError('Explicit valid sizing risk limits are required')
    except (KeyError, TypeError) as exc:
        raise ValueError('Explicit valid sizing risk limits are required') from exc


def replay(sessions, frames, symbols, decisions, dividends, start, end, initial, fee, slip, limits, *, target=None, max_drawdown=None):
    validate_limits(limits)
    if target is not None:
        volatility_weight(target, 0)
    if max_drawdown is not None:
        drawdown_factor(1, 1, max_drawdown)
    if not symbols or not math.isfinite(initial) or initial <= 0 or any(not math.isfinite(x) or not 0 <= x < 1 for x in (fee, slip)):
        raise ValueError('Invalid sizing replay inputs')
    sessions_in_range = [s for s in sessions.itertuples() if start <= s.date <= end]
    openings = {s.open for s in sessions_in_range}
    executions = {}
    for row in decisions:
        try:
            decision_time, execution_time = pd.Timestamp(row['decision_time']), pd.Timestamp(row['execution_time'])
            if (pd.isna(decision_time) or pd.isna(execution_time) or decision_time.tzinfo is None or execution_time.tzinfo is None or
                decision_time >= execution_time or execution_time not in openings or row['symbol'] not in symbols or
                row['action'] not in ('BUY', 'HOLD', 'SELL') or not math.isfinite(row['confidence']) or not 0 <= row['confidence'] <= 1):
                raise ValueError('Invalid sizing decision')
        except (KeyError, TypeError) as exc:
            raise ValueError('Invalid sizing decision') from exc
        group = executions.setdefault(execution_time, [])
        if any(previous['symbol'] == row['symbol'] for previous in group):
            raise ValueError('Duplicate sizing decision')
        group.append(row)
    cash = {symbol: initial / len(symbols) for symbol in symbols}
    shares = {symbol: 0.0 for symbol in symbols}
    receivables = {symbol: 0.0 for symbol in symbols}
    evaluations = []
    previous_equity = initial
    peak = initial
    drawdown_checks = []
    for session in sessions_in_range:
        for symbol in symbols:
            event = dividends.get((symbol, session.date))
            if event:
                receivables[symbol] += shares[symbol] * event[0]
        prices = {symbol: float(frames[symbol].loc[session.open, 'open']) for symbol in symbols}
        trade_count = 0
        if max_drawdown is not None:
            equity = sum(cash[s] + shares[s] * prices[s] + receivables[s] for s in symbols)
            peak = max(peak, equity)
            exposure = sum(shares[s] * prices[s] for s in symbols)
            factor = drawdown_factor(peak, equity, max_drawdown)
            cap = min(limits['max_exposure'], equity) * factor
            fraction = 0.0
            if exposure > cap:
                sell_cost = 1 - (1 - slip) * (1 - fee)
                low, high = 0.0, 1.0
                for _ in range(60):
                    middle = (low + high) / 2
                    remaining_equity = max(0.0, equity - exposure * middle * sell_cost)
                    remaining_cap = min(limits['max_exposure'], remaining_equity) * drawdown_factor(peak, remaining_equity, max_drawdown)
                    if exposure * (1 - middle) <= remaining_cap:
                        high = middle
                    else:
                        low = middle
                fraction = high
            for symbol in symbols:
                quantity = shares[symbol] * fraction
                price = prices[symbol]
                if quantity * price <= 16 * math.ulp(max(equity, 1.0)):
                    continue
                equity = sum(cash[s] + shares[s] * prices[s] + receivables[s] for s in symbols)
                exposure = sum(shares[s] * prices[s] for s in symbols)
                daily_loss = max(0.0, previous_equity - equity)
                state = {'source': 'drawdown', 'symbol': symbol, 'action': 'SELL', 'execution_time': session.open.isoformat(),
                         'price': price, 'position': shares[symbol], 'equity': equity, 'exposure': exposure,
                         'daily_loss': daily_loss, 'trade_count': trade_count, 'quantity': quantity, 'confidence': 0.0,
                         'peak': peak, 'max_drawdown': max_drawdown, 'factor': factor, 'initial_cap': cap,
                         'cash': dict(cash), 'shares': dict(shares), 'prices': dict(prices), 'receivables': dict(receivables),
                         'fee': fee, 'slippage': slip, 'limits': dict(limits)}
                state_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                reasons = evaluate('SELL', 0.0, price, position=shares[symbol], equity=equity, exposure=exposure,
                                   daily_loss=daily_loss, trade_count=trade_count, quantity=quantity, **limits)
                evaluations.append({**state, 'state_sha256': state_hash, 'risk_rejections': reasons, 'executed': not reasons})
                if not reasons:
                    cash[symbol] += quantity * price * (1 - slip) * (1 - fee)
                    shares[symbol] -= quantity
                    trade_count += 1
        for row in executions.get(session.open, []):
            symbol, action, price = row['symbol'], row['action'], prices[row['symbol']]
            if action == 'HOLD' or (action == 'SELL' and shares[symbol] == 0):
                continue
            equity = sum(cash[s] + shares[s] * prices[s] + receivables[s] for s in symbols)
            exposure = sum(shares[s] * prices[s] for s in symbols)
            daily_loss = max(0.0, previous_equity - equity)
            weight, annual_volatility = 1.0, None
            info = None
            risk_probe, veto = None, []
            if action == 'BUY':
                affordable = cash[symbol] / (price * (1 + slip) * (1 + fee))
                capacity = min(affordable, max(0.0, (limits['max_position'] - shares[symbol] * price) / price),
                               max(0.0, (limits['max_exposure'] - exposure) / price), max(0.0, (equity - exposure) / price))
                if max_drawdown is not None:
                    drawdown_cap = min(limits['max_exposure'], equity) * drawdown_factor(peak, equity, max_drawdown)
                    capacity = min(capacity, max(0.0, (drawdown_cap - exposure) / price))
            if action == 'BUY' and target is not None and capacity > 0:
                risk_probe = min(capacity, 1.0) / 2 or capacity
                veto = evaluate(action, row['confidence'], price, position=shares[symbol], equity=equity, exposure=exposure,
                                daily_loss=daily_loss, trade_count=trade_count, quantity=risk_probe, **limits)
            if action == 'BUY' and target is not None and capacity > 0 and not veto:
                events = {day: event[0] for (name, day), event in dividends.items() if name == symbol}
                info = liquidity(sessions, frames[symbol], pd.Timestamp(row['decision_time']), dividends=events)
                if info is None:
                    return {'ending_balance': None, 'reason': f'Insufficient prior liquidity history for {symbol}', 'evaluations': evaluations}
                annual_volatility = info['daily_volatility'] * math.sqrt(252)
                weight = volatility_weight(target, annual_volatility)
            elif action == 'BUY' and target is not None:
                weight = None
            if action == 'BUY':
                desired = max(0.0, (equity * weight / len(symbols) - shares[symbol] * price) / price) if weight is not None else 0.0
                quantity = min(desired, capacity)
                if quantity > 0 and max_drawdown is not None:
                    buy_cost = price * ((1 + slip) * (1 + fee) - 1)
                    low, high = 0.0, quantity
                    for _ in range(60):
                        middle = (low + high) / 2
                        remaining_equity = max(0.0, equity - middle * buy_cost)
                        remaining_cap = min(limits['max_exposure'], remaining_equity) * drawdown_factor(peak, remaining_equity, max_drawdown)
                        if exposure + middle * price <= remaining_cap:
                            low = middle
                        else:
                            high = middle
                    quantity = low
                exhausts_cash = quantity == affordable
                if quantity > 0 and (quantity * price * (1 + slip) * (1 + fee) > cash[symbol] or
                                     (shares[symbol] + quantity) * price > limits['max_position'] or
                                     exposure + quantity * price > min(limits['max_exposure'], equity)):
                    quantity = math.nextafter(quantity, 0.0)
                if quantity * price <= 16 * math.ulp(max(equity, 1.0)):
                    quantity = 0.0
            else:
                quantity = shares[symbol]
            state = {'symbol': symbol, 'action': action, 'decision_time': row['decision_time'], 'execution_time': row['execution_time'],
                     'price': price, 'position': shares[symbol], 'equity': equity, 'exposure': exposure, 'daily_loss': daily_loss,
                     'trade_count': trade_count, 'quantity': quantity, 'confidence': row['confidence'], 'weight': weight,
                     'annual_volatility': annual_volatility, 'target_volatility': target, 'liquidity': info,
                     'risk_probe_quantity': risk_probe,
                     'cash': dict(cash), 'shares': dict(shares), 'prices': dict(prices), 'receivables': dict(receivables),
                     'fee': fee, 'slippage': slip, 'limits': dict(limits)}
            if max_drawdown is not None:
                state.update(peak=peak, max_drawdown=max_drawdown, drawdown_factor=drawdown_factor(peak, equity, max_drawdown))
            state_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            reasons = veto or (evaluate(action, row['confidence'], price, position=shares[symbol], equity=equity, exposure=exposure,
                                daily_loss=daily_loss, trade_count=trade_count, quantity=quantity, **limits)
                       if quantity > 0 else ['No capacity for this trade'])
            evaluations.append({**state, 'state_sha256': state_hash, 'risk_rejections': reasons, 'executed': not reasons})
            if reasons:
                continue
            if action == 'BUY':
                cash[symbol] = 0.0 if exhausts_cash else cash[symbol] - quantity * price * (1 + slip) * (1 + fee)
                shares[symbol] += quantity
            else:
                cash[symbol] += quantity * price * (1 - slip) * (1 - fee)
                shares[symbol] -= quantity
            trade_count += 1
        if max_drawdown is not None:
            equity = sum(cash[s] + shares[s] * prices[s] + receivables[s] for s in symbols)
            exposure = sum(shares[s] * prices[s] for s in symbols)
            drawdown_checks.append({'time': session.open.isoformat(), 'peak': peak, 'equity': equity,
                                    'factor': drawdown_factor(peak, equity, max_drawdown), 'exposure_after': exposure,
                                    'cap_after': min(limits['max_exposure'], equity) * drawdown_factor(peak, equity, max_drawdown)})
        last_bar = pd.date_range(session.open, session.close, freq='h', inclusive='left')[-1]
        previous_equity = sum(cash[s] + shares[s] * float(frames[s].loc[last_bar, 'close']) + receivables[s] for s in symbols)
        peak = max(peak, previous_equity)
    result = {'ending_balance': round(previous_equity, 2), 'return_pct': round((previous_equity / initial - 1) * 100, 2), 'evaluations': evaluations}
    if max_drawdown is not None:
        result['drawdown_checks'] = drawdown_checks
    return result
