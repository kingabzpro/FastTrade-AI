import math
import time
from concurrent.futures import ThreadPoolExecutor

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


def _vote(state, url, model, key):
    for attempts in range(1, 4):
        try:
            return predict(state, url, model, key), attempts
        except ValueError as exc:
            if str(exc) != 'Invalid SystemOne response' or attempts == 3:
                raise


def predict_samples(state, url, model, key, samples):
    """Run N independent decision requests concurrently and aggregate them.

    The chosen action is the majority vote, ties breaking to the higher mean
    probability. Dispersion is the normalized vote disagreement in 0-1, and
    effective_confidence discounts the mean confidence by it. Every sample is
    returned for the audit trail. Makes N paid model requests; wall time is
    the slowest request, not the sum, because the votes run in parallel.
    """
    if not isinstance(samples, int) or samples < 2:
        raise ValueError('Samples must be an integer of at least 2')
    with ThreadPoolExecutor(max_workers=samples) as pool:
        results = list(pool.map(lambda _: _vote(state, url, model, key), range(samples)))
    collected = [decision for decision, _ in results]
    attempts = sum(attempt for _, attempt in results)
    votes = [d['action'] for d in collected]
    top = max(votes.count(action) for action in set(votes))
    winners = sorted(action for action in set(votes) if votes.count(action) == top)
    probabilities = {k: sum(d['probabilities'][k] for d in collected) / len(collected) for k in ('BUY', 'HOLD', 'SELL')}
    action = winners[0] if len(winners) == 1 else max(winners, key=lambda a: probabilities[a])
    confidence = sum(d['confidence'] for d in collected) / len(collected)
    dispersion = (1 - top / len(collected)) * len(collected) / (len(collected) - 1)
    usages = [d['usage'] for d in collected]
    usage = None if all(u is None for u in usages) else {'input_tokens': sum((u or {}).get('input_tokens') or 0 for u in usages), 'output_tokens': (None if any((u or {}).get('output_tokens') is None for u in usages) else sum(u['output_tokens'] for u in usages))}
    return {'action': action, 'probabilities': probabilities, 'confidence': confidence, 'dispersion': round(dispersion, 6), 'effective_confidence': round(confidence * (1 - dispersion), 6), 'sample_count': len(collected), 'request_attempts': attempts, 'samples': [{'action': d['action'], 'probabilities': d['probabilities'], 'confidence': d['confidence'], 'latency_us': d['latency_us'], 'usage': d['usage']} for d in collected], 'model': collected[0]['model'], 'latency_us': sum(d['latency_us'] for d in collected), 'usage': usage, 'usage_complete': all(u is not None for u in usages)}
