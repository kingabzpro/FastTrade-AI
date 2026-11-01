'use strict';

const $ = (id) => document.getElementById(id);
const riskFields = [
  ['position', 'Position · shares', 'Shares of this asset you currently own.'],
  ['equity', 'Account equity', 'Total account value in the instrument’s currency.'],
  ['exposure', 'Current exposure', 'Gross value of your long positions in the instrument’s currency.'],
  ['daily_loss', 'Daily loss', 'Today’s loss as a positive amount in the instrument’s currency.'],
  ['trade_count', 'Trades today', 'Number of trades already made today.'],
  ['quantity', 'Proposed shares', 'Shares to buy or sell; a sale cannot exceed your position.'],
  ['max_position', 'Max position value', 'Maximum value of this asset after a purchase, not a share count.'],
  ['max_exposure', 'Max exposure', 'Maximum gross long exposure after a purchase.'],
  ['max_daily_loss', 'Max daily loss', 'Daily loss at which buy and sell signals are blocked.'],
  ['max_trades', 'Max trades', 'Trade count at which buy and sell signals are blocked.'],
  ['min_confidence', 'Min confidence · 0–1', 'Minimum model confidence for a buy or sell signal.'],
];
for (const [name, label, description] of riskFields) {
  const wrap = document.createElement('label');
  wrap.textContent = label.toUpperCase();
  const input = document.createElement('input');
  input.type = 'number'; input.min = '0'; input.step = ['trade_count', 'max_trades'].includes(name) ? '1' : 'any';
  if (name === 'min_confidence') input.max = '1';
  if (['equity', 'quantity'].includes(name)) {
    input.addEventListener('input', () => input.setCustomValidity(
      input.value !== '' && Number(input.value) <= 0 ? 'Enter a value greater than zero.' : ''
    ));
  }
  input.placeholder = 'Not set'; input.name = name; input.id = `risk-${name}`;
  const help = document.createElement('span');
  help.className = 'risk-help'; help.id = `risk-help-${name}`; help.textContent = description;
  input.setAttribute('aria-describedby', help.id);
  wrap.append(input, help); $('risk-fields').append(wrap);
}

let current = null;
let selectedCandle = null;
let chartLayoutState = null;
const history = [];
const setText = (id, text) => { $(id).textContent = text; };
const number = (value, digits = 2) => Number(value).toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits});
function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false}).format(date) + ' UTC';
}
const noticeIcons = {loading: 'i-spark', warning: 'i-alert', error: 'i-error'};
function showNotice(message, state = '') {
  const notice = $('notice'); notice.className = `notice ${state}`.trim();
  notice.querySelector('.notice-icon use').setAttribute('href', '#' + (noticeIcons[state] || 'i-spark'));
  notice.querySelector('span:last-child').textContent = message;
}
function clearResults() {
  current = null;
  selectedCandle = null;
  chartLayoutState = null;
  $('chart-readout').hidden = true;
  $('chart-window').disabled = true; $('chart-overlay').disabled = true;
  $('overlay-legend').hidden = true;
  $('chart-empty').hidden = false; $('chart-badge').className = 'pill muted';
  setText('chart-badge', 'AWAITING DATA'); setText('chart-symbol', 'Your market, in focus');
  setText('chart-subtitle', 'Completed candles will appear here after analysis.'); setText('chart-price', '');
  $('decision-empty').hidden = false; $('decision-content').hidden = true;
  $('indicators').hidden = true; $('indicator-empty').hidden = false;
  $('evidence').replaceChildren(); $('evidence-empty').hidden = false; $('news-panel').hidden = true;
  $('decision-badge').className = 'pill muted'; setText('decision-badge', 'NOT ANALYZED');
  const context = $('chart').getContext('2d'); context.clearRect(0, 0, $('chart').width, $('chart').height);
}

