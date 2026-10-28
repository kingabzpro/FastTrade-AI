const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chartLayout, candleAtX, timeLabels, priceDigits, hasTimeGap } = require('../fasttrade/static/chart-math.js');

test('dense charts keep candle bodies within each interval', () => {
  const layout = chartLayout(290, 300, 200);
  assert.ok(layout.step > 0);
  assert.ok(layout.bodyWidth <= layout.step);
  assert.equal(candleAtX(layout.left, layout, 200), 0);
  assert.equal(candleAtX(layout.left + layout.plotWidth, layout, 200), 199);
});

test('time labels use actual displayed candle timestamps in UTC', () => {
  const candles = Array.from({length: 60}, (_, i) => ({timestamp: new Date(Date.UTC(2026, 0, 2, 14, 30) + i * 300000).toISOString()}));
  const labels = timeLabels(candles, 2);
  assert.deepEqual(labels.map(item => item.index), [0, 59]);
  assert.equal(labels[0].text, '02 Jan 14:30');
  assert.equal(labels[1].text, '02 Jan 19:25');
});

test('empty chart and out-of-range pointer have safe defaults', () => {
  const layout = chartLayout(200, 200, 0);
  assert.equal(candleAtX(0, layout, 0), null);
  assert.deepEqual(timeLabels([], 3), []);
});

test('small price ranges retain precision and time gaps remain visible', () => {
  assert.ok(priceDigits(0.0002) >= 5);
  assert.equal(priceDigits(5), 2);
  assert.equal(hasTimeGap('2026-01-02T14:30:00Z', '2026-01-02T14:40:00Z', '5m'), true);
  assert.equal(hasTimeGap('2026-01-02T14:30:00Z', '2026-01-02T14:35:00Z', '5m'), false);
});

test('wide price labels reserve their own axis space', () => {
  const layout = chartLayout(320, 300, 30, 95);
  assert.equal(layout.right, 95);
  assert.equal(layout.plotWidth, 320 - layout.left - 95);
});
