/* ============================================================
   Chat Blocks — hiển thị event câu trả lời (b07 D3, D4)
   Chỉ dựng DOM bằng createElement + textContent. Nội dung từ
   server (trích dẫn, nhận định) không đáng tin: không chèn HTML.
   ============================================================ */

window.ChatBlocks = (() => {
  const MAX_CHART_ANSWERS = 20;
  const EXPORT_ERRORS = {
    EXPORT_TOO_LARGE: 'Dữ liệu cần xuất vượt dung lượng cho phép. Bạn thu hẹp khoảng thời gian rồi xuất lại giúp tôi.',
    EXPORT_FAILED: 'Tôi chưa tạo được file lúc này. Bạn thử lại sau ít phút giúp tôi.',
  };
  const HTML_ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  // canvasId → answerId; để huỷ theo câu trả lời hoặc toàn bộ.
  const _charts = new Map();
  let _chartSeq = 0;

  function escape(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, ch => HTML_ESCAPES[ch]);
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null && text !== '') node.textContent = String(text);
    return node;
  }

  function button(className, text, onClick) {
    const node = el('button', className, text);
    node.type = 'button';
    if (typeof onClick === 'function') node.addEventListener('click', onClick);
    return node;
  }

  /* ---- Khung một câu trả lời ---- */

  function createAnswerView(answerId) {
    const root = el('div', 'chat-answer');
    root.dataset.answerId = answerId || '';
    const refusals = el('div', 'chat-answer-refusals');
    const blocks = el('div', 'chat-answer-blocks');
    const commentary = el('div', 'chat-answer-commentary chat-commentary');
    commentary.setAttribute('aria-live', 'polite');
    commentary.hidden = true;
    const extras = el('div', 'chat-answer-extras');
    const footer = el('div', 'chat-answer-footer');
    root.append(refusals, blocks, commentary, extras, footer);
    return root;
  }

  function slot(container, name) {
    return container.querySelector(`:scope > .chat-answer-${name}`) || container;
  }

  /**
   * Khung của một phần báo cáo (b10 D4). Không có `section` thì dùng thẳng khung khối,
   * nên câu trả lời thường hiển thị y như trước.
   */
  function sectionSlot(container, section) {
    const blocks = slot(container, 'blocks');
    if (!section || !section.id) return blocks;
    const id = String(section.id);
    let box = blocks.querySelector(`:scope > .chat-section[data-section-id="${CSS.escape(id)}"]`);
    if (!box) {
      box = el('section', 'chat-section');
      box.dataset.sectionId = id;
      if (section.title) box.appendChild(el('h3', 'chat-section-title', section.title));
      blocks.appendChild(box);
    }
    return box;
  }

  /* ---- Điều phối theo kiểu event ---- */

  /**
   * Hiển thị một event vào khung câu trả lời.
   * opts: { onAsk(question), onRetry(), onStatus(data), charts: boolean }
   */
  function renderEvent(container, event, opts = {}) {
    if (!container || !event) return;
    const data = event.data || {};
    switch (event.type) {
      case 'status':
        if (typeof opts.onStatus === 'function') opts.onStatus(data);
        return;
      case 'data_block': {
        const node = renderBlock(data, { answerId: container.dataset.answerId, charts: opts.charts !== false });
        if (node) {
          sectionSlot(container, data.section).appendChild(node);
          drawPendingCharts(node);
        }
        return;
      }
      case 'commentary':
        renderCommentary(container, data);
        return;
      case 'refusal':
        slot(container, 'refusals').appendChild(renderRefusal(data));
        return;
      case 'clarify':
        slot(container, 'extras').appendChild(renderClarify(data, opts));
        return;
      case 'suggestions':
        slot(container, 'extras').appendChild(renderSuggestions(data, opts));
        return;
      case 'error':
        slot(container, 'extras').appendChild(renderError(data, opts));
        return;
      case 'done':
        renderDone(container, data);
        return;
      default:
        console.warn('ChatBlocks: bỏ qua kiểu event không biết', event.type);
    }
  }

  /* ---- data_block ---- */

  const BLOCK_RENDERERS = {
    kpi: renderKpi,
    ranking: renderRanking,
    table: renderTable,
    timeseries: renderTimeseries,
    quote: renderQuote,
    export: renderExport,
  };

  function renderBlock(block, ctx = {}) {
    const render = BLOCK_RENDERERS[block && block.kind];
    if (!render) {
      console.warn('ChatBlocks: bỏ qua data_block có kind không biết', block && block.kind);
      return null;
    }
    const card = el('section', `chat-block chat-block-${block.kind}`);
    const head = el('div', 'chat-block-head');
    head.appendChild(el('h4', 'chat-block-title', block.title || 'Kết quả'));
    if (block.subtitle) head.appendChild(el('span', 'chat-block-subtitle', block.subtitle));
    card.appendChild(head);
    render(card, block.payload || {}, block, ctx);
    return card;
  }

  function renderKpi(card, payload) {
    // Khối "Điểm chính" (b10 D5) là các câu đã dựng sẵn, không phải ô KPI.
    if ((payload.items || []).some(item => item.display_text)) {
      const list = el('ul', 'chat-highlights');
      (payload.items || []).forEach(item => {
        list.appendChild(el('li', 'chat-highlight', item.display_text || item.display));
      });
      card.appendChild(list);
      return;
    }
    const grid = el('div', 'chat-kpi-grid');
    (payload.items || []).forEach(item => {
      const available = item.available !== false;
      const tile = el('div', `chat-kpi${available ? '' : ' chat-kpi-unavailable'}`);
      tile.appendChild(el('div', 'chat-kpi-label', item.label || item.key));
      tile.appendChild(el('div', 'chat-kpi-value', item.display || 'Chưa đủ dữ liệu'));
      const cmp = item.comparison;
      if (available && cmp) {
        const text = cmp.available
          ? `Kỳ trước: ${cmp.display || ''}${cmp.change_display ? ` (${cmp.change_display})` : ''}`
          : `Kỳ trước: ${cmp.change_display || 'Chưa đủ dữ liệu'}`;
        const sub = el('div', `chat-kpi-sub${cmp.direction ? ` chat-kpi-${cmp.direction}` : ''}`, text);
        tile.appendChild(sub);
      }
      grid.appendChild(tile);
    });
    card.appendChild(grid);
  }

  function renderRanking(card, payload, block, ctx) {
    const items = payload.items || [];
    const hint = block.chart_hint;
    if (ctx.charts && items.length >= 2 && (hint === 'bar' || hint === 'donut')) {
      card.appendChild(chartSlot(ctx.answerId, hint === 'bar' ? 'bar' : 'doughnut', {
        labels: items.map(i => i.label),
        values: items.map(i => Number(i.value) || 0),
        label: block.title || '',
        rows: items.length,
      }));
    }
    const wrap = el('div', 'chat-table-wrap');
    const table = el('table', 'chat-table');
    const thead = el('thead');
    const hr = el('tr');
    ['#', payload.label_title || 'Tên', payload.value_title || 'Số lượng', 'Tỉ lệ'].forEach(h => hr.appendChild(el('th', '', h)));
    thead.appendChild(hr);
    const tbody = el('tbody');
    items.forEach(item => {
      const tr = el('tr');
      tr.appendChild(el('td', 'chat-num', item.rank));
      tr.appendChild(el('td', '', item.label));
      tr.appendChild(el('td', 'chat-num', item.display));
      tr.appendChild(el('td', 'chat-num', item.percent_display || ''));
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    wrap.appendChild(table);
    card.appendChild(wrap);
    appendNotes(card, payload, items.length);
  }

  function renderTable(card, payload) {
    const columns = payload.columns || [];
    const rows = payload.rows || [];
    const wrap = el('div', 'chat-table-wrap');
    const table = el('table', 'chat-table');
    const thead = el('thead');
    const hr = el('tr');
    columns.forEach(col => hr.appendChild(el('th', '', col.header_vi || col.key)));
    thead.appendChild(hr);
    const tbody = el('tbody');
    rows.forEach(row => {
      const tr = el('tr');
      columns.forEach(col => {
        const numeric = col.format === 'int' || col.format === 'pct';
        tr.appendChild(el('td', numeric ? 'chat-num' : '', row[col.key]));
      });
      tbody.appendChild(tr);
    });
    table.append(thead, tbody);
    wrap.appendChild(table);
    card.appendChild(wrap);
    if (!rows.length) card.appendChild(el('p', 'chat-note', 'Không có dữ liệu.'));
    appendNotes(card, payload, rows.length);
  }

  function renderTimeseries(card, payload, block, ctx) {
    const points = (payload.points || []).filter(p => p.value !== null && p.value !== undefined);
    if (ctx.charts && points.length >= 2) {
      card.appendChild(chartSlot(ctx.answerId, 'line', {
        labels: points.map(p => p.display_date || p.date),
        values: points.map(p => Number(p.value) || 0),
        label: block.title || '',
        rows: 0,
      }));
    }
    // Chỉ hiện tổng khi server cho phép cộng các điểm (daily_trend đếm distinct theo ngày).
    if (payload.aggregate_allowed === true && payload.total_display) {
      card.appendChild(el('p', 'chat-note', `Tổng: ${payload.total_display}`));
    }
    const details = el('details', 'chat-details');
    details.appendChild(el('summary', '', 'Xem bảng số liệu'));
    const wrap = el('div', 'chat-table-wrap');
    const table = el('table', 'chat-table');
    const tbody = el('tbody');
    points.forEach(p => {
      const tr = el('tr');
      tr.appendChild(el('td', '', p.display_date || p.date));
      tr.appendChild(el('td', 'chat-num', p.display));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    details.appendChild(wrap);
    card.appendChild(details);
  }

  /** Khối `export` (b10 D9): tải qua API.download để không lộ link trực tiếp tới /api. */
  function renderExport(card, payload) {
    if (payload.error) {
      card.appendChild(el('p', 'chat-note chat-note-error', EXPORT_ERRORS[payload.error] || EXPORT_ERRORS.EXPORT_FAILED));
      return;
    }
    const filename = payload.filename || 'bao-cao.xlsx';
    const action = button('btn btn-primary chat-export-download', 'Tải file Excel', async ev => {
      const node = ev.currentTarget;
      node.disabled = true;
      try {
        await API.download(`/chat/exports/${encodeURIComponent(payload.export_id)}`, filename);
      } catch (_) {
        /* API.download đã báo lỗi bằng toast */
      } finally {
        node.disabled = false;
      }
    });
    card.appendChild(action);
    const notes = [filename, formatBytes(payload.size_bytes)];
    if (payload.truncated) notes.push(`chỉ gồm ${payload.rows_exported} dòng đầu`);
    card.appendChild(el('p', 'chat-note', notes.filter(Boolean).join(' · ')));
  }

  function formatBytes(size) {
    const bytes = Number(size);
    if (!bytes || bytes < 0) return '';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function renderQuote(card, payload) {
    const list = el('div', 'chat-quotes');
    (payload.quotes || []).forEach(q => {
      const item = el('blockquote', 'chat-quote');
      item.appendChild(el('p', 'chat-quote-content', q.content));
      const meta = [q.issue_code, q.unit_name, q.display_date, q.sentiment, q.status];
      if (q.source_file_name) {
        meta.push(q.source_row_number ? `${q.source_file_name} · dòng ${q.source_row_number}` : q.source_file_name);
      }
      item.appendChild(el('footer', 'chat-quote-meta', meta.filter(Boolean).join(' · ')));
      list.appendChild(item);
    });
    card.appendChild(list);
    if (payload.total_display) card.appendChild(el('p', 'chat-note', `Tổng: ${payload.total_display} phản hồi`));
  }

  function appendNotes(card, payload, shown) {
    if (payload.truncated && payload.total_rows) {
      card.appendChild(el('p', 'chat-note', `Hiển thị ${shown}/${payload.total_rows}`));
    }
    if (payload.total_display) {
      card.appendChild(el('p', 'chat-note', `Tổng: ${payload.total_display}`));
    }
  }

  /* ---- Biểu đồ ---- */

  function chartSlot(answerId, type, spec) {
    const canvasId = `chat-chart-${++_chartSeq}`;
    const box = el('div', `chat-chart chat-chart-${type}`);
    if (type === 'bar') box.style.height = `${Math.max(160, Math.min(spec.rows, 20) * 26 + 40)}px`;
    const canvas = el('canvas');
    canvas.id = canvasId;
    box.appendChild(canvas);
    box._chatChart = { canvasId, answerId: answerId || '', type, spec };
    return box;
  }

  function drawPendingCharts(root) {
    if (!window.Charts || !root || !root.isConnected) return;
    root.querySelectorAll('.chat-chart').forEach(box => {
      const job = box._chatChart;
      if (!job || _charts.has(job.canvasId)) return;
      const { canvasId, type, spec } = job;
      let chart = null;
      if (type === 'bar') {
        chart = Charts.createBarChart(canvasId, spec.labels, spec.values, {
          label: spec.label,
          chartOptions: {
            indexAxis: 'y',
            scales: {
              x: { beginAtZero: true, ticks: { precision: 0 } },
              y: { grid: { display: false } },
            },
          },
        });
      } else if (type === 'doughnut') {
        chart = Charts.createDoughnutChart(canvasId, spec.labels, spec.values, null, {});
      } else if (type === 'line') {
        chart = Charts.createLineChart(canvasId, spec.labels, [{
          label: spec.label,
          data: spec.values,
          borderColor: '#3b82f6',
          backgroundColor: 'rgba(59, 130, 246, 0.15)',
          fill: true,
        }]);
      }
      if (chart) _charts.set(canvasId, job.answerId);
    });
  }

  function destroyCharts(answerId) {
    Array.from(_charts.entries()).forEach(([canvasId, owner]) => {
      if (answerId !== undefined && owner !== answerId) return;
      if (window.Charts) Charts.destroy(canvasId);
      _charts.delete(canvasId);
      const canvas = document.getElementById(canvasId);
      const box = canvas && canvas.closest('.chat-chart');
      if (box && answerId !== undefined) box.remove();
    });
  }

  /** Chỉ giữ biểu đồ cho MAX_CHART_ANSWERS câu trả lời mới nhất (thứ tự cũ → mới). */
  function enforceChartLimit(answerIdsInOrder) {
    const keep = new Set((answerIdsInOrder || []).slice(-MAX_CHART_ANSWERS));
    new Set(_charts.values()).forEach(owner => {
      if (!keep.has(owner)) destroyCharts(owner);
    });
    return keep;
  }

  function chartCount() {
    return _charts.size;
  }

  /* ---- Nhận định, từ chối, hỏi lại, gợi ý, lỗi, kết thúc ---- */

  function renderCommentary(container, data) {
    if (!data.text) return;
    const box = data.section ? sectionSlot(container, data.section) : slot(container, 'commentary');
    box.hidden = false;
    let para = box.querySelector(':scope > p.chat-commentary-text');
    if (!para) {
      para = el('p', 'chat-commentary-text');
      box.appendChild(para);
    }
    if (para.childNodes.length) para.appendChild(document.createTextNode(' '));
    para.appendChild(el('span', 'chat-sentence', data.text));
  }

  function renderRefusal(data) {
    const box = el('div', `chat-refusal ${data.partial ? 'chat-refusal-partial' : 'chat-refusal-full'}`);
    box.setAttribute('role', data.partial ? 'note' : 'alert');
    box.appendChild(el('span', 'chat-refusal-icon', data.partial ? '⚠' : '⛔'));
    box.appendChild(el('span', 'chat-refusal-text', data.text));
    return box;
  }

  function renderClarify(data, opts) {
    const box = el('div', 'chat-clarify');
    box.appendChild(el('p', 'chat-clarify-text', data.text));
    const options = Array.isArray(data.options) ? data.options : [];
    if (options.length) {
      const row = el('div', 'chat-chip-row');
      options.forEach(option => row.appendChild(button('chat-chip chat-chip-option', option, () => {
        if (typeof opts.onAsk === 'function') opts.onAsk(String(option));
      })));
      box.appendChild(row);
    }
    return box;
  }

  function renderSuggestions(data, opts) {
    const row = el('div', 'chat-chip-row chat-suggestions');
    (Array.isArray(data.items) ? data.items : []).forEach(item => row.appendChild(button('chat-chip', item, () => {
      if (typeof opts.onAsk === 'function') opts.onAsk(String(item));
    })));
    return row;
  }

  function renderError(data, opts) {
    const box = el('div', 'chat-error');
    box.setAttribute('role', 'alert');
    box.appendChild(el('span', 'chat-error-text', data.text || 'Có lỗi xảy ra.'));
    if (typeof opts.onRetry === 'function') {
      box.appendChild(button('btn btn-ghost btn-sm chat-retry', 'Hỏi lại', () => opts.onRetry()));
    }
    return box;
  }

  function renderDone(container, data) {
    const footer = slot(container, 'footer');
    container.classList.add('chat-answer-done');
    if (data.status === 'cancelled') {
      footer.appendChild(el('p', 'chat-note', 'Đã dừng câu trả lời.'));
    }
    if (data.commentary_status === 'unavailable') {
      footer.appendChild(el('p', 'chat-note', 'Chưa tạo được nhận định tự động'));
    }
    // Sắp hết hạn mức token trong ngày (b11 D9): chỉ một dòng nhắc, không chặn.
    const warning = data.budget_warning;
    if (warning && typeof warning.used_ratio === 'number') {
      const percent = Math.min(100, Math.round(warning.used_ratio * 100));
      const resetAt = formatResetTime(warning.reset_at);
      const text = `Bạn đã dùng ${percent}% hạn mức hỏi đáp hôm nay`
        + (resetAt ? `; hạn mức làm mới lúc ${resetAt}.` : '.');
      footer.appendChild(el('p', 'chat-note chat-budget-warning', text));
    }
  }

  /** "2026-09-20T00:00:00+07:00" → "00:00 20/09" (giờ Việt Nam, theo offset của server). */
  function formatResetTime(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(String(value || ''));
    if (!match) return '';
    return `${match[4]}:${match[5]} ${match[3]}/${match[2]}`;
  }

  function renderNote(container, text) {
    slot(container, 'footer').appendChild(el('p', 'chat-note', text));
  }

  return {
    MAX_CHART_ANSWERS,
    escape, el,
    createAnswerView, renderEvent, renderBlock, renderNote, formatResetTime,
    drawPendingCharts, destroyCharts, enforceChartLimit, chartCount,
  };
})();