function render(result, record = true) {
  current = result;
  selectedCandle = null;
  const market = result.market, feature = market.features, decision = result.decision;
  const stale = Boolean(result.stale_preview), sample = result.source === 'Synthetic sample CSV';
  const source = sample ? 'SYNTHETIC SAMPLE' : result.source === 'CSV' ? 'HISTORICAL CSV' : 'ALPACA IEX';
  const time = formatTime(market.candle_timestamp);
  $('chart-badge').className = `pill ${stale ? 'warning' : 'good'}`;
  setText('chart-badge', stale ? 'STALE · RESEARCH ONLY' : source);
  setText('chart-symbol', `${market.symbol} / ${market.timeframe}`);
  setText('chart-subtitle', `${source} · Candle start ${time}`);
  setText('chart-price', number(feature.price));
  $('chart-empty').hidden = true;
  $('chart-window').disabled = false; $('chart-overlay').disabled = false;
  $('decision-empty').hidden = true; $('decision-content').hidden = false;
  $('decision-badge').className = `pill ${stale ? 'warning' : 'good'}`;
  setText('decision-badge', stale ? 'STALE DATA' : 'ANALYSIS READY');
  const action = decision.action;
  $('model-action').className = `action ${action.toLowerCase()}`; setText('model-action', action);
  const probs = decision.probabilities || {};
  setText('top-prob', probs[action] == null ? '—' : `${Math.round(probs[action] * 100)}%`);
  const list = $('probabilities'); list.replaceChildren();
  for (const name of ['BUY', 'HOLD', 'SELL']) {
    if (probs[name] == null) continue;
    const row = document.createElement('div'); row.className = 'prob-row';
    const label = document.createElement('span'); label.textContent = name;
    const track = document.createElement('div'); track.className = 'prob-track';
    const fill = document.createElement('div'); fill.className = `prob-fill ${name.toLowerCase()}`;
    fill.style.width = `${Math.max(0, Math.min(100, probs[name] * 100))}%`; track.append(fill);
    const value = document.createElement('span'); value.className = 'prob-value'; value.textContent = `${Math.round(probs[name] * 100)}%`;
    row.append(label, track, value); list.append(row);
  }
  const comparison = result.rule_comparison;
  const status = !comparison ? 'unavailable' : comparison.agreement;
  $('rule-status').dataset.state = status;
  setText('rule-status', {agree: 'SIGNALS AGREE', disagree: 'SIGNALS DIFFER', no_signal: 'NO RULE SIGNAL', unavailable: 'UNAVAILABLE'}[status] || 'UNAVAILABLE');
  setText('rule-detail', comparison?.signal ? `Indicator rule: ${comparison.signal} · Model: ${action}` : comparison ? 'The indicator rule has no directional signal.' : 'Rule comparison unavailable.');
  const signalOnly = result.actionable_signal == null;
  const blocked = !signalOnly && result.risk_rejections.length > 0 && action !== 'HOLD';
  document.querySelector('.risk-result').classList.toggle('blocked', blocked || stale);
  setText('risk-label', signalOnly ? 'PORTFOLIO' : 'RISK-CHECKED SIGNAL');
  setText('risk-action', signalOnly ? 'SIGNAL ONLY' : blocked ? 'BLOCKED · HOLD' : result.actionable_signal);
  setText('risk-reasons', result.risk_rejections.length ? result.risk_rejections.join(' · ') : signalOnly ? 'Add portfolio and risk settings for a risk-checked signal.' : 'No risk rejections. Signal only; no orders are placed.');
  setText('model-name', decision.model); setText('model-latency', `${number(decision.latency_us / 1000, 0)} ms`);
  const indicators = [
    ['RSI (14)', number(feature.rsi_14, 1), 'Momentum'], ['MACD', number(feature.macd, 3), 'Trend & momentum'],
    ['VWAP DELTA', `${feature.vwap_delta >= 0 ? '+' : ''}${number(feature.vwap_delta * 100)}%`, 'Price vs. VWAP'],
    ['VOLUME RATIO', `${number(feature.volume_ratio)}×`, 'vs. 20-bar average'], ['ATR (14)', number(feature.atr_14, 3), 'Volatility'],
    ['TIMEFRAME', market.timeframe, 'Completed candles'],
  ];
  $('indicators').replaceChildren();
  for (const [label, value, detail] of indicators) {
    const box = document.createElement('div'); box.className = 'indicator';
    const title = document.createElement('span'); title.textContent = label;
    const content = document.createElement('strong'); content.textContent = value;
    const caption = document.createElement('small'); caption.textContent = detail;
    box.append(title, content, caption); $('indicators').append(box);
  }
  $('indicator-empty').hidden = true; $('indicators').hidden = false;
  $('evidence').replaceChildren();
  for (const text of result.indicator_interpretations || []) {
    const item = document.createElement('li'); item.textContent = text; $('evidence').append(item);
  }
  $('evidence-empty').hidden = Boolean(result.indicator_interpretations?.length);
  $('news').replaceChildren(); $('news-panel').hidden = !market.headlines?.length;
  for (const headline of market.headlines || []) {
    const item = document.createElement('li'); item.textContent = headline.title;
    const meta = document.createElement('small'); meta.textContent = `${headline.source} · ${formatTime(headline.published)}`;
    item.append(meta); $('news').append(item);
  }
  if (stale) showNotice('The latest candle is stale. This is a historical research preview; portfolio actions are blocked.', 'warning');
  else if (sample) showNotice('Synthetic sample candles are displayed. The model response is real; this is not live market data.', 'warning');
  else showNotice(`Analysis complete. Candle started ${time}. Model preferences are not trading outcomes.`);
  drawChart();
  if (record) {
    history.unshift(result); if (history.length > 50) history.pop();
    renderHistory();
  }
}

