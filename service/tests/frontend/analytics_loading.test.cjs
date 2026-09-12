const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/pages/analytics.js'), 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));
const bundleKeys = ['overview', 'dailyTrend', 'issueTypes', 'geography', 'sources', 'units', 'groups', 'products', 'status'];
const bundle = () => Object.fromEntries(bundleKeys.map(key => [key, key === 'overview' ? { total_issues: { value: 123 } } : { items: [] }]));

function harness() {
  const nodes = {};
  const ids = [...source.matchAll(/(?:id=\"|getElementById\(')(analytics-[\w-]+)/g)].map(m => m[1]);
  for (const id of [...ids, 'app', 'analytics-sources', 'analytics-units', 'analytics-groups', 'analytics-products', 'analytics-processing-status', 'analytics-issue-types']) {
    nodes[id] = { innerHTML: '', textContent: '', value: '', hidden: true, disabled: false, options: [], classList: { add() {}, remove() {}, toggle() {} } };
  }
  const calls = [];
  let active = 0, maxActive = 0;
  const api = new Proxy({}, { get(_, method) { return (params, opts = {}) => {
    active++;
    maxActive = Math.max(maxActive, active);
    return new Promise((resolve, reject) => {
      const call = { method, params, opts, done: false };
      function finish(error, data) {
        if (call.done) return;
        call.done = true;
        active--;
        error ? reject(error) : resolve(data);
      }
      call.resolve = data => finish(null, data);
      call.reject = error => finish(error);
      opts.signal?.addEventListener('abort', () => finish(Object.assign(new Error('Aborted'), { name: 'AbortError' })), { once: true });
      calls.push(call);
    });
  }; } });
  const chartCalls = [];
  const charts = new Proxy({}, { get(_, method) { return (...args) => chartCalls.push([method, ...args]); } });
  const context = { window: {}, API: api, Charts: charts, AbortController, console: { warn() {}, error() {} }, document: {
    getElementById: id => nodes[id] || null,
    createElement: () => ({ set textContent(v) { this.innerHTML = String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); } }),
  } };
  vm.runInNewContext(source.replace('return { render, destroy,', 'return { _state, loadIssues, loadDuplicates, loadFilterOptions, loadIssueFilterOptions, render, destroy,'), context);
  return { page: context.window.AnalyticsPage, nodes, calls, chartCalls, maxActive: () => maxActive,
    async drain() {
      for (let i = 0; i < 40; i++) {
        await tick();
        const pending = calls.find(c => !c.done);
        if (!pending) return;
        pending.resolve(pending.method === 'getAnalyticsDashboard' ? bundle() : { items: [], units: [], districts: [], labels: [], products: [], statuses: [] });
      }
      throw new Error('Loading did not finish');
    },
  };
}

test('only dashboard 404 uses sequential legacy fallback; other failures stay explicit', async () => {
  for (const status of [404, 401, 403, 500]) {
    const h = harness();
    const loading = h.page.refresh();
    await tick();
    h.calls[0].reject(Object.assign(new Error('bundle unavailable'), { status }));
    await tick();
    assert.equal(h.calls[1].method, status === 404 ? 'getAnalyticsOverview' : 'getAnalyticsIssues');
    if (status !== 404) assert.match(h.nodes['analytics-overview'].innerHTML, /bundle unavailable/);
    if (status === 404) {
      h.calls[1].reject(new Error('overview failed'));
      await tick();
      assert.match(h.nodes['analytics-overview'].innerHTML, /overview failed/);
      assert.equal(h.calls[2].method, 'getAnalyticsDailyTrend');
    }
    await h.drain();
    await loading;
    assert.equal(h.maxActive(), 1);
  }
});

test('API dashboard preserves HTTP status, auth and abort options', async () => {
  const apiSource = fs.readFileSync(path.join(__dirname, '../../static/js/api.js'), 'utf8');
  const calls = [];
  const context = { window: { location: { origin: 'https://test.invalid' } }, localStorage: { getItem: key => key === 'dms_access_token' ? 'token' : null }, URLSearchParams, FormData, console: { error() {} }, fetch: async (url, opts) => {
    calls.push({ url, opts });
    return { ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'missing' }) };
  } };
  vm.runInNewContext(apiSource, context);
  assert.equal(typeof context.window.API.getAnalyticsDashboard, 'function');
  const signal = new AbortController().signal;
  await assert.rejects(context.window.API.getAnalyticsDashboard({ unit: 'A B', from: '' }, { signal }), error => error.status === 404 && error.message === 'missing');
  assert.equal(calls[0].url, 'https://test.invalid/api/analytics/dashboard?unit=A+B');
  assert.equal(calls[0].opts.headers.Authorization, 'Bearer token');
  assert.equal(calls[0].opts.signal, signal);
});

