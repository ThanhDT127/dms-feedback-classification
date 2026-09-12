const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../../static/js/pages/analytics.js'), 'utf8');

function harness() {
  const nodes = { 'analytics-issues': { innerHTML: '' }, 'analytics-issue-unit': { value: '' } };
  const requests = [];
  const modals = [];
  const app = { showModal(html) { modals.push(html); } };
  const context = { AbortController,
    window: { App: app }, App: app,
    document: {
      getElementById(id) { return nodes[id] || null; },
      createElement() {
        return { set textContent(value) {
          this.innerHTML = String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        } };
      },
    },
    API: { async getAnalyticsIssues(params) {
      requests.push(params);
      return { items: [], page: params.page, total_pages: 3, total: 60 };
    } },
  };
  // Test-only access to private render/state; production exports remain unchanged.
  const exportMarker = 'return { render, destroy,';
  assert.ok(source.includes(exportMarker));
  vm.runInNewContext(source.replace(exportMarker, 'return { _state, renderIssues, issueQueryParams, render, destroy,'), context);
  const page = context.window.AnalyticsPage;
  function render(items, pageNumber = 1, totalPages = 3) {
    const data = { items, total: 60, page: pageNumber, total_pages: totalPages };
    page._state.issues = data;
    page._state.issuePage = pageNumber;
    page.renderIssues(nodes['analytics-issues'], data);
    return nodes['analytics-issues'].innerHTML;
  }
  return { page, render, requests, modals };
}

const issue = {
  issue_code: 'QA-1', issue_date: '2026-08-01', source: 'CRM', unit_name: 'Unit A',
  business_status: 'Đã xử lý', sentiment: 'Tích cực', classification_state: 'classified',
  labels: ['Báo CL tốt'], product: 'LED', content: 'Nội dung phản hồi đầy đủ.',
  source_file_name: 'fixture.xlsx', source_row_number: 8, job_id: 'fixture-job',
};

test('detail table displays real status and sentiment badges without priority claims', () => {
  const h = harness();
  const html = h.render([issue]);
  assert.match(html, /table analytics-issues-table/);
  assert.match(html, /badge[^"<>]*analytics-issue-status[^"<>]*">Đã xử lý<\/span>/);
  assert.match(html, /badge-green[^"<>]*"[^>]*>Tích cực<\/span>/);
  assert.match(html, /Kết quả phân loại/);
  assert.match(html, /analytics-issue-summary/);
  assert.match(html, /aria-label="Xem chi tiết vấn đề QA-1"/);
  assert.doesNotMatch(html, /Quá hạn|SLA|Phản hồi cần ưu tiên/);
});

test('unknown values are escaped, kept verbatim and never used as CSS classes', () => {
  const h = harness();
  const attack = '<img src=x onerror="alert(1)">';
  const html = h.render([{ ...issue, issue_code: attack, business_status: attack, sentiment: attack, content: attack }]);
  assert.ok(html.includes('&lt;img'));
  assert.doesNotMatch(html, /<img|class="[^"]*onerror/);
  assert.match(html, /badge-muted[^"<>]*"[^>]*>&lt;img/);
  assert.ok(html.includes('&quot;alert(1)&quot;'));
});

test('pending and missing classifications remain honest; source fallback stays DMS', () => {
  const h = harness();
  const pending = h.render([{ ...issue, source: '', classification_state: 'pending' }]);
  assert.match(pending, />DMS</);
  assert.match(pending, /Chưa có nhãn/);
  assert.match(pending, /Chưa gán cảm xúc/);
  assert.doesNotMatch(pending, /Tích cực|Báo CL tốt/);
  const missing = h.render([{ ...issue, labels: [], sentiment: null, business_status: null }]);
  assert.match(missing, /Chưa có nhãn/);
  assert.match(missing, /Chưa gán cảm xúc/);
  assert.match(missing, /analytics-issue-status[^"<>]*">—<\/span>/);
  for (const [sentiment, color] of [['Tiêu cực', 'red'], ['Trung lập', 'muted']]) {
    assert.match(h.render([{ ...issue, sentiment }]), new RegExp(`badge-${color}[^"<>]*"[^>]*>${sentiment}<`));
  }
});

test('summary retains full content and detail modal retains provenance and sentiment', () => {
  const h = harness();
  const content = ('Phản hồi rất dài <script>không thực thi</script>\n').repeat(50);
  const html = h.render([{ ...issue, content }]);
  assert.ok(html.includes(content.replace(/</g, '&lt;').replace(/>/g, '&gt;')));
  h.page.showIssueDetail(0);
  const modal = h.modals[0];
  for (const value of ['fixture.xlsx', 'fixture-job', 'Dòng Excel', 'Trạng thái phân loại', 'Cảm xúc', 'Tích cực']) {
    assert.ok(modal.includes(value), value);
  }
  assert.doesNotMatch(modal, /<script>/);
  assert.ok(modal.includes(content.replace(/</g, '&lt;').replace(/>/g, '&gt;')));
  h.page.showIssueDetail(999);
  assert.equal(h.modals.length, 1);
});

test('server pagination and unit drilldown preserve global unit scope', async () => {
  const h = harness();
  let html = h.render([issue], 1, 3);
  assert.match(html, /disabled[^>]*changeIssuePage\(-1\)/);
  h.page._state.filters = { from: '2026-08-01', to: '2026-08-31', unit: 'Global unit', district: 'District' };
  h.page._state.units = [{ label: 'Local unit' }];
  h.page.filterIssuesByUnit(0);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests[0].unit, 'Global unit');
  assert.equal(h.requests[0].page, 1);
  assert.equal(h.requests[0].district, 'District');
  h.page.changeIssuePage(1);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.requests[1].page, 2);
  assert.equal(h.requests[1].unit, 'Global unit');
  assert.equal(h.requests[1].page_size, 25);
  html = h.render([issue], 3, 3);
  assert.match(html, /disabled[^>]*changeIssuePage\(1\)/);
  h.page.changeIssuePage(1);
  assert.equal(h.requests.length, 2);
  assert.match(h.render([]), /Không có vấn đề nào/);
});
