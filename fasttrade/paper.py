"""Alpaca paper-trading account access, quotes and order execution.

Everything here submits to the paper API only; the dashboard never places live orders.
"""
import math
import os
import re

import httpx

from . import market
from .resilience import CircuitOpen, pooled_get, pooled_post, request

PAPER_BASE = 'https://paper-api.alpaca.markets'
DATA_BASE = 'https://data.alpaca.markets'
MIN_MARKET_CAP = 1_000_000_000
_symbol_re = re.compile(r'[A-Z0-9.\-]{1,20}')


def _symbol(symbol):
    symbol = str(symbol).upper()
    if not _symbol_re.fullmatch(symbol):
        raise ValueError('Invalid stock symbol')
    return symbol


def _headers():
    headers = {'APCA-API-KEY-ID': os.getenv('ALPACA_API_KEY'), 'APCA-API-SECRET-KEY': os.getenv('ALPACA_SECRET_KEY')}
    if not all(headers.values()):
        raise ValueError('Alpaca credentials are missing')
    return headers


def _error_detail(response):
    try:
        detail = ' '.join(str(response.json().get('message', '')).split())[:200]
    except Exception:
        return ''
    return f': {detail}' if detail else ''


def _call(method, url, **kwargs):
    headers = _headers()
    try:
        response = request(method, url, service='alpaca-paper', credentials=list(headers.values()), retry_transport=True, headers=headers, timeout=15, **kwargs)
        if response.status_code in (400, 401, 403, 404, 422):
            detail = _error_detail(response)
            if response.status_code == 401 or (response.status_code == 403 and not detail):
                raise ValueError('Alpaca rejected the paper trading credentials')
            raise ValueError('Alpaca rejected the request' + detail)
        if response.status_code == 429:
            raise ValueError('Alpaca paper trading rate limit reached')
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise ValueError('Invalid Alpaca paper trading response') from exc
        if not isinstance(data, (dict, list)):
            raise ValueError('Invalid Alpaca paper trading response')
        return data
    except httpx.TimeoutException as exc:
        raise ValueError('Alpaca paper trading timed out') from exc
    except ValueError:
        raise
    except CircuitOpen as exc:
        raise ValueError('Alpaca paper trading rate limited' if 'rate limit' in str(exc).lower() else 'Alpaca paper trading request failed') from exc
    except (httpx.HTTPError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Alpaca paper trading request failed') from exc


def overview():
    """Return paper account cash, equity, buying power and open positions."""
    account = _call(pooled_get, f'{PAPER_BASE}/v2/account')
    rows = _call(pooled_get, f'{PAPER_BASE}/v2/positions')
    if not isinstance(account, dict) or not isinstance(rows, list):
        raise ValueError('Invalid Alpaca paper account response')
    positions = []
    for row in rows:
        try:
            entry = {'symbol': _symbol(row['symbol']), 'quantity': float(row['qty']), 'avg_price': float(row['avg_entry_price']),
                     'price': float(row['current_price']), 'value': float(row['market_value']), 'unrealized': float(row['unrealized_pl'])}
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            continue
        if all(math.isfinite(entry[field]) and entry[field] >= 0 for field in ('quantity', 'avg_price', 'price')):
            positions.append(entry)
    result = {'positions': positions}
    try:
        for field in ('cash', 'equity', 'buying_power'):
            result[field] = float(account[field])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid Alpaca paper account response') from exc
    if not all(math.isfinite(result[field]) and result[field] >= 0 for field in ('cash', 'equity', 'buying_power')):
        raise ValueError('Invalid Alpaca paper account response')
    return result


def last_price(symbol):
    """Return the latest Alpaca trade price for a symbol."""
    symbol = _symbol(symbol)
    data = _call(pooled_get, f'{DATA_BASE}/v2/stocks/trades/latest', params={'symbols': symbol, 'feed': 'iex'})
    try:
        price = float(data['trades'][symbol]['p'])
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        raise ValueError('No Alpaca trade price available for this symbol') from exc
    if not math.isfinite(price) or price <= 0:
        raise ValueError('No Alpaca trade price available for this symbol')
    return {'symbol': symbol, 'price': price}


def market_clock():
    """Return whether the US market is open and when it next opens."""
    data = _call(pooled_get, f'{PAPER_BASE}/v2/clock')
    try:
        is_open = data['is_open'] is True
        next_open = str(data['next_open']) if data['next_open'] else None
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Invalid Alpaca clock response') from exc
    return {'is_open': is_open, 'next_open': next_open}


def submit_order(symbol, side, quantity):
    """Submit a paper market order; Alpaca fills it at the next available price."""
    symbol = _symbol(symbol)
    if side not in ('BUY', 'SELL'):
        raise ValueError('Order side must be BUY or SELL')
    quantity = float(quantity)
    if not math.isfinite(quantity) or quantity <= 0:
        raise ValueError('Order quantity must be positive')
    data = _call(pooled_post, f'{PAPER_BASE}/v2/orders', json={'symbol': symbol, 'qty': str(quantity), 'side': side.lower(), 'type': 'market', 'time_in_force': 'day'})
    try:
        order_id, status = str(data['id']), str(data['status'])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('Invalid Alpaca order response') from exc
    try:
        filled_quantity = float(data['filled_qty'])
        filled_avg_price = float(data['filled_avg_price']) if data['filled_avg_price'] else None
    except (TypeError, ValueError, OverflowError):
        filled_quantity, filled_avg_price = 0.0, None
    pending, next_open = False, None
    try:
        clock = market_clock()
        pending, next_open = not clock['is_open'], clock['next_open']
    except ValueError:
        pass
    return {'symbol': symbol, 'side': side, 'quantity': quantity, 'order_id': order_id, 'status': status, 'filled_quantity': filled_quantity, 'filled_avg_price': filled_avg_price, 'pending': pending, 'next_open': next_open}


def movers(limit=8, minimum_cap=MIN_MARKET_CAP):
    """Return today's top gainer with a market cap of at least minimum_cap.

    Cap candidates come from Yahoo fundamentals; entries with an unknown cap are
    reported in `considered` but never picked.
    """
    data = _call(pooled_get, f'{DATA_BASE}/v1beta1/screener/stocks/movers')
    gainers = data.get('gainers') if isinstance(data, dict) else None
    if not isinstance(gainers, list):
        raise ValueError('Invalid Alpaca movers response')
    considered = []
    for row in gainers:
        if len(considered) >= limit:
            break
        try:
            symbol = _symbol(row['symbol'])
            price, change_percent = float(row['price']), float(row['percent_change'])
        except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
            continue
        if not math.isfinite(price) or price <= 0 or not math.isfinite(change_percent) or change_percent <= 0:
            continue
        entry = {'symbol': symbol, 'price': price, 'change_percent': change_percent, 'market_cap': None}
        try:
            entry['market_cap'] = market.market_cap(symbol)
        except ValueError:
            pass
        considered.append(entry)
        if entry['market_cap'] is not None and entry['market_cap'] >= minimum_cap:
            return {'pick': entry, 'considered': considered, 'minimum_cap': minimum_cap}
    return {'pick': None, 'considered': considered, 'minimum_cap': minimum_cap}
