/* ============================================================
   API Client — fetch() wrapper for all DMS API calls
   ============================================================ */

window.API = (() => {
  const BASE = window.location.origin + '/api';

  let _accessToken = localStorage.getItem('dms_access_token') || null;
  let _refreshToken = localStorage.getItem('dms_refresh_token') || null;
  let _refreshPromise = null;

  let _loading = false;
  const _listeners = new Set();

  function setLoading(val) {
    _loading = val;
    _listeners.forEach(fn => fn(val));
  }

  function onLoading(fn) { _listeners.add(fn); }
  function offLoading(fn) { _listeners.delete(fn); }
  function isLoading() { return _loading; }

  async function request(method, path, body, opts = {}) {
    const url = path.startsWith('http') ? path : `${BASE}${path}`;
    const headers = {};

    const { silent, _isRetry, ...fetchOverrides } = opts;
    const fetchOpts = { method, headers, ...fetchOverrides };

    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
      fetchOpts.body = body;
    }

    if (_accessToken) {
      headers['Authorization'] = `Bearer ${_accessToken}`;
    }

    setLoading(true);

    try {
      const res = await fetch(url, fetchOpts);

      if (res.status === 401 && _refreshToken && !_isRetry) {
        const refreshed = await _tryRefresh();
        if (refreshed) return request(method, path, body, { ...opts, _isRetry: true });
        // Refresh failed; auth:expired already dispatched, don't show duplicate error.
        throw new Error('Session expired');
      }

      if (!res.ok) {
        let errMsg = `Lỗi ${res.status}: ${res.statusText}`;
        try {
          const errData = await res.json();
          errMsg = errData.detail || errData.message || errMsg;
        } catch (_) { /* ignore */ }
        throw new Error(errMsg);
      }

      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('application/json')) {
        return await res.json();
      }
      return await res.text();
    } catch (err) {
      if (err.name !== 'AbortError') {
        console.error(`API ${method} ${path}:`, err);
        if (!silent && window.Toast) {
          window.Toast.show(err.message, 'error');
        }
      }
      throw err;
    } finally {
      setLoading(false);
    }
  }

  function get(path, opts = {}) { return request('GET', path, null, opts); }
  function post(path, data, opts = {}) { return request('POST', path, data, opts); }
  function put(path, data, opts = {}) { return request('PUT', path, data, opts); }
  function del(path, opts = {}) { return request('DELETE', path, null, opts); }

  function upload(path, formData, opts = {}) {
    return request('POST', path, formData, opts);
  }

  function _filenameFromDisposition(disposition, fallback) {
    if (!disposition) return fallback;
    const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf8Match) return decodeURIComponent(utf8Match[1].replace(/"/g, ''));
    const plainMatch = disposition.match(/filename="?([^"]+)"?/i);
    return plainMatch ? plainMatch[1] : fallback;
  }

  async function download(path, fallbackFilename = 'download', opts = {}) {
    const url = path.startsWith('http') ? path : `${BASE}${path}`;
    const doFetch = () => {
      const headers = {};
      if (_accessToken) headers['Authorization'] = `Bearer ${_accessToken}`;
      return fetch(url, { method: 'GET', headers });
    };

    setLoading(true);
    try {
      let res = await doFetch();
      if (res.status === 401 && _refreshToken) {
        const refreshed = await _tryRefresh();
        if (refreshed) res = await doFetch();
      }
      if (!res.ok) {
        let errMsg = `Lỗi ${res.status}: ${res.statusText}`;
        try {
          const errData = await res.json();
          errMsg = errData.detail || errData.message || errMsg;
        } catch (_) { /* ignore */ }
        throw new Error(errMsg);
      }

      const blob = await res.blob();
      const filename = _filenameFromDisposition(
        res.headers.get('content-disposition'),
        fallbackFilename
      );
      const objectUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      return { filename, size: blob.size };
    } catch (err) {
      console.error(`API DOWNLOAD ${path}:`, err);
      if (!opts.silent && window.Toast) window.Toast.show(err.message, 'error');
      throw err;
    } finally {
      setLoading(false);
    }
  }

  function uploadWithProgress(path, formData, onProgress, opts = {}) {
    const url = path.startsWith('http') ? path : `${BASE}${path}`;
    const send = (isRetry = false) => new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      if (_accessToken) xhr.setRequestHeader('Authorization', `Bearer ${_accessToken}`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = async () => {
        if (xhr.status === 401 && _refreshToken && !isRetry) {
          const refreshed = await _tryRefresh();
          if (refreshed) {
            try { resolve(await send(true)); }
            catch (err) { reject(err); }
            return;
          }
        }
        if (xhr.status >= 200 && xhr.status < 300) {
          try { resolve(JSON.parse(xhr.responseText)); }
          catch { resolve({ message: opts.successMessage || 'Upload thành công' }); }
        } else {
          try {
            const errData = JSON.parse(xhr.responseText);
            reject(new Error(errData.detail || errData.message || 'Upload thất bại'));
          } catch {
            reject(new Error('Upload thất bại: ' + xhr.status));
          }
        }
      };
      xhr.onerror = () => reject(new Error('Lỗi mạng'));
      xhr.send(formData);
    });
    return send(false);
  }

  /* convenience methods */
  function getHealth(opts = {})  { return get('/health', opts); }
  function getMetrics()          { return get('/metrics'); }
  function getMetricsDaily()     { return get('/metrics/daily'); }
  function getFiles(folder)      { return get(`/files/${folder}`); }
  function getFilePreview(f, n)  { return get(`/files/${f}/${encodeURIComponent(n)}/preview`); }
  function getFileTree()         { return get('/files/tree'); }
  function getFilesSeen()        { return get('/files/seen'); }
  function uploadFile(formData)  { return upload('/files/upload', formData); }
  function logout(opts = {})     { return post('/auth/logout', null, opts); }
  function classifyText(data)    { return post('/classify/text', data); }
  function classifyFile(formData){ return upload('/classify/file', formData); }
  function getJobs()             { return get('/classify/jobs'); }
  function getSettings()         { return get('/settings'); }
  function putSettings(data)     { return put('/settings', data); }
  function getSecret(key)        { return get(`/settings/secret/${encodeURIComponent(key)}`); }
  function getPrompt()           { return get('/settings/prompt'); }
  function getModels()           { return get('/settings/models'); }
  function testConnection(data)  { return post('/settings/test-connection', data); }
  function getLabels()           { return get('/pipeline/labels'); }
  function getKeywords()         { return get('/pipeline/keywords'); }
  function getBrands()           { return get('/pipeline/brands'); }
  function getLogs()             { return get('/logs'); }
  function syncKeywordsToSP()    { return post('/pipeline/sync-keywords-to-sp', null); }
  function syncProductsToSP()    { return post('/pipeline/sync-products-to-sp', null); }
  function syncSharePoint()      { return post('/files/sync', null); }
  function uploadJobToSharePoint(jobId) { return post(`/classify/jobs/${jobId}/sharepoint`); }

  function setTokens(access, refresh) {
    _accessToken = access;
    _refreshToken = refresh;
    try {
      if (access) localStorage.setItem('dms_access_token', access);
      else localStorage.removeItem('dms_access_token');
      if (refresh) localStorage.setItem('dms_refresh_token', refresh);
      else localStorage.removeItem('dms_refresh_token');
    } catch (e) {
      console.warn('Failed to persist tokens to localStorage:', e.message);
    }
  }

  function getAccessToken() { return _accessToken; }

  function clearTokens() {
    _accessToken = null;
    _refreshToken = null;
    localStorage.removeItem('dms_access_token');
    localStorage.removeItem('dms_refresh_token');
  }

  async function _tryRefresh() {
    if (_refreshPromise) return _refreshPromise;
    _refreshPromise = (async () => {
      try {
        const res = await fetch(BASE.replace('/api', '') + '/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: _refreshToken }),
        });
        if (!res.ok) throw new Error('Refresh failed');
        const data = await res.json();
        _accessToken = data.access_token;
        localStorage.setItem('dms_access_token', data.access_token);
        return true;
      } catch {
        clearTokens();
        window.dispatchEvent(new Event('auth:expired'));
        return false;
      } finally {
        _refreshPromise = null;
      }
    })();
    return _refreshPromise;
  }

  function refreshToken() {
    return _tryRefresh();
  }

  return {
    get, post, put, del, upload, download, uploadWithProgress,
    onLoading, offLoading, isLoading,
    setTokens, getAccessToken, clearTokens, refreshToken,
    getHealth, getMetrics, getMetricsDaily,
    getFiles, getFilePreview, getFileTree, getFilesSeen,
    uploadFile, classifyText, classifyFile, getJobs,
    getSettings, putSettings, getSecret, getPrompt, getModels, testConnection,
    getLabels, getKeywords, getBrands, getLogs,
    syncKeywordsToSP, syncProductsToSP, syncSharePoint, uploadJobToSharePoint,
    logout
  };
})();
