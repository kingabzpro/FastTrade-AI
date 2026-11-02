import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def batch_client(monkeypatch, tmp_path):
    from fasttrade import web
    monkeypatch.setenv('FASTTRADE_BATCH_DATABASE', str(tmp_path / 'batch.sqlite3'))
    monkeypatch.setenv('FASTTRADE_BATCH_KEYS', '{"test-key":{"estimate_per_call":"0.01","request_cap":"0.10","daily_budget":"0.20"}}')
    seen = []
    def analyze(args):
        seen.append(args.symbol)
        if args.symbol == 'FAIL':
            raise ValueError('provider error')
        return {'decision': {'usage': {'input_tokens': 10, 'output_tokens': 2}}, 'actionable_signal': None}
    monkeypatch.setattr(web, 'run_analysis', analyze)
    return TestClient(web.app, base_url='http://127.0.0.1:8765'), seen


def test_batch_estimate_auth_limits_and_replay(batch_client):
    client, seen = batch_client
    body = {'symbols': ['AAPL', 'FAIL']}
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'request-1'}
    assert client.post('/v1/analyze:batch/estimate', json=body).status_code == 401
    estimate = client.post('/v1/analyze:batch/estimate', json=body, headers=headers)
    assert estimate.status_code == 200 and estimate.json()['estimated_cost'] == '0.02'
    first = client.post('/v1/analyze:batch', json=body, headers=headers)
    assert first.status_code == 200
    result = first.json()
    assert result['status'] == 'completed' and len(result['results']) == 2
    assert result['results'][1]['error']
    assert result['usage']['input_tokens_reported'] == 10 and not result['usage']['complete']
    assert client.post('/v1/analyze:batch', json=body, headers=headers).json() == result
    assert seen.count('AAPL') == 1 and seen.count('FAIL') == 1
    assert client.post('/v1/analyze:batch', json={'symbols': ['MSFT']}, headers=headers).status_code == 409
    assert client.post('/v1/analyze:batch', json={'symbols': [f'A{i}' for i in range(51)]}, headers=headers).status_code == 422


def test_batch_budget_rejects_before_call_and_async_job_replays(batch_client):
    client, seen = batch_client
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'async-1'}
    body = {'symbols': [f'A{i}' for i in range(10)]}
    response = client.post('/v1/analyze:batch', json=body, headers=headers)
    assert response.status_code == 202
    job_id = response.json()['id']
    for _ in range(100):
        job = client.get(f'/v1/jobs/{job_id}', headers=headers).json()
        if job['status'] == 'completed':
            break
        time.sleep(.02)
    assert job['status'] == 'completed' and len(job['results']) == 10
    assert client.post('/v1/analyze:batch', json=body, headers=headers).json()['id'] == job_id
    assert len(seen) == 10
    headers['Idempotency-Key'] = 'too-large'
    assert client.post('/v1/analyze:batch', json={'symbols': [f'B{i}' for i in range(11)]}, headers=headers).status_code == 403
    assert len(seen) == 10


def test_daily_reservation_and_key_isolation(batch_client, monkeypatch):
    client, seen = batch_client
    monkeypatch.setenv('FASTTRADE_BATCH_KEYS', '{"test-key":{"estimate_per_call":"0.01","request_cap":"0.20","daily_budget":"0.02"},"other-key":{"estimate_per_call":"0.01","request_cap":"0.20","daily_budget":"0.20"}}')
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'one'}
    response = client.post('/v1/analyze:batch', json={'symbols': ['A', 'B']}, headers=headers)
    assert response.status_code == 200
    assert client.get('/v1/usage', headers=headers).json()['estimated_reserved'] == '0.02'
    assert client.get(f"/v1/jobs/{response.json()['id']}", headers={'X-API-Key': 'other-key'}).status_code == 404
    headers['Idempotency-Key'] = 'two'
    assert client.post('/v1/analyze:batch', json={'symbols': ['C']}, headers=headers).status_code == 403
    assert len(seen) == 2


def test_timeout_does_not_retry_and_retains_budget(batch_client, monkeypatch):
    from fasttrade import batch_api, web
    import threading
    client, _ = batch_client
    entered, release = threading.Event(), threading.Event()
    def slow(args):
        entered.set()
        release.wait(2)
        return {'decision': {'usage': {'input_tokens': 1, 'output_tokens': 1}}}
    monkeypatch.setattr(web, 'run_analysis', slow)
    monkeypatch.setattr(batch_api, '_symbol_timeout_seconds', .05)
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'slow'}
    try:
        result = client.post('/v1/analyze:batch', json={'symbols': ['A']}, headers=headers)
        assert entered.is_set() and result.status_code == 200
        assert result.json()['results'][0]['error'] == 'Analysis timed out'
        assert result.json()['usage']['complete'] is False
        assert client.post('/v1/analyze:batch', json={'symbols': ['A']}, headers=headers).json() == result.json()
        assert client.get('/v1/usage', headers=headers).json()['estimated_reserved'] == '0.01'
    finally:
        release.set()


