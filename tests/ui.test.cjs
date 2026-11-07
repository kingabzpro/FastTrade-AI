const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { chromium } = require('playwright');

let server, browser, page, url;
before(async () => {
  const root = join(__dirname, '../fasttrade/static');
  const files = new Map(['index.html', 'style.css', 'app.js', 'chart-math.js', 'logo.svg', 'favicon.svg', 'favicon.ico']
    .map(name => [name === 'index.html' ? '/' : `/static/${name}`, readFileSync(join(root, name))]));
  server = createServer((req, res) => {
    const types = { css: 'text/css', js: 'text/javascript', svg: 'image/svg+xml', ico: 'image/x-icon' };
    res.writeHead(files.has(req.url) ? 200 : 404, { 'Content-Type': types[req.url.split('.').pop()] || 'text/html; charset=utf-8' });
    res.end(files.get(req.url));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  url = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH });
});
after(async () => { await browser?.close(); server?.close(); });
beforeEach(async () => {
  await page?.close();
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await page.route('**/api/providers', route => route.fulfill({json: {market: ['alpaca', 'yahoo'], decision: ['jev', 'liquid', 'vllm', 'subq', 'baseten']}}));
  await page.route('**/api/market-overview', route => route.fulfill({json: result().market_overview}));
  await page.route('**/api/spy-signals', route => route.fulfill({json: {deterministic: 'HOLD', model: 'BUY', stale: false, candle_timestamp: '2026-01-02T20:55:00Z'}}));
  await page.goto(url);
});

async function openView(view) {
  await page.evaluate(hash => { location.hash = hash; }, `#${view}`);
  await page.waitForFunction(view => !document.getElementById(view === 'workspace' ? 'analysis-view' : view).hidden, view);
}

function result(symbol = 'NVDA') {
  return {
    market: { symbol, timeframe: '5m', candle_timestamp: '2026-01-02T20:55:00Z', features: {
      price: 142.85, rsi_14: 54, macd: .12, vwap_delta: .003, volume_ratio: 1.2, atr_14: .8,
    }, headlines: [] },
    source: 'Alpaca IEX', stale_preview: false,
    market_overview: {symbol: 'SPY', timeframe: '5m', price: 500, change_pct: 0.5,
      candle_timestamp: '2026-01-02T20:55:00Z', change_candle_timestamp: '2026-01-02T20:55:00Z', stale: false, news_status: 'available', news: []},
    decision: { action: 'BUY', probabilities: { BUY: .7, HOLD: .2, SELL: .1 }, model: 'test', latency_us: 1000 },
    actionable_signal: 'HOLD', risk_rejections: [],
    rule_comparison: { signal: 'BUY', agreement: 'agree' },
    indicator_interpretations: ['Price is above VWAP.'],
    candles: Array.from({ length: 65 }, (_, i) => ({
      timestamp: new Date(Date.UTC(2026, 0, 2, 15, 30) + i * 300000).toISOString(),
      open: 140 + i * .04, high: 141 + i * .04, low: 139 + i * .04,
      close: 140.2 + i * .04, volume: 1000 + i * 10, ema_9: 140 + i * .04,
    })),
  };
}

test('dashboard defaults to Alpaca and can analyze with Yahoo Finance', async () => {
  assert.equal(await page.locator('#chart-window').isDisabled(), true);
  assert.equal(await page.locator('#chart-overlay').isDisabled(), true);
  assert.equal(await page.locator('#source').inputValue(), 'alpaca');
  assert.equal(await page.locator('#timeframe').isEnabled(), true);
  assert.match(await page.locator('.summary-card').first().innerText(), /SPY price/);
  let requested;
  const data = result(); data.source = 'Yahoo Finance';
  await page.route('**/api/analyze', route => { requested = route.request().postDataJSON(); return route.fulfill({json: data}); });
  await page.locator('#source').selectOption('yahoo');
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(requested.source, 'yahoo');
  assert.match(await page.locator('#chart-subtitle').innerText(), /YAHOO FINANCE/);
});

test('dashboard uses white and dark blue for its main accents', async () => {
  const colors = await page.evaluate(() => ({
    button: getComputedStyle(document.querySelector('#analyze')).backgroundColor,
    rail: getComputedStyle(document.querySelector('.desktop-rail nav a.active')).backgroundColor,
    card: getComputedStyle(document.querySelector('.summary-card')).backgroundColor,
  }));
  assert.equal(colors.button, 'rgb(28, 49, 85)');
  assert.equal(colors.rail, 'rgb(28, 49, 85)');
  assert.equal(colors.card, 'rgb(255, 255, 255)');
});

