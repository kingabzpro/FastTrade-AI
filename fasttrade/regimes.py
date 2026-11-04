"""Point-in-time retrieval of historical feature snapshots and their outcomes."""
import math
import re
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .calibration import build_ledger
from .indicators import features as calculate_features
from .market import validate_candles


def _timestamp(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('Timestamp must include a timezone')
        return stamp.tz_convert('UTC')
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Timestamp must include a timezone') from exc


def _embedding(features):
    try:
        values = [float(features[key]) for key in ('return_1', 'return_3', 'return_5', 'rsi_14', 'vwap_delta', 'volume_ratio')]
        if not all(math.isfinite(value) for value in values) or values[-1] <= 0 or not 0 <= values[3] <= 100:
            raise ValueError('Invalid regime features')
        return [max(-10, min(10, value)) for value in (values[0] * 100, values[1] * 100, values[2] * 100,
                                                           (values[3] - 50) / 25, values[4] * 100, math.log(values[5]))]
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError('Invalid regime features') from exc


def build_corpus(report_path, data_dir):
    root = Path(data_dir)
    report_bytes = Path(report_path).read_bytes()
    report = json.loads(report_bytes)
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    if not isinstance(report, dict) or report.get('dataset_manifest_sha256') != manifest_hash:
        raise ValueError('Backtest report does not match dataset manifest')
    manifest = json.loads(manifest_bytes)
    if (not isinstance(manifest, dict) or manifest.get('price_basis') != 'unadjusted'
            or not isinstance(manifest.get('sha256'), dict)):
        raise ValueError('Invalid dataset manifest')
    retrieved = None
    if 'retrieved_at' in manifest:
        try:
            retrieved = _timestamp(manifest['retrieved_at'])
        except ValueError as exc:
            raise ValueError('Dataset retrieved_at must include a timezone') from exc
        if retrieved > pd.Timestamp.now(tz='UTC'):
            raise ValueError('Dataset was retrieved in the future')
    snapshot = {}
    for name, expected in manifest['sha256'].items():
        if not isinstance(name, str) or Path(name).name != name or not re.fullmatch(r'[A-Za-z0-9.^_-]+\.csv', name):
            raise ValueError('Invalid dataset manifest filename')
        data = (root / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f'Dataset checksum mismatch: {name}')
        snapshot[name] = data
    report_hash = hashlib.sha256(report_bytes).hexdigest()
    with tempfile.TemporaryDirectory(prefix='fasttrade-regimes-') as directory:
        for name, data in snapshot.items():
            (Path(directory) / name).write_bytes(data)
        rows, _ = build_ledger(report, directory)
        frames = {}
        records = []
        for row in rows:
            symbol = row['symbol']
            if not math.isfinite(row['move']) or row['move'] < -1:
                raise ValueError('Invalid regime outcome')
            if symbol not in frames:
                frames[symbol] = pd.read_csv(Path(directory) / f'{symbol}.csv')
            decision = _timestamp(row['decision_time'])
            frame = validate_candles(frames[symbol], '60m', decision, float('inf'))
            indicators = calculate_features(frame, 'America/New_York')
            _embedding(indicators)
            source_id = hashlib.sha256(f"{report_hash}:{symbol}:{decision.isoformat()}".encode()).hexdigest()
            records.append({'source_id': source_id, 'symbol': symbol, 'decision_time': decision.isoformat(),
                            'outcome_available_at': _timestamp(row['outcome_available_at']).isoformat(),
                            'features': {key: indicators[key] for key in ('return_1', 'return_3', 'return_5', 'rsi_14', 'vwap_delta', 'volume_ratio')},
                            'outcome_return': row['move']})
    created = pd.Timestamp(datetime.now(timezone.utc))
    if any(_timestamp(row['outcome_available_at']) > created for row in records):
        raise ValueError('Regime outcomes are not yet available')
    if retrieved is not None and any(_timestamp(row['outcome_available_at']) > retrieved for row in records):
        raise ValueError('Regime outcomes postdate dataset retrieval')
    if len({row['source_id'] for row in records}) != len(records):
        raise ValueError('Duplicate regime source IDs')
    for row in records:
        row['available_at'] = created.isoformat()
    return {'created_at': created.isoformat(), 'timeframe': '60m', 'source_report_sha256': report_hash,
            'dataset_manifest_sha256': manifest_hash, 'records': records}


def retrieve(corpus, features, as_of, limit=3, timeframe='60m'):
    cutoff = _timestamp(as_of)
    if not isinstance(corpus, dict):
        raise ValueError('Invalid regime corpus')
    created = _timestamp(corpus.get('created_at'))
    if created > cutoff:
        return None
    if not isinstance(corpus.get('records'), list):
        raise ValueError('Invalid regime corpus')
    if corpus.get('timeframe') != timeframe:
        raise ValueError('Regime corpus timeframe does not match analysis')
    if type(limit) is not int or not 1 <= limit <= 3:
        raise ValueError('Regime retrieval limit must be between 1 and 3')
    query = _embedding(features)
    matches = []
    sources = set()
    for row in corpus['records']:
        if not isinstance(row, dict):
            raise ValueError('Invalid regime record')
        publication = _timestamp(row.get('available_at'))
        if publication > cutoff:
            continue
        outcome_time = _timestamp(row.get('outcome_available_at'))
        if outcome_time > cutoff:
            continue
        decision = _timestamp(row.get('decision_time'))
        source_id, symbol = row.get('source_id'), row.get('symbol')
        if outcome_time > created or publication > created:
            raise ValueError('Regime record is newer than corpus creation')
        if (decision >= outcome_time or decision > publication or not isinstance(source_id, str) or not re.fullmatch(r'[A-Za-z0-9:._-]{1,160}', source_id)
                or not isinstance(symbol, str) or not re.fullmatch(r'[A-Z0-9.^=\-]{1,20}', symbol)):
            raise ValueError('Invalid regime record')
        if source_id in sources:
            raise ValueError('Duplicate regime source ID')
        sources.add(source_id)
        try:
            outcome = float(row['outcome_return'])
            if not math.isfinite(outcome) or outcome < -1:
                raise ValueError('Invalid regime outcome')
            distance = math.dist(query, _embedding(row['features']))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError('Invalid regime record') from exc
        matches.append((distance, source_id, {'source_id': source_id, 'symbol': symbol, 'decision_time': decision.isoformat(),
                                               'outcome_available_at': outcome_time.isoformat(),
                                               'available_at': created.isoformat(),
                                               'outcome_return': outcome, 'distance': round(distance, 6)}))
    return [item for _, _, item in sorted(matches, key=lambda item: (item[0], item[1]))[:limit]]
