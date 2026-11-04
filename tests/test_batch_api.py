import time
import hashlib
import json

import pytest
from fastapi.testclient import TestClient


def hashed_keys(config):
    return json.dumps({hashlib.sha256(key.encode()).hexdigest(): value for key, value in config.items()})


@pytest.fixture
def batch_client(monkeypatch, tmp_path):
    from fasttrade import web
    monkeypatch.setattr(web, 'check_api_rate_limit', lambda identity, rate: True)
    monkeypatch.setenv('FASTTRADE_BATCH_DATABASE', str(tmp_path / 'batch.sqlite3'))
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{"test-key":{"estimate_per_call":"0.01","request_cap":"0.10","daily_budget":"0.20"}}')))
    seen = []
    def analyze(args):
        seen.append(args.symbol)
        if args.symbol == 'FAIL':
            raise ValueError('provider error')
        return {'decision': {'usage': {'input_tokens': 10, 'output_tokens': 2}}, 'actionable_signal': None}
    monkeypatch.setattr(web, 'run_analysis', analyze)
    return TestClient(web.app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 50000)), seen


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
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{"test-key":{"estimate_per_call":"0.01","request_cap":"0.20","daily_budget":"0.02"},"other-key":{"estimate_per_call":"0.01","request_cap":"0.20","daily_budget":"0.20"}}')))
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
    overlapping = threading.Event()
    active = peak = 0
    sources = []
    def analyze(args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            sources.append(args.source)
            if active >= 2:
                overlapping.set()
        try:
            overlapping.wait(3)
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
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{"test-key":{"estimate_per_call":"NaN","request_cap":"1","daily_budget":"1"}}')))
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
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{"test-key":{"estimate_per_call":"0.01","request_cap":"0.10","daily_budget":"0.01"}}')))
    barrier = threading.Barrier(2)
    def send(symbol):
        barrier.wait(timeout=2)
        return client.post('/v1/analyze:batch', json={'symbols': [symbol]}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': symbol})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, ['A', 'B']))
    assert sorted(response.status_code for response in responses) == [200, 403]
    assert len(seen) == 1


def test_recovery_resumes_only_work_not_started_before_restart(batch_client, monkeypatch):
    import json
    from contextlib import closing
    from fasttrade import batch_api, web
    client, seen = batch_client
    key_hash, limits = batch_api._authenticate('test-key')
    body = batch_api.BatchRequest(symbols=['A', 'B', 'C'])
    job, _ = batch_api._create(body, 'recover', key_hash, limits)
    results = [{'symbol': 'A', 'result': {'decision': {'usage': {'input_tokens': 5, 'output_tokens': 1}}}},
               {'symbol': 'B', 'status': 'started'}, None]
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'previous-process', state = 'running', results = ? WHERE id = ?", (json.dumps(results), job['id']))
    headers = {'X-API-Key': 'test-key', 'Idempotency-Key': 'recover'}
    with client:
        for _ in range(100):
            result = client.get('/v1/jobs/' + job['id'], headers=headers).json()
            if result['status'] == 'completed':
                break
            time.sleep(.02)
        assert result['status'] == 'completed' and result['progress'] == 3
        assert result['results'][1]['error'] == 'Interrupted; outcome unavailable'
        assert result['results'][2]['result']['decision']['usage']['input_tokens'] == 10
        assert seen == ['C']
        assert client.post('/v1/analyze:batch', json=body.model_dump(), headers=headers).json() == result


def test_recovery_does_not_run_jobs_for_revoked_keys(batch_client, monkeypatch):
    from contextlib import closing
    from fasttrade import batch_api, web
    client, seen = batch_client
    key_hash, limits = batch_api._authenticate('test-key')
    job, _ = batch_api._create(batch_api.BatchRequest(symbols=['A']), 'revoked', key_hash, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'previous-process' WHERE id = ?", (job['id'],))
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{}')))
    batch_api._recover(web.AnalysisRequest, web.analyze)
    time.sleep(.05)
    assert seen == []
    with closing(batch_api._db()) as db:
        row = db.execute('SELECT owner, reserved_micro FROM jobs WHERE id = ?', (job['id'],)).fetchone()
    assert row['owner'] == 'previous-process' and row['reserved_micro'] == 10000


def test_batch_api_passes_decision_provider(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    seen = []
    monkeypatch.setattr(web, 'run_analysis', lambda args: seen.append(args.decision_provider) or {'decision': {'usage': None}, 'actionable_signal': None})
    response = client.post('/v1/analyze:batch', json={'symbols': ['A'], 'decision_provider': 'baseten'}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': 'provider'})
    assert response.status_code == 200
    assert seen == ['baseten']


def test_hashed_batch_keys_support_rotation_without_resetting_budget(monkeypatch, tmp_path):
    import hashlib
    import json
    from fastapi import HTTPException
    from fasttrade import batch_api
    import pytest
    old = hashlib.sha256(b'old-secret').hexdigest()
    new = hashlib.sha256(b'new-secret').hexdigest()
    common = {'account': old, 'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.20'}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps({old: {**common, 'expires_at': '2099-01-01T00:00:00Z'}, new: common}))
    assert batch_api._authenticate('old-secret')[0] == old
    assert batch_api._authenticate('new-secret')[0] == old
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps({old: {**common, 'expires_at': '2000-01-01T00:00:00Z'}, new: common}))
    with pytest.raises(HTTPException) as error:
        batch_api._authenticate('old-secret')
    assert error.value.status_code == 401
    assert batch_api._authenticate('new-secret')[0] == old


def test_hashed_key_configuration_recovers_existing_jobs(batch_client, monkeypatch):
    import hashlib
    import json
    from contextlib import closing
    from fasttrade import batch_api, web
    client, seen = batch_client
    account = hashlib.sha256(b'test-key').hexdigest()
    config = {account: {'account': account, 'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.20'}}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps(config))
    identity, limits = batch_api._authenticate('test-key')
    job, _ = batch_api._create(batch_api.BatchRequest(symbols=['A']), 'hashed-recovery', identity, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'previous-process' WHERE id = ?", (job['id'],))
    batch_api._recover(web.AnalysisRequest, web.analyze)
    for _ in range(100):
        result = client.get('/v1/jobs/' + job['id'], headers={'X-API-Key': 'test-key'}).json()
        if result['status'] == 'completed': break
        time.sleep(.02)
    assert result['status'] == 'completed'
    assert seen == ['A']


def test_rotated_key_uses_same_budget_and_job_history(batch_client, monkeypatch):
    import hashlib
    import json
    client, seen = batch_client
    old = hashlib.sha256(b'test-key').hexdigest()
    new = hashlib.sha256(b'new-key').hexdigest()
    limits = {'account': old, 'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.01'}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps({old: limits, new: limits}))
    first = client.post('/v1/analyze:batch', json={'symbols': ['A']}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': 'one'})
    assert first.status_code == 200
    assert client.get('/v1/jobs/' + first.json()['id'], headers={'X-API-Key': 'new-key'}).status_code == 200
    second = client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={'X-API-Key': 'new-key', 'Idempotency-Key': 'two'})
    assert second.status_code == 403
    assert seen == ['A']


def test_versioned_analysis_requires_authentication_and_reserves_budget(batch_client, monkeypatch):
    client, seen = batch_client
    payload = {'symbol': 'AAPL'}
    assert client.post('/v1/analyze', json=payload).status_code == 401
    first = client.post('/v1/analyze', json=payload, headers={'X-API-Key': 'test-key'})
    assert first.status_code == 200
    assert seen == ['AAPL']
    usage = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()
    assert usage['estimated_reserved'] == '0.01'
    assert usage['usage']['input_tokens_reported'] == 10
    assert usage['usage']['output_tokens_reported'] == 2
    assert usage['usage']['complete'] is True


def test_legacy_single_reservation_stays_incomplete_and_preserves_budget(batch_client, monkeypatch):
    import os
    import sqlite3
    from datetime import datetime, timezone

    client, _ = batch_client
    day = datetime.now(timezone.utc).date().isoformat()
    identity = hashlib.sha256(b'test-key').hexdigest()
    with sqlite3.connect(os.environ['FASTTRADE_BATCH_DATABASE']) as db:
        db.execute('CREATE TABLE single_reservations (id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, day TEXT NOT NULL, reserved_micro INTEGER NOT NULL)')
        db.execute('INSERT INTO single_reservations VALUES (?, ?, ?, ?)', ('legacy', identity, day, 10000))
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'}).status_code == 200
    result = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()
    assert result['estimated_reserved'] == '0.02'
    assert result['usage']['input_tokens_reported'] == 10
    assert result['usage']['output_tokens_reported'] == 2
    assert result['usage']['complete'] is False
    with sqlite3.connect(os.environ['FASTTRADE_BATCH_DATABASE']) as db:
        assert db.execute("SELECT reserved_micro FROM single_reservations WHERE id = 'legacy'").fetchone() == (10000,)


def test_single_analysis_preserves_partial_reported_usage(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'usage': {'input_tokens': 7, 'output_tokens': None}}, 'actionable_signal': None})
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'}).status_code == 200
    usage = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()['usage']
    assert usage['input_tokens_reported'] == 7
    assert usage['output_tokens_reported'] == 0
    assert usage['complete'] is False


