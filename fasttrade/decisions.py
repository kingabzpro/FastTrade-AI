import math
import time
import httpx


def normalize(data, latency_us):
    try:
        answer = data['answers']['action']
        choice = answer['choice']
        probs = answer['probabilities']
        confidence = float(answer['confidence'])
        if answer['type'] != 'choice' or set(probs) != {'BUY', 'HOLD', 'SELL'} or choice not in probs:
            raise ValueError('Invalid decision choices')
        probs = {k: float(v) for k,v in probs.items()}
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in probs.values()) or abs(sum(probs.values()) - 1) > .02 or probs[choice] < max(probs.values()) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('Invalid decision probabilities')
        model = data['model']
        if not isinstance(model, str) or not model:
            raise ValueError('Invalid model')
        usage = data.get('usage')
        if not isinstance(usage, dict) or type(usage.get('input_tokens')) is not int or usage['input_tokens'] < 0 or (usage.get('output_tokens') is not None and (type(usage['output_tokens']) is not int or usage['output_tokens'] < 0)):
            usage = None
        else:
            usage = {'input_tokens': usage['input_tokens'], 'output_tokens': usage.get('output_tokens')}
        return {'action': choice, 'probabilities': probs, 'confidence': confidence, 'model': model, 'latency_us': latency_us, 'usage': usage}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid SystemOne response') from exc


def predict(state, url, model, key):
    payload = {'model': model, 'state': state, 'questions': {'action': {'type': 'choice', 'instructions': 'Which long-only action is supported by this market snapshot?', 'criteria': {'BUY': 'Evidence favors a long entry', 'HOLD': 'No clear trade opportunity', 'SELL': 'Evidence favors reducing or exiting an existing long position'}}}}
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    start = time.perf_counter_ns()
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise ValueError('SystemOne request failed: timeout') from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise ValueError('SystemOne request failed') from exc
    return normalize(data, (time.perf_counter_ns() - start) // 1000)


def _usage(data):
    usage = data.get('usage')
    if not isinstance(usage, dict) or type(usage.get('input_tokens')) is not int or usage['input_tokens'] < 0 or (usage.get('output_tokens') is not None and (type(usage['output_tokens']) is not int or usage['output_tokens'] < 0)):
        return None
    return {'input_tokens': usage['input_tokens'], 'output_tokens': usage.get('output_tokens')}


def _validate_choice(name, answer, question):
    try:
        choice, probs, confidence = answer['choice'], answer['probabilities'], float(answer['confidence'])
        if not isinstance(probs, dict) or set(probs) != set(question['criteria']) or choice not in probs:
            raise ValueError(f'answer {name} does not match its criteria')
        probs = {k: float(v) for k, v in probs.items()}
        if not all(math.isfinite(v) and 0 <= v <= 1 for v in probs.values()) or abs(sum(probs.values()) - 1) > .02 or probs[choice] < max(probs.values()) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError(f'answer {name} has invalid probabilities')
        return {'type': 'choice', 'choice': choice, 'confidence': confidence, 'probabilities': probs}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f'invalid choice answer for {name}') from exc


def _validate_noul(name, answer, question):
    probability = float(answer['probability'])
    if not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError(f'answer {name} probability is outside 0..1')
    return {'type': 'noul', 'probability': probability}


def _validate_score(name, answer, question):
    score = float(answer['score'])
    low, high = question.get('min'), question.get('max')
    if not math.isfinite(score) or (low is not None and score < low) or (high is not None and score > high):
        raise ValueError(f'answer {name} score is outside its range')
    return {'type': 'score', 'score': score}


_VALIDATORS = {'choice': _validate_choice, 'noul': _validate_noul, 'score': _validate_score}


def validate_answer(name, answer, question):
    if not isinstance(question, dict) or question.get('type') not in _VALIDATORS:
        raise ValueError(f'question {name} has an unsupported type')
    if not isinstance(answer, dict) or answer.get('type') != question['type']:
        raise ValueError(f'answer {name} is missing or has the wrong type')
    return _VALIDATORS[question['type']](name, answer, question)


def ask_questions(state: dict, questions: dict, url: str, model: str, key: str | None) -> dict:
    """Send one SystemOne request with several named typed questions; return validated answers, resolved model, latency and usage."""
    payload = {'model': model, 'state': state, 'questions': questions}
    headers = {'Authorization': f'Bearer {key}'} if key else {}
    start = time.perf_counter_ns()
    failure = None
    for _ in range(3):
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise ValueError('SystemOne request failed: timeout') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise ValueError('SystemOne request failed') from exc
        try:
            answers, resolved = data['answers'], data['model']
            if not isinstance(answers, dict) or not isinstance(resolved, str) or not resolved:
                raise ValueError('model or answers missing')
            return {'answers': {name: validate_answer(name, answers.get(name), questions[name]) for name in questions}, 'model': resolved, 'latency_ms': (time.perf_counter_ns() - start) / 1e6, 'usage': _usage(data)}
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            failure = exc
    raise ValueError('Invalid SystemOne response') from failure
