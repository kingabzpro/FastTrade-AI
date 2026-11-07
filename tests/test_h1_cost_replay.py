import importlib.util
from pathlib import Path


def test_published_h1_ledger_cost_scenarios(historical_docs):
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_h1_costs.py'
    spec = importlib.util.spec_from_file_location('replay_h1_costs', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scenarios = module.replay()
    assert [row['ending_balance'] for row in scenarios] == [10660.23, 10601.69, 10543.51, 10428.18]
    assert [row['spy_ending_balance'] for row in scenarios] == [10935.10, 10926.91, 10918.72, 10902.37]
