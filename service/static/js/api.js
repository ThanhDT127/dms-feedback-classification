/* ============================================================
   API Client — fetch() wrapper for all DMS API calls
   ============================================================ */

window.API = (() => {
  const BASE = window.location.origin + '/api';

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

    const fetchOpts = { method, headers, ...opts };

    if (body && !(body instanceof FormData)) {
      headers['Content-Type'] = 'application/json';
      fetchOpts.body = JSON.stringify(body);
    } else if (body instanceof FormData) {
      fetchOpts.body = body;
    }

    setLoading(true);

    try {
      const res = await fetch(url, fetchOpts);

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
        if (window.Toast) {
          window.Toast.show(err.message, 'error');
        }
      }
      throw err;
    } finally {
      setLoading(false);
    }
  }

  function get(path) { return request('GET', path); }
  function post(path, data) { return request('POST', path, data); }
  function put(path, data) { return request('PUT', path, data); }
  function del(path) { return request('DELETE', path); }

  function upload(path, formData) {
    return request('POST', path, formData);
  }

  /* convenience methods */
  function getHealth()           { return get('/health'); }
  function getMetrics()          { return get('/metrics'); }
  function getMetricsDaily()     { return get('/metrics/daily'); }
  function getFiles(folder)      { return get(`/files/${folder}`); }
  function getFilePreview(f, n)  { return get(`/files/${f}/${encodeURIComponent(n)}/preview`); }
  function getFileTree()         { return get('/files/tree'); }
  function getFilesSeen()        { return get('/files/seen'); }
  function uploadFile(formData)  { return upload('/files/upload', formData); }
  function classifyText(data)    { return post('/classify/text', data); }
  function classifyFile(formData){ return upload('/classify/file', formData); }
  function getJobs()             { return get('/classify/jobs'); }
  function getSettings()         { return get('/settings'); }
  function putSettings(data)     { return put('/settings', data); }
  function getPrompt()           { return get('/settings/prompt'); }
  function getModels()           { return get('/settings/models'); }
  function testConnection(data)  { return post('/settings/test-connection', data); }
  function getLabels()           { return get('/pipeline/labels'); }
  function getKeywords()         { return get('/pipeline/keywords'); }
  function getBrands()           { return get('/pipeline/brands'); }
  function getLogs()             { return get('/logs'); }
  function syncKeywordsToSP()    { return post('/pipeline/sync-keywords-to-sp'); }
  function syncProductsToSP()    { return post('/pipeline/sync-products-to-sp'); }
  function syncSharePoint()      { return post('/files/sync'); }
  function uploadJobToSharePoint(jobId) { return post(`/classify/jobs/${jobId}/sharepoint`); }

  return {
    get, post, put, del, upload,
    onLoading, offLoading, isLoading,
    getHealth, getMetrics, getMetricsDaily,
    getFiles, getFilePreview, getFileTree, getFilesSeen,
    uploadFile, classifyText, classifyFile, getJobs,
    getSettings, putSettings, getPrompt, getModels, testConnection,
    getLabels, getKeywords, getBrands, getLogs,
    syncKeywordsToSP, syncProductsToSP, syncSharePoint, uploadJobToSharePoint
  };
})();
