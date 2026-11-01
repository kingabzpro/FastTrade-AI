import json
import threading
import time

import httpx
import pandas as pd
import pytest

from fasttrade import agents, decisions, evidence
from test_core import candles

AS_OF = pd.Timestamp('2026-01-02 19:30', tz='UTC')
QUESTIONS = {'action': {'type': 'choice', 'criteria': {'BUY': 'b', 'HOLD': 'h', 'SELL': 's'}}, 'forecast': {'type': 'noul'}}


def news_items():
    return [{'title': 'Test Corp raises guidance', 'publisher': 'Wire A', 'providerPublishTime': int(pd.Timestamp('2026-01-02 18:00', tz='UTC').timestamp())}]


def snapshot(**overrides):
    base = {'candles': candles(), 'news': news_items(), 'benchmark': candles(), 'portfolio': {'position': 0, 'equity': 1000.0}}
    base.update(overrides)
    return evidence.build_snapshot('TEST', '5m', AS_OF, base.pop('candles'), **base)


def mock_post(monkeypatch, data):
    # stdlib json allows NaN literals, so the raw-content form simulates any provider payload.
    monkeypatch.setattr(decisions.httpx, 'post', lambda url, **kwargs: httpx.Response(200, content=json.dumps(data).encode(), request=httpx.Request('POST', url)))


def good_payload():
    return {'model': 'jev-resolved', 'answers': {'action': {'type': 'choice', 'choice': 'HOLD', 'confidence': .6, 'probabilities': {'BUY': .2, 'HOLD': .6, 'SELL': .2}}, 'forecast': {'type': 'noul', 'probability': .35}}, 'usage': {'input_tokens': 120, 'output_tokens': 8}}


def valid_response(questions, model='jev-test'):
    answers = {}
    for name, question in questions.items():
        if question['type'] == 'choice':
            keys = list(question['criteria'])
            answers[name] = {'type': 'choice', 'choice': keys[0], 'confidence': .8, 'probabilities': {k: 1 / len(keys) for k in keys}}
        elif question['type'] == 'noul':
            answers[name] = {'type': 'noul', 'probability': .4}
        else:
            answers[name] = {'type': 'score', 'score': .5}
    return {'answers': answers, 'model': model, 'latency_ms': 1.5, 'usage': {'input_tokens': 10, 'output_tokens': 2}}


ROLES = set(agents.MODEL_ROLES) | {'fundamentals', 'portfolio'}


# --- Task 1: point-in-time evidence and contracts ---

def test_future_data_cannot_change_snapshot_or_evidence_ids():
    tail_a = pd.DataFrame({'timestamp': pd.date_range('2026-01-02 19:35', periods=4, freq='5min', tz='UTC'), 'open': [200.0] * 4, 'high': [201.0] * 4, 'low': [199.0] * 4, 'close': [200.0] * 4, 'volume': [1] * 4})
    tail_b = tail_a.copy()
    tail_b[['open', 'close']], tail_b['high'], tail_b['low'] = 10.0, 11.0, 9.0
    future_news = news_items() + [{'title': 'Future event', 'publisher': 'Wire B', 'providerPublishTime': int(pd.Timestamp('2026-01-03 12:00', tz='UTC').timestamp())}]
    future_facts = [{'id': 'f1', 'available_at': '2026-01-03T00:00:00+00:00', 'value': 5, 'source': 'X'}]
    a = evidence.build_snapshot('TEST', '5m', AS_OF, pd.concat([candles(), tail_a], ignore_index=True), news=future_news, fundamentals=future_facts, benchmark=pd.concat([candles(), tail_a], ignore_index=True))
    b = evidence.build_snapshot('TEST', '5m', AS_OF, pd.concat([candles(), tail_b], ignore_index=True), news=news_items(), fundamentals=[], benchmark=pd.concat([candles(), tail_b], ignore_index=True))
    assert a == b
    assert [n['id'] for n in a['news']] == [n['id'] for n in b['news']]
    assert a['market']['count'] == 60 and a['market']['last_close'] == 159.0


