"""Replay recorded benchmark decisions with and without the calibration gate.

Both arms use the same model calls and the same execution prices from one
benchmark report; only the gate differs, so the return comparison isolates
what certification does rather than re-running the model. Mirrors the
benchmark engine's rules: per-symbol equal cash split, BUY invests all
remaining cash at the execution price, SELL exits at it, final mark at the
last valuation price.
"""
import json
import sys

from fasttrade.calibration import apply_isotonic, apply_temperature, conformal_interval, interval_excludes_half


def buy_allowed(row, artifact):
    p = row['probabilities']['BUY']
    calibrated = float(apply_temperature(p, artifact['temperature']))
    quantile = artifact['conformal']['quantile']
    interval = conformal_interval(calibrated, quantile) if quantile is not None else [0.0, 1.0]
    if not interval_excludes_half(interval):
        return False, 'direction'
    gate = artifact['returns']
    expected = float(apply_isotonic(p, gate['blocks']))
    lower = (expected - gate['quantile']) if gate['quantile'] is not None else None
    if lower is None or lower <= gate['round_trip_cost']:
        return False, 'return'
    return True, None


def replay(decisions, symbols, initial, artifact, gated):
    cash = {s: initial / len(symbols) for s in symbols}
    shares = {s: 0.0 for s in symbols}
    final_close = {}
    taken = blocked = 0
    blocked_reasons = {'direction': 0, 'return': 0}
    for row in decisions:
        symbol = row['symbol']
        final_close[symbol] = row['next_valuation_price']
        if row['action'] == 'SELL' and shares[symbol] > 0:
            cash[symbol], shares[symbol] = shares[symbol] * row['execution_price'], 0.0
            taken += 1
        elif row['action'] == 'BUY':
            allowed, reason = (buy_allowed(row, artifact) if gated else (True, None))
            if allowed and cash[symbol] > 0:
                shares[symbol], cash[symbol] = cash[symbol] / row['execution_price'], 0.0
                taken += 1
            elif not allowed:
                blocked += 1
                blocked_reasons[reason] += 1
    ending = sum(cash[s] + shares[s] * final_close[s] for s in symbols)
    return {'ending': round(ending, 2), 'return_pct': round((ending / initial - 1) * 100, 2), 'trades_taken': taken, 'buys_blocked': blocked, 'blocked_by': blocked_reasons}


def main(report_path, artifact_path):
    report = json.load(open(report_path, encoding='utf-8'))
    artifact = json.load(open(artifact_path, encoding='utf-8'))
    initial = report['initial_balance']
    ungated = replay(report['decisions'], report['symbols'], initial, artifact, gated=False)
    gated = replay(report['decisions'], report['symbols'], initial, artifact, gated=True)
    print(json.dumps({'window': f"{report['start']} to {report['end']}", 'symbols': report['symbols'], 'decisions': len(report['decisions']), 'engine_return_pct': report['return_pct'], 'replay_ungated': ungated, 'replay_gated': gated, 'spy_return_pct': report['spy_return_pct'], 'directional_accuracy_pct': report['directional_accuracy_pct']}, indent=2))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
