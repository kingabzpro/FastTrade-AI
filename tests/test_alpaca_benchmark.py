import importlib.util
from pathlib import Path

import pandas as pd


def test_iex_aggregation_uses_only_trades_inside_session(historical_docs):
    path = Path(__file__).resolve().parents[1] / 'docs/backtest-2026-09-28-alpaca/prepare.py'
    spec = importlib.util.spec_from_file_location('alpaca_benchmark_prepare', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.execution_dates = {pd.Timestamp('2026-10-12').date()}
    sessions = pd.DataFrame({'open': [pd.Timestamp('2026-10-12T13:30Z')], 'close': [pd.Timestamp('2026-10-12T14:30Z')]})
    minutes = pd.DataFrame([
        {'t':'2026-10-12T13:29:00Z','o':99,'h':99,'l':99,'c':99,'v':999},
        {'t':'2026-10-12T13:32:00Z','o':10,'h':12,'l':9,'c':11,'v':5},
        {'t':'2026-10-12T14:29:00Z','o':11,'h':13,'l':10,'c':12,'v':7},
        {'t':'2026-10-12T14:30:00Z','o':99,'h':99,'l':99,'c':99,'v':999},
    ])
    bars, delays = module.hourly(minutes, sessions)
    assert bars.iloc[0].to_dict() == {'timestamp': '2026-10-12T13:30:00+00:00', 'open': 10, 'high': 13, 'low': 9, 'close': 12, 'volume': 12}
    assert delays[0]['delay_minutes'] == 2
