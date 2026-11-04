import importlib.util
from pathlib import Path

import pytest


def test_recorded_controlled_variants_are_compared_without_model_calls():
    folder = Path(__file__).resolve().parents[1] / 'docs' / 'benchmark-2026-h1-controlled-comparison'
    spec = importlib.util.spec_from_file_location('controlled_reality_check', folder / 'reality_check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.run(folder)
    assert result['variants'] == ['baseline', 'new']
    assert result['observations'] == 25
    assert result['p_value'] == pytest.approx(.7991004497751124)
    assert result['mean_excess_returns'] == pytest.approx([-.00159306454, -.00162049512])
    assert result['final_partial_period_excluded'] is True