def test_concurrent_retry_never_starts_another_model_call(batch_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from fasttrade import web
    client, _ = batch_client
    entered, release = threading.Event(), threading.Event()
    calls = []
    def slow(args):
        calls.append(args.symbol)
        entered.set()
        release.wait(2)
        return {'decision': {'usage': {'input_tokens': 1, 'output_tokens': 0}}}
    monkeypatch.setattr(web, 'run_analysis', slow)
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'concurrent'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post, '/v1/analyze:batch', json={'symbols': ['A']}, headers=headers)
        try:
            assert entered.wait(2)
            second = client.post('/v1/analyze:batch', json={'symbols': ['A']}, headers=headers)
            assert second.status_code == 202
            assert second.json()['status'] == 'running'
            assert calls == ['A']
        finally:
            release.set()
        assert first.result().status_code == 200


def test_restart_marks_incomplete_job_interrupted_without_retry(batch_client, monkeypatch):
    import threading
    from fasttrade import batch_api, web
    client, _ = batch_client
    entered, release = threading.Event(), threading.Event()
    def slow(args):
        entered.set()
        release.wait(2)
        return {'decision': {'usage': None}}
    monkeypatch.setattr(web, 'run_analysis', slow)
    body = {'symbols': [f'A{i}' for i in range(10)]}
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'restart'}
    try:
        response = client.post('/v1/analyze:batch', json=body, headers=headers)
        assert entered.wait(2)
        monkeypatch.setattr(batch_api, '_instance', 'new-instance')
        assert client.get('/v1/jobs/' + response.json()['id'], headers=headers).json()['status'] == 'interrupted'
        replay = client.post('/v1/analyze:batch', json=body, headers=headers)
        assert replay.json()['id'] == response.json()['id'] and replay.json()['status'] == 'interrupted'
    finally:
        release.set()
    for _ in range(100):
        if client.get('/v1/jobs/' + response.json()['id'], headers=headers).json()['status'] == 'completed':
            break
        time.sleep(.02)


def test_batch_parallelism_is_bounded_and_source_is_forwarded(batch_client, monkeypatch):
    import threading
    from fasttrade import web
    client, _ = batch_client
    lock = threading.Lock()
    active = peak = 0
    sources = []
    def analyze(args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            sources.append(args.source)
        try:
            time.sleep(.03)
            return {'decision': {'usage': None}}
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(web, 'run_analysis', analyze)
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'bounded'}
    response = client.post('/v1/analyze:batch', json={'symbols': [f'A{i}' for i in range(8)], 'source': 'yahoo'}, headers=headers)
    assert response.status_code == 200 and response.json()['progress'] == 8
    assert 1 < peak <= 4 and sources == ['yahoo'] * 8


def test_invalid_batch_configuration_fails_closed(batch_client, monkeypatch):
    client, seen = batch_client
    monkeypatch.setenv('FASTTRADE_BATCH_KEYS', '{"test-key":{"estimate_per_call":"NaN","request_cap":"1","daily_budget":"1"}}')
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'bad-config'}
    assert client.post('/v1/analyze:batch', json={'symbols': ['A']}, headers=headers).status_code == 503
    assert seen == []


def test_job_reports_partial_results_while_other_calls_run(batch_client, monkeypatch):
    import threading
    from fasttrade import web
    client, _ = batch_client
    release = threading.Event()
    def analyze(args):
        if args.symbol != 'A0':
            release.wait(2)
        return {'decision': {'usage': {'input_tokens': 1, 'output_tokens': 0}}}
    monkeypatch.setattr(web, 'run_analysis', analyze)
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'partial'}
    response = client.post('/v1/analyze:batch', json={'symbols': [f'A{i}' for i in range(10)]}, headers=headers)
    job_id = response.json()['id']
    try:
        for _ in range(100):
            job = client.get('/v1/jobs/' + job_id, headers=headers).json()
            if job['progress']:
                break
            time.sleep(.02)
        assert job['status'] == 'running' and 0 < job['progress'] < 10
        assert job['results'][0]['symbol'] == 'A0'
        assert job['usage']['complete'] is False
    finally:
        release.set()
    for _ in range(100):
        job = client.get('/v1/jobs/' + job_id, headers=headers).json()
        if job['status'] == 'completed':
            break
        time.sleep(.02)
    assert job['status'] == 'completed' and job['progress'] == 10


def test_concurrent_batches_cannot_exceed_daily_reservation(batch_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    client, seen = batch_client
    monkeypatch.setenv('FASTTRADE_BATCH_KEYS', '{"test-key":{"estimate_per_call":"0.01","request_cap":"0.10","daily_budget":"0.01"}}')
    barrier = threading.Barrier(2)
    def send(symbol):
        barrier.wait(timeout=2)
        return client.post('/v1/analyze:batch', json={'symbols': [symbol]}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': symbol})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, ['A', 'B']))
    assert sorted(response.status_code for response in responses) == [200, 403]
    assert len(seen) == 1
