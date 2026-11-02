import json
import threading
import time

import pytest

from fasttrade import cli


def test_batch_passes_selected_provider(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, 'run', lambda args: seen.append(args.source) or {'source': 'Yahoo Finance'})
    args = cli.parser().parse_args(['analyze-batch', 'AAPL', 'MSFT', '--source', 'yahoo'])
    cli.run_batch(args)
    assert seen == ['yahoo', 'yahoo']


def test_batch_summarizes_reported_usage_and_marks_missing_or_failed_calls(monkeypatch):
    def analyze(args):
        if args.symbol == 'FAIL':
            raise ValueError('Provider failed')
        usage = {'input_tokens': 100, 'output_tokens': 7} if args.symbol == 'A' else {'input_tokens': 200, 'output_tokens': None}
        return {'decision': {'usage': usage}}
    monkeypatch.setattr(cli, 'run', analyze)
    args = cli.parser().parse_args(['analyze-batch', 'A', 'B', 'FAIL'])
    result = cli.run_batch(args)
    assert result['usage'] == {'input_tokens_reported': 300, 'output_tokens_reported': 7, 'complete': False, 'status': 'Token information unavailable for some analyses'}
    assert [item['symbol'] for item in result['results']] == ['A', 'B', 'FAIL']
    assert result['results'][2]['error'] == 'Provider failed'


def test_batch_usage_is_complete_only_when_every_result_reports_both_counts(monkeypatch):
    monkeypatch.setattr(cli, 'run', lambda args: {'decision': {'usage': {'input_tokens': 3, 'output_tokens': 0}}})
    args = cli.parser().parse_args(['analyze-batch', 'A', 'B'])
    assert cli.run_batch(args)['usage'] == {'input_tokens_reported': 6, 'output_tokens_reported': 0, 'complete': True, 'status': 'Token information available'}


def test_missing_token_info_does_not_fail_successful_batch(monkeypatch):
    monkeypatch.setattr(cli, 'run', lambda args: {'decision': {'action': 'BUY', 'usage': None}})
    args = cli.parser().parse_args(['analyze-batch', 'A'])
    result = cli.run_batch(args)
    assert result['results'][0]['result']['decision']['action'] == 'BUY'
    assert result['usage']['status'] == 'Token information unavailable'
    assert result['usage']['complete'] is False


def test_batch_runs_tickers_concurrently_and_returns_partial_results(monkeypatch, capsys):
    barrier = threading.Barrier(2, timeout=3)
    def analyze(args):
        barrier.wait()
        if args.symbol == 'MSFT':
            raise ValueError('Market data unavailable')
        return {'market': {'symbol': args.symbol}, 'actionable_signal': None}
    monkeypatch.setattr(cli, 'run', analyze)
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'analyze-batch', 'aapl', 'msft', '--concurrency', '2'])
    assert cli.main() == 1
    results = json.loads(capsys.readouterr().out)['results']
    assert [item['symbol'] for item in results] == ['AAPL', 'MSFT']
    assert results[0]['result']['actionable_signal'] is None
    assert results[1]['error'] == 'Market data unavailable'


def test_batch_runs_existing_analysis_for_each_symbol(monkeypatch, capsys):
    import pandas as pd
    from test_core import candles
    from test_integration import answer
    from fasttrade import decisions

    def yahoo(symbol, timeframe, as_of):
        frame = candles()
        frame['timestamp'] = pd.date_range(end=as_of - pd.Timedelta(minutes=5), periods=len(frame), freq='5min')
        return frame, {}, []
    monkeypatch.setattr(cli, 'alpaca', yahoo)
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    symbols = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'JPM', 'JNJ', 'XOM']
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'analyze-batch', *symbols])
    assert cli.main() == 0
    outcomes = json.loads(capsys.readouterr().out)['results']
    assert [item['result']['market']['symbol'] for item in outcomes] == symbols
    assert all(item['result']['actionable_signal'] is None for item in outcomes)


def test_batch_respects_worker_limit_and_isolates_unexpected_failure(monkeypatch, capsys):
    lock = threading.Lock()
    active = peak = 0
    def analyze(args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        try:
            time.sleep(.03)
            if args.symbol == 'B':
                raise RuntimeError('sensitive internal details')
            return {'actionable_signal': None}
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(cli, 'run', analyze)
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'analyze-batch', 'A', 'B', 'C', 'D', '--concurrency', '2'])
    assert cli.main() == 1
    results = json.loads(capsys.readouterr().out)['results']
    assert peak == 2
    assert results[1]['error'] == 'Unexpected analysis failure'
    assert len(results) == 4 and 'result' in results[-1]


@pytest.mark.parametrize('args', [
    ['AAPL', 'aapl'], ['AAPL', '../bad'], ['AAPL', '--concurrency', '0'],
    ['AAPL', '--concurrency', '6'], ['AAPL', '--csv', 'foo.csv'],
    ['AAPL', '--position', '1'], [f'TICKER{i}' for i in range(51)],
])
def test_batch_rejects_unsafe_or_invalid_options_before_analysis(monkeypatch, capsys, args):
    monkeypatch.setattr(cli, 'run', lambda args: pytest.fail('analysis must not start'))
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'analyze-batch', *args])
    try:
        status = cli.main()
    except SystemExit as exc:
        status = exc.code
    assert status != 0


@pytest.mark.parametrize('batch', [False, True])
def test_cli_uses_last_completed_candles_as_labeled_preview(monkeypatch, capsys, batch):
    import pandas as pd
    from test_core import candles
    from test_integration import answer
    from fasttrade import decisions
    monkeypatch.setattr(cli, 'alpaca', lambda *args: (candles(), {}, []))
    monkeypatch.setattr(cli, 'predict', lambda *args: decisions.normalize(answer(), 1))
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test')
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'analyze-batch' if batch else 'analyze', 'NVDA'])
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    if batch:
        result = result['results'][0]['result']
    assert result['model_signal'] == 'BUY'
    assert result['stale_preview'] is True
    assert result['actionable_signal'] is None
    assert 'stale' in ' '.join(result['risk_rejections']).lower()
    assert pd.Timestamp(result['market']['evaluation_time']) == pd.Timestamp('2026-01-02T19:30Z')