def test_naive_as_of_rejected():
    with pytest.raises(ValueError, match='timezone'):
        evidence.build_snapshot('TEST', '5m', pd.Timestamp('2026-01-02 19:30'), candles())


def test_missing_source_timestamps_are_dropped():
    undated_news = [{'title': 'undated', 'publisher': 'W'}, {'title': 'naive', 'publisher': 'W', 'pubDate': '2026-01-02 18:00'}]
    undated_facts = [{'id': 'f1', 'value': 1, 'source': 'Facts'}, {'id': 'f2', 'available_at': '2026-01-02 18:00', 'value': 1, 'source': 'Facts'}]
    snap = evidence.build_snapshot('TEST', '5m', AS_OF, candles(), news=undated_news, fundamentals=undated_facts)
    assert snap['news'] == [] and snap['fundamentals'] == []
    assert snap['availability']['news'] == 'unavailable' and snap['availability']['fundamentals'] == 'unavailable'


def test_stale_candles_raise():
    with pytest.raises(ValueError, match='stale'):
        evidence.build_snapshot('TEST', '5m', pd.Timestamp('2026-01-05 14:30', tz='UTC'), candles())


def test_duplicate_ids_are_deduped():
    snap = evidence.build_snapshot('TEST', '5m', AS_OF, candles(), news=news_items() * 2, fundamentals=[
        {'id': 'f1', 'available_at': '2026-01-02T17:00:00+00:00', 'value': 1, 'source': 'Facts'},
        {'id': 'f1', 'available_at': '2026-01-02T17:30:00+00:00', 'value': 2, 'source': 'Facts'}])
    assert len(snap['news']) == 1 and snap['news'][0]['id'].startswith('news-')
    assert [f['id'] for f in snap['fundamentals']] == ['f1'] and snap['fundamentals'][0]['value'] == 1
    assert snap['availability']['news'] == 'ok' and snap['availability']['fundamentals'] == 'ok'


def test_zero_headlines_are_explicitly_unavailable():
    snap = evidence.build_snapshot('TEST', '5m', AS_OF, candles(), news=[])
    assert snap['news'] == [] and snap['availability']['news'] == 'unavailable'
    assert snap['unavailable_reasons']['news']


def test_fundamentals_are_unavailable_in_v1():
    snap = snapshot()
    assert snap['fundamentals'] == [] and snap['availability']['fundamentals'] == 'unavailable'
    assert 'point-in-time' in snap['unavailable_reasons']['fundamentals']


def test_current_only_metadata_is_not_injected():
    snap = snapshot()
    assert 'ticker_details' not in snap and 'shortName' not in str(snap)
    assert snap['notice'].startswith('Third-party')


def test_absent_benchmark_is_explicit():
    snap = evidence.build_snapshot('TEST', '5m', AS_OF, candles())
    assert snap['benchmark']['status'] == 'unavailable' and snap['availability']['benchmark'] == 'unavailable'


# --- Task 2: typed decision client ---

def test_ask_questions_round_trip(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json=good_payload(), request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions.httpx, 'post', post)
    result = decisions.ask_questions({'symbol': 'TEST'}, QUESTIONS, 'https://example.com', 'jev', 'k')
    assert result['answers']['action']['choice'] == 'HOLD'
    assert result['answers']['forecast'] == {'type': 'noul', 'probability': .35}
    assert result['model'] == 'jev-resolved'
    assert result['usage'] == {'input_tokens': 120, 'output_tokens': 8}
    assert result['latency_ms'] >= 0
    assert calls[0]['json']['questions'] == QUESTIONS and calls[0]['headers'] == {'Authorization': 'Bearer k'}


