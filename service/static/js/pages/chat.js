/* ============================================================
   Chat Page — Trợ lý dữ liệu (b07)
   Bố cục, sự kiện người dùng, WebSocket /ws/chat và REST phiên.
   Khối số liệu do ChatBlocks dựng; trạng thái thuần ở ChatState.
   ============================================================ */

window.ChatPage = (() => {
  const BANNER_DELAY_MS = 2000;
  const RESUME_RETRY_MS = 2000;

  let client = null;
  let refs = {};
  let bannerTimer = null;
  let resumeSentAt = new Map(); // answer_id → { lastSeq, at }
  let answerOrder = [];         // answer_id theo thứ tự hiển thị (cũ → mới)
  const expiredAnswers = new Set(); // đã nhận answer_expired: không resume lại khi dựng lại phiên
  let loadToken = 0;
  let _alive = false;

  /** WSClient gốc chỉ chuyển `data` cho handler; chat cần cả answer_id/seq nên nhận nguyên message. */
  function createSocket(handlers) {
    class ChatSocket extends WS.WSClient {
      _dispatch(message) {
        if (this.handlers.onChatMessage) this.handlers.onChatMessage(message);
      }
    }
    return new ChatSocket('/ws/chat', handlers);
  }

  function config() {
    const cfg = (window.App && App.state && App.state.chatConfig) || {};
    return { maxChars: Number(cfg.max_question_chars) || 1000 };
  }

  /* ---- Bố cục ---- */

  function render() {
    const app = document.getElementById('app');
    if (!app) return;
    _alive = true;
    // Khung tĩnh, không có biến: nội dung động đều dựng bằng ChatBlocks.el.
    app.innerHTML = `
      <div class="chat-page">
        <aside class="chat-sessions" id="chat-sessions" aria-label="Cuộc trò chuyện">
          <div class="chat-sessions-head">
            <button type="button" class="btn btn-primary btn-sm chat-new" id="chat-new">+ Cuộc mới</button>
            <button type="button" class="btn btn-ghost btn-sm chat-sessions-close" id="chat-sessions-close" aria-label="Đóng danh sách">✕</button>
          </div>
          <div class="chat-session-list" id="chat-session-list"></div>
        </aside>
        <div class="chat-sessions-backdrop" id="chat-sessions-backdrop"></div>
        <section class="chat-main">
          <div class="chat-topbar">
            <button type="button" class="btn btn-ghost btn-sm chat-sessions-toggle" id="chat-sessions-toggle">☰ Cuộc trò chuyện</button>
            <h2 class="chat-heading">🤖 Trợ lý dữ liệu</h2>
          </div>
          <div class="chat-banner" id="chat-banner" role="status" hidden>Mất kết nối — đang nối lại</div>
          <div class="chat-thread" id="chat-thread"></div>
          <div class="chat-statusbar">
            <span class="chat-status" id="chat-status" aria-live="polite"></span>
            <button type="button" class="btn btn-ghost btn-sm chat-stop" id="chat-stop" hidden>■ Dừng</button>
          </div>
          <form class="chat-composer" id="chat-composer" autocomplete="off">
            <textarea id="chat-input" class="chat-input" rows="2" placeholder="Hỏi về số liệu phản hồi, ví dụ: Tổng quan tháng 8"></textarea>
            <div class="chat-composer-side">
              <span class="chat-counter" id="chat-counter"></span>
              <button type="submit" class="btn btn-primary chat-send" id="chat-send">Gửi</button>
            </div>
          </form>
          <p class="chat-input-error" id="chat-input-error" hidden></p>
        </section>
      </div>
    `;

    refs = {
      page: app.querySelector('.chat-page'),
      sessionList: document.getElementById('chat-session-list'),
      thread: document.getElementById('chat-thread'),
      banner: document.getElementById('chat-banner'),
      status: document.getElementById('chat-status'),
      stop: document.getElementById('chat-stop'),
      form: document.getElementById('chat-composer'),
      input: document.getElementById('chat-input'),
      counter: document.getElementById('chat-counter'),
      send: document.getElementById('chat-send'),
      inputError: document.getElementById('chat-input-error'),
    };

    document.getElementById('chat-new').addEventListener('click', newSession);
    document.getElementById('chat-sessions-toggle').addEventListener('click', () => toggleSessions(true));
    document.getElementById('chat-sessions-close').addEventListener('click', () => toggleSessions(false));
    document.getElementById('chat-sessions-backdrop').addEventListener('click', () => toggleSessions(false));
    refs.stop.addEventListener('click', stop);
    refs.form.addEventListener('submit', (e) => { e.preventDefault(); send(); });
    refs.input.addEventListener('keydown', onInputKeydown);
    refs.input.addEventListener('input', updateComposer);

    answerOrder = [];
    resumeSentAt = new Map();
    ChatState.clearAnswers();
    updateComposer();
    connect();
    loadSessions().then(() => {
      const current = ChatState.getSessionId();
      if (current && ChatState.getSessions().some(s => s.session_id === current)) {
        openSession(current);
      } else {
        newSession();
      }
    });
  }

  function destroy() {
    _alive = false;
    loadToken++;
    if (client) {
      client.close();
      client = null;
    }
    clearTimeout(bannerTimer);
    ChatBlocks.destroyCharts();
    ChatState.clearAnswers();
    answerOrder = [];
    resumeSentAt = new Map();
    refs = {};
  }

  function toggleSessions(open) {
    if (refs.page) refs.page.classList.toggle('chat-sessions-open', !!open);
  }

  /* ---- Kết nối ---- */

  function connect() {
    client = createSocket({
      onOpen,
      onClose,
      onChatMessage: handleMessage,
    });
    client.connect();
  }

  function onOpen() {
    clearTimeout(bannerTimer);
    showBanner(false);
    resumeSentAt = new Map();
    ChatState.resumeMessages().forEach(msg => sendResume(msg.answer_id, msg.last_seq, true));
  }

  function onClose() {
    if (!_alive) return;
    // Câu hỏi chưa được ack thì không biết server đã nhận chưa: trả lại ô nhập.
    restorePendingQuestions('Mất kết nối trước khi gửi xong, vui lòng gửi lại.');
    clearTimeout(bannerTimer);
    if (ChatState.hasUnfinished()) {
      showBanner(true);
    } else {
      bannerTimer = setTimeout(() => showBanner(true), BANNER_DELAY_MS);
    }
  }

  function showBanner(visible) {
    if (refs.banner) refs.banner.hidden = !visible;
  }

  function sendResume(answerId, lastSeq, force) {
    if (!client || !client.isOpen()) return;
    const prev = resumeSentAt.get(answerId);
    const now = Date.now();
    if (!force && prev && prev.lastSeq === lastSeq && now - prev.at < RESUME_RETRY_MS) return;
    resumeSentAt.set(answerId, { lastSeq, at: now });
    client.send({ type: 'resume', answer_id: answerId, last_seq: lastSeq });
  }

  /* ---- Message từ server ---- */

  function handleMessage(message) {
    if (!message || typeof message !== 'object') return;
    if (message.answer_id && Number.isInteger(message.seq)) {
      handleAnswerEvent(message);
      return;
    }
    switch (message.type) {
      case 'ack':
        handleAck(message);
        break;
      case 'error':
        handleProtocolError(message);
        break;
      case 'answer_expired':
        handleExpired(message);
        break;
      case 'pong':
        break;
      default:
        console.warn('ChatPage: bỏ qua message không biết', message.type);
    }
  }

  function handleAck(message) {
    const pending = ChatState.takePending(message.client_msg_id);
    if (!pending) return;
    const isNew = !ChatState.getSessionId();
    ChatState.setSessionId(message.session_id);
    if (isNew) {
      ChatState.upsertSession({ session_id: message.session_id, title: pending.question.slice(0, 60) });
      renderSessionList();
    }
    const bubble = refs.thread && refs.thread.querySelector(`[data-client-msg-id="${cssEscape(message.client_msg_id)}"]`);
    if (bubble) bubble.classList.remove('chat-user-sending');
    ChatState.trackAnswer(message.answer_id, { question: pending.question, clientMsgId: message.client_msg_id });
    ensureAnswerView(message.answer_id);
    setStatus('Đang xử lý');
    updateComposer();
  }

  function handleAnswerEvent(message) {
    const answerId = message.answer_id;
    const outcome = ChatState.applyEvent(answerId, message);
    if (outcome.result === ChatState.APPLY.DUPLICATE) return;
    if (outcome.result === ChatState.APPLY.GAP) {
      // Thiếu event giữa chừng: không hiển thị, xin lại từ last_seq hiện tại.
      sendResume(answerId, outcome.lastSeq, false);
      return;
    }
    const view = ensureAnswerView(answerId);
    const answer = ChatState.getAnswer(answerId);
    ChatBlocks.renderEvent(view, message, {
      onAsk: (question) => ask(question),
      onRetry: () => ask(answer ? answer.question : ''),
      onStatus: (data) => setStatus(data.text),
      charts: true,
    });
    if (message.type === 'done') {
      resumeSentAt.delete(answerId);
      ChatBlocks.enforceChartLimit(answerOrder);
      touchCurrentSession();
      if (!ChatState.hasUnfinished()) {
        setStatus('');
        showBanner(false);
      }
      updateComposer();
    }
    scrollToBottom();
  }

  function handleProtocolError(message) {
    const text = message.text || 'Có lỗi xảy ra.';
    const pending = message.client_msg_id ? ChatState.takePending(message.client_msg_id) : null;
    if (pending) {
      removeUserBubble(message.client_msg_id);
      refs.input.value = pending.question;
    }
    switch (message.code) {
      case 'BUSY':
      case 'RATE_LIMITED':
        Toast.warning(text);
        break;
      case 'INPUT_TOO_LONG':
        showInputError(text);
        break;
      case 'SESSION_NOT_FOUND':
        // Phiên hết hạn hoặc đã bị dọn (b11 D10): mở phiên mới, câu hỏi vẫn còn trong ô nhập.
        Toast.warning(ChatState.getSessionId()
          ? 'Cuộc trò chuyện này đã hết hạn. Đã mở cuộc trò chuyện mới, bạn gửi lại câu hỏi giúp tôi.'
          : text);
        ChatState.setSessionId(null);
        loadSessions().then(newSession);
        break;
      case 'BUDGET_EXCEEDED': {
        const resetAt = ChatBlocks.formatResetTime(message.data && message.data.reset_at);
        const detail = resetAt ? ` Hạn mức được làm mới lúc ${resetAt}.` : '';
        showInputError(`${text}${detail}`);
        Toast.warning(`${text}${detail}`);
        break;
      }
      case 'ANSWER_NOT_FOUND':
        // resume bị từ chối: không còn theo dõi được, dựng lại phiên từ REST.
        ChatState.unfinishedAnswers().forEach(a => {
          expiredAnswers.add(a.answerId);
          ChatState.markDone(a.answerId, 'error');
        });
        reloadCurrentSession();
        break;
      default:
        Toast.error(text);
    }
    if (!ChatState.hasUnfinished()) setStatus('');
    updateComposer();
  }

  function handleExpired(message) {
    expiredAnswers.add(message.answer_id);
    ChatState.markDone(message.answer_id, 'expired');
    reloadCurrentSession();
  }

  /* ---- Gửi câu hỏi ---- */

  function onInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  }

  function send() {
    if (!refs.input) return;
    ask(refs.input.value, { fromInput: true });
  }

  function ask(rawQuestion, { fromInput = false } = {}) {
    const question = String(rawQuestion || '').trim();
    if (!question || !refs.input) return;
    if (question.length > config().maxChars) {
      showInputError(`Câu hỏi dài quá ${config().maxChars} ký tự.`);
      return;
    }
    if (isBusy()) {
      Toast.info('Vui lòng đợi câu trả lời hiện tại hoàn tất hoặc bấm Dừng.');
      return;
    }
    if (!client || !client.isOpen()) {
      if (!fromInput) refs.input.value = question;
      Toast.warning('Đang kết nối lại, vui lòng thử lại');
      updateComposer();
      return;
    }
    showInputError('');
    clearEmptyState();
    const clientMsgId = ChatState.newClientMsgId();
    ChatState.addPending(clientMsgId, question, ChatState.getSessionId());
    appendUserBubble(question, clientMsgId, true);
    client.send({ type: 'ask', client_msg_id: clientMsgId, session_id: ChatState.getSessionId(), question });
    if (fromInput) refs.input.value = '';
    setStatus('Đang gửi');
    updateComposer();
    scrollToBottom(true);
  }

  function stop() {
    if (!client || !client.isOpen()) return;
    ChatState.unfinishedAnswers().forEach(a => client.send({ type: 'cancel', answer_id: a.answerId }));
    setStatus('Đang dừng');
  }

  function isBusy() {
    return ChatState.hasPending() || ChatState.hasUnfinished();
  }

  function restorePendingQuestions(toastText) {
    let restored = false;
    Array.from(refs.thread ? refs.thread.querySelectorAll('.chat-user-sending') : []).forEach(bubble => {
      const pending = ChatState.takePending(bubble.dataset.clientMsgId);
      if (pending) {
        refs.input.value = pending.question;
        restored = true;
      }
      bubble.remove();
    });
    if (restored) {
      Toast.warning(toastText);
      updateComposer();
    }
  }

  function updateComposer() {
    if (!refs.input) return;
    const max = config().maxChars;
    const length = refs.input.value.trim().length;
    const busy = isBusy();
    refs.counter.textContent = `${refs.input.value.length}/${max}`;
    refs.counter.classList.toggle('chat-counter-over', refs.input.value.length > max);
    refs.input.disabled = busy;
    refs.send.disabled = busy || length === 0 || refs.input.value.length > max;
    refs.stop.hidden = !ChatState.hasUnfinished();
  }

  function showInputError(text) {
    if (!refs.inputError) return;
    refs.inputError.textContent = text || '';
    refs.inputError.hidden = !text;
  }

  function setStatus(text) {
    if (refs.status) refs.status.textContent = text ? `${text}…` : '';
  }

  /* ---- Khung hội thoại ---- */

  function clearThread() {
    ChatBlocks.destroyCharts();
    ChatState.clearAnswers();
    answerOrder = [];
    resumeSentAt = new Map();
    if (refs.thread) refs.thread.textContent = '';
    setStatus('');
  }

  function showEmptyState() {
    const el = ChatBlocks.el;
    const box = el('div', 'chat-empty');
    box.appendChild(el('div', 'chat-empty-icon', '🤖'));
    box.appendChild(el('p', 'chat-empty-title', 'Hỏi về số liệu phản hồi khách hàng'));
    box.appendChild(el('p', 'chat-empty-text', 'Số liệu lấy trực tiếp từ dữ liệu đã phân loại, trong phạm vi đơn vị của bạn.'));
    const row = el('div', 'chat-chip-row');
    ['Tổng quan tháng này', 'Sản phẩm nào bị phản hồi nhiều nhất tháng trước?', 'Xu hướng phản hồi 30 ngày qua'].forEach(q => {
      const chip = el('button', 'chat-chip', q);
      chip.type = 'button';
      chip.addEventListener('click', () => ask(q));
      row.appendChild(chip);
    });
    box.appendChild(row);
    refs.thread.appendChild(box);
  }

  function clearEmptyState() {
    const empty = refs.thread && refs.thread.querySelector('.chat-empty');
    if (empty) empty.remove();
  }

  function appendUserBubble(text, clientMsgId, sending) {
    const bubble = ChatBlocks.el('div', `chat-user${sending ? ' chat-user-sending' : ''}`);
    if (clientMsgId) bubble.dataset.clientMsgId = clientMsgId;
    bubble.appendChild(ChatBlocks.el('p', 'chat-user-text', text));
    refs.thread.appendChild(bubble);
    return bubble;
  }

  function removeUserBubble(clientMsgId) {
    const bubble = refs.thread && refs.thread.querySelector(`[data-client-msg-id="${cssEscape(clientMsgId)}"]`);
    if (bubble) bubble.remove();
  }

  function ensureAnswerView(answerId) {
    let view = refs.thread.querySelector(`.chat-answer[data-answer-id="${cssEscape(answerId)}"]`);
    if (!view) {
      view = ChatBlocks.createAnswerView(answerId);
      refs.thread.appendChild(view);
      answerOrder.push(answerId);
    }
    return view;
  }

  function scrollToBottom(force) {
    const thread = refs.thread;
    if (!thread) return;
    const nearBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160;
    if (force || nearBottom) thread.scrollTop = thread.scrollHeight;
  }

  function cssEscape(value) {
    return window.CSS && typeof CSS.escape === 'function' ? CSS.escape(String(value)) : String(value).replace(/["\\]/g, '\\$&');
  }

  /* ---- Phiên ---- */

  async function loadSessions() {
    try {
      const data = await API.get('/chat/sessions', { silent: true });
      ChatState.setSessions((data && data.sessions) || []);
    } catch (err) {
      ChatState.setSessions([]);
    }
    renderSessionList();
  }

  function renderSessionList() {
    const list = refs.sessionList;
    if (!list) return;
    const el = ChatBlocks.el;
    list.textContent = '';
    const sessions = ChatState.getSessions();
    if (!sessions.length) {
      list.appendChild(el('p', 'chat-session-empty', 'Chưa có cuộc trò chuyện nào.'));
      return;
    }
    sessions.forEach(session => {
      const row = el('div', `chat-session${session.session_id === ChatState.getSessionId() ? ' active' : ''}`);
      const open = el('button', 'chat-session-title', session.title || 'Cuộc trò chuyện');
      open.type = 'button';
      open.title = session.title || '';
      open.addEventListener('click', () => { toggleSessions(false); openSession(session.session_id); });
      const rename = el('button', 'chat-session-action', '✎');
      rename.type = 'button';
      rename.setAttribute('aria-label', 'Đổi tên');
      rename.addEventListener('click', () => renameSession(session.session_id));
      const remove = el('button', 'chat-session-action', '🗑');
      remove.type = 'button';
      remove.setAttribute('aria-label', 'Xoá');
      remove.addEventListener('click', () => deleteSession(session.session_id));
      row.append(open, rename, remove);
      list.appendChild(row);
    });
  }

  function newSession() {
    if (!refs.thread) return;
    ChatState.setSessionId(null);
    clearThread();
    showEmptyState();
    renderSessionList();
    updateComposer();
    toggleSessions(false);
    if (refs.input) refs.input.focus();
  }

  async function openSession(sessionId) {
    if (!refs.thread || !sessionId) return;
    const token = ++loadToken;
    ChatState.setSessionId(sessionId);
    clearThread();
    renderSessionList();
    setStatus('Đang tải cuộc trò chuyện');
    let data;
    try {
      data = await API.get(`/chat/sessions/${encodeURIComponent(sessionId)}/messages`, { silent: true });
    } catch (err) {
      if (token !== loadToken) return;
      Toast.error('Không tải được cuộc trò chuyện.');
      ChatState.removeSession(sessionId);
      newSession();
      return;
    }
    if (token !== loadToken || !refs.thread) return;
    setStatus('');
    rebuildThread((data && data.messages) || []);
    updateComposer();
    scrollToBottom(true);
  }

  function reloadCurrentSession() {
    const sessionId = ChatState.getSessionId();
    if (sessionId) openSession(sessionId);
  }

  /** Dựng lại từ tin nhắn đã lưu; dùng cùng ChatBlocks với câu trả lời trực tiếp (D7). */
  function rebuildThread(messages) {
    const assistantIds = messages
      .filter(m => m.role === 'assistant')
      .map((m, i) => (m.metadata && m.metadata.answer_id) || `history-${i}`);
    const chartAnswers = new Set(assistantIds.slice(-ChatBlocks.MAX_CHART_ANSWERS));
    const answered = new Set(assistantIds);
    let assistantIndex = 0;
    let lastUser = null;

    messages.forEach(message => {
      const meta = message.metadata || {};
      if (message.role === 'user') {
        appendUserBubble(message.content, null, false);
        lastUser = { question: message.content, answerId: meta.answer_id || null };
        return;
      }
      if (message.role !== 'assistant') return;
      const answerId = assistantIds[assistantIndex++];
      const view = ensureAnswerView(answerId);
      const events = Array.isArray(meta.events) ? meta.events : null;
      if (events && events.length) {
        const question = lastUser ? lastUser.question : '';
        events.forEach(event => ChatBlocks.renderEvent(view, event, {
          onAsk: (q) => ask(q),
          onRetry: () => ask(question),
          charts: chartAnswers.has(answerId),
        }));
      } else if (message.content) {
        // Tin nhắn cũ không có events: hiển thị summary dạng văn bản.
        ChatBlocks.renderEvent(view, { type: 'commentary', data: { text: message.content } }, {});
      }
      if (meta.truncated_for_storage) ChatBlocks.renderNote(view, 'Bảng đã được rút gọn khi lưu');
      ChatBlocks.renderEvent(view, { type: 'done', data: { status: meta.done_status || 'ok' } }, {});
      ChatState.trackAnswer(answerId, { done: true });
      lastUser = null;
    });

    // Câu hỏi cuối chưa có trả lời: có answer_id thì theo dõi tiếp bằng resume từ đầu.
    if (lastUser && lastUser.answerId && !answered.has(lastUser.answerId) && expiredAnswers.has(lastUser.answerId)) {
      const view = ensureAnswerView(lastUser.answerId);
      ChatBlocks.renderNote(view, 'Câu trả lời không còn khả dụng, vui lòng hỏi lại.');
      ChatState.trackAnswer(lastUser.answerId, { question: lastUser.question, done: true });
    } else if (lastUser && lastUser.answerId && !answered.has(lastUser.answerId)) {
      const view = ensureAnswerView(lastUser.answerId);
      ChatBlocks.renderNote(view, 'Đang trả lời…');
      ChatState.trackAnswer(lastUser.answerId, { question: lastUser.question, lastSeq: 0 });
      sendResume(lastUser.answerId, 0, true);
    }
    if (!messages.length) showEmptyState();
  }

  function touchCurrentSession() {
    const sessionId = ChatState.getSessionId();
    const session = ChatState.getSessions().find(s => s.session_id === sessionId);
    if (session) {
      ChatState.upsertSession(session);
      renderSessionList();
    }
  }

  async function renameSession(sessionId) {
    const session = ChatState.getSessions().find(s => s.session_id === sessionId);
    const title = window.prompt('Tên cuộc trò chuyện', session ? session.title : '');
    if (title === null) return;
    const cleaned = title.trim();
    if (!cleaned || cleaned.length > 120) {
      Toast.warning('Tên phải dài từ 1 đến 120 ký tự.');
      return;
    }
    try {
      if (typeof API.patch !== 'function') {
        // api.js cũ còn trong cache trình duyệt.
        Toast.warning('Vui lòng tải lại trang (Ctrl+F5) để đổi tên cuộc trò chuyện.');
        return;
      }
      const updated = await API.patch(`/chat/sessions/${encodeURIComponent(sessionId)}`, { title: cleaned });
      ChatState.setSessions(ChatState.getSessions().map(s => (s.session_id === sessionId ? { ...s, title: updated.title } : s)));
      renderSessionList();
    } catch (err) {
      /* API client đã hiện toast */
    }
  }

  async function deleteSession(sessionId) {
    if (!window.confirm('Xoá cuộc trò chuyện này? Không thể hoàn tác.')) return;
    try {
      await API.del(`/chat/sessions/${encodeURIComponent(sessionId)}`);
    } catch (err) {
      return;
    }
    const wasCurrent = ChatState.getSessionId() === sessionId;
    ChatState.removeSession(sessionId);
    if (wasCurrent) {
      newSession();
    } else {
      renderSessionList();
    }
  }

  return {
    render, destroy,
    send, ask, stop,
    newSession, openSession, toggleSessions,
    handleMessage,
  };
})();
