const { test, before, after, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createServer } = require('node:http');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { chromium } = require('playwright');

let server, browser, page, url;
before(async () => {
  const root = join(__dirname, '../fasttrade/static');
  const files = new Map(['index.html', 'style.css', 'app.js', 'chart-math.js', 'logo.svg', 'favicon.ico']
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
  await page.goto(url);
});

function result(symbol = 'NVDA') {
  return {
    market: { symbol, timeframe: '5m', candle_timestamp: '2026-01-02T20:55:00Z', features: {
      price: 142.85, rsi_14: 54, macd: .12, vwap_delta: .003, volume_ratio: 1.2, atr_14: .8,
    }, headlines: [] },
    source: 'Alpaca IEX', stale_preview: false,
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

test('dashboard uses Alpaca data without a source selector', async () => {
  assert.equal(await page.locator('#chart-window').isDisabled(), true);
  assert.equal(await page.locator('#chart-overlay').isDisabled(), true);
  assert.equal(await page.locator('#source').count(), 0);
  assert.equal(await page.locator('#timeframe').isEnabled(), true);
  assert.match(await page.locator('#strip-source').innerText(), /Alpaca IEX/);
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

test('workspace has no sidebar and mobile navigation marks the selected destination', async () => {
  assert.equal(await page.locator('.sidebar').count(), 0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.mobile-nav a[href="#chart-panel"]').click();
  await page.waitForFunction(() => document.querySelector('.mobile-nav a[href="#chart-panel"]').getAttribute('aria-current') === 'location');
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
  await page.locator('.history-link').click();
  assert.equal(await page.locator('#risk-action').innerText(), 'SIGNAL ONLY');
});
