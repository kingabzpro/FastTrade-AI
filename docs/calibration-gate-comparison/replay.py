"""Replay recorded benchmark decisions with and without the calibration gate.

v3 gate semantics: certification is model-level. When the artifact certifies
aggregate net-of-cost edge on its test window, signals flow to the portfolio
unchanged; when it does not, BUYs are blocked and the arm holds cash. Both
arms use the same model calls and the same execution prices from one benchmark
report, so the comparison isolates what certification does rather than
re-running the model. Mirrors the benchmark engine's rules: per-symbol equal
cash split, BUY invests all remaining cash at the execution price, SELL exits
at it, final mark at the last valuation price.

Caveat: an artifact fitted on data overlapping the replay window is a
mechanism demonstration, not a walk-forward proof.
"""
import json
import sys


def replay(decisions, symbols, initial, artifact, gated):
    edge = artifact.get('edge') or {}
    gate_open = (not gated) or edge.get('certified') is True
    cash = {s: initial / len(symbols) for s in symbols}
    shares = {s: 0.0 for s in symbols}
    final_close = {}
    taken = blocked = 0
    for row in decisions:
        symbol = row['symbol']
        final_close[symbol] = row['next_valuation_price']
        if row['action'] == 'SELL' and shares[symbol] > 0:
            cash[symbol], shares[symbol] = shares[symbol] * row['execution_price'], 0.0
            taken += 1
        elif row['action'] == 'BUY':
            if gate_open and cash[symbol] > 0:
                shares[symbol], cash[symbol] = cash[symbol] / row['execution_price'], 0.0
                taken += 1
            elif not gate_open:
                blocked += 1
    ending = sum(cash[s] + shares[s] * final_close[s] for s in symbols)
    return {'ending': round(ending, 2), 'return_pct': round((ending / initial - 1) * 100, 2), 'trades_taken': taken, 'buys_blocked': blocked, 'gate_open': bool(gate_open)}


def main(report_path, artifact_path):
    report = json.load(open(report_path, encoding='utf-8'))
    artifact = json.load(open(artifact_path, encoding='utf-8'))
    initial = report['initial_balance']
    ungated = replay(report['decisions'], report['symbols'], initial, artifact, gated=False)
    gated = replay(report['decisions'], report['symbols'], initial, artifact, gated=True)
    print(json.dumps({'window': f"{report['start']} to {report['end']}", 'symbols': report['symbols'], 'decisions': len(report['decisions']), 'engine_return_pct': report['return_pct'], 'replay_ungated': ungated, 'replay_gated': gated, 'artifact_edge': {'certified': artifact.get('edge', {}).get('certified'), 'ci': artifact.get('edge', {}).get('ci')}, 'spy_return_pct': report['spy_return_pct'], 'directional_accuracy_pct': report['directional_accuracy_pct']}, indent=2))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
