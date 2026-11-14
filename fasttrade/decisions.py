import math
import os
import time
import httpx
from .resilience import CircuitOpen, pooled_post, request


PROVIDERS = {
    'liquid': ('LIQUID_API_KEY', 'https://api.liquid.ai/decisions/v1/systemone', 'd1-ultra'),
    'vllm': ('VLLM_SR_API_KEY', 'https://decision.vllm-sr.ai/v1/systemone', 'vllm-sr/d3-ultra'),
    'subq': ('SUBQ_API_KEY', 'https://api.subq.ai/v1/systemone', 'subq-snap-1.1'),
    'baseten': ('BASETEN_API_KEY', 'https://inference.baseten.co/v1/systemone', 'decision-models/baseten-mammoth-27b-latest'),
}


def provider_config(provider):
    name, url, model = PROVIDERS[provider]
    key = os.getenv(name)
    if not key:
        raise ValueError(f'{name} is missing')
    return url, model, key


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
        response = request(pooled_post, url, service='decision:' + url, credentials=key, retry_transport=False, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
    except httpx.TimeoutException as exc:
        raise ValueError('SystemOne request failed: timeout') from exc
    except (httpx.HTTPError, CircuitOpen, ValueError) as exc:
        raise ValueError('SystemOne request failed') from exc
    decision = normalize(data, (time.perf_counter_ns() - start) // 1000)
    if response.extensions.get('fasttrade_attempts', 1) > 1 and decision['usage'] is not None:
        decision['usage']['output_tokens_last_attempt'] = decision['usage']['output_tokens']
        decision['usage']['output_tokens'] = None
    return decision