test('notifications use distinct success, warning and error colors', async () => {
  const data = result('SPY');
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('success'));
  assert.equal(await page.locator('#notice').evaluate(el => getComputedStyle(el).color), 'rgb(20, 100, 68)');
  data.stale_preview = true;
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('warning') && getComputedStyle(document.getElementById('notice')).color === 'rgb(123, 82, 13)');
  assert.equal(await page.locator('#notice').evaluate(el => getComputedStyle(el).color), 'rgb(123, 82, 13)');
  await page.unroute('**/api/analyze');
  await page.route('**/api/analyze', route => route.fulfill({status: 503, json: {error: 'Unavailable'}}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('error') && getComputedStyle(document.getElementById('notice')).color === 'rgb(151, 45, 61)');
  assert.equal(await page.locator('#notice').evaluate(el => getComputedStyle(el).color), 'rgb(151, 45, 61)');
});

test('down candles stay distinguishable without relying on color', async () => {
  const data = result();
  data.candles[10].close = data.candles[10].open - .2;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.evaluate(() => {
    const original = CanvasRenderingContext2D.prototype.strokeRect;
    CanvasRenderingContext2D.prototype.strokeRect = function (...args) { window.downCandles = (window.downCandles || 0) + 1; return original.apply(this, args); };
  });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.ok(await page.evaluate(() => window.downCandles > 0));
});

test('chart uses green and red candles and a blue overlay', async () => {
  const data = result('SPY');
  data.candles[10].close = data.candles[10].open - .2;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.evaluate(() => {
    window.chartColors = [];
    for (const method of ['fillRect', 'strokeRect', 'stroke']) {
      const original = CanvasRenderingContext2D.prototype[method];
      CanvasRenderingContext2D.prototype[method] = function (...args) {
        window.chartColors.push(method === 'fillRect' ? this.fillStyle : this.strokeStyle);
        return original.apply(this, args);
      };
    }
  });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#chart-overlay').inputValue(), 'ema_9');
  assert.match(await page.locator('#overlay-name').innerText(), /EMA 9/);
  const colors = await page.evaluate(() => window.chartColors);
  assert.ok(colors.includes('#0b8055'));
  assert.ok(colors.includes('#bd374b'));
  assert.ok(colors.includes('#245de0'));
});

test('collapsed invalid risk settings open so the invalid field can receive focus', async () => {
  await page.locator('.risk-settings summary').click();
  await page.fill('#risk-min_confidence', '2');
  await page.locator('.risk-settings summary').click();
  await page.locator('#analyze').click();
  assert.equal(await page.locator('.risk-settings').getAttribute('open'), '');
  assert.equal(await page.locator('#risk-min_confidence').evaluate(el => el === document.activeElement), true);
});

test('risk inputs explain their meaning and connect help to each input', async () => {
  await page.locator('.risk-settings summary').click();
  for (const input of await page.locator('#risk-fields input').all()) {
    const description = await input.getAttribute('aria-describedby');
    assert.ok(description);
    assert.ok((await page.locator(`#${description}`).innerText()).length > 10);
  }
  assert.match(await page.locator('.risk-settings').innerText(), /not the chance of profit/i);
  assert.match(await page.locator('.risk-settings').innerText(), /no orders/i);
  await page.setViewportSize({ width: 320, height: 844 });
  await page.locator('#risk-position').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.locator('#risk-equity').evaluate(el => el === document.activeElement), true);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
});

test('positive portfolio amounts reject zero and accept fractional values or omission', async () => {
  await page.locator('.risk-settings summary').click();
  for (const name of ['equity', 'quantity']) {
    const input = page.locator(`#risk-${name}`);
    await input.fill('0');
    assert.equal(await input.evaluate(el => el.checkValidity()), false);
    await input.fill('0.5');
    assert.equal(await input.evaluate(el => el.checkValidity()), true);
    await input.fill('');
    assert.equal(await input.evaluate(el => el.checkValidity()), true);
  }
});

test('example risk inputs are opt-in and results identify their assumptions', async () => {
  await page.locator('.risk-settings summary').click();
  assert.equal(await page.locator('#risk-equity').inputValue(), '');
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  await page.getByRole('button', {name: 'Load example values'}).click();
  assert.equal(await page.locator('#risk-equity').inputValue(), '10000');
  assert.equal(await page.locator('#risk-position').inputValue(), '0');
  assert.match(await page.locator('#risk-example-status').innerText(), /example/i);
  let sent;
  await page.route('**/api/analyze', route => { sent = route.request().postDataJSON(); return route.fulfill({json: result('SPY')}); });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(sent.equity, 10000);
  assert.equal(sent.example_risk, undefined);
  assert.match(await page.locator('#risk-label').innerText(), /example/i);
  await openView('history-panel');
  assert.match(await page.locator('#history tbody').innerText(), /example/i);
  await page.reload();
  await page.locator('.history-link').click();
  assert.match(await page.locator('#risk-label').innerText(), /example/i);
  await page.locator('.risk-settings summary').click();
  await page.getByRole('button', {name: 'Clear risk inputs'}).click();
  assert.equal(await page.locator('#risk-equity').inputValue(), '');
});

