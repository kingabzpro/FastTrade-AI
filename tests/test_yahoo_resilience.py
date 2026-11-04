import pandas as pd
import pytest
import yfinance
from curl_cffi.requests.exceptions import Timeout
from yfinance.exceptions import YFDataException, YFRateLimitError

from fasttrade import market, resilience
from test_core import candles


@pytest.fixture(autouse=True)
def isolated_circuit(monkeypatch):
    resilience._circuits.clear()
    monkeypatch.setattr(resilience, 'sleep', lambda _: None)
    monkeypatch.setattr(resilience.random, 'uniform', lambda *_: 0)
    yield
    resilience._circuits.clear()


def test_yahoo_retries_read_timeouts_with_a_bounded_budget(monkeypatch):
    attempts = []
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise Timeout('timeout')
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        info = {}
        news = []
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    frame, _, _ = market.yahoo('TEST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(frame) == 60 and len(attempts) == 3


def test_yahoo_rate_limit_sets_shared_cooldown_without_immediate_retry(monkeypatch):
    now, attempts, constructed = [0.0], [], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise YFRateLimitError()
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        info = {}
        news = []
    def ticker(symbol):
        constructed.append(symbol)
        return Ticker()
    monkeypatch.setattr(yfinance, 'Ticker', ticker)
    with pytest.raises(ValueError, match='rate limit'):
        market.yahoo('FIRST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    with pytest.raises(ValueError, match='rate limit'):
        market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 1
    assert constructed == ['FIRST']
    now[0] = 31
    market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 2


def test_yahoo_optional_news_rate_limit_blocks_later_history(monkeypatch):
    now, attempts = [0.0], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        info = {}
        @property
        def news(self):
            raise YFRateLimitError()
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    assert market.yahoo('FIRST', '5m', pd.Timestamp('2026-01-02T19:30Z'))[2] == []
    with pytest.raises(ValueError, match='rate limit'):
        market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 1


def test_yahoo_circuit_opens_after_repeated_transport_failures_and_recovers(monkeypatch):
    now, attempts = [0.0], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            if now[0] < 30:
                raise Timeout('private details')
            return candles().set_index('timestamp').rename_axis('Datetime').rename(columns=str.capitalize)
        info = {}
        news = []
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    for _ in range(3):
        with pytest.raises(ValueError, match='timed out'):
            market.yahoo('FIRST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    with pytest.raises(ValueError, match='provider failed'):
        market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 9
    now[0] = 31
    market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 10


def test_yahoo_does_not_retry_nontransient_errors(monkeypatch):
    attempts = []
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            raise RuntimeError('private details')
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    with pytest.raises(ValueError, match='provider failed') as error:
        market.yahoo('TEST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert 'private details' not in str(error.value)
    assert len(attempts) == 1


def test_yahoo_maintenance_response_opens_the_circuit(monkeypatch):
    now, attempts = [0.0], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    class Ticker:
        def history(self, **kwargs):
            attempts.append(1)
            raise YFDataException('*** YAHOO! FINANCE IS CURRENTLY DOWN! ***')
    monkeypatch.setattr(yfinance, 'Ticker', lambda symbol: Ticker())
    for _ in range(3):
        with pytest.raises(ValueError, match='provider failed'):
            market.yahoo('FIRST', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    with pytest.raises(ValueError, match='provider failed'):
        market.yahoo('SECOND', '5m', pd.Timestamp('2026-01-02T19:30Z'))
    assert len(attempts) == 9
