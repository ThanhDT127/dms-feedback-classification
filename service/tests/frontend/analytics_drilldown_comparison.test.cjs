const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/pages/analytics.js'), 'utf8');
function harness() {
  const nodes = {};
  for (const id of ['analytics-issue-unit', 'analytics-issue-label', 'analytics-issue-product', 'analytics-issue-status', 'analytics-issue-options-error', 'analytics-comparison', 'analytics-comparison-period']) nodes[id] = { value: '', innerHTML: '', disabled: false, hidden: true };
  const calls = [];
  const api = {
    async getAnalyticsIssueFilterOptions(params) { calls.push(params); return { units: ['A'], labels: ['Báo lỗi', '__unlabeled__'], products: ['LED'], statuses: ['Chờ xử lý'] }; },
    async getAnalyticsComparison(params) { calls.push(params); return { current_range: { from: params.from, to: params.to }, previous_range: { from: '2025-08-01', to: '2025-08-31' }, metrics: { total_issues: { current: 12, previous: 10, change: 2, change_percent: 20, unit: 'count', available: true } } }; },
  };
  const context = { window: {}, API: api, AbortController, console, document: { getElementById: id => nodes[id] || null, createElement() { return { set textContent(v) { this.innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }; } } };
  vm.runInNewContext(source.replace('return { render, destroy,', 'return { _state, loadIssueFilterOptions, onIssueFilterChange, loadComparison, renderComparison, render, destroy,'), context);
  return { page: context.window.AnalyticsPage, nodes, api, calls };
}
test('detail dropdowns load real server options and preserve global scope', async () => {
  const h = harness();
  h.page._state.filters = { from: '2026-08-01', to: '2026-08-31', unit: 'Global', district: 'D' };
  await h.page.loadIssueFilterOptions();
  assert.equal(h.calls[0].unit, 'Global');
  assert.equal(h.calls[0].district, 'D');
  assert.equal(h.calls[0].page, undefined);
  assert.match(h.nodes['analytics-issue-label'].innerHTML, /Chưa có nhãn/);
  assert.equal(h.nodes['analytics-issue-unit'].disabled, true);
});
test('parent change clears and disables descendants before awaiting; stale responses ignored', async () => {
  const h = harness();
  const pending = [];
  h.api.getAnalyticsIssueFilterOptions = () => new Promise(resolve => pending.push(resolve));
  h.nodes['analytics-issue-label'].value = 'old';
  h.nodes['analytics-issue-product'].value = 'old';
  h.nodes['analytics-issue-unit'].value = 'A';
  const first = h.page.onIssueFilterChange('unit');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.nodes['analytics-issue-product'].value, '');
  assert.equal(h.nodes['analytics-issue-product'].disabled, true);
  h.nodes['analytics-issue-unit'].value = 'B';
  const second = h.page.onIssueFilterChange('unit');
  pending[0]({ units: ['A'], labels: ['stale'], products: [], statuses: [] });
  await first;
  await new Promise(resolve => setImmediate(resolve));
  pending[1]({ units: ['A', 'B'], labels: ['new'], products: [], statuses: [] });
  await second;
  assert.match(h.nodes['analytics-issue-label'].innerHTML, /new/);
  assert.doesNotMatch(h.nodes['analytics-issue-label'].innerHTML, /stale/);
});
test('changing unit drilldown clears dependent applied filters and reloads options', async () => {
  const h = harness();
  h.page._state.issueFilters = { unit: '', label: 'old', product: 'old', status: 'old' };
  h.page._state.units = [{ label: 'A' }];
  h.nodes['analytics-issue-label'].value = 'old';
  h.page.filterIssuesByUnit(0);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.page._state.issueFilters.label, '');
  assert.equal(h.nodes['analytics-issue-label'].value, '');
  assert.equal(h.calls[0].issue_unit, 'A');
});
test('rehydration restores applied detail filters instead of empty new controls', async () => {
  const h = harness();
  h.page._state.issueFilters = { unit: 'A', label: 'Báo lỗi', product: 'LED', status: 'Chờ xử lý' };
  await h.page.loadIssueFilterOptions(true);
  assert.equal(h.calls[0].issue_unit, 'A');
  assert.equal(h.nodes['analytics-issue-product'].value, 'LED');
});
test('unapplied global unit selection cannot change comparison scope', async () => {
  const h = harness();
  h.nodes['analytics-unit'] = { value: 'B' };
  h.nodes['analytics-district'] = { value: 'D' };
  h.api.getAnalyticsFilterOptions = async () => ({ units: ['A', 'B'], districts: ['E'] });
  h.page._state.filters = { from: '2026-08-01', to: '2026-08-31', unit: 'A', district: 'D' };
  await h.page.onUnitChange();
  await h.page.loadComparison();
  assert.equal(h.calls[0].unit, 'A');
  assert.equal(h.calls[0].district, 'D');
});
test('new controls are wired into render, refresh and public handlers', () => {
  for (const key of ['unit', 'label', 'product', 'status']) assert.ok(source.includes(`selectField('analytics-issue-${key}'`));
  assert.match(source, /await loadIssueFilterOptions\(true\)/);
  assert.match(source, /await loadComparison\(\)/);
  assert.doesNotMatch(source, /Promise\.all(?:Settled)?\(/);
  assert.match(source, /onchange="AnalyticsPage.loadComparison\(\)"/);
  assert.match(source, /\['quarter', 'Quý trước'\]/);
  assert.match(source, /\['year', 'Năm trước'\]/);
});
test('comparison requires selected dates and requests chosen calendar period with global filters', async () => {
  const h = harness();
  h.nodes['analytics-comparison-period'].value = 'quarter';
  await h.page.loadComparison();
  assert.equal(h.calls.length, 0);
  assert.match(h.nodes['analytics-comparison'].innerHTML, /ngày/);
  h.page._state.filters = { from: '2026-08-01', to: '2026-08-31', unit: 'A', district: 'D' };
  await h.page.loadComparison();
  assert.equal(h.calls[0].period, 'quarter');
  assert.equal(h.calls[0].unit, 'A');
  assert.match(h.nodes['analytics-comparison'].innerHTML, /2025-08-01/);
  assert.match(h.nodes['analytics-comparison'].innerHTML, /20/);
});
test('comparison unavailable values never fabricate percent or model accuracy', () => {
  const h = harness();
  h.page.renderComparison(h.nodes['analytics-comparison'], { current_range: {}, previous_range: {}, metrics: { total_issues: { current: 5, previous: 0, change: null, change_percent: null, available: false }, model_accuracy: { current: null, previous: null, available: false } } });
  const html = h.nodes['analytics-comparison'].innerHTML;
  assert.match(html, /Chưa đủ dữ liệu/);
  assert.doesNotMatch(html, /NaN|Infinity|null%|undefined/);
});
