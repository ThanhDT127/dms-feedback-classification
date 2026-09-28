/* ============================================================
   Chat State — trạng thái thuần của trang chat (b07 D2, D6)
   Không đụng DOM, không gọi mạng: chỉ giữ phiên, tin nhắn và
   các câu trả lời theo answer_id để bỏ trùng / phát hiện thiếu seq.
   ============================================================ */

window.ChatState = (() => {
  // Phiên đang mở được nhớ qua lần tải lại trang; mọi thứ khác chỉ sống trong RAM.
  // localStorage có thể ném (chế độ riêng tư, chặn cookie) nên mọi truy cập đều bọc try/catch.
  const SESSION_KEY = 'dms_chat_session_id';

  function readStoredSessionId() {
    try {
      return localStorage.getItem(SESSION_KEY) || null;
    } catch (e) {
      return null;
    }
  }

  function writeStoredSessionId(sessionId) {
    try {
      if (sessionId) localStorage.setItem(SESSION_KEY, sessionId);
      else localStorage.removeItem(SESSION_KEY);
    } catch (e) {
      /* không nhớ được phiên thì vẫn chạy bình thường, chỉ mất khả năng mở lại */
    }
  }

  /** Quên phiên đang nhớ — dùng khi đăng xuất, tránh người sau mở nhầm cuộc của người trước. */
  function forgetSession() {
    writeStoredSessionId(null);
  }

  /** Kết quả của applyEvent. */
  const APPLY = Object.freeze({
    APPLIED: 'applied',     // event mới, liền kề → render
    DUPLICATE: 'duplicate', // seq <= last_seq → bỏ qua
    GAP: 'gap',             // seq > last_seq + 1 → không render, cần resume
  });

  function createStore() {
    return {
      sessionId: readStoredSessionId(),
      sessions: [],
      answers: new Map(), // answer_id → { answerId, question, clientMsgId, lastSeq, done, doneStatus, events }
      pending: new Map(), // client_msg_id → { question, sessionId }
    };
  }

  let store = createStore();

  function reset() {
    store = createStore();
  }

  function setSessions(list) {
    store.sessions = Array.isArray(list) ? list.slice(0, 50) : [];
  }

  function getSessions() {
    return store.sessions;
  }

  function upsertSession(session) {
    if (!session || !session.session_id) return;
    store.sessions = [session, ...store.sessions.filter(s => s.session_id !== session.session_id)].slice(0, 50);
  }

  function removeSession(sessionId) {
    store.sessions = store.sessions.filter(s => s.session_id !== sessionId);
    if (store.sessionId === sessionId) setSessionId(null);
  }

  function setSessionId(sessionId) {
    store.sessionId = sessionId || null;
    writeStoredSessionId(store.sessionId);
  }

  function getSessionId() {
    return store.sessionId;
  }

  /** Chuyển phiên: bỏ mọi câu trả lời đang theo dõi của phiên cũ. */
  function clearAnswers() {
    store.answers = new Map();
    store.pending = new Map();
  }

  function addPending(clientMsgId, question, sessionId) {
    store.pending.set(clientMsgId, { question, sessionId: sessionId || null });
  }

  function takePending(clientMsgId) {
    const item = store.pending.get(clientMsgId) || null;
    store.pending.delete(clientMsgId);
    return item;
  }

  function hasPending() {
    return store.pending.size > 0;
  }

  function trackAnswer(answerId, { question = '', clientMsgId = null, lastSeq = 0, done = false } = {}) {
    if (!answerId) return null;
    const existing = store.answers.get(answerId);
    if (existing) return existing;
    const answer = { answerId, question, clientMsgId, lastSeq, done, doneStatus: null, events: [] };
    store.answers.set(answerId, answer);
    return answer;
  }

  function getAnswer(answerId) {
    return store.answers.get(answerId) || null;
  }

  /**
   * Áp một event của câu trả lời.
   * - seq <= last_seq  → DUPLICATE (đã hiển thị, bỏ qua)
   * - seq >  last_seq + 1 → GAP (không hiển thị, trang gửi lại resume với last_seq hiện tại)
   * - còn lại → APPLIED, cập nhật last_seq và cờ done
   */
  function applyEvent(answerId, event) {
    const answer = store.answers.get(answerId) || trackAnswer(answerId);
    const seq = Number(event && event.seq);
    if (!answer || !Number.isInteger(seq) || seq < 1) return { result: APPLY.DUPLICATE, lastSeq: answer ? answer.lastSeq : 0 };
    if (seq <= answer.lastSeq) {
      return { result: APPLY.DUPLICATE, lastSeq: answer.lastSeq };
    }
    if (seq > answer.lastSeq + 1) {
      return { result: APPLY.GAP, lastSeq: answer.lastSeq, needsResume: true };
    }
    answer.lastSeq = seq;
    answer.events.push(event);
    if (event.type === 'done') {
      answer.done = true;
      answer.doneStatus = (event.data && event.data.status) || null;
    }
    return { result: APPLY.APPLIED, lastSeq: seq };
  }

  /** Câu trả lời chưa nhận done: cần gửi resume khi kết nối mở lại. */
  function unfinishedAnswers() {
    return Array.from(store.answers.values()).filter(a => !a.done);
  }

  function hasUnfinished() {
    return unfinishedAnswers().length > 0;
  }

  function resumeMessages() {
    return unfinishedAnswers().map(a => ({ type: 'resume', answer_id: a.answerId, last_seq: a.lastSeq }));
  }

  function markDone(answerId, status) {
    const answer = store.answers.get(answerId);
    if (answer) {
      answer.done = true;
      answer.doneStatus = status || answer.doneStatus;
    }
  }

  function newClientMsgId() {
    if (window.crypto && typeof window.crypto.randomUUID === 'function') {
      return window.crypto.randomUUID();
    }
    return `m${Date.now()}${Math.floor(Math.random() * 1e9)}`;
  }

  return {
    APPLY,
    reset,
    setSessions, getSessions, upsertSession, removeSession,
    setSessionId, getSessionId, forgetSession, clearAnswers,
    addPending, takePending, hasPending,
    trackAnswer, getAnswer, applyEvent, markDone,
    unfinishedAnswers, hasUnfinished, resumeMessages,
    newClientMsgId,
  };
})();
