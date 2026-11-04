import numpy as np
import pytest

from fasttrade.significance import reality_check


def test_reality_check_uses_the_joint_variant_family():
    returns = np.array([[.04, -.02], [-.03, .01], [.02, -.01], [-.01, .03], [.05, -.04], [-.02, .02]])
    result = reality_check(returns, draws=500, seed=9)
    assert result['variants_tested'] == 2
    assert result['observations'] == 6
    assert result['p_value'] == reality_check(returns[:, ::-1], draws=500, seed=9)['p_value']
    assert result['p_value'] == reality_check(np.column_stack((returns, returns[:, 0])), draws=500, seed=9)['p_value']


def test_reality_check_recenters_the_null_and_rejects_invalid_inputs():
    result = reality_check(np.full((12, 2), .01), draws=99, seed=1)
    assert result['p_value'] == pytest.approx(.01)
    assert reality_check(np.zeros((12, 2)), draws=99, seed=1)['p_value'] == 1
    for values in ([], [[1]], [[1, float('nan')], [2, 3]]):
        with pytest.raises(ValueError):
            reality_check(values)


def test_reality_check_preserves_opposite_variant_dependence():
    series = np.array([.04, -.03, .02, -.01, .05, -.02, .01, -.04])
    family = reality_check(np.column_stack((series, -series)), draws=1000, seed=2)
    single = reality_check(series[:, None], draws=1000, seed=2)
    assert family['p_value'] >= single['p_value']
    assert family['p_value'] > single['p_value'] + .05
