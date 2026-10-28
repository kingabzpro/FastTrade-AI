from datetime import datetime
import math
from rich.text import Text


def render_chart(candles, interval_seconds, window=30, offset=0, overlay='ema_9', marker=None, compact=False):
    """Render completed OHLC bars and volume; gaps are shown as spaces."""
    if not candles:
        return Text('No completed candles. Run an analysis to load a chart.')
    end = len(candles) - max(0, min(offset, len(candles) - 1))
    bars = candles[max(0, end - window):end]
    columns = []
    previous = None
    for bar in bars:
        stamp = datetime.fromisoformat(bar['timestamp'].replace('Z', '+00:00'))
        if previous and (stamp - previous).total_seconds() > interval_seconds * 1.5:
            columns.append(None)
        columns.append(bar)
        previous = stamp
    overlay_values = [b[overlay] for b in bars if overlay and b.get(overlay) is not None and math.isfinite(b[overlay])]
    low = min([b['low'] for b in bars] + overlay_values)
    high = max([b['high'] for b in bars] + overlay_values)
    span = max(high - low, 1e-12)
    levels = 4 if compact else 6
    def row(value):
        return max(0, min(levels - 1, round((high - value) / span * (levels - 1))))
    output = Text(f'OHLC  {high:.2f} high / {low:.2f} low\n', style='bold cyan')
    for y in range(levels):
        output.append(f'{high - span * y / (levels - 1):>8.2f} ', style='dim')
        for bar in columns:
            if bar is None:
                output.append(' ', style='dim')
                continue
            o, c = row(bar['open']), row(bar['close'])
            h, l = row(bar['high']), row(bar['low'])
            char = '█' if min(o, c) <= y <= max(o, c) else ('│' if h <= y <= l else ' ')
            val = bar.get(overlay) if overlay else None
            if val is not None and math.isfinite(val) and row(val) == y:
                output.append('•', style='cyan')
            else:
                output.append(char, style='green' if bar['close'] >= bar['open'] else 'red')
        output.append('\n')
    peak = max((b['volume'] for b in bars), default=0)
    blocks = '▁▂▃▄▅▆▇█'
    output.append('     VOL ', style='dim')
    for bar in columns:
        output.append(' ' if bar is None or not peak else blocks[min(7, int(bar['volume'] / peak * 7))], style='blue')
    last = bars[-1]
    if not compact:
        output.append(f"\n{bars[0]['timestamp'][5:16].replace('T', ' ')} to {last['timestamp'][5:16].replace('T', ' ')} UTC")
        output.append(f"\nO {last['open']:.2f}  H {last['high']:.2f}  L {last['low']:.2f}  C {last['close']:.2f}")
        if overlay:
            output.append(f'  • {overlay.upper()}', style='cyan')
    if marker and end == len(candles):
        output.append(f'\nLatest model preference: {marker} (not an executed trade)', style='yellow')
    if any(b is None for b in columns):
        output.append('\nGAP = time discontinuity (may be a closed session)', style='yellow')
    return output
