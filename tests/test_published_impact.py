import importlib.util
from pathlib import Path


def test_published_impact_replay_matches_flat_reference_and_reports_scenarios(historical_docs):
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_impact_costs.py'
    spec = importlib.util.spec_from_file_location('replay_impact_costs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replay = module.replay()
    assert replay['flat_1x_ending_balance'] == 10230.73
    scenarios = replay['impact_sensitivity']['scenarios']
    assert all(row['ending_balance'] is not None for row in scenarios)
    assert scenarios[0]['ending_balance'] > scenarios[1]['ending_balance'] > scenarios[2]['ending_balance']
    assert all(row['trades'] for row in scenarios)


def test_recorded_flagship_uses_primary_impact_without_provider_calls(historical_docs):
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_impact_costs.py'
    spec = importlib.util.spec_from_file_location('replay_impact_costs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.replay(slippage_model='impact')
    assert result['primary_return_pct'] == 2.42
    assert result['primary_spy_return_pct'] == -.06
    assert result['primary_ending_balance'] == result['impact_sensitivity']['scenarios'][1]['ending_balance']


def test_controlled_h1_impact_replay_matches_recorded_no_cost_result(historical_docs):
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_impact_costs.py'
    spec = importlib.util.spec_from_file_location('replay_impact_costs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    replay = module.replay_h1()
    assert replay['no_cost_matches_report'] is True
    scenarios = replay['impact_sensitivity']['scenarios']
    assert all(row['ending_balance'] is not None for row in scenarios)
    assert scenarios[0]['ending_balance'] > scenarios[1]['ending_balance'] > scenarios[2]['ending_balance']
    assert any(t['missing_sessions'] for t in scenarios[1]['trades'])


def test_h1_calendar_preserves_early_closes_and_detects_missing_session_edges(historical_docs):
    import pandas as pd
    from fasttrade.impact import liquidity

    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_impact_costs.py'
    spec = importlib.util.spec_from_file_location('replay_impact_costs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sessions = module.h1_sessions('2025-12-24', '2026-02-02')
    early = sessions[sessions.date == pd.Timestamp('2025-12-24').date()].iloc[0]
    assert early['close'] == pd.Timestamp('2025-12-24T18:00:00Z')
    january = sessions[sessions.date == pd.Timestamp('2026-01-30').date()].iloc[0]
    assert january['close'] == pd.Timestamp('2026-01-30T21:00:00Z')
    spy = pd.read_csv(Path(__file__).resolve().parents[1] / 'docs' / 'benchmark-2026-h1-controlled-comparison' / 'SPY.csv.gz')
    spy['timestamp'] = pd.to_datetime(spy.timestamp, utc=True)
    frame = spy.set_index('timestamp')
    assert liquidity(sessions, frame, pd.Timestamp('2026-01-30T21:30:00Z')) is None
