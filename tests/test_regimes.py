import json
from pathlib import Path

import pandas as pd
import pytest

from fasttrade.regimes import retrieve


def sample_features(value=0):
    return {'return_1': value, 'return_3': value, 'return_5': value,
            'rsi_14': 50, 'vwap_delta': 0, 'volume_ratio': 1}


def record(source, decision, available, outcome, feature=0):
    return {'source_id': source, 'symbol': 'TEST', 'decision_time': decision, 'available_at': decision, 'outcome_available_at': available,
            'features': sample_features(feature), 'outcome_return': outcome}


def test_future_outcomes_and_future_mutations_do_not_affect_retrieval():
    corpus = {'created_at': '2026-01-09T00:00:00Z', 'timeframe': '60m', 'records': [
        record('A', '2026-01-02T00:00:00Z', '2026-01-09T00:00:00Z', .1),
        record('B', '2026-01-09T00:00:00Z', '2026-01-16T00:00:00Z', -.9),
    ]}
    cutoff = '2026-01-10T00:00:00Z'
    before = retrieve(corpus, sample_features(), cutoff)
    corpus['records'][1]['outcome_return'] = 999
    corpus['records'][1]['features'] = {'changed': 'future'}
    assert retrieve(corpus, sample_features(), cutoff) == before
    assert [item['source_id'] for item in before] == ['A']
    assert before[0]['outcome_return'] == .1
    assert before[0]['outcome_available_at'] == '2026-01-09T00:00:00+00:00'


def test_future_record_publication_does_not_affect_earlier_lookup():
    corpus = {'created_at': '2026-01-09T00:00:00Z', 'timeframe': '60m', 'records': [
        record('A', '2026-01-02T00:00:00Z', '2026-01-09T00:00:00Z', .1)]}
    cutoff = '2026-01-10T00:00:00Z'
    before = retrieve(corpus, sample_features(), cutoff)
    later = record('B', '2026-01-02T00:00:00Z', '2026-01-09T00:00:00Z', 999)
    later['available_at'] = '2026-01-11T00:00:00Z'
    later['outcome_available_at'] = 'future mutation'
    corpus['records'].append(later)
    assert retrieve(corpus, sample_features(), cutoff) == before


def test_corpus_not_yet_created_is_not_visible():
    corpus = {'created_at': '2026-02-01T00:00:00Z', 'timeframe': '60m', 'records': [record('A', '2026-01-01T00:00:00Z', '2026-01-08T00:00:00Z', .1)]}
    assert retrieve(corpus, sample_features(), '2026-01-15T00:00:00Z') is None
    corpus['records'] = 'future mutation'
    corpus['timeframe'] = '5m'
    assert retrieve(corpus, sample_features(), '2026-01-15T00:00:00Z') is None


@pytest.mark.parametrize('value', ['2026-01-01', 'not-a-date', None])
def test_retrieval_requires_timezone_aware_cutoff(value):
    with pytest.raises(ValueError):
        retrieve({'created_at': '2026-01-01T00:00:00Z', 'timeframe': '60m', 'records': []}, sample_features(), value)


def test_invalid_or_future_ordered_eligible_record_is_rejected():
    bad = record('A', '2026-01-09T00:00:00Z', '2026-01-08T00:00:00Z', .1)
    with pytest.raises(ValueError):
        retrieve({'created_at': '2026-01-10T00:00:00Z', 'timeframe': '60m', 'records': [bad]}, sample_features(), '2026-01-15T00:00:00Z')


def test_retrieval_rejects_mismatched_timeframe():
    corpus = {'created_at': '2026-01-01T00:00:00Z', 'timeframe': '60m', 'records': []}
    with pytest.raises(ValueError, match='timeframe'):
        retrieve(corpus, sample_features(), '2026-01-15T00:00:00Z', timeframe='5m')


