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

const suggestedTickers = ['SPY', 'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA'];
const symbolList = () => [...new Set($('symbol').value.trim().toUpperCase().split(/[,\s]+/).filter(Boolean))];
function renderSelectedTickers() {
  const selected = $('selected-tickers'); selected.replaceChildren();
  for (const symbol of symbolList()) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'selected-ticker';
    button.textContent = `${symbol} ×`; button.setAttribute('aria-label', `Remove ${symbol}`);
    button.disabled = $('analysis-form').getAttribute('aria-busy') === 'true';
    button.addEventListener('click', () => {
      $('symbol').value = symbolList().filter(item => item !== symbol).join(', ');
      renderSelectedTickers(); $('symbol').focus();
    });
    selected.append(button);
  }
}
$('symbol').addEventListener('input', renderSelectedTickers);
renderSelectedTickers();
function addTicker(ticker) {
  const input = $('symbol');
  const list = input.value.trim().toUpperCase().split(/[,\s]+/).filter(Boolean);
  if (!list.includes(ticker) && list.length >= 5) { showNotice('Choose up to five tickers for one analysis.', 'error'); return; }
  if (!list.includes(ticker)) list.push(ticker);
  input.value = list.slice(0, 5).join(', ');
  renderSelectedTickers();
}
for (const ticker of suggestedTickers) {
  const chip = document.createElement('button');
  chip.type = 'button'; chip.className = 'ticker-chip'; chip.textContent = ticker;
  chip.setAttribute('aria-label', `Add ${ticker} to the symbol field`);
  chip.addEventListener('click', () => { addTicker(ticker); $('symbol').focus(); });
  $('ticker-chips').append(chip);
}

