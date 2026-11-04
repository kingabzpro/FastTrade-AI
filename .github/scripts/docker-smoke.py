"""Exercise both Compose profiles without paid provider requests."""
import hashlib
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
import uuid

project = 'fasttrade-smoke-' + uuid.uuid4().hex[:12]
base = ['docker', 'compose', '-p', project, '-f', 'compose.yaml']
private = base + ['-f', 'compose.private.yaml']
env = dict(os.environ, FASTTRADE_PRIVATE_IP='127.0.0.1', FASTTRADE_DOMAIN='localhost',
           FASTTRADE_ACME_IP='127.0.0.1', FASTTRADE_PROXY_PORT='8765',
           FASTTRADE_BATCH_KEY_HASHES='', SYSTEMONE_URL='http://127.0.0.1:1/systemone',
           FASTTRADE_MODEL_PRICES=json.dumps({'jev': {'test': {'input_per_million': '1', 'output_per_million': '2'}}}))
for name in ('TYPESAFE_API_KEY', 'ALPACA_API_KEY', 'ALPACA_SECRET_KEY', 'LIQUID_API_KEY',
             'VLLM_SR_API_KEY', 'SUBQ_API_KEY', 'BASETEN_API_KEY'):
    env[name] = ''


def request(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        return urllib.request.urlopen(req, timeout=10, context=ssl._create_unverified_context())
    except urllib.error.HTTPError as exc:
        return exc


try:
    subprocess.run(base + ['up', '-d', '--wait', '--build'], env=env, check=True)
    with request('http://127.0.0.1:8765/') as response:
        assert response.status == 200 and response.headers['X-Request-ID']
    with request('http://127.0.0.1:8765/healthz') as response:
        assert response.status == 200
    with request('http://127.0.0.1:8765/readyz') as response:
        assert response.status == 200
    uid = subprocess.check_output(base + ['exec', '-T', 'app', 'id', '-u'], env=env, text=True).strip()
    assert uid == '10001'
    prices = subprocess.check_output(base + ['exec', '-T', 'app', 'python', '-c', 'import os; print(os.environ["FASTTRADE_MODEL_PRICES"])'], env=env, text=True)
    assert json.loads(prices) == json.loads(env['FASTTRADE_MODEL_PRICES'])
    container = subprocess.check_output(base + ['ps', '-q', 'app'], env=env, text=True).strip()
    grace = subprocess.check_output(['docker', 'inspect', '--format', '{{.Config.StopTimeout}}', container], env=env, text=True).strip()
    assert int(grace) > 100

    env['FASTTRADE_BATCH_KEY_HASHES'] = json.dumps({hashlib.sha256(b'smoke-key').hexdigest(): {
        'estimate_per_call': '0.01', 'request_cap': '0.10', 'daily_budget': '0.20'}})
    subprocess.run(private + ['up', '-d', '--wait'], env=env, check=True)
    with request('https://localhost/', {'X-Forwarded-For': '127.0.0.1'}) as response:
        assert response.status == 403
    with request('https://localhost/healthz') as response:
        assert response.status == 200
    with request('https://localhost/v1/usage', {'X-API-Key': 'bad-key'}) as response:
        assert response.status == 401
    with request('https://localhost/v1/usage', {'X-API-Key': 'smoke-key'}) as response:
        assert response.status == 200 and response.headers['X-Request-ID']
finally:
    subprocess.run(private + ['down', '--volumes', '--remove-orphans'], env=env, check=False)
