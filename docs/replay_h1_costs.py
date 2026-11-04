"""Replay the published H1 decisions under flat fees and slippage, without model calls."""
import csv
import json
from collections import defaultdict
from pathlib import Path


def replay():
    path = Path(__file__).with_name('benchmark-2026-h1.csv')
    with path.open(newline='', encoding='utf-8') as handle:
        rows = list(csv.DictReader(handle))
    by_symbol = defaultdict(list)
    for row in rows:
        by_symbol[row['symbol']].append(row)
    if len(rows) != 650 or len(by_symbol) != 25 or any(len(group) != 26 for group in by_symbol.values()):
        raise ValueError('Unexpected benchmark ledger shape')
    scenarios = []
    for multiplier in (0, .5, 1, 2):
        fee, slip = .0005 * multiplier, .001 * multiplier
        cash = {symbol: 400.0 for symbol in by_symbol}
        shares = {symbol: 0.0 for symbol in by_symbol}
        for row in rows:
            symbol, action, price = row['symbol'], row['action'], float(row['adjusted_open'])
            if action == 'BUY' and cash[symbol] > 0:
                shares[symbol], cash[symbol] = cash[symbol] / (price * (1 + slip) * (1 + fee)), 0.0
            elif action == 'SELL' and shares[symbol] > 0:
                cash[symbol], shares[symbol] = shares[symbol] * price * (1 - slip) * (1 - fee), 0.0
        ending = sum(cash[symbol] + shares[symbol] * float(group[-1]['next_adjusted_price']) for symbol, group in by_symbol.items())
        spy_ending = 10000 * 744.920227 / (681.219228 * (1 + slip) * (1 + fee))
        scenarios.append({'multiplier': multiplier, 'ending_balance': round(ending, 2),
                          'return_pct': round((ending / 10000 - 1) * 100, 2),
                          'spy_ending_balance': round(spy_ending, 2), 'spy_return_pct': round((spy_ending / 10000 - 1) * 100, 2)})
    return scenarios


if __name__ == '__main__':
    print(json.dumps(replay(), indent=2))