function renderHistory() {
  $('history-empty').hidden = history.length > 0; $('history').hidden = history.length === 0;
  const body = $('history').querySelector('tbody'); body.replaceChildren();
  for (const result of history) {
    const row = document.createElement('tr');
    const first = document.createElement('td'); first.dataset.label = 'Market'; const button = document.createElement('button');
    button.type = 'button'; button.className = 'history-link'; button.textContent = `${result.market.symbol} · ${result.market.timeframe}`;
    button.addEventListener('click', () => { render(result, false); $('chart-panel').scrollIntoView({behavior: 'smooth'}); });
    first.append(button); row.append(first);
    const cells = [['Model', result.decision.action], ['Risk-checked', result.actionable_signal == null ? 'SIGNAL ONLY' : result.risk_rejections.length && result.decision.action !== 'HOLD' ? 'BLOCKED · HOLD' : result.actionable_signal], ['Data time', formatTime(result.market.candle_timestamp)], ['Latency', `${number(result.decision.latency_us / 1000, 0)} ms`]];
    for (const [label, value] of cells) {
      const cell = document.createElement('td'); cell.dataset.label = label; cell.textContent = value; row.append(cell);
    }
    body.append(row);
  }
}

function visibleCandles() {
  return current?.candles?.slice(-Number($('chart-window').value)) || [];
}

