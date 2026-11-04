from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from fasttrade import resilience


@pytest.fixture(autouse=True)
def isolated_breakers(monkeypatch):
    resilience._circuits.clear()
    monkeypatch.setattr(resilience, 'sleep', lambda seconds: None)
    monkeypatch.setattr(resilience.random, 'uniform', lambda low, high: 0)


def response(status, headers=None):
    return httpx.Response(status, headers=headers, request=httpx.Request('GET', 'https://example.test/'))


def test_rate_limit_waits_for_retry_after_and_retries_at_most_three_times(monkeypatch):
    delays = []
    monkeypatch.setattr(resilience, 'sleep', delays.append)
    outcomes = iter([response(429, {'Retry-After': '2'}), response(429, {'Retry-After': '3'}), response(200)])
    calls = []
    def get(url, **kwargs):
        calls.append(1)
        return next(outcomes)
    result = resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert result.status_code == 200 and len(calls) == 3
    assert delays == [2, 3]


def test_rate_limit_delay_beyond_budget_is_not_retried_early(monkeypatch):
    calls, delays = [], []
    monkeypatch.setattr(resilience, 'sleep', delays.append)
    def get(url, **kwargs):
        calls.append(1)
        return response(429, {'Retry-After': '60'})
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 429
    assert len(calls) == 1 and delays == []


def test_retry_after_blocks_subsequent_requests_for_same_credentials(monkeypatch):
    now, calls = [0.0], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    def get(url, **kwargs):
        calls.append(1)
        return response(429, {'Retry-After': '60'}) if now[0] < 60 else response(200)
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 429
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert len(calls) == 1
    now[0] = 61
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 200


def test_active_retry_after_survives_circuit_cache_churn():
    calls = []
    def limited(url, **kwargs):
        calls.append(1)
        return response(429, {'Retry-After': '60'})
    resilience.request(limited, 'https://example.test/', service='bars', credentials='limited', retry_transport=True)
    for index in range(128):
        resilience.request(lambda *a, **k: response(200), 'https://example.test/', service='bars', credentials=str(index), retry_transport=True)
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(limited, 'https://example.test/', service='bars', credentials='limited', retry_transport=True)
    assert len(calls) == 1


def test_circuit_capacity_fails_closed_when_all_entries_are_active():
    calls = []
    def limited(url, **kwargs):
        calls.append(1)
        return response(429, {'Retry-After': '60'})
    for index in range(128):
        resilience.request(limited, 'https://example.test/', service='bars', credentials=str(index), retry_transport=True)
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(limited, 'https://example.test/', service='bars', credentials='new', retry_transport=True)
    assert len(calls) == 128


def test_old_idle_failures_can_be_evicted_after_retention(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    for index in range(128):
        resilience.request(lambda *a, **k: response(503), 'https://example.test/', service='bars', credentials=str(index), retry_transport=True)
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(lambda *a, **k: response(200), 'https://example.test/', service='bars', credentials='new', retry_transport=True)
    now[0] = 301
    assert resilience.request(lambda *a, **k: response(200), 'https://example.test/', service='bars', credentials='new', retry_transport=True).status_code == 200


def test_unrepresentably_large_retry_after_never_retries_early():
    calls = []
    def get(url, **kwargs):
        calls.append(1)
        return response(429, {'Retry-After': '9' * 400})
    resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert len(calls) == 1


def test_retry_after_http_date_is_supported(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(resilience, 'utcnow', lambda: now)
    delays = []
    monkeypatch.setattr(resilience, 'sleep', delays.append)
    outcomes = iter([response(429, {'Retry-After': format_datetime(datetime(2026, 1, 1, 0, 0, 4, tzinfo=timezone.utc))}), response(200)])
    resilience.request(lambda *a, **k: next(outcomes), 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert delays == [4]


def test_circuit_opens_after_failed_calls_and_recovers_with_probe(monkeypatch):
    now, calls = [0.0], []
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    def get(url, **kwargs):
        calls.append(1)
        return response(503 if now[0] < 30 else 200)
    for _ in range(3):
        assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 503
    assert len(calls) == 9
    with pytest.raises(resilience.CircuitOpen):
        resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert len(calls) == 9
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='b', retry_transport=True).status_code == 503
    now[0] = 31
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 200
    assert resilience.request(get, 'https://example.test/', service='bars', credentials='a', retry_transport=True).status_code == 200


def test_circuit_allows_only_one_recovery_probe_and_ignores_older_success(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(resilience, 'monotonic', lambda: now[0])
    circuit = resilience._Circuit()
    older = circuit.enter()
    for _ in range(3):
        token = circuit.enter()
        circuit.finish(token, False)
    circuit.finish(older, True)
    with pytest.raises(resilience.CircuitOpen):
        circuit.enter()
    now[0] = 31
    probe = circuit.enter()
    with pytest.raises(resilience.CircuitOpen):
        circuit.enter()
    circuit.finish(probe, True)
    circuit.enter()


def test_market_transport_errors_are_retried_but_model_server_errors_are_not():
    calls = []
    def failing_get(url, **kwargs):
        calls.append(1)
        raise httpx.ConnectError('unavailable')
    with pytest.raises(httpx.ConnectError):
        resilience.request(failing_get, 'https://example.test/', service='bars', credentials='a', retry_transport=True)
    assert len(calls) == 3
    calls.clear()
    def failed_post(url, **kwargs):
        calls.append(1)
        return response(503)
    assert resilience.request(failed_post, 'https://example.test/', service='model', credentials='a', retry_transport=False).status_code == 503
    assert len(calls) == 1


def test_paid_request_does_not_retry_ambiguous_failure(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('unknown outcome')
    with pytest.raises(httpx.ReadTimeout):
        resilience.request(post, 'https://example.test/', service='model', credentials='a', retry_transport=False)
    assert len(calls) == 1
