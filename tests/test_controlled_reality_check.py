import importlib.util
from pathlib import Path

import pytest


def test_recorded_controlled_variants_are_compared_without_model_calls(historical_docs):
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
    analysis = result['overfitting']
    assert analysis['cscv']['observations_used'] == 24
    assert len(analysis['cscv']['folds']) == 20
    assert analysis['cscv']['pbo'] == 1.0
    assert len(analysis['purged_folds']['folds']) == 15
    assert analysis['purged_folds']['embargo_days'] == 7
    actual = [fold['test_mean_excess_return'] * 100 for fold in analysis['purged_folds']['folds']]
    assert actual == pytest.approx([-.0313, .3158, -.2195, -.2610, .3179, -.0731, -.6753, -.6470,
                                    .0043, -.2849, -.2372, .3681, -.8503, -.1990, -.2494], abs=.00005)
