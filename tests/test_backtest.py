import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from fasttrade import cli


def dataset(folder, future_price=100, periods=25):
    folder.mkdir()
    dates = pd.bdate_range('2026-01-05', periods=periods, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14, minutes=30),
                             'close': dates + pd.Timedelta(hours=21)})
    sessions.to_csv(folder / 'sessions.csv', index=False)
    stamps = [start + pd.Timedelta(hours=h) for start in sessions['open'] for h in range(7)]
    prices = [future_price if t >= pd.Timestamp('2026-01-27T14:30Z') else 100 for t in stamps]
    for symbol in ('AAPL', 'SPY'):
        pd.DataFrame({'timestamp': stamps, 'open': prices, 'high': prices, 'low': prices,
                      'close': prices, 'volume': 100}).to_csv(folder / f'{symbol}.csv', index=False)
    names = ['sessions.csv', 'AAPL.csv', 'SPY.csv']
    manifest = {'price_basis': 'unadjusted', 'corporate_actions': 'none',
                'sha256': {n: hashlib.sha256((folder / n).read_bytes()).hexdigest() for n in names}}
    (folder / 'manifest.json').write_text(json.dumps(manifest))


def run_fixture(folder, monkeypatch):
    from fasttrade import backtest
    states = []
    def predict(state, url, model, key):
        states.append(state)
        return {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': model, 'latency_us': 1000, 'usage': {'input_tokens': 500, 'output_tokens': 5}}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    return backtest.run(args), states


def test_backtest_uses_only_past_data_and_charges_costs(tmp_path, monkeypatch):
    dataset(tmp_path / 'one')
    dataset(tmp_path / 'two', future_price=110)
    first, states_a = run_fixture(tmp_path / 'one', monkeypatch)
    second, states_b = run_fixture(tmp_path / 'two', monkeypatch)
    assert states_a == states_b
    assert first['decisions'][0]['state_sha256'] == second['decisions'][0]['state_sha256']
    assert first['decisions'][0]['decision_time'] < first['decisions'][0]['execution_time']
    assert first['ending_balance'] == pytest.approx(10000 / 1.001 / 1.0005, abs=.01)
    assert second['ending_balance'] > first['ending_balance']
    assert first['total_fees'] > 0
    assert first['mean_model_latency_ms'] == 1
    assert first['input_tokens_successful'] == 500
    assert first['usage_complete'] is True
    assert first['overfitting']['pbo'] is None
    assert first['overfitting']['fold_distribution'] is None
    assert 'multiple variants' in first['overfitting']['reason']


def test_backtest_records_forecast_used_in_each_decision(tmp_path, monkeypatch):
    from fasttrade import backtest, forecast
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(forecast, '_predict', lambda values: [99.0, 100.0, 101.0])
    states = []
    def predict(state, url, model, key):
        states.append(state)
        return {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': model, 'latency_us': 1000, 'usage': {'input_tokens': 500, 'output_tokens': 5}}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json'), '--forecast'])
    report = backtest.run(args)
    assert all(item['forecast'] == state['forecast'] for item, state in zip(report['decisions'], states))
    assert all(item['forecast']['status'] == 'unvalidated' for item in report['decisions'])
    assert all(item['forecast_disagreement']['direction'] == 'flat' for item in report['decisions'])


