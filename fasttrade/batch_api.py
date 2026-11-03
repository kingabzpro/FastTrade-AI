"""Local batch API with persistent idempotency and conservative budget reservations."""
import asyncio
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    symbols: list[str] = Field(min_length=1, max_length=50)
    timeframe: Literal['1m', '2m', '5m', '15m', '30m', '60m'] = '5m'
    source: Literal['alpaca', 'yahoo'] = 'alpaca'
    decision_provider: Literal['jev', 'liquid', 'vllm', 'subq', 'baseten'] = 'jev'

    @field_validator('symbols')
    @classmethod
    def validate_symbols(cls, symbols):
        import re
        normalized = [symbol.upper() for symbol in symbols]
        if any(not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', symbol) for symbol in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError('Provide distinct valid ticker symbols')
        return normalized


_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='batch-job')
_slots = threading.BoundedSemaphore(4)
_instance = uuid.uuid4().hex
_symbol_timeout_seconds = 90


def _db():
    path = os.environ.get('FASTTRADE_BATCH_DATABASE', str(Path.home() / '.fasttrade' / 'batch.sqlite3'))
    Path(path).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, key_hash TEXT NOT NULL, idem TEXT NOT NULL,
        request_hash TEXT NOT NULL, request TEXT NOT NULL, day TEXT NOT NULL,
        reserved_micro INTEGER NOT NULL, state TEXT NOT NULL, owner TEXT NOT NULL,
        results TEXT NOT NULL, UNIQUE(key_hash, idem))''')
    connection.commit()
    return connection


def _money(value):
    try:
        amount = Decimal(str(value))
        micro = amount * 1000000
        if not amount.is_finite() or amount < 0 or micro != micro.to_integral_value() or micro > 10**12:
            raise ValueError()
        return int(micro)
    except (ValueError, InvalidOperation, TypeError):
        raise HTTPException(503, 'Invalid batch budget configuration') from None


def _dollars(micro):
    return format(Decimal(micro) / 1000000, '.6f').rstrip('0').rstrip('.') or '0'


def _authenticate(key):
    try:
        keys = json.loads(os.environ.get('FASTTRADE_BATCH_KEYS', '{}'))
        if not isinstance(keys, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, 'Batch API configuration unavailable') from None
    if not key or not isinstance(key, str):
        raise HTTPException(401, 'Invalid API key')
    match = next((config for candidate, config in keys.items() if hmac.compare_digest(candidate.encode(), key.encode())), None)
    if not isinstance(match, dict):
        raise HTTPException(401, 'Invalid API key')
    try:
        limits = {name: _money(match[name]) for name in ('estimate_per_call', 'request_cap', 'daily_budget')}
    except KeyError:
        raise HTTPException(503, 'Invalid batch budget configuration') from None
    if not limits['estimate_per_call']:
        raise HTTPException(503, 'Invalid batch budget configuration')
    return hashlib.sha256(key.encode()).hexdigest(), limits


def _usage(results):
    values = [item.get('result', {}).get('decision', {}).get('usage') for item in results if item]
    complete = len(values) == len(results) and all(item is not None and item.get('output_tokens') is not None for item in values)
    return {'input_tokens_reported': sum(item['input_tokens'] for item in values if item is not None),
            'output_tokens_reported': sum(item['output_tokens'] for item in values if item is not None and item.get('output_tokens') is not None),
            'complete': complete, 'status': 'Token information available' if complete else 'Token information unavailable'}


def _view(row):
    results = json.loads(row['results'])
    state = row['state']
    if state in ('queued', 'running') and row['owner'] != _instance:
        state = 'interrupted'
    return {'id': row['id'], 'status': state, 'progress': sum(item is not None and item.get('status') != 'started' for item in results),
            'total': len(results), 'results': results, 'estimated_cost': _dollars(row['reserved_micro']),
            'usage': _usage(results)}


def _find(job_id, key_hash):
    with closing(_db()) as db:
        row = db.execute('SELECT * FROM jobs WHERE id = ? AND key_hash = ?', (job_id, key_hash)).fetchone()
    if not row:
        raise HTTPException(404, 'Job not found')
    return _view(row)


def _create(body, idem, key_hash, limits):
    if not idem or len(idem) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in idem):
        raise HTTPException(400, 'A valid Idempotency-Key is required')
    payload = body.model_dump_json()
    request_hash = hashlib.sha256(payload.encode()).hexdigest()
    estimate = limits['estimate_per_call'] * len(body.symbols)
    today = datetime.now(timezone.utc).date().isoformat()
    db = _db()
    try:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM jobs WHERE key_hash = ? AND idem = ?', (key_hash, idem)).fetchone()
        if existing:
            if existing['request_hash'] != request_hash:
                raise HTTPException(409, 'Idempotency-Key already used for a different request')
            return _view(existing), False
        if estimate > limits['request_cap']:
            raise HTTPException(403, 'Estimated cost exceeds request cap')
        spent = db.execute('SELECT COALESCE(SUM(reserved_micro), 0) FROM jobs WHERE key_hash = ? AND day = ?', (key_hash, today)).fetchone()[0]
        if spent + estimate > limits['daily_budget']:
            raise HTTPException(403, 'Estimated cost exceeds daily budget')
        job_id = uuid.uuid4().hex
        db.execute('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   (job_id, key_hash, idem, request_hash, payload, today, estimate, 'queued', _instance, json.dumps([None] * len(body.symbols))))
        db.commit()
        row = db.execute('SELECT * FROM jobs WHERE id = ?', (job_id,)).fetchone()
        return _view(row), True
    finally:
        db.close()


def _process(job_id, key_hash, body, analysis_request, analyze):
    with closing(_db()) as db, db:
        db.execute("UPDATE jobs SET state = 'running' WHERE id = ?", (job_id,))
        results = json.loads(db.execute('SELECT results FROM jobs WHERE id = ?', (job_id,)).fetchone()[0])
    started = {}
    result_lock = threading.Lock()
    def one(index, symbol):
        if not _slots.acquire(timeout=_symbol_timeout_seconds):
            return {'symbol': symbol, 'error': 'Analysis timed out waiting for a worker', 'actionable_signal': None}
        try:
            with result_lock:
                results[index] = {'symbol': symbol, 'status': 'started'}
                with closing(_db()) as db, db:
                    db.execute('UPDATE jobs SET results = ? WHERE id = ?', (json.dumps(results, allow_nan=False), job_id))
            started[index] = time.monotonic()
            try:
                response = asyncio.run(analyze(analysis_request(symbol=symbol, timeframe=body.timeframe, source=body.source, decision_provider=body.decision_provider)))
                if isinstance(response, JSONResponse):
                    return {'symbol': symbol, 'error': json.loads(response.body)['error'], 'actionable_signal': None}
                return {'symbol': symbol, 'result': response}
            except Exception:
                return {'symbol': symbol, 'error': 'Analysis unavailable', 'actionable_signal': None}
        finally:
            _slots.release()
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=4)
        futures = {pool.submit(one, i, symbol): i for i, symbol in enumerate(body.symbols) if results[i] is None}
        pending = set(futures)
        while pending:
            done, _ = wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
            now = time.monotonic()
            timed_out = {future for future in pending if futures[future] in started and now - started[futures[future]] >= _symbol_timeout_seconds}
            if done or timed_out:
                with result_lock:
                    for future in done | timed_out:
                        index = futures[future]
                        results[index] = future.result() if future in done else {'symbol': body.symbols[index], 'error': 'Analysis timed out', 'actionable_signal': None}
                    pending -= done | timed_out
                    with closing(_db()) as db, db:
                        db.execute('UPDATE jobs SET results = ? WHERE id = ?', (json.dumps(results, allow_nan=False), job_id))
        with closing(_db()) as db, db:
            db.execute("UPDATE jobs SET state = 'completed' WHERE id = ?", (job_id,))
    except Exception:
        with closing(_db()) as db, db:
            db.execute("UPDATE jobs SET state = 'interrupted' WHERE id = ?", (job_id,))
    finally:
        if pool is not None:
            pool.shutdown(wait=False)
    return _find(job_id, key_hash)


def _recover(analysis_request, analyze):
    from dotenv import load_dotenv
    load_dotenv()
    try:
        configured = json.loads(os.environ.get('FASTTRADE_BATCH_KEYS', '{}'))
        if not isinstance(configured, dict):
            return
    except (ValueError, TypeError):
        return
    authorized = set()
    for key in configured:
        try:
            authorized.add(_authenticate(key)[0])
        except HTTPException:
            continue
    if not authorized:
        return
    with closing(_db()) as db, db:
        db.execute('BEGIN IMMEDIATE')
        rows = [row for row in db.execute("SELECT * FROM jobs WHERE state IN ('queued', 'running') AND owner != ?", (_instance,)).fetchall() if row['key_hash'] in authorized]
        for row in rows:
            results = json.loads(row['results'])
            for index, result in enumerate(results):
                if result is not None and result.get('status') == 'started':
                    results[index] = {'symbol': result['symbol'], 'error': 'Interrupted; outcome unavailable', 'actionable_signal': None}
            db.execute("UPDATE jobs SET owner = ?, state = 'queued', results = ? WHERE id = ?", (_instance, json.dumps(results), row['id']))
    for row in rows:
        body = BatchRequest.model_validate_json(row['request'])
        _workers.submit(_process, row['id'], row['key_hash'], body, analysis_request, analyze)


def make_batch_router(analysis_request, analyze):
    router = APIRouter(tags=['Batch'])

    @router.post('/v1/analyze:batch/estimate')
    def estimate(body: BatchRequest, x_api_key: str = Header(default=None)):
        key_hash, limits = _authenticate(x_api_key)
        cost = limits['estimate_per_call'] * len(body.symbols)
        today = datetime.now(timezone.utc).date().isoformat()
        with closing(_db()) as db:
            reserved = db.execute('SELECT COALESCE(SUM(reserved_micro), 0) FROM jobs WHERE key_hash = ? AND day = ?', (key_hash, today)).fetchone()[0]
        return {'estimated_cost': _dollars(cost), 'currency': 'USD', 'request_cap': _dollars(limits['request_cap']),
                'daily_remaining': _dollars(max(0, limits['daily_budget'] - reserved)),
                'within_request_cap': cost <= limits['request_cap'], 'within_daily_budget': cost + reserved <= limits['daily_budget'],
                'note': 'Estimate, not a guarantee of actual charges'}

    @router.post('/v1/analyze:batch')
    async def submit(body: BatchRequest, x_api_key: str = Header(default=None), idempotency_key: str = Header(default=None)):
        key_hash, limits = _authenticate(x_api_key)
        job, created = await run_in_threadpool(_create, body, idempotency_key, key_hash, limits)
        if created:
            try:
                future = _workers.submit(_process, job['id'], key_hash, body, analysis_request, analyze)
            except RuntimeError:
                with closing(_db()) as db, db:
                    db.execute("UPDATE jobs SET state = 'interrupted' WHERE id = ?", (job['id'],))
                raise HTTPException(503, 'Batch worker unavailable') from None
            if len(body.symbols) < 10:
                job = await run_in_threadpool(future.result)
        return JSONResponse(job, status_code=202 if len(body.symbols) >= 10 or job['status'] in ('queued', 'running') else 200)

    @router.get('/v1/jobs/{job_id}')
    def get_job(job_id: str, x_api_key: str = Header(default=None)):
        key_hash, _ = _authenticate(x_api_key)
        return _find(job_id, key_hash)

    @router.get('/v1/usage')
    def get_usage(x_api_key: str = Header(default=None)):
        key_hash, limits = _authenticate(x_api_key)
        today = datetime.now(timezone.utc).date().isoformat()
        with closing(_db()) as db:
            rows = db.execute('SELECT * FROM jobs WHERE key_hash = ? AND day = ?', (key_hash, today)).fetchall()
        outcomes = [item for row in rows for item in json.loads(row['results'])]
        return {'date_utc': today, 'estimated_reserved': _dollars(sum(row['reserved_micro'] for row in rows)),
                'daily_budget': _dollars(limits['daily_budget']), 'currency': 'USD', 'usage': _usage(outcomes)}

    return router
