(function (root) {
  'use strict';
  function chartLayout(width, height, count, axisWidth = 62) {
    const left = 8, right = axisWidth, top = 12, bottom = 25;
    const plotWidth = Math.max(1, width - left - right);
    const usableHeight = Math.max(1, height - top - bottom);
    const priceBottom = top + usableHeight * .72;
    const volumeTop = top + usableHeight * .79;
    const volumeBottom = top + usableHeight;
    const step = plotWidth / Math.max(1, count);
    return {left, right, top, bottom, plotWidth, priceBottom, volumeTop, volumeBottom, step, bodyWidth: Math.min(9, step * .68)};
  }
  function candleAtX(x, layout, count) {
    if (!count) return null;
    return Math.max(0, Math.min(count - 1, Math.floor((x - layout.left) / layout.step)));
  }
  function timeLabels(candles, maximum) {
    if (!candles.length || maximum < 1) return [];
    const count = Math.min(candles.length, maximum);
    const format = new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false});
    return Array.from({length: count}, (_, i) => {
      const index = count === 1 ? candles.length - 1 : Math.round(i * (candles.length - 1) / (count - 1));
      return {index, text: format.format(new Date(candles[index].timestamp)).replace(',', '')};
    });
  }
  function priceDigits(span) {
    return Math.min(8, Math.max(2, Math.ceil(-Math.log10(Math.max(span, 1e-12))) + 2));
  }
  function hasTimeGap(previous, next, timeframe) {
    return new Date(next) - new Date(previous) > parseInt(timeframe, 10) * 60 * 1500;
  }
  const api = {chartLayout, candleAtX, timeLabels, priceDigits, hasTimeGap};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ChartMath = api;
})(typeof window !== 'undefined' ? window : globalThis);
