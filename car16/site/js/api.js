// fetch wrapper：帶 token、統一錯誤
window.api = {
  token() { return localStorage.getItem("car16_token") || ""; },
  setToken(t) { t ? localStorage.setItem("car16_token", t) : localStorage.removeItem("car16_token"); },

  async call(method, path, body) {
    const headers = { "content-type": "application/json" };
    const t = this.token();
    if (t) headers.authorization = `Bearer ${t}`;
    const res = await fetch(`${CAR16.API_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch { /* 非 JSON 回應 */ }
    if (!res.ok) {
      const msg = data?.error?.message || `HTTP ${res.status}`;
      const e = new Error(msg);
      e.code = data?.error?.code || String(res.status);
      e.status = res.status;
      throw e;
    }
    return data;
  },
  get(p) { return this.call("GET", p); },
  post(p, b) { return this.call("POST", p, b); },
  del(p) { return this.call("DELETE", p); },
};