def test_backtest_rejects_modified_data(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    with (folder / 'AAPL.csv').open('a') as f:
        f.write('\n')
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--output', str(folder / 'report.json')])
    with pytest.raises(ValueError, match='checksum'):
        backtest.run(args)


def test_backtest_sells_existing_shares_and_applies_both_sides(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    for symbol in ('AAPL', 'SPY'):
        path = folder / f'{symbol}.csv'
        frame = pd.read_csv(path)
        later = pd.to_datetime(frame.timestamp, utc=True) >= pd.Timestamp('2026-02-02T14:30Z')
        frame.loc[later, ['open', 'high', 'low', 'close']] = 120
        frame.to_csv(path, index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for symbol in ('AAPL', 'SPY'):
        name = f'{symbol}.csv'
        manifest['sha256'][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    actions = iter(['BUY', 'SELL'])
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': next(actions), 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    expected_shares = 10000 / (100 * 1.001 * 1.0005)
    assert [t['action'] for t in result['trades']] == ['BUY', 'SELL']
    assert result['ending_balance'] == pytest.approx(expected_shares * 120 * .999 * .9995, abs=.01)
    assert result['equity_curve'][-1]['equity'] == pytest.approx(result['ending_balance'], abs=.01)
    scenarios = result['cost_sensitivity']['scenarios']
    assert [scenario['multiplier'] for scenario in scenarios] == [.5, 1, 2]
    for scenario in scenarios:
        factor = scenario['multiplier']
        expected = 10000 / (100 * (1 + .001 * factor) * (1 + .0005 * factor)) * 120 * (1 - .001 * factor) * (1 - .0005 * factor)
        assert scenario['ending_balance'] == pytest.approx(expected, abs=.01)
        expected_spy = 10000 / (100 * (1 + .001 * factor) * (1 + .0005 * factor)) * 120
        assert scenario['spy_ending_balance'] == pytest.approx(expected_spy, abs=.01)


def test_backtest_compares_complete_execution_intervals_to_spy(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder, future_price=110, periods=35)
    spy = pd.read_csv(folder / 'SPY.csv')
    spy.loc[:, ['open', 'high', 'low', 'close']] = 100
    spy.to_csv(folder / 'SPY.csv', index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['sha256']['SPY.csv'] = hashlib.sha256((folder / 'SPY.csv').read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-10', '--symbols', 'AAPL', '--model', 'test', '--fee-bps', '0', '--slippage-bps', '0', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    significance = result['significance']
    assert significance['observations'] == 2
    assert significance['mean_weekly_excess_return'] == pytest.approx(.05)
    assert significance['confidence_interval'] == pytest.approx([0, .1])
    assert significance['final_partial_period_excluded'] is True
    assert significance['p_value'] is None


def test_backtest_significance_includes_first_spy_entry_cost(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    significance = backtest.run(args)['significance']
    assert significance['observations'] == 1
    assert significance['mean_weekly_excess_return'] == pytest.approx(1 - 1 / (1.001 * 1.0005))
    assert significance['confidence_interval'] is None


def test_cost_sensitivity_rejects_invalid_scaled_cost_without_rejecting_base_run(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--slippage-bps', '6000', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    assert result['cost_sensitivity']['scenarios'][1]['ending_balance'] == result['ending_balance']
    stressed = result['cost_sensitivity']['scenarios'][2]
    assert stressed['ending_balance'] is None
    assert '100%' in stressed['reason']


def test_impact_sensitivity_uses_prior_liquidity_and_preserves_flat_result(tmp_path, monkeypatch):
    folder = tmp_path / 'data'
    dataset(folder)
    result, _ = run_fixture(folder, monkeypatch)
    impact = result['impact_sensitivity']['scenarios']
    assert [scenario['multiplier'] for scenario in impact] == [.5, 1, 2]
    assert impact[0]['ending_balance'] == pytest.approx(10000 / (1.00005 * 1.00025), abs=.01)
    assert impact[0]['ending_balance'] > impact[1]['ending_balance'] > impact[2]['ending_balance']
    assert impact[1]['trades'][0]['impact_bps'] == 1
    assert impact[1]['trades'][0]['daily_volume'] == 700
    assert result['cost_sensitivity']['scenarios'][1]['ending_balance'] == result['ending_balance']


def test_impact_reports_unavailable_for_zero_volume_without_rejecting_backtest(tmp_path, monkeypatch):
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
    result, _ = run_fixture(folder, monkeypatch)
    assert result['ending_balance'] is not None
    assert all(s['ending_balance'] is None and 'SPY' in s['reason'] for s in result['impact_sensitivity']['scenarios'])


def test_recorded_backtest_significance_replays_without_model_calls(tmp_path, monkeypatch):
    from fasttrade import backtest
    root = Path(__file__).resolve().parents[1] / 'docs' / 'backtest-2026-09-28'
    original = json.loads((root / 'report-measured.json').read_text(encoding='utf-8'))
    rows = {(row['symbol'], row['decision_time']): row for row in original['decisions']}
    def recorded(args):
        row = rows[(args.symbol, args.as_of)]
        return {'decision': dict(row, latency_us=row['latency_ms'] * 1000, confidence=0), 'market': {'candle_timestamp': row['candle_timestamp']}}
    monkeypatch.setattr(cli, 'run', recorded)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(root), '--start', original['start'], '--end', original['end'], '--symbols', ','.join(original['symbols']), '--model', original['model_requested'], '--fee-bps', str(original['fee_bps']), '--slippage-bps', str(original['slippage_bps']), '--output', str(tmp_path / 'unused.json')])
    result = backtest.run(args)
    for key in ('ending_balance', 'spy_ending_balance', 'total_fees', 'dataset_manifest_sha256'):
        assert result[key] == original[key]
    base = result['cost_sensitivity']['scenarios'][1]
    assert base['ending_balance'] == original['ending_balance']
    assert base['spy_ending_balance'] == original['spy_ending_balance']
    assert [scenario['ending_balance'] for scenario in result['cost_sensitivity']['scenarios']] == [10240.46, 10230.73, 10211.30]
    assert [scenario['spy_ending_balance'] for scenario in result['cost_sensitivity']['scenarios']] == [9992.89, 9985.41, 9970.46]
    assert result['significance']['observations'] == 3
    assert result['significance']['mean_weekly_excess_return'] == pytest.approx(.0128976731)
    assert result['significance']['confidence_interval'] == pytest.approx([.003917102, .021878244])


@pytest.mark.parametrize(('folder_name', 'observations', 'mean', 'interval'), [
    ('backtest-2026-09-28-alpaca', 3, .012007806862188533, [.0037734611748741673, .0202421525495029]),
    ('backtest-2026-10', 1, .010036297729023813, None),
])
def test_other_recorded_backtests_replay_significance_without_model_calls(tmp_path, monkeypatch, folder_name, observations, mean, interval):
    from fasttrade import backtest
    root = Path(__file__).resolve().parents[1] / 'docs' / folder_name
    original = json.loads((root / 'report.json').read_text(encoding='utf-8'))
    rows = {(row['symbol'], row['decision_time']): row for row in original['decisions']}
    def recorded(args):
        row = rows[(args.symbol, args.as_of)]
        return {'decision': dict(row, latency_us=row.get('latency_ms', 0) * 1000, confidence=row.get('confidence', 0)),
                'market': {'candle_timestamp': row['candle_timestamp']}}
    monkeypatch.setattr(cli, 'run', recorded)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(root), '--start', original['start'], '--end', original['end'], '--symbols', ','.join(original['symbols']), '--model', original['model_requested'], '--fee-bps', str(original['fee_bps']), '--slippage-bps', str(original['slippage_bps']), '--output', str(tmp_path / 'unused.json')])
    result = backtest.run(args)
    for key in ('ending_balance', 'spy_ending_balance', 'total_fees', 'dataset_manifest_sha256'):
        assert result[key] == original[key]
    assert result['significance']['observations'] == observations
    assert result['significance']['mean_weekly_excess_return'] == pytest.approx(mean)
    if interval is None:
        assert result['significance']['confidence_interval'] is None
    else:
        assert result['significance']['confidence_interval'] == pytest.approx(interval)


@pytest.mark.parametrize('name', ['baseline', 'forecast'])
def test_forecast_comparison_replays_recorded_decisions(tmp_path, monkeypatch, name):
    from fasttrade import backtest
    docs = Path(__file__).resolve().parents[1] / 'docs'
    original = json.loads((docs / 'tsfm-comparison' / f'{name}.json').read_text(encoding='utf-8'))
    other_name = 'forecast' if name == 'baseline' else 'baseline'
    other = json.loads((docs / 'tsfm-comparison' / f'{other_name}.json').read_text(encoding='utf-8'))
    assert original['dataset_manifest_sha256'] == other['dataset_manifest_sha256']
    assert [(d['symbol'], d['decision_time']) for d in original['decisions']] == [(d['symbol'], d['decision_time']) for d in other['decisions']]
    rows = {(d['symbol'], d['decision_time']): d for d in original['decisions']}
    def recorded(args):
        row = rows[(args.symbol, args.as_of)]
        return {'decision': dict(row, latency_us=row['latency_ms'] * 1000),
                'market': {'candle_timestamp': row['candle_timestamp'], 'forecast': row.get('forecast')}}
    monkeypatch.setattr(cli, 'run', recorded)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(docs / 'backtest-2026-09-28-alpaca'), '--start', original['start'], '--end', original['end'], '--symbols', ','.join(original['symbols']), '--model', original['model_requested'], '--output', str(tmp_path / 'unused.json')])
    result = backtest.run(args)
    for key in ('ending_balance', 'spy_ending_balance', 'total_fees', 'dataset_manifest_sha256'):
        assert result[key] == original[key]


def test_backtest_does_not_compress_missing_execution_weeks(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder, periods=35)
    for name, column in (('sessions.csv', 'open'), ('AAPL.csv', 'timestamp'), ('SPY.csv', 'timestamp')):
        frame = pd.read_csv(folder / name)
        times = pd.to_datetime(frame[column], utc=True)
        frame = frame[(times < pd.Timestamp('2026-02-02', tz='UTC')) | (times >= pd.Timestamp('2026-02-07', tz='UTC'))]
        frame.to_csv(folder / name, index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name in ('sessions.csv', 'AAPL.csv', 'SPY.csv'):
        manifest['sha256'][name] = hashlib.sha256((folder / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-10', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    significance = backtest.run(args)['significance']
    assert significance['confidence_interval'] is None
    assert significance['mean_weekly_excess_return'] is None
    assert 'not consecutive' in significance['reason']


def test_backtest_rejects_corporate_actions_and_missing_session(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['corporate_actions'] = 'present'
    manifest_path.write_text(json.dumps(manifest))
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--output', str(folder / 'report.json')])
    with pytest.raises(ValueError, match='corporate actions'):
        backtest.run(args)
    manifest['corporate_actions'] = 'none'
    path = folder / 'AAPL.csv'
    frame = pd.read_csv(path)
    frame = frame[pd.to_datetime(frame.timestamp, utc=True) != pd.Timestamp('2026-01-20T14:30Z')]
    frame.to_csv(path, index=False)
    manifest['sha256']['AAPL.csv'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Missing session candles'):
        backtest.run(args)


def test_cli_saves_report_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    argv = ['ft', 'backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')]
    monkeypatch.setattr(cli.sys, 'argv', argv)
    assert cli.main() == 0
    saved = (folder / 'report.json').read_text()
    assert json.loads(saved)['decisions'][0]['action'] == 'HOLD'
    assert cli.main() == 1
    assert (folder / 'report.json').read_text() == saved
    capsys.readouterr()


def test_invalid_response_retries_without_inventing_decisions(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    attempts = []
    def predict(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError('Invalid SystemOne response')
        return {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': 1}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    assert len(result['decisions']) == 1 and result['model_retries'] == 1
    assert result['trades'] == [] and result['ending_balance'] == 10000
    assert result['usage_complete'] is False


def test_backtest_averages_request_latency_and_sums_usage(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    metrics = iter([(1000, 500), (3000, 1000)])
    def predict(*args):
        latency, tokens = next(metrics)
        return {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': latency, 'usage': {'input_tokens': tokens, 'output_tokens': 0}}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    assert result['mean_model_latency_ms'] == 2
    assert result['input_tokens_successful'] == 1500
    assert result['usage_complete'] is True


def test_partial_usage_is_not_reported_as_complete(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'HOLD', 'confidence': .8, 'probabilities': {'BUY': .1, 'HOLD': .8, 'SELL': .1}, 'model': 'test', 'latency_us': 1000, 'usage': {'input_tokens': 500, 'output_tokens': None}})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    assert result['input_tokens_successful'] == 500
    assert result['usage_complete'] is False


def test_unpaid_dividends_are_earned_only_on_shares_held_before_ex_date(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    pd.DataFrame([
        {'symbol': 'AAPL', 'ex_date': '2026-01-26', 'pay_date': '2026-02-10', 'amount': 50},
        {'symbol': 'AAPL', 'ex_date': '2026-01-27', 'pay_date': '2026-02-10', 'amount': 2},
        {'symbol': 'SPY', 'ex_date': '2026-01-27', 'pay_date': '2026-02-10', 'amount': 1},
    ]).to_csv(folder / 'dividends.csv', index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['corporate_actions'] = 'cash_dividends_only'
    manifest['sha256']['dividends.csv'] = hashlib.sha256((folder / 'dividends.csv').read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    shares = 10000 / (100 * 1.001 * 1.0005)
    assert result['dividend_receivables'] == pytest.approx(shares * 2, abs=.01)
    assert result['spy_dividend_receivables'] == pytest.approx(shares, abs=.01)
    assert result['ending_balance'] == pytest.approx(shares * 102, abs=.01)
    assert result['spy_ending_balance'] == pytest.approx(shares * 101, abs=.01)


def test_dividends_payable_during_test_are_rejected(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    pd.DataFrame([{'symbol': 'AAPL', 'ex_date': '2026-01-26', 'pay_date': '2026-01-27', 'amount': 2}]).to_csv(folder / 'dividends.csv', index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['corporate_actions'] = 'cash_dividends_only'
    manifest['sha256']['dividends.csv'] = hashlib.sha256((folder / 'dividends.csv').read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('Model must not be called'))
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-01-27', '--symbols', 'AAPL', '--output', str(folder / 'report.json')])
    with pytest.raises(ValueError, match='payment'):
        backtest.run(args)


def test_dividend_receivable_survives_a_later_sale(tmp_path, monkeypatch):
    from fasttrade import backtest
    folder = tmp_path / 'data'
    dataset(folder)
    pd.DataFrame([{'symbol': 'AAPL', 'ex_date': '2026-01-28', 'pay_date': '2026-02-10', 'amount': 2},
                  {'symbol': 'SPY', 'ex_date': '2026-01-28', 'pay_date': '2026-02-10', 'amount': 1}]).to_csv(folder / 'dividends.csv', index=False)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['corporate_actions'] = 'cash_dividends_only'
    manifest['sha256']['dividends.csv'] = hashlib.sha256((folder / 'dividends.csv').read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    actions = iter(['BUY', 'SELL'])
    monkeypatch.setattr(cli, 'predict', lambda *args: {'action': next(actions), 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': 'test', 'latency_us': 1})
    args = cli.parser().parse_args(['backtest', '--data-dir', str(folder), '--start', '2026-01-19', '--end', '2026-02-03', '--symbols', 'AAPL', '--model', 'test', '--output', str(folder / 'report.json')])
    result = backtest.run(args)
    shares = 10000 / (100 * 1.001 * 1.0005)
    assert result['dividend_receivables'] == pytest.approx(shares * 2, abs=.01)
    assert result['ending_balance'] == pytest.approx(shares * (100 * .999 * .9995 + 2), abs=.01)
    assert [trade['action'] for trade in result['trades']] == ['BUY', 'SELL']
    assert result['significance']['mean_weekly_excess_return'] == pytest.approx(.01 / (1.001 * 1.0005))
    for scenario in result['cost_sensitivity']['scenarios']:
        factor = scenario['multiplier']
        replay_shares = 10000 / (100 * (1 + .001 * factor) * (1 + .0005 * factor))
        assert scenario['ending_balance'] == pytest.approx(replay_shares * (100 * (1 - .001 * factor) * (1 - .0005 * factor) + 2), abs=.01)
        assert scenario['spy_ending_balance'] == pytest.approx(replay_shares * 101, abs=.01)