test('desktop rail and mobile navigation link to real destinations', async () => {
  assert.equal(await page.locator('.desktop-rail').isVisible(), true);
  assert.equal(await page.locator('.desktop-rail a[href="#chart-panel"]').count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('.desktop-rail').isHidden(), true);
  assert.equal(await page.locator('.mobile-nav a[href="#chart-panel"]').count(), 0);
  assert.equal(await page.locator('#chart-panel').isVisible(), true);
});

test('SPY summary remains separate from other results and errors', async () => {
  await page.waitForFunction(() => document.getElementById('summary-decision').textContent === 'BUY');
  assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
  await page.route('**/api/analyze', route => route.fulfill({ json: result() }));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
  await page.unroute('**/api/analyze');
  await page.route('**/api/analyze', route => route.fulfill({ status: 503, json: {error: 'Unavailable'} }));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('error'));
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
});

test('SPY cards show separate completed-bar change and recent news', async () => {
  const data = result('NVDA');
  data.market_overview = {symbol: 'SPY', timeframe: '5m', price: 500, change_pct: -1.25,
    candle_timestamp: '2026-01-02T20:55:00Z', change_candle_timestamp: '2026-01-02T20:50:00Z', stale: true, news_status: 'available',
    news: [{title: 'Market headline', source: 'Example', published: '2026-01-02T20:00:00Z'}]};
  await page.route('**/api/market-overview', route => route.fulfill({json: data.market_overview}));
  await page.reload();
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.match(await page.locator('#summary-price').innerText(), /500/);
  assert.match(await page.locator('#summary-change').innerText(), /-1.25%/);
  assert.equal(await page.locator('#price-stale').isVisible(), true);
  assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
  assert.match(await page.locator('#summary-news').innerText(), /Market headline/);
  assert.match(await page.locator('#summary-news').getAttribute('title'), /Example/);
});

test('SPY overview and signals load on refresh without triggering a manual analysis', async () => {
  let calls = 0;
  await page.route('**/api/analyze', () => { calls++; });
  await page.reload();
  await page.waitForFunction(() => document.getElementById('summary-price').textContent === '500.00');
  assert.equal(calls, 0);
  assert.match(await page.locator('#summary-change').innerText(), /Up \+0.50%/);
});

test('SPY signals load automatically with a spinner and show independent results', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/spy-signals', async route => {
    assert.equal(route.request().method(), 'POST');
    await pending;
    await route.fulfill({json: {deterministic: 'HOLD', model: 'SELL', stale: true, candle_timestamp: '2026-01-02T20:55:00Z'}});
  });
  try {
    await page.emulateMedia({reducedMotion: 'no-preference'});
    await page.reload();
    assert.equal(await page.locator('#summary-rule .spy-spinner').isVisible(), true);
    assert.equal(await page.locator('#summary-decision .spy-spinner').isVisible(), true);
    assert.equal(await page.locator('#summary-rule .spy-spinner').evaluate(el => getComputedStyle(el).animationName), 'spin');
    await page.emulateMedia({reducedMotion: 'reduce'});
    assert.equal(await page.locator('#summary-rule .spy-spinner').evaluate(el => getComputedStyle(el).animationName), 'none');
    release();
    await page.waitForFunction(() => document.getElementById('summary-decision').textContent === 'SELL');
    assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
    assert.equal(await page.locator('#rule-stale').isVisible(), true);
    assert.equal(await page.locator('#model-stale').isVisible(), true);
    assert.equal(await page.locator('#spy-status').count(), 0);
    assert.equal(await page.locator('#last-analysis').count(), 0);
    assert.equal(await page.locator('.summary-card small').count(), 0);
  } finally { release(); }
});

test('all summary cards stay on SPY when another symbol is analyzed', async () => {
  await page.evaluate(() => localStorage.setItem('fasttrade-last-analysis', JSON.stringify({symbol: 'MU', timeframe: '5m', action: 'BUY', candle_timestamp: '2026-01-02T20:55:00Z', source: 'Alpaca IEX'})));
  await page.reload();
  await page.waitForFunction(() => document.getElementById('summary-price').textContent === '500.00');
  assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  await page.fill('#symbol', 'MU');
  await page.route('**/api/analyze', route => route.fulfill({json: result('MU')}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.match(await page.locator('#chart-symbol').innerText(), /MU/);
  assert.equal(await page.locator('#summary-rule').innerText(), 'HOLD');
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
});

test('SPY model card reflects the actual BUY HOLD or SELL output', async () => {
  let calls = 0;
  let action = 'BUY';
  await page.route('**/api/spy-signals', route => { calls++; return route.fulfill({json: {deterministic: 'HOLD', model: action, stale: false}}); });
  assert.equal(await page.locator('#symbol').inputValue(), 'SPY');
  for (action of ['BUY', 'HOLD', 'SELL']) {
    await page.reload();
    await page.waitForFunction(expected => document.getElementById('summary-decision').textContent === expected, action);
    assert.equal(await page.locator('#summary-decision').innerText(), action);
  }
  assert.equal(calls, 3);
});

test('last model preference returns after refresh without saving portfolio data or calling the model', async () => {
  let calls = 0;
  const data = result('SPY');
  data.portfolio = {equity: 12345};
  await page.route('**/api/analyze', route => { calls++; return route.fulfill({json: data}); });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  const saved = await page.evaluate(() => localStorage.getItem('fasttrade-last-analysis'));
  assert.ok(saved);
  assert.doesNotMatch(saved, /12345|portfolio|key/i);
  data.market.symbol = 'MU';
  data.decision.action = 'SELL';
  await page.fill('#symbol', 'MU');
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('model-action').textContent === 'SELL');
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  await page.reload();
  assert.equal(calls, 2);
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  assert.equal(await page.locator('#last-analysis').count(), 0);
  assert.match(await page.evaluate(() => localStorage.getItem('fasttrade-last-analysis')), /MU/);
  assert.equal(await page.locator('#decision-content').isHidden(), true);
});

test('analysis stays usable when browser storage is unavailable', async () => {
  await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error('storage blocked'); }; });
  await page.route('**/api/analyze', route => route.fulfill({json: result('SPY')}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#summary-decision').innerText(), 'BUY');
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
});

