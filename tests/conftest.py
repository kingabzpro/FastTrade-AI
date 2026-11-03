import pytest


@pytest.fixture(autouse=True)
def isolate_provider_settings(monkeypatch):
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-only-key')
    monkeypatch.setenv('SYSTEMONE_URL', 'https://api.typesafe.ai/v1/systemone')
    monkeypatch.setenv('SYSTEMONE_MODEL', 'jev-latest')
    monkeypatch.delenv('ALPACA_API_KEY', raising=False)
    monkeypatch.delenv('ALPACA_SECRET_KEY', raising=False)
    monkeypatch.delenv('FASTTRADE_BATCH_KEYS', raising=False)
    monkeypatch.delenv('FASTTRADE_BATCH_KEY_HASHES', raising=False)
    monkeypatch.delenv('FASTTRADE_REDIS_URL', raising=False)
    monkeypatch.delenv('FASTTRADE_HOST', raising=False)
    monkeypatch.delenv('FASTTRADE_PORT', raising=False)
    monkeypatch.delenv('FASTTRADE_CORS_ORIGINS', raising=False)