def test_single_usage_storage_failure_keeps_result_and_reservation(batch_client, monkeypatch):
    from fasttrade import batch_api
    client, _ = batch_client
    monkeypatch.setattr(batch_api, 'record_single_usage', lambda *args: (_ for _ in ()).throw(OSError('storage failed')))
    response = client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'})
    assert response.status_code == 200
    result = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()
    assert result['estimated_reserved'] == '0.01'
    assert result['usage']['complete'] is False


def test_single_usage_is_isolated_by_api_key(batch_client, monkeypatch):
    client, _ = batch_client
    limits = {'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.20'}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys({'test-key': limits, 'other-key': limits}))
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'}).status_code == 200
    usage = client.get('/v1/usage', headers={'X-API-Key': 'other-key'}).json()
    assert usage['estimated_reserved'] == '0'
    assert usage['usage']['input_tokens_reported'] == 0
    assert usage['usage']['output_tokens_reported'] == 0


def test_single_and_batch_tokens_are_combined(batch_client):
    client, _ = batch_client
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    assert client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={**key, 'Idempotency-Key': 'batch'}).status_code == 200
    usage = client.get('/v1/usage', headers=key).json()['usage']
    assert usage['input_tokens_reported'] == 20
    assert usage['output_tokens_reported'] == 4
    assert usage['complete'] is True