test('long SPY news stays the same height as the other summary cards', async () => {
  const overview = result().market_overview;
  overview.news = [{title: 'A very long SPY market headline '.repeat(12), source: 'Market News', published: '2026-01-02T20:00:00Z'}];
  await page.route('**/api/market-overview', route => route.fulfill({json: overview}));
  await page.reload();
  await page.waitForFunction(() => document.getElementById('summary-news').textContent.startsWith('A very long'));
  for (const width of [1440, 820, 390]) {
    await page.setViewportSize({width, height: 900});
    const heights = await page.locator('.summary-card').evaluateAll(cards => cards.map(card => card.getBoundingClientRect().height));
    assert.ok(Math.abs(heights[0] - heights[4]) < 1, `news card height at ${width}px: ${heights}`);
  }
});

test('tablet and phone layouts keep chart, headings and controls within their cards', async () => {
  for (const width of [1440, 1024, 820, 768, 760, 390, 320]) {
    await page.setViewportSize({ width, height: 950 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `page overflow at ${width}`);
    if (width <= 1100) assert.ok(await page.locator('#chart').evaluate(el => el.clientWidth) > width / 2, `squeezed chart at ${width}`);
    if (width <= 760) {
      assert.equal(await page.locator('.dashboard-grid').evaluate(el => [...el.querySelectorAll('.card:not([hidden])')]
        .every(card => Math.abs(card.getBoundingClientRect().width - el.getBoundingClientRect().width) < 1)), true, `uneven card widths at ${width}`);
    }
    for (const heading of await page.locator('.card-heading').all()) {
      assert.equal(await heading.evaluate(el => [...el.children].every(child => child.getBoundingClientRect().right <= el.getBoundingClientRect().right + 1)), true, `heading overflow at ${width}`);
    }
  }
});

test('loading, chart inspection, history replay and failures restore usable controls', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/analyze', async route => {
    assert.equal(route.request().postDataJSON().timeframe, '5m');
    assert.notEqual(route.request().postDataJSON().sample, true);
    await pending;
    await route.fulfill({ json: result() });
  });
  await page.locator('#analyze').click();
  assert.equal(await page.locator('#analyze').isDisabled(), true);
  assert.equal(await page.locator('#analysis-form').getAttribute('aria-busy'), 'true');
  release();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.match(await page.locator('#risk-action').innerText(), /HOLD/);
  assert.equal(await page.locator('#timeframe').isEnabled(), true);
  assert.equal(await page.locator('#chart-overlay').isEnabled(), true);
  await page.selectOption('#chart-overlay', 'ema_9');
  await page.locator('#chart').focus();
  await page.keyboard.press('Home');
  assert.match(await page.locator('#candle-time').innerText(), /15:55/);
  await openView('history-panel');
  await page.locator('.history-link').click();
  assert.equal(await page.locator('#history tbody tr').count(), 1);
  for (const width of [1024, 390, 320]) {
    await page.setViewportSize({ width, height: 950 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `populated page overflow at ${width}`);
  }
  await page.unroute('**/api/analyze');
  let finishError;
  const errorPending = new Promise(resolve => { finishError = resolve; });
  await page.route('**/api/analyze', async route => {
    await errorPending;
    await route.fulfill({ status: 503, json: { error: 'Model request timed out.' } });
  });
  await page.locator('#analyze').click();
  assert.equal(await page.locator('.history-link').isDisabled(), true);
  assert.equal(await page.locator('#decision-content').isHidden(), true);
  finishError();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('error'));
  assert.match(await page.locator('#notice').innerText(), /timed out/);
  assert.equal(await page.locator('#chart-window').isDisabled(), true);
  assert.equal(await page.locator('#analyze').isEnabled(), true);
  assert.equal(await page.locator('#analysis-form').getAttribute('aria-busy'), 'false');
  assert.equal(await page.locator('.history-link').isEnabled(), true);
});

