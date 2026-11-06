import math
import hashlib
import json

import pytest
import pandas as pd


@pytest.mark.parametrize(('target', 'volatility', 'weight'), [(.1, .2, .5), (.1, .05, 1), (.1, 0, 1)])
def test_volatility_weight_caps_at_equal_weight(target, volatility, weight):
    from fasttrade.sizing import volatility_weight
    assert volatility_weight(target, volatility) == pytest.approx(weight)


@pytest.mark.parametrize(('target', 'volatility'), [(0, .2), (.1, -1), (.1, math.nan), (math.inf, .2)])
def test_volatility_weight_rejects_invalid_inputs(target, volatility):
    from fasttrade.sizing import volatility_weight
    with pytest.raises(ValueError):
        volatility_weight(target, volatility)


@pytest.mark.parametrize(('equity', 'expected'), [(10000, 1.), (9000, .5), (8000, 0.), (7000, 0.), (11000, 1.)])
def test_drawdown_factor_declines_linearly_to_zero(equity, expected):
    from fasttrade.sizing import drawdown_factor
    assert drawdown_factor(10000, equity, .2) == pytest.approx(expected)


@pytest.mark.parametrize(('peak', 'equity', 'limit'), [(0, 1, .2), (1, -1, .2), (1, 1, 0), (1, 1, 1.1), (1, math.nan, .2)])
def test_drawdown_factor_rejects_invalid_inputs(peak, equity, limit):
    from fasttrade.sizing import drawdown_factor
    with pytest.raises(ValueError):
        drawdown_factor(peak, equity, limit)


def replay_fixture():
    dates = pd.bdate_range('2026-01-05', periods=8, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=15)})
    sessions['date'] = sessions.open.dt.date
    frame = pd.DataFrame({'open': 100., 'close': [100, 120, 100, 120, 100, 120, 100, 100], 'volume': 10000.}, index=sessions.open)
    decision = {'symbol': 'AAPL', 'action': 'BUY', 'confidence': .8,
                'decision_time': sessions.close.iloc[5].isoformat(), 'execution_time': sessions.open.iloc[6].isoformat()}
    limits = {'max_position': 1000., 'max_exposure': 1000., 'max_daily_loss': 10000., 'max_trades': 1, 'min_confidence': .5}
    return sessions, {'AAPL': frame}, [decision], limits


def test_replay_clips_to_limits_and_runs_existing_risk_checks():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .0005, .001, limits)
    trade = result['evaluations'][0]
    assert trade['quantity'] == pytest.approx(10)
    assert trade['risk_rejections'] == []
    state = {key: value for key, value in trade.items() if key not in ('state_sha256', 'risk_rejections', 'executed')}
    expected_hash = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert trade['state_sha256'] == expected_hash
    assert result['ending_balance'] < 10000
    limits['min_confidence'] = .9
    rejected = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .0005, .001, limits)
    assert rejected['ending_balance'] == 10000
    assert 'Confidence below threshold' in rejected['evaluations'][0]['risk_rejections']


def test_targeted_replay_uses_only_prior_prices():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    args = (sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits)
    first = replay(*args, target=.1)
    assert 0 < first['evaluations'][0]['weight'] < 1
    frames['AAPL'].loc[sessions.open.iloc[-1], 'close'] = 9999
    second = replay(*args, target=.1)
    assert first['evaluations'][0] == second['evaluations'][0]


def test_targeted_replay_does_not_assume_missing_volatility():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['volume'] = 0
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, target=.1)
    assert result['ending_balance'] is None
    assert 'liquidity' in result['reason'].lower()


def test_second_trade_sees_first_trade_cost_as_daily_loss():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['MSFT'] = frames['AAPL'].copy()
    decisions.append({**decisions[0], 'symbol': 'MSFT'})
    limits.update(max_position=10000., max_exposure=10000., max_daily_loss=1., max_trades=2)
    result = replay(sessions, frames, ['AAPL', 'MSFT'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .01, 0., limits)
    assert result['evaluations'][0]['risk_rejections'] == []
    assert 'Daily loss limit reached' in result['evaluations'][1]['risk_rejections']


def test_repeated_buy_does_not_execute_a_floating_point_dust_trade():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    limits.update(max_position=10000., max_exposure=10000., max_trades=2)
    decisions.append({**decisions[0], 'decision_time': sessions.close.iloc[6].isoformat(), 'execution_time': sessions.open.iloc[7].isoformat()})
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits)
    assert sum(row['executed'] for row in result['evaluations']) == 1


def test_repeated_partial_target_does_not_execute_a_dust_trade(monkeypatch):
    from fasttrade import sizing

    sessions, frames, decisions, limits = replay_fixture()
    price, weight = 290.01586257917955, 0.9614779889500835
    frames['AAPL']['open'] = price
    frames['AAPL']['close'] = price
    limits.update(max_position=10000., max_exposure=10000., max_trades=2)
    decisions.append({**decisions[0], 'decision_time': sessions.close.iloc[6].isoformat(), 'execution_time': sessions.open.iloc[7].isoformat()})
    monkeypatch.setattr(sizing, 'liquidity', lambda *args, **kwargs: {'daily_volatility': .1 / weight / math.sqrt(252)})
    result = sizing.replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, target=.1)
    assert sum(row['executed'] for row in result['evaluations']) == 1


