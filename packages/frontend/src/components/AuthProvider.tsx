'use client';

/**
 * 全局身份上下文（用户系统 v0.8）。
 *
 * 会话凭据是 httpOnly Cookie，前端读不到也不该读——身份一律靠 GET /api/auth/me 拿。
 * 挂载时拉一次，登录/登出后 refresh 重取；三态：已登录(user)/未登录有访客(visitorId)/纯匿名。
 * 访客身份由后端在 me() 时下发 Cookie，前端只拿 visitorId 用于展示，不手动构造。
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';

import { getMe, login as apiLogin, logout as apiLogout, type User } from '@/lib/api';

interface AuthState {
  loading: boolean;
  user: User | null;
  authenticated: boolean;
  isAdmin: boolean;
  visitorId: string | null;
  refresh: () => Promise<void>;
  doLogin: (username: string, password: string) => Promise<number>; // 返回并入的访客项目数
  doLogout: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [user, setUser] = useState<User | null>(null);
  const [visitorId, setVisitorId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await getMe();
      setUser(me.authenticated && me.user ? me.user : null);
      setVisitorId(me.visitor_id ?? null);
    } catch {
      setUser(null);
      setVisitorId(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doLogin = useCallback(
    async (username: string, password: string) => {
      const res = await apiLogin(username, password);
      setUser(res.user);
      await refresh(); // 认领后 me() 会清掉访客态
      return res.claimed_project_count ?? 0;
    },
    [refresh]
  );

  const doLogout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      setUser(null);
      await refresh();
    }
  }, [refresh]);

  const value: AuthState = {
    loading,
    user,
    authenticated: user != null,
    isAdmin: user?.role === 'manager',
    visitorId,
    refresh,
    doLogin,
    doLogout,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const c = useContext(Ctx);
  if (!c) throw new Error('useAuth 必须在 AuthProvider 内使用');
  return c;
}