test('shows rule agreement without claiming certainty', async () => {
  const data = result();
  await page.route('**/api/analyze', route => route.fulfill({ json: data }));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#rule-status').innerText(), 'SIGNALS AGREE');
  assert.match(await page.locator('#rule-comparison').innerText(), /BUY/);
  assert.match(await page.locator('#rule-comparison').innerText(), /unvalidated/i);
  data.rule_comparison = { signal: 'SELL', agreement: 'disagree' };
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('rule-status').textContent === 'SIGNALS DIFFER');
  assert.match(await page.locator('#rule-comparison').innerText(), /SELL/);
  data.rule_comparison = { signal: null, agreement: 'no_signal' };
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('rule-status').textContent === 'NO RULE SIGNAL');
  assert.match(await page.locator('#rule-comparison').innerText(), /unvalidated/i);
});

test('incomplete portfolio shows signal only in the decision and history', async () => {
  const data = result();
  data.source = 'Alpaca IEX';
  data.actionable_signal = null;
  data.risk_rejections = [];
  await page.route('**/api/analyze', route => route.fulfill({ json: data }));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
  assert.equal(await page.locator('#risk-action').innerText(), 'SIGNAL ONLY');
  assert.match(await page.locator('#history tbody').innerText(), /SIGNAL ONLY/);
  await openView('history-panel');
  await page.locator('.history-link').click();
  assert.equal(await page.locator('#risk-action').innerText(), 'SIGNAL ONLY');
});

test('optional history survives reload, reopens without analysis, and can be deleted', async () => {
  let calls = 0;
  const data = result('NVDA');
  data.portfolio = {equity: 12345};
  data.api_key = 'private-secret';
  await page.route('**/api/analyze', route => { calls++; return route.fulfill({json: data}); });
  assert.equal(await page.locator('#history-save').isChecked(), false);
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.querySelectorAll('#history tbody tr').length === 1);
  const stored = await page.evaluate(() => localStorage.getItem('fasttrade-history'));
  assert.ok(stored);
  assert.doesNotMatch(stored, /private-secret|12345|api_key|portfolio/i);
  await page.reload();
  assert.equal(await page.locator('#history-save').isChecked(), true);
  assert.match(await page.locator('#history tbody').innerText(), /Alpaca IEX/);
  await openView('history-panel');
  await page.locator('.history-link').click();
  assert.equal(calls, 1);
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
  assert.match(await page.locator('#notice').innerText(), /previous.*2026.*no new model/i);
  await openView('history-panel');
  await page.locator('.history-delete').click();
  assert.equal(await page.locator('#history tbody tr').count(), 0);
  await page.reload();
  assert.equal(await page.locator('#history tbody tr').count(), 0);
});

test('history storage failures are visible and do not block analysis', async () => {
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key === 'fasttrade-history') throw new DOMException('Full', 'QuotaExceededError');
      return original.call(this, key, value);
    };
  });
  await openView('history-panel');
  await page.locator('#history-save').check({force: true});
  assert.match(await page.locator('#history-storage-status').innerText(), /storage.*unavailable|storage.*full/i);
  await openView('workspace');
  await page.route('**/api/analyze', route => route.fulfill({json: result()}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#model-action').innerText(), 'BUY');
});

test('history size limit preserves earlier saved results and clear removes them', async () => {
  const data = result();
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.querySelectorAll('#history tbody tr').length === 1);
  data.market.headlines = [{title: 'x'.repeat(2 * 1024 * 1024), source: 'test', published: '2026-01-02T20:55:00Z'}];
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.querySelectorAll('#history tbody tr').length === 2);
  const saved = JSON.parse(await page.evaluate(() => localStorage.getItem('fasttrade-history')));
  assert.equal(saved.entries.length, 1);
  assert.match(await page.locator('#history-storage-status').innerText(), /too large/i);
  await openView('history-panel');
  await page.locator('#history-save').uncheck();
  await page.reload();
  assert.equal(await page.locator('#history tbody tr').count(), 0);
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  assert.equal(await page.locator('#history tbody tr').count(), 1);
  await openView('history-panel');
  await page.locator('#history-clear').click();
  await page.reload();
  assert.equal(await page.locator('#history tbody tr').count(), 0);
});

test('reopening history respects the reduced-motion setting', async () => {
  await page.route('**/api/analyze', route => route.fulfill({ json: result() }));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  await page.evaluate(() => {
    const chart = document.getElementById('chart-panel');
    chart.scrollIntoView = options => { chart.dataset.scrollBehavior = options?.behavior || 'auto'; };
  });
  await openView('history-panel');
  await page.locator('.history-link').click();
  assert.equal(await page.locator('#chart-panel').getAttribute('data-scroll-behavior'), 'auto');
  assert.equal(await page.locator('html').evaluate(el => getComputedStyle(el).scrollBehavior), 'auto');
});

