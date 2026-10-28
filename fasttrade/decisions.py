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
        return {'action': choice, 'probabilities': probs, 'confidence': confidence, 'model': model, 'latency_us': latency_us}
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
