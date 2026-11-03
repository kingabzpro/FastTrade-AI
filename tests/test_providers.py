import pytest
from fasttrade import decisions, cli, web

@pytest.mark.parametrize(('provider','key_name','url','model'), [
    ('liquid', 'LIQUID_API_KEY', 'https://api.liquid.ai/decisions/v1/systemone', 'd1-ultra'),
    ('vllm', 'VLLM_SR_API_KEY', 'https://decision.vllm-sr.ai/v1/systemone', 'vllm-sr/d3-ultra'),
    ('subq', 'SUBQ_API_KEY', 'https://api.subq.ai/v1/systemone', 'subq-snap-1.1'),
    ('baseten', 'BASETEN_API_KEY', 'https://inference.baseten.co/v1/systemone', 'decision-models/baseten-mammoth-27b-latest'),
])
def test_provider_configuration(monkeypatch, provider, key_name, url, model):
    monkeypatch.setenv(key_name, 'test-key')
    assert decisions.provider_config(provider) == (url, model, 'test-key')


def test_selection_is_available_in_cli_and_api():
    assert cli.parser().parse_args(['analyze', 'SPY', '--decision-provider', 'liquid']).decision_provider == 'liquid'
    assert web.AnalysisRequest(symbol='SPY', decision_provider='baseten').decision_provider == 'baseten'
    with pytest.raises(ValueError):
        web.AnalysisRequest(symbol='SPY', decision_provider='unknown')


def test_provider_missing_key(monkeypatch):
    monkeypatch.delenv('SUBQ_API_KEY', raising=False)
    with pytest.raises(ValueError, match='SUBQ_API_KEY'):
        decisions.provider_config('subq')

@pytest.mark.parametrize('provider', ['liquid', 'vllm', 'subq', 'baseten'])
def test_analysis_routes_to_selected_provider_without_portfolio(monkeypatch, provider, tmp_path):
    from test_core import candles
    key_name, url, model = decisions.PROVIDERS[provider]
    monkeypatch.setenv(key_name, 'secret')
    path = tmp_path / 'bars.csv'
    candles().to_csv(path, index=False)
    def predict(state, actual_url, actual_model, key):
        assert (actual_url, actual_model, key) == (url, model, 'secret')
        assert 'position' not in state and 'equity' not in state
        return {'action':'HOLD', 'confidence':.6, 'probabilities':{'BUY':.2,'HOLD':.6,'SELL':.2}, 'model':model, 'latency_us':1}
    monkeypatch.setattr(cli, 'predict', predict)
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z', '--decision-provider', provider, '--position', '2'])
    result = cli.run(args)
    assert result['decision']['provider'] == provider
    assert result['actionable_signal'] is None


def test_non_jev_calibration_is_rejected_before_request(monkeypatch, tmp_path):
    from test_core import candles
    path = tmp_path / 'bars.csv'
    candles().to_csv(path, index=False)
    monkeypatch.setattr(cli, 'predict', lambda *args: pytest.fail('must not call model'))
    args = cli.parser().parse_args(['analyze', 'TEST', '--csv', str(path), '--as-of', '2026-01-02T19:30:00Z', '--decision-provider', 'liquid', '--calibration', 'jev.json'])
    with pytest.raises(ValueError, match='only supported with Jev'):
        cli.run(args)