test('loading indicator does not animate with reduced motion', async () => {
  await page.locator('#notice').evaluate(el => el.classList.add('loading'));
  const icon = page.locator('#notice .notice-icon');
  assert.equal(await icon.evaluate(el => getComputedStyle(el).animationName), 'none');
});

test('selected decision provider is submitted and displayed', async () => {
  await page.selectOption('#decision-provider', 'baseten');
  await page.route('**/api/analyze', async route => {
    assert.equal(route.request().postDataJSON().decision_provider, 'baseten');
    const data = result();
    data.decision.provider = 'baseten';
    data.decision.model = 'mammoth';
    await route.fulfill({json: data});
  });
  await page.click('#analyze');
  await page.waitForSelector('#decision-content:not([hidden])');
  assert.match(await page.locator('#model-name').textContent(), /baseten.*mammoth/);
});

test('single configured providers are selected and hidden', async () => {
  await page.route('**/api/providers', route => route.fulfill({json: {market: ['yahoo'], decision: ['subq']}}));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#decision-provider').value === 'subq');
  assert.equal(await page.locator('#source').isVisible(), false);
  assert.equal(await page.locator('#decision-provider').isVisible(), false);
  let payload;
  await page.route('**/api/analyze', route => { payload = route.request().postDataJSON(); return route.fulfill({json: result()}); });
  await page.click('#analyze');
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(payload.source, 'yahoo');
  assert.equal(payload.decision_provider, 'subq');
});

test('no configured decision model disables analysis', async () => {
  await page.route('**/api/providers', route => route.fulfill({json: {market: ['yahoo'], decision: []}}));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('decision model'));
  assert.equal(await page.locator('#analyze').isDisabled(), true);
});

test('multiple configured providers show only configured options', async () => {
  await page.route('**/api/providers', route => route.fulfill({json: {market: ['alpaca', 'yahoo'], decision: ['liquid', 'baseten']}}));
  await page.reload();
  await page.waitForFunction(() => !document.getElementById('analyze').disabled);
  assert.equal(await page.locator('#source').isVisible(), true);
  assert.equal(await page.locator('#decision-provider').isVisible(), true);
  assert.deepEqual(await page.locator('#decision-provider option').evaluateAll(options => options.map(option => option.value)), ['liquid', 'baseten']);
});

test('provider discovery failure prevents an unintended analysis', async () => {
  await page.route('**/api/providers', route => route.fulfill({status: 503, body: 'unavailable'}));
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#notice').textContent.includes('provider configuration'));
  assert.equal(await page.locator('#analyze').isDisabled(), true);
});

test('suggested ticker chips fill the symbol field without duplicates', async () => {
  assert.ok(await page.locator('.ticker-chip').count() >= 5);
  await page.locator('.ticker-chip', {hasText: 'NVDA'}).click();
  assert.equal(await page.locator('#symbol').inputValue(), 'SPY, NVDA');
  await page.locator('.ticker-chip', {hasText: 'NVDA'}).click();
  assert.equal(await page.locator('#symbol').inputValue(), 'SPY, NVDA');
});

test('multi-ticker workspace shows progress and lets users compare and select results', async () => {
  await page.fill('#symbol', 'AAPL, MSFT, NVDA, TSLA');
  await page.getByRole('button', {name: 'Remove TSLA'}).click();
  assert.equal(await page.locator('#symbol').inputValue(), 'AAPL, MSFT, NVDA');
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('**/api/analyze', async route => {
    const symbol = route.request().postDataJSON().symbol;
    if (symbol === 'MSFT') await pending;
    if (symbol === 'NVDA') return route.fulfill({status: 503, json: {error: 'Timed out'}});
    const data = result(symbol); if (symbol === 'AAPL') data.stale_preview = true;
    return route.fulfill({json: data});
  });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.querySelector('[data-symbol="AAPL"] .tab-state')?.textContent === 'BUY');
  assert.match(await page.locator('[data-symbol="MSFT"]').innerText(), /Analyzing/);
  assert.match(await page.locator('[data-symbol="AAPL"]').innerText(), /Stale/);
  assert.match(await page.locator('[data-symbol="NVDA"]').innerText(), /ERROR/);
  await page.locator('[data-symbol="AAPL"]').click();
  assert.match(await page.locator('#chart-symbol').innerText(), /AAPL/);
  release();
  await page.waitForFunction(() => document.getElementById('analysis-form').getAttribute('aria-busy') === 'false');
  await page.locator('[data-symbol="MSFT"]').click();
  assert.match(await page.locator('#chart-symbol').innerText(), /MSFT/);
  assert.match(await page.locator('[data-symbol="MSFT"]').innerText(), /142.85/);
  assert.match(await page.locator('[data-symbol="MSFT"]').innerText(), /HOLD/);
});

