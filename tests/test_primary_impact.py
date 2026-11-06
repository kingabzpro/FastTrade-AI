import hashlib
import json

import pandas as pd
import pytest

from fasttrade import backtest, cli
from test_backtest import dataset


def args_for(folder, *extra):
    return cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27',
                                    '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json'), *extra])


def test_impact_is_primary_by_default_and_matches_one_times_replay(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1, 'usage': None})
    result = backtest.run(args_for(folder))
    assert result['slippage_model'] == 'impact'
    assert result['slippage_bps'] is None
    assert result['flat_slippage_bps'] == 10
    assert result['ending_balance'] == pytest.approx(10000 / (1.0001 * 1.0005), abs=.01)
    assert result['spy_ending_balance'] == result['ending_balance']
    assert result['trades'][0]['impact_bps'] == 1
    assert result['ending_balance'] == result['impact_sensitivity']['scenarios'][1]['ending_balance']
    assert result['spy_ending_balance'] == result['impact_sensitivity']['scenarios'][1]['spy_ending_balance']


def test_explicit_flat_mode_preserves_previous_primary_results(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1, 'usage': None})
    result = backtest.run(args_for(folder, '--slippage-model', 'flat'))
    assert result['slippage_model'] == 'flat'
    assert result['ending_balance'] == pytest.approx(10000 / (1.001 * 1.0005), abs=.01)
    assert result['ending_balance'] == result['cost_sensitivity']['scenarios'][1]['ending_balance']


def test_missing_impact_liquidity_is_rejected_before_any_model_request(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder)
    path = folder / 'SPY.csv'
    frame = pd.read_csv(path)
    frame['volume'] = 0
    frame.to_csv(path, index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['sha256']['SPY.csv'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    with pytest.raises(ValueError, match='liquidity.*SPY'):
        backtest.run(args_for(folder))


def test_primary_impact_charges_both_sides_and_matches_replay(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder, future_price=120, periods=35)
    actions = iter(['BUY', 'SELL'])
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': next(actions), 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1, 'usage': None})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    buy, sell = result['trades']
    assert [buy['action'], sell['action']] == ['BUY', 'SELL']
    assert buy['fill_price'] == pytest.approx(buy['reference_open'] * (1 + buy['impact_bps'] / 10000))
    assert sell['fill_price'] == pytest.approx(sell['reference_open'] * (1 - sell['impact_bps'] / 10000))
    expected = buy['shares'] * sell['fill_price'] * (1 - .0005)
    assert result['ending_balance'] == pytest.approx(expected, abs=.01)
    assert result['ending_balance'] == result['impact_sensitivity']['scenarios'][1]['ending_balance']
    assert result['spy_ending_balance'] == result['impact_sensitivity']['scenarios'][1]['spy_ending_balance']


def test_future_candles_do_not_change_first_impact_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1, 'usage': None})
    first, second = tmp_path / 'first', tmp_path / 'second'
    dataset(first, future_price=100)
    dataset(second, future_price=120)
    result_a, result_b = backtest.run(args_for(first)), backtest.run(args_for(second))
    assert result_a['trades'][0]['impact'] == result_b['trades'][0]['impact']
    assert result_a['spy_entry_impact'] == result_b['spy_entry_impact']


def test_later_missing_liquidity_is_rejected_before_first_model_request(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder, periods=35)
    path = folder / 'AAPL.csv'
    frame = pd.read_csv(path)
    frame.loc[frame.timestamp.str.startswith('2026-01-30'), 'volume'] = 0
    frame.to_csv(path, index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['sha256']['AAPL.csv'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--output', str(folder / 'report.json')])
    with pytest.raises(ValueError, match='liquidity.*AAPL'):
        backtest.run(args)


def test_primary_impact_preserves_dividend_receivable_on_sale(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder, periods=35)
    dividends = folder / 'dividends.csv'
    pd.DataFrame([{'symbol': 'AAPL', 'ex_date': '2026-01-27', 'pay_date': '2026-03-01', 'amount': 1.0}]).to_csv(dividends, index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['corporate_actions'] = 'cash_dividends_only'
    manifest['sha256']['dividends.csv'] = hashlib.sha256(dividends.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    actions = iter(['BUY', 'SELL'])
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': next(actions), 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1, 'usage': None})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    buy, sell = result['trades']
    assert result['dividend_receivables'] == pytest.approx(buy['shares'], abs=.01)
    assert result['ending_balance'] == pytest.approx(buy['shares'] * sell['fill_price'] * (1 - .0005) + buy['shares'], abs=.01)
    assert result['ending_balance'] == result['impact_sensitivity']['scenarios'][1]['ending_balance']
