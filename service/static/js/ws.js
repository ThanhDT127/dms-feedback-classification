/* ============================================================
   WebSocket Client — classification progress + live logs
   ============================================================ */

window.WS = (() => {
  const wsBase = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;

  class WSClient {
    constructor(path, handlers = {}) {
      this.path = path;
      this.url = `${wsBase}${path}`;
      this.handlers = handlers;
      this.ws = null;
      this.reconnectTimer = null;
      this.reconnectDelay = 1000;
      this.maxReconnectDelay = 30000;
      this.shouldReconnect = true;
      this._closed = false;
    }

    connect() {
      if (this._closed) return;
      try {
        this.ws = new WebSocket(this.url);
      } catch (e) {
        console.warn('WS connect error:', e);
        this._scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this.reconnectDelay = 1000;
        if (this.handlers.onOpen) this.handlers.onOpen();
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          this._dispatch(data);
        } catch (_) {
          if (this.handlers.onRaw) this.handlers.onRaw(event.data);
        }
      };

      this.ws.onerror = (err) => {
        console.warn('WS error:', this.path, err);
        if (this.handlers.onError) this.handlers.onError(err);
      };

      this.ws.onclose = () => {
        if (this.handlers.onClose) this.handlers.onClose();
        if (this.shouldReconnect && !this._closed) {
          this._scheduleReconnect();
        }
      };
    }

    _dispatch(data) {
      const type = data.type || data.event || '';
      switch (type) {
        case 'progress':
          if (this.handlers.onProgress) this.handlers.onProgress(data);
          break;
        case 'batch_result':
          if (this.handlers.onBatchResult) this.handlers.onBatchResult(data);
          break;
        case 'complete':
          if (this.handlers.onComplete) this.handlers.onComplete(data);
          break;
        case 'error':
          if (this.handlers.onError) this.handlers.onError(data);
          break;
        case 'log':
          if (this.handlers.onLogMessage) this.handlers.onLogMessage(data);
          break;
        default:
          if (this.handlers.onMessage) this.handlers.onMessage(data);
      }
    }

    _scheduleReconnect() {
      if (this._closed) return;
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectDelay = Math.min(this.reconnectDelay * 1.5, this.maxReconnectDelay);
        this.connect();
      }, this.reconnectDelay);
    }

    send(data) {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(typeof data === 'string' ? data : JSON.stringify(data));
      }
    }

    close() {
      this._closed = true;
      this.shouldReconnect = false;
      clearTimeout(this.reconnectTimer);
      if (this.ws) {
        this.ws.close();
        this.ws = null;
      }
    }

    isOpen() {
      return this.ws && this.ws.readyState === WebSocket.OPEN;
    }
  }

  function classifyWS(jobId, handlers) {
    const client = new WSClient(`/ws/classify/${jobId}`, handlers);
    client.connect();
    return client;
  }

  function logsWS(handlers) {
    const client = new WSClient('/ws/logs', handlers);
    client.connect();
    return client;
  }

  return { WSClient, classifyWS, logsWS };
})();