test('several tickers analyze in one run and switch through tabs', async () => {
  await page.fill('#symbol', 'AAPL, MSFT, NVDA');
  await page.route('**/api/analyze', route => {
    const symbol = route.request().postDataJSON().symbol;
    const data = result(symbol);
    if (symbol === 'MSFT') data.decision.action = 'SELL';
    if (symbol === 'NVDA') return route.fulfill({status: 503, json: {error: 'Model request timed out.'}});
    return route.fulfill({json: data});
  });
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('analysis-form').getAttribute('aria-busy') === 'false');
  assert.match(await page.locator('#chart-symbol').innerText(), /AAPL/);
  const tabs = page.locator('.result-tab');
  assert.equal(await tabs.nth(0).locator('.tab-state').innerText(), 'BUY');
  assert.equal(await tabs.nth(1).locator('.tab-state').innerText(), 'SELL');
  assert.equal(await tabs.nth(2).locator('.tab-state').innerText(), 'ERROR');
  await tabs.nth(1).click();
  await page.waitForFunction(() => document.getElementById('chart-symbol').textContent.includes('MSFT'));
  assert.equal(await page.locator('#model-action').innerText(), 'SELL');
  await tabs.nth(2).click();
  await page.waitForFunction(() => document.getElementById('notice').classList.contains('error'));
  assert.match(await page.locator('#notice').innerText(), /NVDA.*timed out/);
});

test('every succeeded ticker of a multi-ticker run lands in history', async () => {
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  await page.fill('#symbol', 'AAPL, MSFT');
  await page.route('**/api/analyze', route => route.fulfill({json: result(route.request().postDataJSON().symbol)}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('analysis-form').getAttribute('aria-busy') === 'false');
  await page.waitForFunction(() => document.querySelectorAll('#history tbody tr').length === 2);
  const stored = JSON.parse(await page.evaluate(() => localStorage.getItem('fasttrade-history')));
  assert.deepEqual(stored.entries.map(entry => entry.market.symbol), ['AAPL', 'MSFT']);
  assert.ok(stored.entries.every(entry => !Object.keys(entry).some(key => key.startsWith('_'))));
});

test('paper trading fills at the analyzed close and only sells held shares', async () => {
  const data = result('NVDA');
  data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#paper-badge').innerText(), 'READY');
  assert.equal(await page.locator('#paper-sell').isHidden(), true);
  await page.fill('#paper-qty', '2');
  assert.equal(await page.locator('#paper-buy').isEnabled(), true);
  await page.locator('#paper-buy').click();
  await page.waitForFunction(() => document.getElementById('paper-position').textContent.includes('2.00 shares'));
  assert.match(await page.locator('#paper-cash').innerText(), /9,714/);
  data.decision.action = 'SELL';
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('model-action').textContent === 'SELL');
  assert.equal(await page.locator('#paper-qty').inputValue(), '0.2');
  await page.fill('#paper-qty', '2');
  assert.equal(await page.locator('#paper-sell').isEnabled(), true);
  await page.locator('#paper-sell').click();
  await page.waitForFunction(() => document.getElementById('paper-position').textContent.includes('None held'));
  assert.equal(await page.locator('#paper-sell').isDisabled(), true);
  await page.locator('.desktop-rail a[href="#portfolio-panel"]').click();
  await page.waitForFunction(() => !document.getElementById('fills').hidden);
  assert.match(await page.locator('#fills tbody').innerText(), /NVDA/);
  assert.match(await page.locator('#portfolio-realized').innerText(), /\+0\.00/);
});

test('paper cash limits buys and the portfolio persists across reloads', async () => {
  const data = result('NVDA'); data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  await page.fill('#paper-qty', '100000');
  assert.equal(await page.locator('#paper-buy').isDisabled(), true);
  await page.fill('#paper-qty', '2');
  await page.locator('#paper-buy').click();
  await page.waitForFunction(() => document.getElementById('paper-position').textContent.includes('2.00 shares'));
  await page.reload();
  await page.waitForFunction(() => document.getElementById('portfolio-cash').textContent.includes('9,714'));
  assert.match(await page.locator('#positions tbody').innerText(), /NVDA/);
  await openView('portfolio-panel');
  await page.locator('#portfolio-reset').click();
  await page.waitForFunction(() => document.getElementById('portfolio-cash').textContent.includes('10,000.00'));
  assert.equal(await page.locator('#positions').isHidden(), true);
});

test('stale results cannot be paper traded and history replays stay blocked', async () => {
  const data = result('NVDA');
  data.stale_preview = true;
  data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => !document.getElementById('decision-content').hidden);
  assert.equal(await page.locator('#paper-badge').innerText(), 'TRADING BLOCKED');
  await page.fill('#paper-qty', '2');
  assert.equal(await page.locator('#paper-buy').isDisabled(), true);
  assert.equal(await page.locator('#paper-sell').isDisabled(), true);
});

test('replayed history entries cannot be paper traded', async () => {
  await openView('history-panel');
  await page.locator('#history-save').check();
  await openView('workspace');
  const data = result('NVDA'); data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.querySelectorAll('#history tbody tr').length === 1);
  await openView('history-panel');
  await page.locator('.history-link').click();
  await page.waitForFunction(() => document.getElementById('paper-badge').textContent === 'TRADING BLOCKED');
  await page.fill('#paper-qty', '2');
  assert.equal(await page.locator('#paper-buy').isDisabled(), true);
  assert.equal(await page.locator('#paper-sell').isDisabled(), true);
});

