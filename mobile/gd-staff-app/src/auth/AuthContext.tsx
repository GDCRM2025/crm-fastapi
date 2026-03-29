import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import * as SecureStore from "expo-secure-store";

import { Me, TOKEN_KEY, getRole, login as apiLogin, logout as apiLogout, me as apiMe } from "../api/client";

type AuthState = {
  loading: boolean;
  token: string | null;
  me: Me | null;
  role: string;
  error: string | null;
  refreshMe: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("AuthContext missing");
  return v;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  const role = useMemo(() => getRole(me), [me]);

  useEffect(() => {
    (async () => {
      try {
        const t = await SecureStore.getItemAsync(TOKEN_KEY);
        if (t) setToken(String(t));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const refreshMe = useCallback(async () => {
    if (!token) {
      setMe(null);
      return;
    }
    try {
      const j = await apiMe();
      setMe(j);
      setError(null);
    } catch (e: any) {
      const msg = String(e?.message || e);
      setError(msg);
      if (msg.includes("401") || msg.includes("Token inválido") || msg.includes("SIN_TOKEN")) {
        setToken(null);
        setMe(null);
        await apiLogout();
      }
    }
  }, [token]);

  useEffect(() => {
    refreshMe();
  }, [refreshMe]);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    const t = await apiLogin(username, password);
    setToken(t);
    await refreshMe();
  }, [refreshMe]);

  const logout = useCallback(async () => {
    await apiLogout();
    setToken(null);
    setMe(null);
    setError(null);
  }, []);

  const value: AuthState = {
    loading,
    token,
    me,
    role,
    error,
    refreshMe,
    login,
    logout,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