test('dashboard renders first, heavy panels finish one at a time, options stay bounded', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  assert.deepEqual(h.calls.map(c => c.method), ['getAnalyticsDashboard']);
  h.calls[0].resolve({ ...bundle(), dailyTrend: { items: [{ date: '2026-08-01', issue_count: 3 }] } });
  await tick();
  assert.match(h.nodes['analytics-overview'].innerHTML, /123/);
  assert.equal(h.calls[1].method, 'getAnalyticsIssues');
  const chartCount = h.chartCalls.length;
  h.calls[1].resolve({ items: [], total: 0 });
  await tick();
  assert.match(h.nodes['analytics-issues'].innerHTML, /Không có vấn đề nào/);
  assert.equal(h.calls[2].method, 'getAnalyticsUnitIssueTypeMatrix');
  await h.drain();
  await loading;
  assert.deepEqual(h.calls.map(c => c.method), ['getAnalyticsDashboard', 'getAnalyticsIssues', 'getAnalyticsUnitIssueTypeMatrix', 'getAnalyticsPriorityIssues', 'getAnalyticsDuplicates', 'getAnalyticsFilterOptions', 'getAnalyticsIssueFilterOptions']);
  assert.equal(h.maxActive(), 1);
  assert.equal(h.chartCalls.length, chartCount, 'heavy completion must not redraw initial charts');
});


test('refresh and destroy abort stale work without stale renders or queued fanout', async () => {
  const h = harness();
  const first = h.page.refresh();
  await tick();
  const firstCall = h.calls[0];
  const second = h.page.refresh();
  await tick();
  assert.equal(firstCall.opts.signal?.aborted, true);
  assert.equal(h.calls[1].method, 'getAnalyticsDashboard');
  h.calls[1].resolve(bundle());
  await tick();
  assert.match(h.nodes['analytics-overview'].innerHTML, /123/);
  const html = h.nodes['analytics-overview'].innerHTML;
  h.page.destroy();
  await tick();
  assert.ok(h.calls.every(call => call.done));
  assert.equal(h.nodes['analytics-overview'].innerHTML, html);
  await Promise.all([first, second]);
  assert.equal(h.maxActive(), 1);
});

test('issue pagination cannot cancel remaining panels and stale duplicate pages cannot overwrite', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  h.calls[0].resolve(bundle());
  await tick();
  h.page._state.issuePage = 2;
  const pageLoad = h.page.loadIssues();
  await tick();
  // A pending initial issues response must not replace the newer page.
  h.calls[1].resolve({ items: [{ issue_code: 'STALE' }], page: 1 });
  await h.drain();
  await Promise.all([loading, pageLoad]);
  assert.ok(h.calls.some(c => c.method === 'getAnalyticsPriorityIssues'));
  assert.ok(h.calls.some(c => c.method === 'getAnalyticsDuplicates'));
  assert.equal(h.maxActive(), 1);
  assert.doesNotMatch(h.nodes['analytics-issues'].innerHTML, /STALE/);
  h.page._state.duplicatePage = 2;
  const oldPage = h.page.loadDuplicates();
  await tick();
  const oldCall = h.calls.at(-1);
  h.page._state.duplicatePage = 3;
  const newPage = h.page.loadDuplicates();
  await tick();
  oldCall.resolve({ items: [], page: 2 });
  h.calls.at(-1).resolve({ items: [], page: 3 });
  await Promise.all([oldPage, newPage]);
  assert.equal(h.page._state.duplicates.page, 3);
});