def test_per_key_token_cost_uses_rates_recorded_for_each_call(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    prices = {'jev': {'test-model': {'input_per_million': '0.5', 'output_per_million': '1.5'}}}
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', json.dumps(prices))
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'provider': 'jev', 'model': 'test-model', 'usage': {'input_tokens': 1000000, 'output_tokens': 2000000}}, 'actionable_signal': None})
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    assert client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={**key, 'Idempotency-Key': 'cost'}).status_code == 200
    prices['jev']['test-model']['input_per_million'] = '9'
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', json.dumps(prices))
    result = client.get('/v1/usage', headers=key).json()
    assert result['estimated_reserved'] == '0.02'
    assert result['token_cost']['estimated_usd'] == '7'
    assert result['token_cost']['priced_calls'] == 2
    assert result['token_cost']['complete'] is True


def test_token_cost_keeps_partial_usage_and_unknown_prices_explicit(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', json.dumps({'jev': {'test-model': {'input_per_million': '1', 'output_per_million': '2'}}}))
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'provider': 'jev', 'model': 'test-model', 'usage': {'input_tokens': 100, 'output_tokens': None}}, 'actionable_signal': None})
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    result = client.get('/v1/usage', headers=key).json()['token_cost']
    assert result['estimated_usd'] == '0.0001'
    assert result['complete'] is False
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', '{}')
    assert client.post('/v1/analyze', json={'symbol': 'B'}, headers=key).status_code == 200
    result = client.get('/v1/usage', headers=key).json()['token_cost']
    assert result['estimated_usd'] == '0.0001'
    assert result['unpriced_outcomes'] == 1
    assert result['complete'] is False


