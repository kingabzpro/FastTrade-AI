"""Prepare a fixed IEX snapshot for the September–October 2026 comparison."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os

import httpx
import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT.parent / 'backtest-2026-09-28'


def hourly(minutes, sessions):
    times = pd.to_datetime(minutes['t'], utc=True)
    frame = minutes.assign(timestamp=times).set_index('timestamp').sort_index()
    if frame.index.has_duplicates:
        raise ValueError('Duplicate minute timestamps')
    rows = []
    delays = []
    for session in sessions.itertuples():
        if session.open >= pd.Timestamp('2026-10-28', tz='America/New_York'):
            break
        day = frame.loc[(frame.index >= session.open) & (frame.index < session.close)]
        for start in pd.date_range(session.open, session.close, freq='h', inclusive='left'):
            group = day.loc[(day.index >= start) & (day.index < min(start + pd.Timedelta(hours=1), session.close))]
            if group.empty:
                raise ValueError(f'No IEX trades in interval starting {start}')
            if start == session.open and session.open.date() in execution_dates:
                delay = int((group.index[0] - start).total_seconds() / 60)
                delays.append({'session': start.isoformat(), 'first_trade_bar': group.index[0].isoformat(), 'delay_minutes': delay})
            rows.append({'timestamp': start.isoformat(), 'open': group.o.iloc[0], 'high': group.h.max(), 'low': group.l.min(), 'close': group.c.iloc[-1], 'volume': group.v.sum()})
    return pd.DataFrame(rows), delays


def fetch(symbol):
    headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY'], 'APCA-API-SECRET-KEY': os.environ['ALPACA_SECRET_KEY']}
    params = {'timeframe': '1Min', 'feed': 'iex', 'adjustment': 'raw', 'start': sessions.open.iloc[0].isoformat(), 'end': sessions.close.iloc[-2].isoformat(), 'limit': 10000, 'sort': 'asc'}
    bars = []
    with httpx.Client(timeout=30) as client:
        while True:
            response = client.get(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars', headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            bars.extend(data.get('bars') or [])
            token = data.get('next_page_token')
            if not token:
                break
            if token == params.get('page_token'):
                raise ValueError(f'{symbol}: repeated pagination token')
            params['page_token'] = token
    if not bars:
        raise ValueError(f'{symbol}: no bars')
    frame, delays = hourly(pd.DataFrame(bars), sessions)
    (ROOT / f'{symbol}.csv').write_text(frame.to_csv(index=False), encoding='utf-8', newline='')
    return symbol, len(bars), len(frame), delays


if __name__ == '__main__':
    if (ROOT / 'manifest.json').exists():
        raise SystemExit('Snapshot already exists; choose a new directory to avoid overwriting it')
    load_dotenv()
    if not os.getenv('ALPACA_API_KEY') or not os.getenv('ALPACA_SECRET_KEY'):
        raise SystemExit('Alpaca credentials are missing')
    symbols = (ORIGINAL / 'symbols.txt').read_text(encoding='utf-8').strip().split(',')
    sessions = pd.read_csv(ORIGINAL / 'sessions.csv')
    sessions['open'] = pd.to_datetime(sessions['open'], utc=True)
    sessions['close'] = pd.to_datetime(sessions['close'], utc=True)
    execution_dates = {pd.Timestamp(x).date() for x in ('2026-10-05', '2026-10-12', '2026-10-19', '2026-10-26')}
    for name in ('sessions.csv', 'dividends.csv', 'symbols.txt'):
        (ROOT / name).write_bytes((ORIGINAL / name).read_bytes())
    openings = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for symbol, minutes, hours, delays in pool.map(fetch, symbols + ['SPY']):
            openings[symbol] = delays
            print(f'{symbol}: {minutes} minute bars -> {hours} hourly bars; max execution-open delay {max(d["delay_minutes"] for d in delays)}m', flush=True)
    (ROOT / 'opening-delays.json').write_text(json.dumps(openings, indent=2) + '\n', encoding='utf-8')
    files = ['sessions.csv', 'dividends.csv', 'opening-delays.json'] + [f'{symbol}.csv' for symbol in symbols + ['SPY']]
    manifest = {'price_basis': 'unadjusted', 'corporate_actions': 'cash_dividends_only', 'source': 'Alpaca IEX raw minute bars, aggregated to NYSE session-aligned hourly bars; gaps have no trades', 'calendar_source': 'NYSE sessions from existing backtest', 'corporate_actions_source': 'Existing 2026-09-28 Yahoo dataset; not independently verified', 'retrieved_at': datetime.now(timezone.utc).isoformat(), 'sha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