test('priority and option errors stay explicit with retry, never derived from a partial issues page', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  h.calls[0].resolve(bundle());
  await tick();
  h.calls[1].resolve({ items: [{ issue_code: 'ONLY-PAGE-1', labels: ['Partial label'], sentiment: 'Tiêu cực' }], total: 300 });
  await tick();
  h.calls[2].resolve({ items: [] });
  await tick();
  h.calls[3].reject(new Error('priority offline'));
  await tick();
  assert.match(h.nodes['analytics-priority-issues'].innerHTML, /priority offline/);
  assert.match(h.nodes['analytics-priority-issues'].innerHTML, /Thử lại/);
  assert.doesNotMatch(h.nodes['analytics-priority-issues'].innerHTML, /ONLY-PAGE-1/);
  h.calls[4].resolve({ items: [] });
  await tick();
  h.calls[5].resolve({ units: [], districts: [] });
  await tick();
  h.calls[6].reject(new Error('options offline'));
  await loading;
  assert.equal(h.nodes['analytics-issue-options-error'].hidden, false);
  assert.match(h.nodes['analytics-issue-options-error'].innerHTML, /Thử lại/);
  assert.doesNotMatch(h.nodes['analytics-issue-label'].innerHTML, /Partial label/);
});

test('destroy during staged load discards partial cache before re-entry', async () => {
  const h = harness();
  h.page.render();
  await tick();
  h.calls[0].resolve(bundle());
  await tick();
  assert.equal(h.calls[1].method, 'getAnalyticsIssues');
  h.page.destroy();
  await tick();
  const before = h.calls.length;
  h.page.render();
  await tick();
  assert.equal(h.calls[before].method, 'getAnalyticsDashboard');
  await h.drain();
});

test('successful dashboard bundle reports every missing required panel as failure', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  const partial = bundle();
  delete partial.overview;
  delete partial.products;
  h.calls[0].resolve(partial);
  await tick();
  assert.match(h.nodes['analytics-overview'].innerHTML, /Không thể tải dữ liệu/);
  assert.match(h.nodes['analytics-products'].innerHTML, /Không thể tải dữ liệu/);
  assert.doesNotMatch(h.nodes['analytics-overview'].innerHTML, />0</);
  await h.drain();
  await loading;
  assert.match(h.nodes['analytics-page-status'].textContent, /2 phần dữ liệu/);
});

test('complete cache keeps aggregate failure status after page re-entry', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  const partial = bundle();
  delete partial.overview;
  delete partial.products;
  h.calls[0].resolve(partial);
  await h.drain();
  await loading;
  assert.match(h.nodes['analytics-page-status'].textContent, /2 phần dữ liệu/);

  h.page.destroy();
  h.page.render();
  assert.match(h.nodes['analytics-page-status'].textContent, /2 phần dữ liệu/);
  assert.match(h.nodes['analytics-overview'].innerHTML, /Không thể tải dữ liệu/);
  await h.drain();
});

test('pending refresh does not overwrite draft unit and district before Apply', async () => {
  const h = harness();
  const loading = h.page.refresh();
  await tick();
  h.calls[0].resolve(bundle());
  await tick();

  h.nodes['analytics-unit'].value = 'Draft Unit';
  const draftOptions = h.page.onUnitChange();
  h.calls[1].resolve({ items: [], total: 0 });
  await tick();
  assert.equal(h.calls[2].method, 'getAnalyticsFilterOptions');
  h.calls[2].resolve({ units: ['Draft Unit'], districts: ['Draft District'] });
  await draftOptions;
  h.nodes['analytics-district'].value = 'Draft District';

  await h.drain();
  await loading;
  assert.equal(h.nodes['analytics-unit'].value, 'Draft Unit');
  assert.equal(h.nodes['analytics-district'].value, 'Draft District');
  assert.deepEqual(
    { ...h.page._state.filters },
    { from: '', to: '', district: '', unit: '' },
    'draft selections must not alter the applied request scope',
  );
});

test('render and apply load options once, cache revisits do not refetch panels', async () => {
  const h = harness();
  h.page.render();
  await tick();
  assert.deepEqual(h.calls.map(c => c.method), ['getAnalyticsDashboard']);
  await h.drain();
  const initial = h.calls.length;
  h.page.destroy();
  h.page.render();
  assert.match(h.nodes['analytics-overview'].innerHTML, /123/);
  await h.drain();
  assert.deepEqual(h.calls.slice(initial).map(c => c.method), ['getAnalyticsFilterOptions', 'getAnalyticsIssueFilterOptions']);
  h.page.applyFilters();
  await h.drain();
  assert.equal(h.calls.filter(c => c.method === 'getAnalyticsFilterOptions').length, 3);
  assert.equal(h.maxActive(), 1);
});
