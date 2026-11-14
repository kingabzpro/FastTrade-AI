"""Bounded upstream retries and per-credential circuit breakers."""
import atexit
import hashlib
import json
import math
import random
from collections import OrderedDict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock
from time import monotonic, sleep
from urllib.parse import urlsplit

import httpx
from tenacity import Retrying, retry_if_exception_type, retry_if_result


# Shared client keeps upstream connections warm across decisions; per-request
# timeouts and proxies behave exactly like the top-level httpx API.
_client = httpx.Client(limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=30))
atexit.register(_client.close)


def pooled_get(url, **kwargs):
    return _client.get(url, **kwargs)


def pooled_post(url, **kwargs):
    return _client.post(url, **kwargs)


class CircuitOpen(Exception):
    pass


class _Circuit:
    def __init__(self):
        self.lock = Lock()
        self.failures = 0
        self.last_failure = 0
        self.opened_until = 0
        self.not_before = 0
        self.generation = 0
        self.probing = False
        self.active = 0

    def enter(self):
        with self.lock:
            if monotonic() < self.not_before:
                raise CircuitOpen('Upstream rate limit active')
            if self.opened_until:
                if monotonic() < self.opened_until or self.probing:
                    raise CircuitOpen('Upstream temporarily unavailable')
                self.probing = True
                self.active += 1
                return self.generation, True
            self.active += 1
            return self.generation, False

    def evictable(self):
        with self.lock:
            now = monotonic()
            return (not self.active and now >= self.not_before and now >= self.opened_until
                    and (not self.failures or now >= self.last_failure + 300))

    def defer(self, delay):
        deadline = monotonic() + delay
        with self.lock:
            self.not_before = max(self.not_before, deadline)
        return deadline

    def check_defer(self, own_deadline):
        # shortcut: calls already admitted may reach upstream after another call receives 429; serialize admission if required by the provider.
        with self.lock:
            if self.not_before > own_deadline and monotonic() < self.not_before:
                raise CircuitOpen('Upstream rate limit active')

    def finish(self, token, healthy):
        with self.lock:
            self.active -= 1
            generation, probe = token
            if generation != self.generation:
                return
            if healthy is None:
                if probe:
                    self.probing = False
            elif healthy:
                self.failures = 0
                if probe:
                    self.opened_until = 0
                    self.probing = False
                    self.generation += 1
            else:
                self.failures += 1
                self.last_failure = monotonic()
                if probe or self.failures >= 3:
                    self.opened_until = monotonic() + 30
                    self.probing = False
                    self.generation += 1


_circuits = OrderedDict()
_circuits_lock = Lock()


def utcnow():
    return datetime.now(timezone.utc)


def _retry_after(value):
    if not value:
        return None
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            return math.inf if value.strip().isdigit() else None
        return max(0.0, seconds)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - utcnow()).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _wait(state):
    jitter = random.uniform(0, min(1.0, 0.25 * 2 ** (state.attempt_number - 1)))
    if not state.outcome.failed and state.outcome.result().status_code == 429:
        delay = _retry_after(state.outcome.result().headers.get('Retry-After'))
        if delay is not None:
            return max(delay, jitter)
    return jitter


def request(call, url, *, service, credentials, retry_transport, **kwargs):
    parsed = urlsplit(url)
    identity = hashlib.sha256(json.dumps([service, parsed.scheme, parsed.netloc, credentials]).encode()).digest()
    with _circuits_lock:
        circuit = _circuits.get(identity)
        if circuit is None:
            if len(_circuits) >= 128:
                unused = next((key for key, item in _circuits.items() if item.evictable()), None)
                if unused is None:
                    raise CircuitOpen('Upstream circuit capacity reached')
                del _circuits[unused]
            circuit = _circuits[identity] = _Circuit()
        _circuits.move_to_end(identity)
        token = circuit.enter()
    healthy = False
    own_deadline = 0
    attempts = 0
    try:
        def attempt():
            nonlocal own_deadline, attempts
            circuit.check_defer(own_deadline)
            attempts += 1
            result = call(url, **kwargs)
            if result.status_code == 429:
                delay = _retry_after(result.headers.get('Retry-After'))
                if delay is not None:
                    own_deadline = circuit.defer(delay)
            return result
        retry = retry_if_result(lambda result: result.status_code == 429 or (retry_transport and 500 <= result.status_code < 600))
        if retry_transport:
            retry |= retry_if_exception_type(httpx.TransportError)
        runner = Retrying(sleep=sleep, wait=_wait,
                          stop=lambda state: state.attempt_number >= 3 or state.idle_for + state.upcoming_sleep > 10,
                          retry=retry, retry_error_callback=lambda state: state.outcome.result())
        result = runner(attempt)
        result.extensions['fasttrade_attempts'] = attempts
        healthy = result.status_code != 429 and result.status_code < 500
        return result
    except CircuitOpen:
        healthy = None
        raise
    finally:
        circuit.finish(token, healthy)