def test_sell_and_cash_dividend_preserve_portfolio_value():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    decisions.append({**decisions[0], 'action': 'SELL', 'decision_time': sessions.close.iloc[6].isoformat(), 'execution_time': sessions.open.iloc[7].isoformat()})
    dividends = {('AAPL', sessions.date.iloc[7]): (1., sessions.date.iloc[7] + pd.Timedelta(days=30))}
    result = replay(sessions, frames, ['AAPL'], decisions, dividends, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits)
    assert [row['action'] for row in result['evaluations'] if row['executed']] == ['BUY', 'SELL']
    assert result['ending_balance'] == pytest.approx(10010.)


def test_zero_risk_limits_are_valid_and_block_trades():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    limits.update(max_position=0., max_exposure=0., max_daily_loss=0., max_trades=0)
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits)
    assert result['ending_balance'] == 10000.
    assert not result['evaluations'][0]['executed']


def test_zero_capacity_does_not_require_unused_volatility():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['volume'] = 0
    limits['max_position'] = 0.
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, target=.1)
    assert result['ending_balance'] == 10000.
    assert not result['evaluations'][0]['executed']
    assert result['evaluations'][0]['weight'] is None


@pytest.mark.parametrize(('limit', 'value', 'expected'), [
    ('max_trades', 0, 'Trade count limit reached'),
    ('max_daily_loss', 0., 'Daily loss limit reached'),
    ('min_confidence', .9, 'Confidence below threshold'),
])
def test_unconditional_risk_veto_does_not_require_unused_volatility(limit, value, expected):
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['volume'] = 0
    limits[limit] = value
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, target=.1)
    assert result['ending_balance'] == 10000.
    assert expected in result['evaluations'][0]['risk_rejections']
    assert not result['evaluations'][0]['executed']


def test_drawdown_overlay_reduces_existing_exposure_even_without_a_model_sell():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['AAPL'].loc[sessions.open.iloc[-1], ['open', 'close']] = 50.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=0.)
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, max_drawdown=.5)
    reduction = [row for row in result['evaluations'] if row.get('source') == 'drawdown']
    assert len(reduction) == 1
    assert reduction[0]['quantity'] == pytest.approx(100.)
    assert reduction[0]['executed'] is True
    assert result['ending_balance'] == 5000.


def test_drawdown_reduction_obeys_risk_engine_and_reports_remaining_exposure():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['AAPL'].loc[sessions.open.iloc[-1], ['open', 'close']] = 50.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=.5)
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, max_drawdown=.5)
    reduction = [row for row in result['evaluations'] if row.get('source') == 'drawdown'][0]
    assert not reduction['executed']
    assert 'Confidence below threshold' in reduction['risk_rejections']
    assert result['drawdown_checks'][-1]['exposure_after'] == pytest.approx(5000.)
    assert result['drawdown_checks'][-1]['cap_after'] == 0


def test_drawdown_reduction_scales_multiple_positions_proportionally():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['MSFT'] = frames['AAPL'].copy()
    decisions.append({**decisions[0], 'symbol': 'MSFT'})
    for frame in frames.values():
        frame.loc[sessions.open.iloc[-1], ['open', 'close']] = 90.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=0.)
    result = replay(sessions, frames, ['AAPL', 'MSFT'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, max_drawdown=.2)
    reductions = [row for row in result['evaluations'] if row.get('source') == 'drawdown']
    assert len(reductions) == 2
    assert [row['quantity'] for row in reductions] == pytest.approx([25., 25.])
    assert result['drawdown_checks'][-1]['exposure_after'] == pytest.approx(4500.)
    assert result['drawdown_checks'][-1]['cap_after'] == pytest.approx(4500.)


def test_drawdown_buy_respects_cap_after_transaction_costs():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=0.)
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .01, 0., limits, max_drawdown=.2)
    check = result['drawdown_checks'][-2]
    assert check['exposure_after'] <= check['cap_after'] + 1e-7


def test_drawdown_reduction_accounts_for_costs_in_resulting_cap():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['AAPL'].loc[sessions.open.iloc[-1], ['open', 'close']] = 90.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=0.)
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .01, 0., limits, max_drawdown=.2)
    check = result['drawdown_checks'][-1]
    assert check['exposure_after'] <= check['cap_after'] + 1e-7


def test_multi_position_drawdown_reduction_accounts_for_costs():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['MSFT'] = frames['AAPL'].copy()
    decisions.append({**decisions[0], 'symbol': 'MSFT'})
    for frame in frames.values():
        frame.loc[sessions.open.iloc[-1], ['open', 'close']] = 90.
    limits.update(max_position=10000., max_exposure=10000., max_trades=2, min_confidence=0.)
    result = replay(sessions, frames, ['AAPL', 'MSFT'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., .01, .005, limits, max_drawdown=.2)
    assert result['drawdown_checks'][-1]['exposure_after'] <= result['drawdown_checks'][-1]['cap_after'] + 1e-7


def test_drawdown_throttle_limits_new_buy_to_remaining_exposure_capacity():
    from fasttrade.sizing import replay

    sessions, frames, decisions, limits = replay_fixture()
    frames['AAPL']['close'] = 100.
    frames['AAPL'].loc[sessions.open.iloc[-1], ['open', 'close']] = 50.
    limits.update(max_position=5000., max_exposure=10000., max_trades=2, min_confidence=0.)
    decisions.append({**decisions[0], 'decision_time': sessions.close.iloc[6].isoformat(), 'execution_time': sessions.open.iloc[7].isoformat()})
    result = replay(sessions, frames, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000., 0., 0., limits, max_drawdown=.5)
    check = result['drawdown_checks'][-1]
    assert check['cap_after'] == pytest.approx(3750.)
    assert check['exposure_after'] == pytest.approx(3750.)