def test_build_from_frozen_backtest_is_unavailable_before_corpus_creation():
    from fasttrade.regimes import build_corpus
    folder = Path(__file__).resolve().parents[1] / 'docs' / 'backtest-2026-09-28'
    corpus = build_corpus(folder / 'report-measured.json', folder)
    assert corpus['timeframe'] == '60m'
    assert len(corpus['records']) == 75
    created = pd.Timestamp(corpus['created_at'])
    assert retrieve(corpus, sample_features(), created - pd.Timedelta(seconds=1)) is None
    found = retrieve(corpus, sample_features(), created + pd.Timedelta(seconds=1))
    assert len(found) == 3
    assert all(pd.Timestamp(row['available_at']) <= created + pd.Timedelta(seconds=1) for row in found)


def test_static_corpus_rejects_eligible_outcomes_from_after_creation():
    corpus = {'created_at': '2026-01-01T00:00:00Z', 'timeframe': '60m', 'records': [
        record('A', '2026-01-02T00:00:00Z', '2026-01-09T00:00:00Z', .1)]}
    with pytest.raises(ValueError, match='creation'):
        retrieve(corpus, sample_features(), '2026-01-10T00:00:00Z')


def test_duplicate_eligible_source_ids_are_rejected():
    entry = record('A', '2026-01-02T00:00:00Z', '2026-01-09T00:00:00Z', .1)
    corpus = {'created_at': '2026-01-10T00:00:00Z', 'timeframe': '60m', 'records': [entry, dict(entry)]}
    with pytest.raises(ValueError, match='Duplicate'):
        retrieve(corpus, sample_features(), '2026-01-11T00:00:00Z')


def test_mismatched_manifest_is_rejected_before_creating_corpus(tmp_path):
    from fasttrade.regimes import build_corpus
    (tmp_path / 'manifest.json').write_text('{}', encoding='utf-8')
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'dataset_manifest_sha256': 'wrong'}), encoding='utf-8')
    with pytest.raises(ValueError, match='manifest'):
        build_corpus(report, tmp_path)


def test_future_dataset_retrieval_time_is_rejected(tmp_path):
    import hashlib
    from fasttrade.regimes import build_corpus
    manifest = {'price_basis': 'unadjusted', 'retrieved_at': '2099-01-01T00:00:00Z', 'sha256': {}}
    raw = json.dumps(manifest).encode()
    (tmp_path / 'manifest.json').write_bytes(raw)
    report = tmp_path / 'report.json'
    report.write_text(json.dumps({'dataset_manifest_sha256': hashlib.sha256(raw).hexdigest()}), encoding='utf-8')
    with pytest.raises(ValueError, match='retrieved'):
        build_corpus(report, tmp_path)


def test_builder_rejects_outcomes_after_attested_retrieval_and_accepts_unknown_retrieval(tmp_path):
    import hashlib
    import shutil
    from fasttrade.regimes import build_corpus
    original = Path(__file__).resolve().parents[1] / 'docs' / 'backtest-2026-09-28'
    folder = tmp_path / 'snapshot'
    shutil.copytree(original, folder)
    manifest_path = folder / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    report = json.loads((folder / 'report-measured.json').read_text(encoding='utf-8'))
    report_path = tmp_path / 'report.json'
    def save():
        raw = json.dumps(manifest).encode()
        manifest_path.write_bytes(raw)
        report['dataset_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        report_path.write_text(json.dumps(report), encoding='utf-8')
    manifest['retrieved_at'] = '2026-10-01T00:00:00Z'
    save()
    with pytest.raises(ValueError, match='postdate'):
        build_corpus(report_path, folder)
    del manifest['retrieved_at']
    save()
    assert len(build_corpus(report_path, folder)['records']) == 75


def test_builder_cli_writes_once_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    from fasttrade import cli, regimes
    artifact = tmp_path / 'regimes.json'
    corpus = {'created_at': '2026-01-01T00:00:00+00:00', 'timeframe': '60m', 'records': []}
    monkeypatch.setattr(regimes, 'build_corpus', lambda *args: corpus)
    monkeypatch.setattr(cli.sys, 'argv', ['ft', 'build-regimes', '--report', 'report.json', '--data-dir', '.', '--output', str(artifact)])
    assert cli.main() == 0
    saved = artifact.read_bytes()
    assert json.loads(saved) == corpus
    assert cli.main() == 1
    assert artifact.read_bytes() == saved
    capsys.readouterr()