def test_token_cost_formats_whole_dollar_values_without_losing_zeros(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', json.dumps({'jev': {'test-model': {'input_per_million': '10', 'output_per_million': '0'}}}))
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'provider': 'jev', 'model': 'test-model', 'usage': {'input_tokens': 1000000, 'output_tokens': 0}}, 'actionable_signal': None})
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    assert client.get('/v1/usage', headers=key).json()['token_cost']['estimated_usd'] == '10'


@pytest.mark.parametrize('config', ['not json', '{"jev":{"test-model":{"input_per_million":"NaN","output_per_million":"1"}}}'])
def test_missing_or_invalid_price_never_implies_free_usage(batch_client, monkeypatch, config):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', config)
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'provider': 'jev', 'model': 'test-model', 'usage': {'input_tokens': 10, 'output_tokens': 2}}, 'actionable_signal': None})
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    result = client.get('/v1/usage', headers=key).json()['token_cost']
    assert result['estimated_usd'] is None
    assert result['unpriced_outcomes'] == 1 and result['complete'] is False


def test_token_cost_is_isolated_by_account(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    limits = {'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.20'}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys({'test-key': limits, 'other-key': limits}))
    monkeypatch.setenv('FASTTRADE_MODEL_PRICES', json.dumps({'jev': {'test-model': {'input_per_million': '1', 'output_per_million': '0'}}}))
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'provider': 'jev', 'model': 'test-model', 'usage': {'input_tokens': 1000000, 'output_tokens': 0}}, 'actionable_signal': None})
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'}).status_code == 200
    other = client.get('/v1/usage', headers={'X-API-Key': 'other-key'}).json()['token_cost']
    assert other['estimated_usd'] == '0' and other['priced_calls'] == 0


def test_pending_batch_slots_are_not_reported_as_unpriced_calls(batch_client, monkeypatch):
    import threading
    from fasttrade import web
    client, _ = batch_client
    entered, release = threading.Event(), threading.Event()
    def slow(args):
        entered.set()
        release.wait(5)
        return {'decision': {'usage': None}, 'actionable_signal': None}
    monkeypatch.setattr(web, 'run_analysis', slow)
    key = {'X-API-Key': 'test-key', 'Idempotency-Key': 'pending-cost'}
    try:
        response = client.post('/v1/analyze:batch', json={'symbols': [f'A{i}' for i in range(10)]}, headers=key)
        assert response.status_code == 202 and entered.wait(3)
        cost = client.get('/v1/usage', headers=key).json()['token_cost']
        assert cost['pending_or_unknown_outcomes'] == 10
        assert cost['unpriced_outcomes'] == 0 and cost['priced_calls'] == 0
        assert cost['estimated_usd'] is None and cost['complete'] is False
    finally:
        release.set()


def test_reported_tokens_from_retried_calls_are_counted_but_incomplete(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setattr(web, 'run_analysis', lambda args: {'decision': {'usage': {
        'input_tokens': 10, 'output_tokens': None, 'output_tokens_last_attempt': 3}}, 'actionable_signal': None})
    key = {'X-API-Key': 'test-key'}
    assert client.post('/v1/analyze', json={'symbol': 'A'}, headers=key).status_code == 200
    assert client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={**key, 'Idempotency-Key': 'batch'}).status_code == 200
    usage = client.get('/v1/usage', headers=key).json()['usage']
    assert usage['input_tokens_reported'] == 20
    assert usage['output_tokens_reported'] == 6
    assert usage['complete'] is False


