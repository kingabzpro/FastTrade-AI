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
