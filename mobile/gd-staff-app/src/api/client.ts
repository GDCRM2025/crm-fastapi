import * as SecureStore from "expo-secure-store";

export const API_BASE = "https://greendiamond.cl/crm";
export const TOKEN_KEY = "gd_jwt";

export type Me = {
  id?: number | string | null;
  username?: string;
  name?: string;
  email?: string;
  role?: string;
  rol?: string;
  marcas?: number[];
};

export function getRole(me: Me | null): string {
  const r = String(me?.role || me?.rol || "").trim();
  return r.toUpperCase();
}

export function hasOperacionAccess(role: string): boolean {
  const r = role.toUpperCase();
  return (
    r.includes("OPERACIONES") ||
    r.includes("OPERACION") ||
    r.includes("OPERADOR") ||
    r.includes("CONDUCTOR") ||
    r.includes("CHOFER")
  );
}

async function readToken(): Promise<string | null> {
  try {
    const t = await SecureStore.getItemAsync(TOKEN_KEY);
    return t ? String(t) : null;
  } catch {
    return null;
  }
}

export async function apiJSON<T>(path: string, opts?: RequestInit): Promise<T> {
  const url = API_BASE + path;
  const res = await fetch(url, opts);
  const txt = await res.text();
  if (!res.ok) {
    const msg = txt || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return (txt ? JSON.parse(txt) : {}) as T;
}

export async function apiAuthedJSON<T>(path: string, opts?: RequestInit): Promise<T> {
  const t = await readToken();
  if (!t) throw new Error("SIN_TOKEN");
  const headers = new Headers(opts?.headers || {});
  headers.set("Authorization", `Bearer ${t}`);
  return apiJSON<T>(path, { ...opts, headers });
}

export async function login(username: string, password: string): Promise<string> {
  const j = await apiJSON<any>("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: username.trim(), password }),
  });
  const t = String(j?.token || j?.access_token || j?.jwt || "").trim();
  if (!t) throw new Error("Login sin token");
  await SecureStore.setItemAsync(TOKEN_KEY, t);
  return t;
}

export async function logout(): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(TOKEN_KEY);
  } catch {
    // ignore
  }
}

export async function me(): Promise<Me> {
  return apiAuthedJSON<Me>("/me");
}