def test_usage_reads_reservations_and_tokens_from_same_snapshot(batch_client, monkeypatch):
    import os
    import sqlite3
    from contextlib import closing
    from datetime import datetime, timezone
    from fasttrade import batch_api

    client, _ = batch_client
    path = os.environ['FASTTRADE_BATCH_DATABASE']
    with closing(batch_api._db()) as db:
        db.execute('PRAGMA journal_mode=WAL')
    original = batch_api._spent
    identity = hashlib.sha256(b'test-key').hexdigest()
    day = datetime.now(timezone.utc).date().isoformat()
    def interleaved_spent(db, key_hash, date):
        reserved = original(db, key_hash, date)
        with sqlite3.connect(path) as writer:
            writer.execute('INSERT INTO single_reservations VALUES (?, ?, ?, ?)', ('concurrent', identity, day, 10000))
            writer.execute('INSERT INTO single_usage VALUES (?, ?, ?)', ('concurrent', 42, 7))
        return reserved
    monkeypatch.setattr(batch_api, '_spent', interleaved_spent)
    before = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()
    assert before['estimated_reserved'] == '0'
    assert before['usage']['input_tokens_reported'] == 0
    monkeypatch.setattr(batch_api, '_spent', original)
    after = client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()
    assert after['estimated_reserved'] == '0.01'
    assert after['usage']['input_tokens_reported'] == 42


def test_v1_authenticates_before_body_validation_and_limits_requests(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    assert client.post('/v1/analyze', json={}).status_code == 401
    calls = []
    def deny(identity, rate):
        calls.append((identity, rate))
        return False
    monkeypatch.setattr(web, 'check_api_rate_limit', deny)
    response = client.get('/v1/usage', headers={'X-API-Key': 'test-key'})
    assert response.status_code == 429
    assert len(calls) == 1


def test_v1_rate_storage_failure_fails_closed(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    def unavailable(identity, rate):
        raise RuntimeError('redis unavailable')
    monkeypatch.setattr(web, 'check_api_rate_limit', unavailable)
    assert client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).status_code == 503


def test_v1_same_origin_cannot_bypass_limiter(batch_client, monkeypatch):
    from fasttrade import web
    client, _ = batch_client
    monkeypatch.setattr(web, 'check_api_rate_limit', lambda identity, rate: False)
    response = client.get('/v1/usage', headers={'X-API-Key': 'test-key', 'Origin': 'http://127.0.0.1:8765'})
    assert response.status_code == 429


def test_versioned_analysis_shares_budget_with_batch(batch_client, monkeypatch):
    client, seen = batch_client
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys(json.loads('{"test-key":{"estimate_per_call":"0.01","request_cap":"0.10","daily_budget":"0.01"}}')))
    first = client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'})
    assert first.status_code == 200
    batch = client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': 'test'})
    assert batch.status_code == 403
    assert seen == ['A']
    assert client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).json()['usage']['complete'] is True