function drawChart() {
  const canvas = $('chart'), ctx = canvas.getContext('2d');
  const w = canvas.clientWidth, h = canvas.clientHeight, scale = window.devicePixelRatio || 1;
  if (!w || !h) return;
  canvas.width = Math.round(w * scale); canvas.height = Math.round(h * scale);
  ctx.setTransform(scale, 0, 0, scale, 0, 0); ctx.clearRect(0, 0, w, h);
  const candles = visibleCandles();
  if (!candles.length) { $('chart-readout').hidden = true; return; }
  const overlay = $('chart-overlay').value;
  $('overlay-legend').hidden = overlay === 'none';
  if (overlay !== 'none') setText('overlay-name', overlay === 'vwap' ? 'VWAP' : overlay.replace('_', ' ').toUpperCase());
  const overlayValues = candles.map(c => c[overlay]).filter(v => typeof v === 'number' && Number.isFinite(v));
  const rawLow = Math.min(...candles.map(c => c.low), ...overlayValues);
  const rawHigh = Math.max(...candles.map(c => c.high), ...overlayValues);
  const padding = rawHigh > rawLow ? (rawHigh - rawLow) * .07 : Math.max(Math.abs(rawHigh) * .005, 1e-8);
  const low = rawLow - padding, high = rawHigh + padding, span = high - low;
  const precision = ChartMath.priceDigits(span);
  ctx.font = '10px ui-monospace, monospace';
  const axisWidth = Math.max(62, ctx.measureText(number(high, precision)).width + 12, ctx.measureText(number(low, precision)).width + 12);
  const layout = ChartMath.chartLayout(w, h, candles.length, axisWidth);
  chartLayoutState = layout;
  const {left, plotWidth, top, priceBottom, volumeTop, volumeBottom, step, bodyWidth} = layout;
  const maxVolume = Math.max(...candles.map(c => c.volume), 1);
  const y = value => top + (high - value) / span * (priceBottom - top);
  const x = index => left + step * (index + .5);

  ctx.font = '10px ui-monospace, monospace'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let n = 0; n <= 4; n++) {
    const value = high - span * n / 4, py = y(value);
    ctx.strokeStyle = '#dce3ed'; ctx.lineWidth = .7;
    ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(left + plotWidth, py); ctx.stroke();
    ctx.fillStyle = '#506177'; ctx.fillText(number(value, precision), w - 2, py);
  }
  ctx.strokeStyle = '#dce3ed'; ctx.beginPath(); ctx.moveTo(left, volumeTop - 5); ctx.lineTo(left + plotWidth, volumeTop - 5); ctx.stroke();
  ctx.fillStyle = '#53647a'; ctx.textAlign = 'left'; ctx.textBaseline = 'top'; ctx.fillText('VOLUME', left, volumeTop);

  candles.forEach((c, i) => {
    if (i && ChartMath.hasTimeGap(candles[i - 1].timestamp, c.timestamp, current.market.timeframe)) {
      ctx.save(); ctx.setLineDash([2, 4]); ctx.strokeStyle = '#a56b20'; ctx.beginPath();
      ctx.moveTo(x(i) - step / 2, top); ctx.lineTo(x(i) - step / 2, volumeBottom); ctx.stroke(); ctx.restore();
    }
    const up = c.close >= c.open;
    ctx.strokeStyle = ctx.fillStyle = up ? '#087f5b' : '#bd3d51'; ctx.lineWidth = Math.min(1, step * .7);
    ctx.beginPath(); ctx.moveTo(x(i), y(c.high)); ctx.lineTo(x(i), y(c.low)); ctx.stroke();
    const bodyTop = y(Math.max(c.open, c.close)), bodyBottom = y(Math.min(c.open, c.close));
    ctx.fillRect(x(i) - bodyWidth / 2, bodyTop, bodyWidth, Math.max(1, bodyBottom - bodyTop));
    ctx.fillStyle = up ? '#94cdb5' : '#e1a5ac';
    const volumeHeight = (volumeBottom - volumeTop - 13) * c.volume / maxVolume;
    ctx.fillRect(x(i) - bodyWidth / 2, volumeBottom - volumeHeight, bodyWidth, volumeHeight);
  });
  if (overlay !== 'none') {
    ctx.strokeStyle = '#455fca'; ctx.lineWidth = 1.5; ctx.beginPath();
    let drawing = false;
    candles.forEach((c, i) => {
      const value = c[overlay];
      if (typeof value !== 'number' || !Number.isFinite(value)) { drawing = false; return; }
      const gap = i && ChartMath.hasTimeGap(candles[i - 1].timestamp, c.timestamp, current.market.timeframe);
      if (!drawing || gap) ctx.moveTo(x(i), y(value)); else ctx.lineTo(x(i), y(value));
      drawing = true;
    });
    ctx.stroke();
  }
  ctx.fillStyle = '#506177'; ctx.textBaseline = 'bottom';
  const labels = ChartMath.timeLabels(candles, plotWidth >= 450 ? 3 : 2);
  labels.forEach((label, i) => {
    ctx.textAlign = i === 0 ? 'left' : i === labels.length - 1 ? 'right' : 'center';
    const pos = i === 0 ? left : i === labels.length - 1 ? left + plotWidth : x(label.index);
    ctx.fillText(label.text, pos, h - 3);
  });
  if (selectedCandle !== null) {
    selectedCandle = Math.min(selectedCandle, candles.length - 1);
    ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = '#536ab4bb'; ctx.beginPath();
    ctx.moveTo(x(selectedCandle), top); ctx.lineTo(x(selectedCandle), volumeBottom); ctx.stroke(); ctx.restore();
  }
  const selected = candles[selectedCandle === null ? candles.length - 1 : selectedCandle];
  $('chart-readout').hidden = false;
  setText('candle-time', `CANDLE START ${formatTime(selected.timestamp)}`);
  for (const field of ['open', 'high', 'low', 'close']) setText(`candle-${field}`, number(selected[field], precision));
  setText('candle-volume', number(selected.volume, 0));
  setText('candle-count', `${candles.length} bars shown`);
}

