import math

import pandas as pd
import pytest


def test_square_root_impact_uses_volume_and_clamps_both_sides():
    from fasttrade.impact import impact_bps

    assert impact_bps(100, .02, 10000) == pytest.approx(20)
    assert impact_bps(1, .001, 1000000) == pytest.approx(1)
    assert impact_bps(10000, .1, 10000) == pytest.approx(50)
    with pytest.raises(ValueError):
        impact_bps(100, .02, 0)
    with pytest.raises(ValueError):
        impact_bps(100, math.nan, 10000)


def test_liquidity_uses_only_completed_sessions_and_rejects_insufficient_history():
    from fasttrade.impact import liquidity

    dates = pd.bdate_range('2026-01-05', periods=8, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=16)})
    sessions['date'] = sessions.open.dt.date
    stamps = [opening + pd.Timedelta(hours=h) for opening in sessions.open for h in range(2)]
    frame = pd.DataFrame({'close': [100.0] * len(stamps), 'volume': [1000.0] * len(stamps)}, index=stamps)
    cutoff = sessions.close.iloc[5]
    result = liquidity(sessions, frame, cutoff)
    assert result['sessions'] == 6
    assert result['daily_volume'] == 2000
    assert result['daily_volatility'] == 0
    frame.loc[frame.index >= cutoff, ['close', 'volume']] = [9999, 9999]
    assert liquidity(sessions, frame, cutoff) == result
    assert liquidity(sessions, frame, sessions.close.iloc[4]) is None


def test_cash_dividend_is_not_counted_as_a_volatility_shock():
    from fasttrade.impact import liquidity

    dates = pd.bdate_range('2026-01-05', periods=6, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=15)})
    sessions['date'] = sessions.open.dt.date
    frame = pd.DataFrame({'close': [100, 100, 100, 100, 99, 99], 'volume': [1000] * 6}, index=sessions.open)
    ex_date = sessions.date.iloc[4]
    result = liquidity(sessions, frame, sessions.close.iloc[-1], dividends={ex_date: 1.0})
    assert result['daily_volatility'] == 0


def test_incomplete_session_is_excluded_without_bridging_its_return():
    from fasttrade.impact import liquidity

    dates = pd.bdate_range('2026-01-05', periods=8, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=16)})
    sessions['date'] = sessions.open.dt.date
    stamps = [opening + pd.Timedelta(hours=h) for opening in sessions.open for h in range(2)]
    frame = pd.DataFrame({'close': [100.0] * len(stamps), 'volume': [1000.0] * len(stamps)}, index=stamps)
    frame = frame.drop(sessions.open.iloc[3])
    result = liquidity(sessions, frame, sessions.close.iloc[-1])
    assert result['sessions'] == 7
    assert result['missing_sessions'] == 1
    assert result['return_observations'] == 5
    assert result['daily_volatility'] == 0


def test_replay_rejects_recorded_executions_outside_a_session():
    from fasttrade.backtest import replay_costs

    dates = pd.bdate_range('2026-01-05', periods=8, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=15)})
    sessions['date'] = sessions.open.dt.date
    frame = pd.DataFrame({'open': 100., 'close': 100., 'volume': 1000.}, index=sessions.open)
    decisions = [
        {'symbol': 'AAPL', 'action': 'BUY', 'decision_time': sessions.close.iloc[5].isoformat(), 'execution_time': sessions.open.iloc[6].isoformat()},
        {'symbol': 'AAPL', 'action': 'SELL', 'decision_time': sessions.close.iloc[6].isoformat(), 'execution_time': (sessions.close.iloc[7] + pd.Timedelta(hours=1)).isoformat()},
    ]
    with pytest.raises(ValueError, match='outside'):
        replay_costs(sessions, {'AAPL': frame, 'SPY': frame}, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000, 0, 0, 1)


def test_replay_rejects_decision_after_execution():
    from fasttrade.backtest import replay_costs

    dates = pd.bdate_range('2026-01-05', periods=8, tz='UTC')
    sessions = pd.DataFrame({'open': dates + pd.Timedelta(hours=14), 'close': dates + pd.Timedelta(hours=15)})
    sessions['date'] = sessions.open.dt.date
    frame = pd.DataFrame({'open': 100., 'close': 100., 'volume': 1000.}, index=sessions.open)
    decisions = [{'symbol': 'AAPL', 'action': 'BUY', 'decision_time': sessions.close.iloc[7].isoformat(), 'execution_time': sessions.open.iloc[6].isoformat()}]
    with pytest.raises(ValueError, match='decision'):
        replay_costs(sessions, {'AAPL': frame, 'SPY': frame}, ['AAPL'], decisions, {}, sessions.date.iloc[0], sessions.date.iloc[-1], 10000, 0, 0, 1, impact=True)
