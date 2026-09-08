const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const chartSource = fs.readFileSync(path.join(__dirname, '../../static/js/components/charts.js'), 'utf8');
const pageSource = fs.readFileSync(path.join(__dirname, '../../static/js/pages/analytics.js'), 'utf8');
const plain = value => JSON.parse(JSON.stringify(value));
const canvasId = 'analytics-daily-trend-chart';
const daily = {
  count_semantics: 'sentiment_memberships',
  items: [
    { date: '2026-08-01', issue_count: 5, sentiment_counts: { 'Tích cực': 3, 'Trung lập': 2, 'Tiêu cực': 1, 'Chưa gán': 1 }, sentiment_membership_count: 7 },
    { date: '2026-08-02', issue_count: 2, sentiment_counts: { 'Tích cực': 0, 'Trung lập': 0, 'Tiêu cực': 0, 'Chưa gán': 2 }, sentiment_membership_count: 2 },
  ],
};

function harness(theme = 'dark') {
  const charts = [];
  const element = { innerHTML: '' };
  const ctx = { createLinearGradient: () => ({ addColorStop() {} }) };
  class Chart {
    constructor(context, config) {
      this.config = config;
      this.data = config.data;
      this.options = config.options;
      this.destroyed = false;
      charts.push(this);
    }
    destroy() { this.destroyed = true; }
    update() {}
  }
  const context = {
    window: { Chart }, Chart,
    document: {
      readyState: 'loading', addEventListener() {},
      documentElement: { getAttribute: () => theme },
      getElementById(id) {
        return id === canvasId && element.innerHTML.includes('<canvas')
          ? { getContext: () => ctx } : null;
      },
      createElement() {
        return { set textContent(value) {
          this.innerHTML = String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        } };
      },
    },
    getComputedStyle: () => ({ getPropertyValue: () => '' }),
  };
  vm.runInNewContext(chartSource, context);
  context.Charts = context.window.Charts;
  // Expose the private renderer only inside this VM; production API is unchanged.
  const marker = 'return { render, destroy,';
  assert.ok(pageSource.includes(marker));
  vm.runInNewContext(pageSource.replace(marker, 'return { renderDailyTrend, render, destroy,'), context);
  return {
    charts, element, helpers: context.Charts, page: context.window.AnalyticsPage,
    render(data) {
      context.window.AnalyticsPage.renderDailyTrend(element, data);
      return element.innerHTML;
    },
  };
}

test('daily renderer preserves exact sentiment memberships including overlap and explicit unassigned counts', () => {
  const h = harness();
  const before = plain(daily);
  const html = h.render(daily);
  const chart = h.charts[0];
  assert.equal(chart.config.type, 'bar');
  assert.deepEqual(plain(chart.data.labels), ['2026-08-01', '2026-08-02']);
  assert.deepEqual(plain(chart.data.datasets.map(ds => [ds.label, ds.data, ds.backgroundColor])), [
    ['Tích cực', [3, 0], '#22c55e'],
    ['Trung lập', [2, 0], '#3b82f6'],
    ['Tiêu cực', [1, 0], '#ef4444'],
    ['Chưa gán', [1, 2], '#94a3b8'],
  ]);
  assert.equal(chart.data.datasets.reduce((sum, ds) => sum + ds.data[0], 0), daily.items[0].sentiment_membership_count);
  assert.ok(daily.items[0].sentiment_membership_count > daily.items[0].issue_count);
  assert.match(html, /analytics-panel-note/);
  assert.match(html, /lượt cảm xúc/);
  assert.match(html, /Một vấn đề có thể có nhiều cảm xúc trong cùng ngày/);
  assert.match(html, /tổng lượt cảm xúc có thể vượt số vấn đề duy nhất theo ngày/);
  assert.doesNotMatch(html, /chờ cập nhật backend/);
  assert.deepEqual(daily, before);
  h.page.destroy();
  assert.equal(chart.destroyed, true, 'page destroy retains the daily chart ID');
});

test('generic stacked helper preserves datasets and configures integer zero-based stacked axes in both themes', () => {
  for (const theme of ['light', 'dark']) {
    const h = harness(theme);
    h.element.innerHTML = '<canvas></canvas>';
    assert.equal(typeof h.helpers.createStackedDatasetChart, 'function');
    const datasets = [{ label: 'One', data: [2], backgroundColor: '#22c55e' }, { label: 'Two', data: [3], backgroundColor: '#ef4444' }];
    const chart = h.helpers.createStackedDatasetChart(canvasId, ['2026-08-01'], datasets);
    assert.equal(chart.config.type, 'bar');
    assert.deepEqual(plain(chart.data), { labels: ['2026-08-01'], datasets });
    assert.equal(chart.options.scales.x.stacked, true);
    assert.equal(chart.options.scales.y.stacked, true);
    assert.equal(chart.options.scales.y.beginAtZero, true);
    assert.equal(chart.options.scales.y.ticks.precision, 0);
    assert.equal(chart.options.plugins.legend.display, true);
    assert.equal(chart.options.scales.y.grid.color, theme === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)');
    h.helpers.createStackedDatasetChart(canvasId, [], []);
    assert.equal(chart.destroyed, true);
    h.helpers.destroy(canvasId);
    assert.equal(h.charts[1].destroyed, true);
    h.element.innerHTML = '';
    assert.equal(h.helpers.createStackedDatasetChart(canvasId, [], []), null);
  }
});