function selectCandle(event) {
  const canvas = $('chart'), candles = visibleCandles();
  if (!candles.length) return;
  const rect = canvas.getBoundingClientRect();
  const layout = chartLayoutState || ChartMath.chartLayout(canvas.clientWidth, canvas.clientHeight, candles.length);
  selectedCandle = ChartMath.candleAtX(event.clientX - rect.left, layout, candles.length);
  drawChart();
}
$('chart').addEventListener('pointermove', event => { if (event.pointerType === 'mouse') selectCandle(event); });
$('chart').addEventListener('pointerdown', selectCandle);
$('chart').addEventListener('pointerleave', () => { selectedCandle = null; drawChart(); });
$('chart').addEventListener('keydown', event => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  const candles = visibleCandles();
  if (!candles.length) return;
  event.preventDefault();
  const index = selectedCandle === null ? candles.length - 1 : selectedCandle;
  selectedCandle = event.key === 'Home' ? 0 : event.key === 'End' ? candles.length - 1 : Math.max(0, Math.min(candles.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
  drawChart();
});
window.addEventListener('resize', drawChart);
$('chart-window').addEventListener('change', () => { selectedCandle = null; drawChart(); });
$('chart-overlay').addEventListener('change', drawChart);
$('chart-window').disabled = true; $('chart-overlay').disabled = true;

function syncNavigation() {
  const destination = ['#chart-panel', '#history-panel'].includes(location.hash) ? location.hash : '#workspace';
  for (const link of document.querySelectorAll('.mobile-nav a[href^="#"]')) {
    const active = link.getAttribute('href') === destination;
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
}
window.addEventListener('hashchange', syncNavigation);
syncNavigation();

$('analysis-form').addEventListener('invalid', event => {
  const settings = document.querySelector('.risk-settings');
  if (settings.contains(event.target)) settings.open = true;
}, true);

$('analysis-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const symbol = $('symbol').value.trim().toUpperCase();
  if (!symbol) { $('symbol').focus(); return; }
  const payload = {symbol, timeframe: $('timeframe').value};
  for (const [name] of riskFields) {
    const raw = $(`risk-${name}`).value.trim();
    if (raw !== '') payload[name] = Number(raw);
  }
  const button = $('analyze'); button.disabled = true; button.querySelector('span').textContent = 'Analyzing…';
  $('analysis-form').setAttribute('aria-busy', 'true');
  const fields = [...$('analysis-form').querySelectorAll('input, select'), ...document.querySelectorAll('.history-link')];
  fields.forEach(field => { field.disabled = true; });
  clearResults(); showNotice('Fetching completed candles and requesting a model decision…', 'loading');
  try {
    const response = await fetch('/api/analyze', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok) {
      const message = typeof data.error === 'string' ? data.error : 'Check your symbol, timeframe, and risk settings.';
      showNotice(`${message} No actionable signal is available.`, 'error');
    } else render(data);
  } catch {
    showNotice('Could not reach the local server. Check that FastTrade is running, then try again.', 'error');
  } finally {
    button.disabled = false; button.querySelector('span').textContent = 'Run analysis';
    fields.forEach(field => { field.disabled = false; });
    $('analysis-form').setAttribute('aria-busy', 'false');
  }
});
