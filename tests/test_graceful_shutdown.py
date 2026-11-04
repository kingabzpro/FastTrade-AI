import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest


@pytest.mark.skipif(os.name == 'nt', reason='SIGTERM graceful shutdown requires a POSIX process')
def test_sigterm_drains_an_inflight_analysis(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class Model(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            started.set()
            if not release.wait(15):
                self.send_error(504)
                return
            body = json.dumps({'model': 'test-model', 'answers': {'action': {'type': 'choice', 'choice': 'BUY', 'confidence': .8, 'probabilities': {'BUY': .7, 'HOLD': .2, 'SELL': .1}}}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    model = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    model_thread = threading.Thread(target=model.serve_forever, daemon=True)
    model_thread.start()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    env = os.environ.copy()
    env.update({'SYSTEMONE_URL': f'http://127.0.0.1:{model.server_port}/', 'SYSTEMONE_MODEL': 'test-model',
                'FASTTRADE_HOST': '127.0.0.1', 'FASTTRADE_BATCH_DATABASE': str(tmp_path / 'batch.sqlite3'),
                'FASTTRADE_BATCH_KEY_HASHES': '{}', 'NO_PROXY': '127.0.0.1,localhost'})
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'fasttrade.web:app', '--host', '127.0.0.1',
                               '--port', str(port), '--timeout-graceful-shutdown', '100'],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 20
        while True:
            try:
                if httpx.get(url + '/api/health', timeout=1, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if server.poll() is not None or time.monotonic() >= deadline:
                pytest.fail('Server did not start')
            time.sleep(.05)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(httpx.post, url + '/api/analyze', json={'symbol': 'TEST', 'sample': True}, timeout=15, trust_env=False)
            assert started.wait(10), 'Analysis did not reach the model'
            server.send_signal(signal.SIGTERM)
            time.sleep(.3)
            assert server.poll() is None, 'Server exited before the model completed'
            release.set()
            response = future.result(timeout=10)
            assert response.status_code == 200
            assert response.json()['model_signal'] == 'BUY'
        assert server.wait(timeout=10) == -signal.SIGTERM
    finally:
        release.set()
        if server.poll() is None:
            server.kill()
            server.wait(timeout=10)
        model.shutdown()
        model.server_close()
        model_thread.join(timeout=5)
