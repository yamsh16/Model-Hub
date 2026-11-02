"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { apiJson, getToken, setToken, SIGNED_OUT_EVENT } from "@/lib/api";
import { clearAccountData } from "@/lib/storage";
import type { AuthReply, User } from "@/lib/types";

type Auth = {
  user: User | null;
  /** False until the saved token (if any) has been checked, so pages don't flash "signed out". */
  ready: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  signOut: () => void;
};

const AuthContext = createContext<Auth | null>(null);

export function useAuth(): Auth {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside <AuthProvider>");
  return auth;
}

export default function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const lastUserId = useRef<string | null>(null);

  // One place decides who is signed in. Switching to a different account wipes the saved
  // chats and workspace first, so nothing leaks from the previous one.
  const changeUser = useCallback((next: User | null) => {
    if (lastUserId.current && lastUserId.current !== next?.id) clearAccountData();
    lastUserId.current = next?.id ?? lastUserId.current; // remember the last account even after sign-out
    setUser(next);
  }, []);

  // Restore a saved session; a stale token is dropped by apiFetch's 401 handling.
  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (getToken()) {
        try {
          const me = await apiJson<User>("/auth/me");
          if (!cancelled) changeUser(me);
        } catch {
          // backend down: stay "signed out" in the UI but keep the token for next time
        }
      }
      if (!cancelled) setReady(true);
    }
    restore();
    const onSignedOut = () => setUser(null);
    window.addEventListener(SIGNED_OUT_EVENT, onSignedOut);
    return () => {
      cancelled = true;
      window.removeEventListener(SIGNED_OUT_EVENT, onSignedOut);
    };
  }, [changeUser]);

  const authenticate = useCallback(
    async (mode: "login" | "register", email: string, password: string) => {
      const reply = await apiJson<AuthReply>(`/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      setToken(reply.access_token);
      changeUser(reply.user);
    },
    [changeUser],
  );

  const value = useMemo<Auth>(
    () => ({
      user,
      ready,
      signIn: (email, password) => authenticate("login", email, password),
      register: (email, password) => authenticate("register", email, password),
      signOut: () => {
        setToken(null);
        clearAccountData();
        lastUserId.current = null;
        setUser(null);
      },
    }),
    [user, ready, authenticate],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
