import hashlib
import json

import pandas as pd
import pytest

from fasttrade import cli


def dataset(folder, future_price=100):
    folder.mkdir()
    dates = pd.bdate_range('2026-01-05', periods=25, tz='UTC')
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
        return {'action': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .8, 'HOLD': .1, 'SELL': .1}, 'model': model, 'latency_us': 1}
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