test('existing paper positions and fills remain visible after reload', async () => {
  await page.evaluate(() => localStorage.setItem('fasttrade-paper', JSON.stringify({version: 1, cash: 9714.30, positions: {NVDA: {shares: 2, cost: 285.70}}, realized: 0, fills: [{time: '2026-01-02T20:55:00Z', symbol: 'NVDA', side: 'BUY', quantity: 2, price: 142.85, value: 285.70}]})));
  await page.reload();
  await openView('portfolio-panel');
  assert.match(await page.locator('#positions tbody').innerText(), /NVDA/);
  assert.match(await page.locator('#fills tbody').innerText(), /NVDA/);
  assert.match(await page.locator('#portfolio-cash').innerText(), /9,714/);
});

test('a corrupt paper portfolio is reported and reset restores storage', async () => {
  await page.evaluate(() => localStorage.setItem('fasttrade-paper', '{"version":1,"cash":"lots","positions":{"NVDA":{"shares":"2"}},"fills":[]}'));
  await page.reload();
  await page.waitForFunction(() => document.getElementById('portfolio-note').textContent.includes('invalid'));
  await openView('portfolio-panel');
  await page.locator('#portfolio-reset').click();
  await page.waitForFunction(() => document.getElementById('portfolio-cash').textContent.includes('10,000.00'));
  const stored = JSON.parse(await page.evaluate(() => localStorage.getItem('fasttrade-paper')));
  assert.equal(stored.cash, 10000);
});

test('history and portfolio pages are reachable from both navigations', async () => {
  assert.equal(await page.locator('.desktop-rail a[href="#portfolio-panel"]').count(), 1);
  assert.equal(await page.locator('.mobile-nav a[href="#portfolio-panel"]').count(), 1);
  await page.locator('.desktop-rail a[href="#portfolio-panel"]').click();
  await page.waitForFunction(() => document.querySelector('.desktop-rail a[href="#portfolio-panel"]').classList.contains('active'));
  assert.equal(await page.locator('#portfolio-panel').isVisible(), true);
  assert.equal(await page.locator('#history-panel').isHidden(), true);
  assert.equal(await page.locator('#analysis-form').isHidden(), true);
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('.mobile-nav a[href="#history-panel"]').click();
  await page.waitForFunction(() => document.querySelector('.mobile-nav a[href="#history-panel"]').getAttribute('aria-current') === 'location');
  assert.equal(await page.locator('#history-panel').isVisible(), true);
  assert.equal(await page.locator('#portfolio-panel').isHidden(), true);
  await page.locator('.mobile-nav a[href="#workspace"]').click();
  await page.waitForFunction(() => !document.getElementById('analysis-view').hidden);
  assert.equal(await page.locator('#analysis-form').isVisible(), true);
  assert.equal(await page.locator('#history-panel').isHidden(), true);
  await page.setViewportSize({width: 1440, height: 1000});
});

test('paper trading controls are present and the existing portfolio remains readable', async () => {
  assert.equal(await page.locator('.paper-card').count(), 1);
  await openView('portfolio-panel');
  assert.equal(await page.locator('#portfolio-cash').innerText(), '10,000.00');
});

test('paper controls follow the current signal and do not offer a blocked trade', async () => {
  const data = result('NVDA');
  data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('model-action').textContent === 'BUY');
  assert.equal(await page.locator('#paper-buy').isVisible(), true);
  assert.equal(await page.locator('#paper-sell').isHidden(), true);
  data.decision.action = 'SELL';
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('model-action').textContent === 'SELL');
  assert.equal(await page.locator('#paper-buy').isHidden(), true);
  assert.equal(await page.locator('#paper-sell').isVisible(), true);
  data.actionable_signal = 'HOLD'; data.risk_rejections = ['Risk limit reached'];
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('risk-action').textContent.includes('HOLD'));
  assert.equal(await page.locator('#paper-buy').isHidden(), true);
  assert.equal(await page.locator('#paper-sell').isHidden(), true);
});

test('paper quantity and estimated value are suggested and remain editable', async () => {
  const data = result('NVDA'); data.actionable_signal = null;
  await page.route('**/api/analyze', route => route.fulfill({json: data}));
  await page.locator('#analyze').click();
  await page.waitForFunction(() => document.getElementById('model-action').textContent === 'BUY');
  assert.equal(await page.locator('#paper-allocation').count(), 0);
  assert.equal(await page.locator('#paper-qty').inputValue(), '7');
  assert.equal(await page.locator('#paper-cash').innerText(), '10,000.00');
  assert.match(await page.locator('#paper-estimate').innerText(), /999\.95/);
  await page.locator('#paper-qty').fill('2');
  assert.match(await page.locator('#paper-estimate').innerText(), /285\.70/);
});