INVALID_PAYLOADS = [
    ('missing answer', {'model': 'jev', 'answers': {}}),
    ('answers missing entirely', {'model': 'jev'}),
    ('answer not an object', {'model': 'jev', 'answers': {'action': 'BUY', 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('nan probability', {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .5, 'probabilities': {'BUY': float('nan'), 'HOLD': .5, 'SELL': .5}}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('unexpected probability keys', {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .5, 'probabilities': {'BUY': .8, 'HOLD': .2}}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('chosen is not the max', {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'HOLD', 'confidence': .5, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('probabilities do not sum to one', {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .5, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .2}}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('confidence out of range', {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': 1.5, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('empty model identifier', {'model': '', 'answers': good_payload()['answers']}),
    ('model not a string', {'model': 5, 'answers': good_payload()['answers']}),
    ('wrong answer type', {'model': 'jev', 'answers': {'action': {'type': 'noul', 'probability': .5}, 'forecast': {'type': 'noul', 'probability': .5}}}),
    ('probability outside range', {'model': 'jev', 'answers': {'action': good_payload()['answers']['action'], 'forecast': {'type': 'noul', 'probability': 1.5}}}),
]


@pytest.mark.parametrize('label,data', INVALID_PAYLOADS)
def test_ask_questions_rejects_invalid_responses(monkeypatch, label, data):
    mock_post(monkeypatch, data)
    with pytest.raises(ValueError, match='Invalid SystemOne response'):
        decisions.ask_questions({}, QUESTIONS, 'https://example.com', 'm', None)


def test_noul_and_score_questions_are_validated(monkeypatch):
    questions = {'forecast': {'type': 'noul'}, 'quality': {'type': 'score', 'min': 0, 'max': 1}}
    data = {'model': 'jev', 'answers': {'forecast': {'type': 'noul', 'probability': .4}, 'quality': {'type': 'score', 'score': .7}}}
    mock_post(monkeypatch, data)
    assert decisions.ask_questions({}, questions, 'u', 'm', None)['answers']['quality'] == {'type': 'score', 'score': .7}
    mock_post(monkeypatch, {'model': 'jev', 'answers': {'forecast': {'type': 'noul', 'probability': .4}, 'quality': {'type': 'score', 'score': 2}}})
    with pytest.raises(ValueError, match='Invalid SystemOne response'):
        decisions.ask_questions({}, questions, 'u', 'm', None)


def test_invalid_response_is_retried_bounded(monkeypatch):
    calls, payloads = [], iter([{'model': ''}, {'model': ''}, good_payload()])
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json=next(payloads), request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions.httpx, 'post', post)
    assert decisions.ask_questions({}, QUESTIONS, 'https://example.com', 'm', None)['answers']['action']['choice'] == 'HOLD'
    assert len(calls) == 3


def test_always_invalid_raises_after_three_attempts(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(200, json={'model': ''}, request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions.httpx, 'post', post)
    with pytest.raises(ValueError, match='Invalid SystemOne response'):
        decisions.ask_questions({}, QUESTIONS, 'https://example.com', 'm', None)
    assert len(calls) == 3


def test_timeout_is_not_retried(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        raise httpx.ReadTimeout('timeout')
    monkeypatch.setattr(decisions.httpx, 'post', post)
    with pytest.raises(ValueError, match='timeout'):
        decisions.ask_questions({}, QUESTIONS, 'https://example.com', 'm', 'k')
    assert len(calls) == 1


def test_http_error_is_not_retried_and_key_stays_hidden(monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(401, json={'error': 'bad key'}, request=httpx.Request('POST', url))
    monkeypatch.setattr(decisions.httpx, 'post', post)
    with pytest.raises(ValueError, match='request failed') as info:
        decisions.ask_questions({}, QUESTIONS, 'https://example.com', 'm', 'sekret-key')
    assert len(calls) == 1 and calls[0]['headers'] == {'Authorization': 'Bearer sekret-key'}
    assert 'sekret-key' not in str(info.value)


@pytest.mark.parametrize('choice,probs', [('BUY', {'BUY': .7, 'HOLD': .2, 'SELL': .1}), ('HOLD', {'BUY': .2, 'HOLD': .6, 'SELL': .2}), ('SELL', {'BUY': .1, 'HOLD': .2, 'SELL': .7})])
def test_existing_normalization_unchanged(choice, probs):
    data = {'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': choice, 'confidence': .8, 'probabilities': probs}}}
    normalized = decisions.normalize(data, 1)
    assert normalized['action'] == choice and normalized['latency_us'] == 1
    with pytest.raises(ValueError, match='Invalid SystemOne response'):
        decisions.normalize({'model': 'jev', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .5, 'probabilities': {'BUY': .6, 'HOLD': .6, 'SELL': -.2}}}}, 1)


# --- Task 3: independent specialists and parallelism ---

def test_all_specialists_share_one_batched_call(monkeypatch):
    calls = []
    def ask(state, questions, url, model, key):
        calls.append({'state': state, 'questions': questions, 'key': key})
        return valid_response(questions)
    monkeypatch.setattr(agents, 'ask_questions', ask)
    snap = snapshot()
    result = agents.run_specialists(snap, 'https://example.com', 'jev', 'k', timeout_s=5)
    assert len(calls) == 1 and calls[0]['key'] == 'k'
    assert set(calls[0]['questions']) == set(agents.MODEL_ROLES)
    assert 'portfolio' not in calls[0]['state'] and 'fundamentals' not in calls[0]['questions']
    assert set(result) == ROLES
    assert all(result[name]['status'] == 'ok' for name in agents.MODEL_ROLES)
    assert result['market']['answer']['choice'] in calls[0]['questions']['market']['criteria']
    assert result['market']['evidence_ids'] == ['candles:TEST', 'benchmark:SPY']
    assert result['technical']['evidence_ids'] == [f'indicator:{name}' for name in sorted(snap['technical']['features'])]
    assert result['news']['evidence_ids'] == result['sentiment']['evidence_ids'] == [n['id'] for n in snap['news']]
    assert result['forecast']['model'] == 'jev-test' and result['forecast']['latency_ms'] == 1.5
    assert result['fundamentals']['status'] == 'unavailable' and result['fundamentals']['answer'] is None
    assert 'point-in-time' in result['fundamentals']['reason'] and result['fundamentals']['model'] is None
    assert result['portfolio']['status'] == 'ok'
    for role in result.values():
        assert {'status', 'answer', 'evidence_ids', 'model', 'latency_ms', 'usage'} <= set(role)


def test_specialist_questions_carry_insufficient_options(monkeypatch):
    captured = {}
    def ask(state, questions, *args):
        captured.update(questions)
        return valid_response(questions)
    monkeypatch.setattr(agents, 'ask_questions', ask)
    agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=5)
    for name, question in captured.items():
        if question['type'] == 'choice':
            assert 'insufficient' in question['criteria'] or 'none' in question['criteria']
            assert 'BUY' not in question['criteria'] and 'SELL' not in question['criteria']
    assert 'five trading sessions' in captured['forecast']['instructions'] and 'unvalidated' in captured['forecast']['instructions']
    assert 'never invent a headline' in captured['news']['instructions']


def test_specialist_calls_overlap_and_finish_in_either_order(monkeypatch):
    started, release, order, threads = threading.Event(), threading.Event(), [], {}
    def slow_ask(state, questions, *args):
        threads['model'] = threading.get_ident()
        started.set()
        release.wait(5)
        order.append('model')
        return valid_response(questions)
    monkeypatch.setattr(agents, 'ask_questions', slow_ask)
    real_portfolio = agents._portfolio_role
    def tracked_portfolio(portfolio):
        threads['portfolio'] = threading.get_ident()
        role = real_portfolio(portfolio)
        order.append('portfolio')
        return role
    monkeypatch.setattr(agents, '_portfolio_role', tracked_portfolio)
    box = {}
    worker = threading.Thread(target=lambda: box.update(result=agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=10)))
    worker.start()
    assert started.wait(5)  # the model call is parked while the stage is still running
    time.sleep(0.05)
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    assert order == ['portfolio', 'model']  # portfolio completed while the model call was still blocked
    assert threads['model'] != threads['portfolio']
    assert set(box['result']) == ROLES and box['result']['portfolio']['status'] == 'ok'


def test_results_do_not_depend_on_completion_order(monkeypatch):
    monkeypatch.setattr(agents, 'ask_questions', lambda state, questions, *args: valid_response(questions))
    real_portfolio = agents._portfolio_role
    monkeypatch.setattr(agents, '_portfolio_role', lambda p: (time.sleep(0.15), real_portfolio(p))[1])
    result = agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=5)
    assert result['market']['status'] == 'ok' and result['portfolio']['status'] == 'ok'


def test_bounded_timeout_marks_unfinished_roles_error(monkeypatch):
    started = threading.Event()
    def slow_ask(state, questions, *args):
        started.set()
        time.sleep(0.6)
        return valid_response(questions)
    monkeypatch.setattr(agents, 'ask_questions', slow_ask)
    began = time.perf_counter()
    result = agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=0.15)
    elapsed = time.perf_counter() - began
    assert started.wait(1) and elapsed < 0.5
    for name in agents.MODEL_ROLES:
        assert result[name]['status'] == 'error' and 'timed out' in result[name]['reason'] and result[name]['answer'] is None
    assert result['portfolio']['status'] == 'ok' and set(result) == ROLES


def test_model_failure_is_never_an_action(monkeypatch):
    def ask(*args):
        raise ValueError('Invalid SystemOne response')
    monkeypatch.setattr(agents, 'ask_questions', ask)
    result = agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=5)
    for name in agents.MODEL_ROLES:
        assert result[name]['status'] == 'error' and result[name]['answer'] is None
        assert result[name].get('answer') not in ('BUY', 'SELL')
    assert result['fundamentals']['status'] == 'unavailable' and result['portfolio']['status'] == 'ok'
    assert set(result) == ROLES


def test_missing_news_is_unavailable_without_a_question(monkeypatch):
    captured = {}
    monkeypatch.setattr(agents, 'ask_questions', lambda state, questions, *args: captured.update(questions) or valid_response(questions))
    result = agents.run_specialists(snapshot(news=None), 'u', 'm', 'k', timeout_s=5)
    assert 'news' not in captured and 'sentiment' not in captured
    assert result['news']['status'] == 'unavailable' and result['sentiment']['status'] == 'unavailable'
    assert result['market']['status'] == 'ok' and set(result) == ROLES


def test_absent_benchmark_keeps_forecast_unavailable(monkeypatch):
    captured = {}
    monkeypatch.setattr(agents, 'ask_questions', lambda state, questions, *args: captured.update(questions) or valid_response(questions))
    result = agents.run_specialists(snapshot(benchmark=None), 'u', 'm', 'k', timeout_s=5)
    assert 'forecast' not in captured
    assert result['forecast']['status'] == 'unavailable' and result['forecast']['answer'] is None
    assert result['market']['status'] == 'ok' and set(result) == ROLES


def test_portfolio_role_is_deterministic_without_model_call(monkeypatch):
    monkeypatch.setattr(agents, 'ask_questions', lambda state, questions, *args: valid_response(questions))
    first = agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=5)['portfolio']
    second = agents.run_specialists(snapshot(), 'u', 'm', 'k', timeout_s=5)['portfolio']
    assert first == second and first['model'] is None and first['latency_ms'] is None and first['usage'] is None
    assert first['answer']['provided'] == {'position': 0, 'equity': 1000.0}
    assert 'quantity' in first['answer']['missing'] and 'equity' not in first['answer']['missing']
    empty = agents.run_specialists(snapshot(portfolio=None), 'u', 'm', 'k', timeout_s=5)['portfolio']
    assert empty['status'] == 'unavailable' and empty['answer'] is None


def test_concurrency_limit_configures_the_pool(monkeypatch):
    seen = []
    class Pool(agents.ThreadPoolExecutor):
        def __init__(self, max_workers=None, **kwargs):
            seen.append(max_workers)
            super().__init__(max_workers=max_workers, **kwargs)
    monkeypatch.setattr(agents, 'ThreadPoolExecutor', Pool)
    monkeypatch.setattr(agents, 'ask_questions', lambda state, questions, *args: valid_response(questions))
    agents.run_specialists(snapshot(), 'u', 'm', 'k', concurrency=1, timeout_s=5)
    assert seen == [1]
