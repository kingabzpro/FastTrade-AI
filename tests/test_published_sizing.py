import importlib.util
from pathlib import Path


def test_recorded_alpaca_sizing_comparison_replays_without_model_calls():
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_sizing.py'
    spec = importlib.util.spec_from_file_location('replay_sizing', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.replay()
    assert result['original_ending_balance'] == 10204.14
    assert result['sizing_ablation']['fixed']['ending_balance'] is not None
    assert result['sizing_ablation']['volatility_targeted']['ending_balance'] is not None
    assert result['sizing_ablation']['fixed']['evaluations']


def test_recorded_alpaca_comparison_can_enable_drawdown_overlay():
    path = Path(__file__).resolve().parents[1] / 'docs' / 'replay_sizing.py'
    spec = importlib.util.spec_from_file_location('replay_sizing', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.replay(max_drawdown=.02)
    assert result['original_ending_balance'] == 10204.14
    assert result['sizing_ablation']['max_drawdown'] == .02
    assert result['sizing_ablation']['fixed']['drawdown_checks']