def test_recovery_does_not_steal_jobs_from_live_worker(batch_client, monkeypatch):
    from contextlib import closing
    from fasttrade import batch_api, web
    client, seen = batch_client
    identity, limits = batch_api._authenticate('test-key')
    job, _ = batch_api._create(batch_api.BatchRequest(symbols=['A']), 'active-worker', identity, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'active-process' WHERE id = ?", (job['id'],))
        db.execute('INSERT INTO worker_leases VALUES (?, ?)', ('active-process', time.time()))
    batch_api._recover(web.AnalysisRequest, web.analyze)
    time.sleep(.05)
    assert seen == []
    with closing(batch_api._db()) as db:
        assert db.execute('SELECT owner FROM jobs WHERE id = ?', (job['id'],)).fetchone()['owner'] == 'active-process'


def test_job_owned_by_live_worker_is_reported_as_running(batch_client):
    from contextlib import closing
    from fasttrade import batch_api
    client, _ = batch_client
    identity, limits = batch_api._authenticate('test-key')
    job, _ = batch_api._create(batch_api.BatchRequest(symbols=['A']), 'other-worker', identity, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'other-worker', state = 'running' WHERE id = ?", (job['id'],))
        db.execute('INSERT INTO worker_leases VALUES (?, ?)', ('other-worker', time.time()))
    assert client.get('/v1/jobs/' + job['id'], headers={'X-API-Key': 'test-key'}).json()['status'] == 'running'


def test_v1_cors_allowlist_and_preflight(batch_client, monkeypatch):
    client, _ = batch_client
    monkeypatch.setenv('FASTTRADE_CORS_ORIGINS', 'https://client.example')
    headers = {'Origin': 'https://client.example', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type,x-api-key'}
    response = client.options('/v1/analyze', headers=headers)
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'https://client.example'
    assert 'access-control-allow-credentials' not in response.headers
    response = client.get('/v1/usage', headers={'Origin': 'https://client.example', 'X-API-Key': 'test-key'})
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == 'https://client.example'
    assert response.headers['access-control-expose-headers'] == 'X-Request-ID'
    assert response.headers['x-request-id']
    assert client.get('/v1/usage', headers={'Origin': 'https://evil.example', 'X-API-Key': 'test-key'}).status_code == 403
    monkeypatch.setenv('FASTTRADE_CORS_ORIGINS', '*')
    assert client.get('/v1/usage', headers={'Origin': 'https://evil.example', 'X-API-Key': 'test-key'}).status_code == 503


def test_allowlisted_origin_can_read_request_id_on_unhandled_error(batch_client, monkeypatch):
    from fastapi.testclient import TestClient
    from fasttrade import batch_api, web

    calls = iter([('account', {'rate_per_minute': 60}), RuntimeError('private detail')])
    def authenticate(*args, **kwargs):
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(batch_api, '_authenticate', authenticate)
    monkeypatch.setenv('FASTTRADE_CORS_ORIGINS', 'https://client.example')
    client = TestClient(web.app, base_url='http://127.0.0.1:8765', client=('127.0.0.1', 50000), raise_server_exceptions=False)
    response = client.post('/v1/analyze', json={'symbol': 'AAPL'}, headers={'Origin': 'https://client.example', 'X-API-Key': 'test-key'})
    assert response.status_code == 500
    assert response.headers['access-control-allow-origin'] == 'https://client.example'
    assert response.headers['access-control-expose-headers'] == 'X-Request-ID'
    assert response.headers['x-request-id']
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['x-frame-options'] == 'DENY'
    assert 'private detail' not in response.text


def test_network_mode_allows_only_authenticated_v1_and_health(batch_client, monkeypatch):
    import hashlib
    import json
    from fasttrade import web
    _, seen = batch_client
    digest = hashlib.sha256(b'test-key').hexdigest()
    monkeypatch.setenv('FASTTRADE_HOST', '0.0.0.0')
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps({digest: {'estimate_per_call': '0.01', 'request_cap': '1', 'daily_budget': '1'}}))
    client = TestClient(web.app, base_url='http://192.0.2.10:8765', client=('203.0.113.1', 41000))
    assert client.get('/healthz').status_code == 200
    assert client.get('/').status_code == 403
    assert client.post('/api/analyze', json={'symbol': 'A'}).status_code == 403
    assert client.post('/v1/analyze', json={'symbol': 'A'}).status_code == 401
    response = client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'})
    assert response.status_code == 200
    assert seen == ['A']


def test_legacy_plaintext_configuration_fails_with_migration_message(monkeypatch):
    from fastapi import HTTPException
    from fasttrade import batch_api
    monkeypatch.delenv('FASTTRADE_BATCH_KEY_HASHES', raising=False)
    monkeypatch.setenv('FASTTRADE_BATCH_KEYS', '{"test-key": {}}')
    with pytest.raises(HTTPException) as error:
        batch_api._authenticate('test-key')
    assert error.value.status_code == 503
    assert 'FASTTRADE_BATCH_KEY_HASHES' in error.value.detail


def test_rotated_keys_cannot_use_different_rate_or_budget(monkeypatch):
    from fastapi import HTTPException
    from fasttrade import batch_api
    old = hashlib.sha256(b'old').hexdigest()
    new = hashlib.sha256(b'new').hexdigest()
    base = {'account': old, 'estimate_per_call': '0.01', 'request_cap': '1', 'daily_budget': '1', 'rate_per_minute': 2}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps({old: base, new: {**base, 'rate_per_minute': 100}}))
    with pytest.raises(HTTPException) as error:
        batch_api._authenticate('new')
    assert error.value.status_code == 503


def test_stale_worker_cannot_start_or_overwrite_reassigned_job(batch_client):
    from contextlib import closing
    from fasttrade import batch_api, web
    client, seen = batch_client
    identity, limits = batch_api._authenticate('test-key')
    body = batch_api.BatchRequest(symbols=['A'])
    job, _ = batch_api._create(body, 'stale-worker', identity, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'new-owner', state = 'running' WHERE id = ?", (job['id'],))
        db.execute('INSERT INTO worker_leases VALUES (?, ?)', ('new-owner', time.time()))
    batch_api._process(job['id'], identity, body, web.AnalysisRequest, web.analyze)
    assert seen == []
    with closing(batch_api._db()) as db:
        row = db.execute('SELECT owner, state, results FROM jobs WHERE id = ?', (job['id'],)).fetchone()
    assert row['owner'] == 'new-owner' and row['state'] == 'running'
    assert json.loads(row['results']) == [None]


def test_concurrent_single_and_batch_share_budget(batch_client, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    client, seen = batch_client
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', hashed_keys({'test-key': {'estimate_per_call': '0.01', 'request_cap': '1', 'daily_budget': '0.01'}}))
    def single():
        return client.post('/v1/analyze', json={'symbol': 'A'}, headers={'X-API-Key': 'test-key'}).status_code
    def batch():
        return client.post('/v1/analyze:batch', json={'symbols': ['B']}, headers={'X-API-Key': 'test-key', 'Idempotency-Key': 'concurrent'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(single), pool.submit(batch)]
        assert sorted(f.result() for f in results) == [200, 403]
    assert len(seen) == 1


@pytest.mark.parametrize(('method', 'path'), [('GET', '/v1/usage'), ('GET', '/v1/jobs/id'), ('POST', '/v1/analyze'), ('POST', '/v1/analyze:batch'), ('POST', '/v1/analyze:batch/estimate'), ('GET', '/v1/unknown')])
def test_every_versioned_route_rejects_unauthenticated_requests(batch_client, method, path):
    client, _ = batch_client
    assert client.request(method, path, json={}).status_code == 401


def test_expired_rotation_key_is_rejected_by_api(batch_client, monkeypatch):
    client, _ = batch_client
    digest = hashlib.sha256(b'test-key').hexdigest()
    config = {digest: {'estimate_per_call': '0.01', 'request_cap': '1', 'daily_budget': '1', 'expires_at': '2000-01-01T00:00:00Z'}}
    monkeypatch.setenv('FASTTRADE_BATCH_KEY_HASHES', json.dumps(config))
    assert client.get('/v1/usage', headers={'X-API-Key': 'test-key'}).status_code == 401


def test_idempotent_retry_sees_job_owned_by_live_worker(batch_client):
    from contextlib import closing
    from fasttrade import batch_api
    identity, limits = batch_api._authenticate('test-key')
    body = batch_api.BatchRequest(symbols=['A'])
    job, _ = batch_api._create(body, 'retry-other-worker', identity, limits)
    with closing(batch_api._db()) as db, db:
        db.execute("UPDATE jobs SET owner = 'active-other', state = 'running' WHERE id = ?", (job['id'],))
        db.execute('INSERT INTO worker_leases VALUES (?, ?)', ('active-other', time.time()))
    retry, created = batch_api._create(body, 'retry-other-worker', identity, limits)
    assert not created and retry['status'] == 'running'