let current = null;
let selectedCandle = null;
let chartLayoutState = null;
const history = [];
const results = new Map();
const failures = new Map();
const runningSymbols = new Set();
const lastPrices = new Map();
let requestedSymbols = [];
let activeSymbol = null;
const setText = (id, text) => { $(id).textContent = text; };
const historyKey = 'fasttrade-history';
let savedHistory = [];
let historyReadable = true;
const pick = (object, keys) => Object.fromEntries(keys.filter(key => object?.[key] !== undefined).map(key => [key, object[key]]));
function historySnapshot(result) {
  const market = pick(result.market, ['symbol', 'timeframe', 'candle_timestamp']);
  market.features = pick(result.market.features, ['price', 'rsi_14', 'macd', 'vwap_delta', 'volume_ratio', 'atr_14']);
  market.headlines = (result.market.headlines || []).map(item => pick(item, ['title', 'source', 'published']));
  const decision = pick(result.decision, ['action', 'model', 'provider', 'latency_us']);
  decision.probabilities = pick(result.decision.probabilities, ['BUY', 'HOLD', 'SELL']);
  return {...pick(result, ['source', 'stale_preview', 'actionable_signal', 'risk_rejections', 'indicator_interpretations']), market, decision,
    rule_comparison: result.rule_comparison ? pick(result.rule_comparison, ['signal', 'agreement']) : null,
    candles: (result.candles || []).map(item => pick(item, ['timestamp', 'open', 'high', 'low', 'close', 'volume', 'ema_9', 'ema_21', 'ema_50', 'vwap']))};
}
function saveHistory(entries = savedHistory) {
  if (!historyReadable) { setText('history-storage-status', 'Stored history is unavailable; clear it to start again.'); return false; }
  const kept = entries.slice(0, 50);
  let data = JSON.stringify({version: 1, enabled: $('history-save').checked, entries: kept}, (key, value) => key.startsWith('_') ? undefined : value);
  while (new TextEncoder().encode(data).length > 2 * 1024 * 1024 && kept.length > 1) {
    kept.pop(); data = JSON.stringify({version: 1, enabled: $('history-save').checked, entries: kept});
  }
  if (new TextEncoder().encode(data).length > 2 * 1024 * 1024) {
    setText('history-storage-status', 'This analysis is too large to save; previous saved history is unchanged.'); return false;
  }
  try {
    localStorage.setItem(historyKey, data); savedHistory = kept;
    setText('history-storage-status', kept.length < entries.length ? 'Older saved analyses were removed to fit the storage limit.' : '');
    return true;
  } catch { setText('history-storage-status', 'Browser storage is full or unavailable; history was not saved.'); return false; }
}
try {
  const stored = JSON.parse(localStorage.getItem(historyKey) || 'null');
  if (stored) {
    if (stored.version !== 1 || !Array.isArray(stored.entries) || stored.entries.some(item => !item?.market?.features || !item?.decision || !Array.isArray(item.risk_rejections) || !Array.isArray(item.candles))) throw new Error('Invalid history');
    savedHistory = stored.entries.slice(0, 50);
    $('history-save').checked = stored.enabled === true;
    if (stored.enabled) history.push(...savedHistory);
  }
} catch { historyReadable = false; setText('history-storage-status', 'Stored history is unavailable or invalid; clear it to start again.'); }
$('history-save').addEventListener('change', () => {
  if ($('history-save').checked) {
    for (const entry of savedHistory) if (!history.includes(entry)) history.push(entry);
    saveHistory(history);
  } else saveHistory();
  renderHistory();
});
$('history-clear').addEventListener('click', () => {
  try {
    localStorage.removeItem(historyKey); historyReadable = true; savedHistory = []; history.length = 0;
    if ($('history-save').checked) saveHistory(); else setText('history-storage-status', '');
    renderHistory();
  } catch { setText('history-storage-status', 'Browser storage is unavailable; saved history could not be cleared.'); }
});
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
async function loadMarketOverview() {
  try {
    const response = await fetch('/api/market-overview');
    if (!response.ok) throw new Error('Market overview unavailable');
    const spyOverview = await response.json();
    const change = spyOverview.change_pct;
    const news = spyOverview.news?.[0];
    setText('summary-price', Number.isFinite(spyOverview.price) ? number(spyOverview.price) : 'Unavailable');
    setText('summary-change', Number.isFinite(change) ? `${change > 0 ? 'Up +' : change < 0 ? 'Down ' : 'Unchanged '}${number(change)}%` : 'Unavailable');
    $('summary-change').className = Number.isFinite(change) ? change > 0 ? 'up-value' : change < 0 ? 'down-value' : '' : '';
    setText('summary-news', news?.title || (spyOverview.news_status === 'available' ? 'No recent headlines' : 'News unavailable'));
    $('summary-news').title = news ? `${news.source} · ${formatTime(news.published)}` : $('summary-news').textContent;
    $('price-stale').hidden = !spyOverview.stale;
  } catch {
    setText('summary-price', 'Unavailable'); setText('summary-change', 'Unavailable');
    setText('summary-news', 'News unavailable');
  }
}
async function loadSpySignals() {
  try {
    const response = await fetch('/api/spy-signals', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'});
    if (!response.ok) throw new Error('SPY signals unavailable');
    const spySignals = await response.json();
    $('rule-stale').hidden = !spySignals.stale;
    $('model-stale').hidden = !spySignals.stale;
    for (const [id, value] of [['summary-rule', spySignals.deterministic], ['summary-decision', spySignals.model]]) {
      const known = ['BUY', 'HOLD', 'SELL'].includes(value);
      setText(id, known ? value : 'Unavailable');
      $(id).className = known ? `signal-${value.toLowerCase()}` : '';
    }
  } catch {
    setText('summary-rule', 'Unavailable'); setText('summary-decision', 'Unavailable');
  }
}
loadMarketOverview();
loadSpySignals();

function clearResults() {
  current = null;
  selectedCandle = null;
  chartLayoutState = null;
  activeSymbol = null;
  $('batch-results').hidden = true;
  const paperStatus = $('paper-status'); paperStatus.textContent = ''; paperStatus.className = 'paper-status';
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
  renderPaper();
}

function render(result, record = true, replay = false) {
  current = result;
  selectedCandle = null;
  if (replay) { activeSymbol = null; $('batch-results').hidden = true; }
  else if (results.has(result.market.symbol)) activeSymbol = result.market.symbol;
  if (!replay && Number.isFinite(result.market.features?.price)) lastPrices.set(result.market.symbol, result.market.features.price);
  const market = result.market, feature = market.features, decision = result.decision;
  const stale = Boolean(result.stale_preview), sample = result.source === 'Synthetic sample CSV';
  const source = sample ? 'SYNTHETIC SAMPLE' : result.source === 'CSV' ? 'HISTORICAL CSV' : result.source === 'Yahoo Finance' ? 'YAHOO FINANCE' : 'ALPACA IEX';
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
  setText('decision-badge', replay ? 'PREVIOUS RESULT' : stale ? 'STALE DATA' : 'ANALYSIS READY');
  const action = decision.action;
  $('model-action').className = `action ${action.toLowerCase()}`; setText('model-action', action);
  const probs = decision.probabilities || {};
  setText('top-prob', probs[action] == null ? '-' : `${Math.round(probs[action] * 100)}%`);
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
  setText('risk-label', signalOnly ? 'PORTFOLIO' : replay ? 'PREVIOUS RISK-CHECKED SIGNAL' : 'RISK-CHECKED SIGNAL');
  const riskValue = signalOnly ? 'SIGNAL ONLY' : blocked ? 'BLOCKED · HOLD' : result.actionable_signal;
  setText('risk-action', riskValue);
  $('risk-action').className = blocked ? 'warn' : ['BUY', 'SELL'].includes(riskValue) ? riskValue.toLowerCase() : signalOnly ? '' : 'flat';
  setText('risk-reasons', result.risk_rejections.length ? result.risk_rejections.join(' · ') : signalOnly ? 'Add portfolio and risk settings for a risk-checked signal.' : 'No risk rejections. Signal only; no orders are placed.');
  setText('model-name', `${decision.provider || 'jev'} / ${decision.model}`); setText('model-latency', `${number(decision.latency_us / 1000, 0)} ms`);
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
  const freshNotice = stale ? ['The latest candle is stale. This is a historical research preview; portfolio actions are blocked.', 'warning']
    : sample ? ['Synthetic sample candles are displayed. The model response is real; this is not live market data.', 'warning']
    : [`Analysis complete. Candle started ${time}. Model preferences are not trading outcomes.`, 'success'];
  if (record) { result._notice = freshNotice; showNotice(freshNotice[0], freshNotice[1]); }
  else if (result._notice) showNotice(result._notice[0], result._notice[1]);
  else showNotice(`Previous analysis from ${time}, source ${result.source}. No new model request was made; this is not a current signal.`, 'warning');
  drawChart();
  if (record) {
    try {
      localStorage.setItem('fasttrade-last-analysis', JSON.stringify({symbol: market.symbol, timeframe: market.timeframe,
        action: decision.action, candle_timestamp: market.candle_timestamp, source: result.source}));
    } catch { /* Storage may be unavailable. */ }
    history.unshift(historySnapshot(result)); if (history.length > 50) history.pop();
    if ($('history-save').checked) saveHistory(history);
    renderHistory();
  }
  renderPaper();
  renderPortfolio();
  if (!replay) renderTabs();
}

function badge(value) {
  const span = document.createElement('span');
  const kind = value === 'BUY' ? 'good' : value === 'SELL' ? 'bad' : String(value).startsWith('BLOCKED') ? 'warn' : value === 'HOLD' ? 'flat' : 'muted';
  span.className = `result-badge ${kind}`;
  span.textContent = value;
  return span;
}

function renderHistory() {
  $('history-empty').hidden = history.length > 0; $('history').hidden = history.length === 0;
  const body = $('history').querySelector('tbody'); body.replaceChildren();
  for (const result of history) {
    const row = document.createElement('tr');
    const first = document.createElement('td'); first.dataset.label = 'Market'; const button = document.createElement('button');
    button.type = 'button'; button.className = 'history-link'; button.textContent = `${result.market.symbol} · ${result.market.timeframe}`;
    button.addEventListener('click', () => { location.hash = '#chart-panel'; syncNavigation(); result._replay = true; render(result, false, true); $('chart-panel').scrollIntoView(); });
    first.append(button); row.append(first);
    const cells = [['Model', badge(result.decision.action)], ['Risk-checked', badge(result.actionable_signal == null ? 'SIGNAL ONLY' : result.risk_rejections.length && result.decision.action !== 'HOLD' ? 'BLOCKED · HOLD' : result.actionable_signal)], ['Data time', formatTime(result.market.candle_timestamp)], ['Source', result.source], ['Latency', `${number(result.decision.latency_us / 1000, 0)} ms`]];
    for (const [label, value] of cells) {
      const cell = document.createElement('td'); cell.dataset.label = label;
      if (typeof value === 'string') cell.textContent = value; else cell.append(value);
      row.append(cell);
    }
    const actions = document.createElement('td'); actions.dataset.label = 'Actions';
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'history-delete'; remove.textContent = 'Delete';
    remove.setAttribute('aria-label', `Delete ${result.market.symbol} analysis from ${formatTime(result.market.candle_timestamp)}`);
    remove.addEventListener('click', () => {
      const remaining = savedHistory.filter(item => item !== result);
      if (remaining.length !== savedHistory.length && !saveHistory(remaining)) return;
      history.splice(history.indexOf(result), 1); renderHistory();
    });
    actions.append(remove); row.append(actions); body.append(row);
  }
}
renderHistory();

/* Result tabs: one tab per ticker requested in the last run. */
function renderTabs() {
  const tabs = $('result-tabs');
  const symbols = requestedSymbols;
  $('batch-results').hidden = symbols.length < 2;
  setText('batch-progress', `${results.size + failures.size} of ${symbols.length} complete`);
  const focused = document.activeElement?.dataset?.symbol;
  tabs.replaceChildren();
  for (const symbol of symbols) {
    const entry = results.get(symbol);
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.dataset.symbol = symbol;
    tab.className = `result-tab${symbol === activeSymbol ? ' active' : ''}`;
    tab.setAttribute('aria-pressed', symbol === activeSymbol ? 'true' : 'false');
    const name = document.createElement('span'); name.textContent = symbol;
    const action = entry?.decision?.action;
    const state = document.createElement('span');
    state.className = `tab-state ${action ? action.toLowerCase() : failures.has(symbol) ? 'error' : ''}`;
    state.textContent = action || (failures.has(symbol) ? 'ERROR' : runningSymbols.has(symbol) ? 'Analyzing' : 'Waiting');
    const price = document.createElement('strong');
    price.className = 'batch-price'; price.textContent = entry && Number.isFinite(entry.market.features?.price) ? number(entry.market.features.price) : '—';
    const risk = document.createElement('span'); risk.className = 'batch-risk';
    risk.textContent = entry ? entry.stale_preview ? 'Stale · research only' : entry.source === 'Synthetic sample CSV' ? 'Synthetic sample' : entry.actionable_signal == null ? 'Signal only' : entry.risk_rejections.length && action !== 'HOLD' ? 'Blocked · HOLD' : `Risk checked · ${entry.actionable_signal}` : failures.has(symbol) ? 'Select to see error' : 'Awaiting result';
    tab.disabled = !entry && !failures.has(symbol);
    tab.append(name, state, price, risk);
    tab.addEventListener('click', () => {
      activeSymbol = symbol;
      const data = results.get(symbol);
      if (data) render(data, false, false);
      else { clearResults(); activeSymbol = symbol; showNotice(`${symbol}: ${failures.get(symbol) || 'The analysis failed.'}`, 'error'); }
      renderTabs();
    });
    tabs.append(tab);
  }
  if (focused) tabs.querySelector(`[data-symbol="${CSS.escape(focused)}"]`)?.focus();
}

/* Paper portfolio: simulated fills stored in this browser only; no orders are ever placed. */
const paperKey = 'fasttrade-paper';
const PAPER_START_CASH = 10000;
let paperReadable = true;
let paper = {version: 1, cash: PAPER_START_CASH, positions: {}, fills: [], realized: 0};
let paperLoadWarning = '';
try {
  const storedPaper = JSON.parse(localStorage.getItem(paperKey) || 'null');
  if (storedPaper) {
    if (storedPaper.version !== 1 || !Number.isFinite(storedPaper.cash) || typeof storedPaper.positions !== 'object' || storedPaper.positions === null || Array.isArray(storedPaper.positions)) throw new Error('Invalid paper portfolio');
    const positions = {};
    for (const [symbol, held] of Object.entries(storedPaper.positions)) {
      if (held && Number.isFinite(held.shares) && held.shares > 0 && Number.isFinite(held.cost) && held.cost >= 0) positions[symbol] = {shares: held.shares, cost: held.cost};
    }
    paper = {version: 1, cash: storedPaper.cash, positions, realized: Number.isFinite(storedPaper.realized) ? storedPaper.realized : 0,
      fills: Array.isArray(storedPaper.fills) ? storedPaper.fills.filter(fill => fill && typeof fill.symbol === 'string' && (fill.side === 'BUY' || fill.side === 'SELL') && Number.isFinite(fill.quantity) && Number.isFinite(fill.price)) : []};
  }
} catch {
  paperReadable = false;
  paperLoadWarning = 'The stored paper portfolio was invalid and was not loaded; the balance below is the default. Reset clears the broken data.';
}
function savePaper() {
  if (!paperReadable) return false;
  try { localStorage.setItem(paperKey, JSON.stringify(paper)); return true; } catch { return false; }
}
function paperTradable() {
  return Boolean(current) && !current._replay && !current.stale_preview && current.source !== 'Synthetic sample CSV' && Number.isFinite(current.market?.features?.price) && current.market.features.price > 0;
}
const paperAction = () => current?.actionable_signal ?? current?.decision?.action;
let paperStatusSymbol = null;
let paperSizingResult = null;
function suggestPaperQuantity() {
  const action = paperAction(), price = current?.market?.features?.price;
  const held = paper.positions[current?.market?.symbol]?.shares || 0;
  const shares = Number.isFinite(price) && price > 0 ? action === 'BUY' ? Math.floor(paper.cash * 0.1 / price) : action === 'SELL' ? Math.floor(held * 0.1 * 10000) / 10000 : 0 : 0;
  $('paper-qty').value = shares > 0 ? String(shares) : '';
}
function renderPaper() {
  const result = current;
  const tradable = paperTradable();
  const blocked = Boolean(result) && !tradable;
  const action = paperAction();
  $('paper-badge').className = `pill ${tradable && action !== 'HOLD' ? 'good' : blocked ? 'warning' : 'muted'}`;
  setText('paper-badge', blocked ? 'TRADING BLOCKED' : tradable && action === 'HOLD' ? 'NO TRADE' : tradable ? 'READY' : 'NO SIGNAL');
  $('paper-empty').hidden = Boolean(result);
  $('paper-content').hidden = !result;
  if (paperStatusSymbol && paperStatusSymbol !== result?.market?.symbol) {
    const status = $('paper-status'); status.textContent = ''; status.className = 'paper-status';
    paperStatusSymbol = null;
  }
  if (!result) return;
  $('paper-buy').hidden = action !== 'BUY'; $('paper-sell').hidden = action !== 'SELL';
  $('paper-quantity-label').hidden = action !== 'BUY' && action !== 'SELL';
  setText('paper-guidance', action === 'HOLD' ? 'Hold signal · No simulated trade' : `Suggested size: 10% of ${action === 'SELL' ? 'shares held' : 'paper cash'} · Not risk-checked${result.actionable_signal == null ? ' · Model preference only' : ''}`);
  if (paperSizingResult !== result) { paperSizingResult = result; suggestPaperQuantity(); }
  const held = paper.positions[result.market.symbol];
  setText('paper-cash', number(paper.cash));
  setText('paper-position', held ? `${number(held.shares)} shares · avg ${number(held.cost / held.shares)}` : 'None held');
  setText('paper-price', `${number(result.market.features.price)} · ${formatTime(result.market.candle_timestamp)}`);
  const qty = Number($('paper-qty').value);
  setText('paper-estimate', action === 'HOLD' ? '' : Number.isFinite(qty) && qty > 0 ? `Estimated ${action.toLowerCase()} value · ${number(qty * result.market.features.price)} at the last close` : action === 'SELL' && !held ? 'No shares held to sell' : 'Enter a share quantity to see the estimated value');
  if (!tradable) { $('paper-buy').disabled = true; $('paper-sell').disabled = true; return; }
  $('paper-buy').disabled = !Number.isFinite(qty) || !(qty > 0) || qty * result.market.features.price > paper.cash + 1e-9;
  $('paper-sell').disabled = !Number.isFinite(qty) || !(qty > 0) || !held || qty > held.shares + 1e-9;
}
function placePaper(side) {
  if (!paperTradable() || side !== paperAction()) return;
  const symbol = current.market.symbol;
  const price = current.market.features.price;
  const qty = Number($('paper-qty').value);
  const say = (message, ok) => { const el = $('paper-status'); el.textContent = message; el.className = `paper-status ${ok ? 'good' : 'bad'}`.trim(); paperStatusSymbol = symbol; };
  if (!Number.isFinite(qty) || !(qty > 0)) { say('Enter a share quantity greater than zero.', false); return; }
  const value = qty * price;
  if (side === 'BUY') {
    if (value > paper.cash + 1e-9) { say(`Not enough paper cash: ${number(value)} needed, ${number(paper.cash)} available.`, false); return; }
    paper.cash -= value;
    const held = paper.positions[symbol] || {shares: 0, cost: 0};
    held.shares += qty; held.cost += value;
    paper.positions[symbol] = held;
  } else {
    const held = paper.positions[symbol];
    if (!held || qty > held.shares + 1e-9) { say(`The paper portfolio holds no ${symbol} shares to sell.`, false); return; }
    const avg = held.cost / held.shares;
    paper.cash += value;
    paper.realized += (price - avg) * qty;
    held.shares -= qty; held.cost -= avg * qty;
    if (held.shares <= 1e-9) delete paper.positions[symbol];
  }
  paper.fills.unshift({time: new Date().toISOString(), symbol, side, quantity: qty, price, value});
  if (paper.fills.length > 100) paper.fills.length = 100;
  const saved = savePaper();
  say(`${side} ${number(qty)} ${symbol} at ${number(price)} simulated against the last completed close. ${saved ? 'Stored in this browser.' : 'Browser storage is unavailable; it was not saved.'}`, true);
  renderPaper();
  renderPortfolio();
}
$('paper-qty').addEventListener('input', renderPaper);
$('paper-buy').addEventListener('click', () => placePaper('BUY'));
$('paper-sell').addEventListener('click', () => placePaper('SELL'));
$('portfolio-reset').addEventListener('click', () => {
  paper = {version: 1, cash: PAPER_START_CASH, positions: {}, fills: [], realized: 0};
  try { localStorage.removeItem(paperKey); paperReadable = true; savePaper(); } catch { /* keep the reset in memory only */ }
  paperLoadWarning = '';
  const status = $('paper-status'); status.textContent = ''; status.className = 'paper-status';
  renderPaper(); renderPortfolio();
});

/* Portfolio page: paper cash, open positions and simulated fills. */
function renderPortfolio() {
  const entries = Object.entries(paper.positions);
  $('positions-empty').hidden = entries.length > 0;
  $('positions').hidden = entries.length === 0;
  const body = $('positions').querySelector('tbody'); body.replaceChildren();
  let marketValue = 0, allPriced = true;
  for (const [symbol, held] of entries) {
    const last = lastPrices.get(symbol);
    const avg = held.cost / held.shares;
    const value = last != null ? held.shares * last : held.cost;
    if (last == null) allPriced = false;
    marketValue += value;
    const row = document.createElement('tr');
    for (const [label, text] of [['Symbol', symbol], ['Shares', number(held.shares)], ['Avg cost', number(avg)], ['Last price', last != null ? number(last) : 'No price yet'], ['Value', number(value)], ['Unrealized P&L', last != null ? `${last >= avg ? '+' : ''}${number((last - avg) * held.shares)}` : 'n/a']]) {
      const cell = document.createElement('td'); cell.dataset.label = label; cell.textContent = text; row.append(cell);
    }
    body.append(row);
  }
  setText('portfolio-cash', number(paper.cash));
  setText('portfolio-value', number(marketValue));
  setText('portfolio-equity', number(paper.cash + marketValue));
  setText('portfolio-realized', `${paper.realized >= 0 ? '+' : ''}${number(paper.realized)}`);
  setText('portfolio-note', paperLoadWarning || (allPriced ? '' : 'Symbols without a recent analysis are valued at cost.'));
  const fillsBody = $('fills').querySelector('tbody'); fillsBody.replaceChildren();
  $('fills').hidden = paper.fills.length === 0;
  $('fills-empty').hidden = paper.fills.length > 0;
  for (const fill of paper.fills) {
    const row = document.createElement('tr');
    for (const [label, text] of [['Time', formatTime(fill.time)], ['Symbol', fill.symbol], ['Side', fill.side], ['Shares', number(fill.quantity)], ['Price', number(fill.price)], ['Value', number(fill.value)]]) {
      const cell = document.createElement('td'); cell.dataset.label = label; cell.textContent = text; row.append(cell);
    }
    fillsBody.append(row);
  }
}
for (const entry of history) if (!lastPrices.has(entry.market.symbol) && Number.isFinite(entry.market?.features?.price)) lastPrices.set(entry.market.symbol, entry.market.features.price);
renderPortfolio();

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
      ctx.save(); ctx.setLineDash([2, 4]); ctx.strokeStyle = '#536b86'; ctx.beginPath();
      ctx.moveTo(x(i) - step / 2, top); ctx.lineTo(x(i) - step / 2, volumeBottom); ctx.stroke(); ctx.restore();
    }
    const up = c.close >= c.open;
    ctx.strokeStyle = ctx.fillStyle = up ? '#0b8055' : '#bd374b'; ctx.lineWidth = Math.min(1, step * .7);
    ctx.beginPath(); ctx.moveTo(x(i), y(c.high)); ctx.lineTo(x(i), y(c.low)); ctx.stroke();
    const bodyTop = y(Math.max(c.open, c.close)), bodyBottom = y(Math.min(c.open, c.close));
    if (up) ctx.fillRect(x(i) - bodyWidth / 2, bodyTop, bodyWidth, Math.max(1, bodyBottom - bodyTop));
    else ctx.strokeRect(x(i) - bodyWidth / 2, bodyTop, bodyWidth, Math.max(1, bodyBottom - bodyTop));
    ctx.fillStyle = '#a1b1c5';
    const volumeHeight = (volumeBottom - volumeTop - 13) * c.volume / maxVolume;
    ctx.fillRect(x(i) - bodyWidth / 2, volumeBottom - volumeHeight, bodyWidth, volumeHeight);
  });
  if (overlay !== 'none') {
    ctx.strokeStyle = '#245de0'; ctx.lineWidth = 1.5; ctx.beginPath();
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
    ctx.save(); ctx.setLineDash([3, 3]); ctx.strokeStyle = '#1c3155bb'; ctx.beginPath();
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
  const destination = ['#chart-panel', '#history-panel', '#portfolio-panel'].includes(location.hash) ? location.hash : '#workspace';
  const analysisWasHidden = $('analysis-view').hidden;
  $('analysis-view').hidden = destination === '#history-panel' || destination === '#portfolio-panel';
  $('history-panel').hidden = destination !== '#history-panel';
  $('portfolio-panel').hidden = destination !== '#portfolio-panel';
  for (const link of document.querySelectorAll('.mobile-nav a[href^="#"], .desktop-rail nav a[href^="#"]')) {
    const active = link.getAttribute('href') === (destination === '#chart-panel' ? '#workspace' : destination);
    link.classList.toggle('active', active);
    if (active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
  if (!$('analysis-view').hidden && current) drawChart();
  if (analysisWasHidden && destination === '#chart-panel') $('chart-panel').scrollIntoView();
}
window.addEventListener('hashchange', syncNavigation);
syncNavigation();

async function loadProviders() {
  try {
    const response = await fetch('/api/providers');
    if (!response.ok) throw new Error('Provider configuration unavailable');
    const config = await response.json();
    for (const [id, names] of [['source', config.market], ['decision-provider', config.decision]]) {
      if (!Array.isArray(names)) throw new Error('Invalid provider configuration');
      const select = $(id);
      for (const option of [...select.options]) if (!names.includes(option.value)) option.remove();
      if (select.options.length !== names.length) throw new Error('Invalid provider configuration');
      select.closest('label').hidden = names.length < 2;
    }
    if (!$('source').value || !$('decision-provider').value) {
      showNotice('No decision model is configured. Add a model API key and reload.', 'error');
      return;
    }
    $('analyze').disabled = false;
  } catch {
    showNotice('Could not load provider configuration. Check the server and reload.', 'error');
  }
}
loadProviders();

$('analysis-form').addEventListener('invalid', event => {
  const settings = document.querySelector('.risk-settings');
  if (settings.contains(event.target)) settings.open = true;
}, true);

$('analysis-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const symbols = symbolList();
  if (!symbols.length) { $('symbol').focus(); return; }
  if (symbols.length > 5) { showNotice('Choose up to five tickers for one analysis.', 'error'); $('symbol').focus(); return; }
  const payload = {timeframe: $('timeframe').value, source: $('source').value, decision_provider: $('decision-provider').value};
  for (const [name] of riskFields) {
    const raw = $(`risk-${name}`).value.trim();
    if (raw !== '') payload[name] = Number(raw);
  }
  const button = $('analyze'); button.disabled = true;
  button.querySelector('span').textContent = symbols.length > 1 ? `Analyzing ${symbols.length} tickers…` : 'Analyzing…';
  $('analysis-form').setAttribute('aria-busy', 'true');
  renderSelectedTickers();
  const fields = [...$('analysis-form').querySelectorAll('input, select, .ticker-chip'), ...document.querySelectorAll('.history-link')];
  fields.forEach(field => { field.disabled = true; });
  clearResults();
  results.clear(); failures.clear();
  requestedSymbols = symbols;
  runningSymbols.clear(); renderTabs();
  let settled = 0;
  showNotice(symbols.length > 1 ? `Requesting ${symbols.length} model decisions, up to three at a time…` : 'Fetching completed candles and requesting a model decision…', 'loading');
  const runOne = async symbol => {
    runningSymbols.add(symbol); renderTabs();
    try {
      const response = await fetch('/api/analyze', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...payload, symbol})});
      const data = await response.json();
      if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : 'Check the symbol, timeframe, and risk settings.');
      results.set(symbol, data);
      data._notice = data.stale_preview ? ['The latest candle is stale. This is a historical research preview; portfolio actions are blocked.', 'warning']
        : data.source === 'Synthetic sample CSV' ? ['Synthetic sample candles are displayed. The model response is real; this is not live market data.', 'warning']
        : [`Analysis complete. Candle started ${formatTime(data.market.candle_timestamp)}. Model preferences are not trading outcomes.`, 'success'];
      if (Number.isFinite(data.market?.features?.price)) lastPrices.set(symbol, data.market.features.price);
    } catch (error) {
      failures.set(symbol, error instanceof TypeError ? 'Could not reach the local server. Check that FastTrade is running, then try again.'
        : error instanceof Error && error.message ? error.message : 'The request failed.');
    }
    runningSymbols.delete(symbol);
    settled += 1;
    if (!activeSymbol && results.has(symbol)) render(results.get(symbol), false);
    renderTabs();
    if (!current && symbols.length > 1 && settled < symbols.length) showNotice(`Model decisions received: ${settled} of ${symbols.length}…`, 'loading');
  };
  try {
    const queue = [...symbols];
    await Promise.all(Array.from({length: Math.min(3, queue.length)}, async () => { while (queue.length) await runOne(queue.shift()); }));
  } finally {
    button.disabled = false; button.querySelector('span').textContent = 'Run analysis';
    fields.forEach(field => { field.disabled = false; });
    $('analysis-form').setAttribute('aria-busy', 'false');
    renderSelectedTickers();
  }
  const succeeded = symbols.filter(symbol => results.has(symbol));
  if (!succeeded.length) {
    const message = failures.get(symbols[0]) || 'The analysis failed.';
    showNotice(symbols.length > 1 ? `${symbols[0]}: ${message}` : `${message} No actionable signal is available.`, 'error');
    renderTabs(); renderPortfolio();
    return;
  }
  const selected = results.has(activeSymbol) ? activeSymbol : succeeded[0];
  for (const symbol of succeeded.filter(symbol => symbol !== selected)) {
    const data = results.get(symbol);
    history.unshift(historySnapshot(data));
    if (history.length > 50) history.pop();
  }
  if (results.has(activeSymbol) || !activeSymbol) render(results.get(selected), true);
  else {
    history.unshift(historySnapshot(results.get(selected)));
    if ($('history-save').checked) saveHistory(history);
    renderHistory(); renderTabs();
  }
  if (failures.size && !current?.stale_preview && current?.source !== 'Synthetic sample CSV' && !failures.has(activeSymbol)) showNotice(`Analyzed ${succeeded.length} of ${symbols.length} tickers. Select a failed ticker to see its error.`, 'warning');
});
