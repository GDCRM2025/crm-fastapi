// Stub mínimo para no reventar views antiguas que esperan api.js
export function getToken(){
  return localStorage.getItem("token") || sessionStorage.getItem("token") || "";
}
export function authHeaders(extra={}){
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : extra;
}
export async function api(url, opts={}){
  const headers = authHeaders(opts.headers || {});
  const r = await fetch(url, { ...opts, headers });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}
