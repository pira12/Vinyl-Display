// Token-aware API client. The token arrives once as ?token=… (logged by the
// server), is stored in localStorage, and stripped from the URL — same flow as
// the original app. Display endpoints (/, /ws, /art) need no token.

export function loadToken() {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    localStorage.setItem("vinyl_token", fromUrl);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.pathname + url.search);
  }
  return localStorage.getItem("vinyl_token") || "";
}

export function setToken(t) {
  localStorage.setItem("vinyl_token", t || "");
}

export function getToken() {
  return localStorage.getItem("vinyl_token") || "";
}

class Unauthorized extends Error {}
export { Unauthorized };

async function request(path, { method = "GET", json, body, headers } = {}) {
  const h = { "X-Auth-Token": getToken(), ...(headers || {}) };
  let payload = body;
  if (json !== undefined) {
    h["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  }
  const res = await fetch(path, { method, headers: h, body: payload });
  if (res.status === 401) throw new Unauthorized("unauthorized");
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : null;
  if (!res.ok) {
    const msg = (data && data.error) || `request failed (${res.status})`;
    const err = new Error(msg);
    err.fields = data && data.fields;
    throw err;
  }
  return data;
}

export const api = {
  get: (p) => request(p),
  postJson: (p, json) => request(p, { method: "POST", json }),
  postBytes: (p, bytes) =>
    request(p, { method: "POST", body: bytes, headers: { "Content-Type": "application/octet-stream" } }),
};
