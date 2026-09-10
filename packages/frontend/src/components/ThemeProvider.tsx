'use client';

/**
 * 全局主题 Provider：单一数据源管理 dark/light，并据此反应式切换 antd ConfigProvider
 * 的算法与 token，使 antd 组件原生就是设计稿（v3-dark-faithful）配色，
 * 不再只靠 globals.css 的 !important 硬压。
 *
 * 首屏防闪：layout.js 的内联脚本已在 <html> 上预设 data-theme；这里初始化时读取它，
 * 保证 React 侧的 antd 算法与 CSS 变量首帧一致。
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { ConfigProvider, theme as antdTheme } from 'antd';

type ThemeMode = 'dark' | 'light';

const FONT_FAMILY =
  "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

// 设计稿 token（与 globals.css :root / [data-theme=light] 保持一致）
const DARK_TOKENS = {
  colorPrimary: '#8b5cf6',
  colorBgLayout: '#0a0a0a',
  colorBgContainer: '#111111',
  colorBgElevated: '#111111',
  colorBorder: '#1f1f1f',
  colorBorderSecondary: '#1f1f1f',
  colorText: '#f3f4f6',
  colorTextSecondary: '#9ca3af',
  colorTextTertiary: '#6b7280',
};

const LIGHT_TOKENS = {
  colorPrimary: '#8b5cf6',
  colorBgLayout: '#f8fafc',
  colorBgContainer: '#ffffff',
  colorBgElevated: '#ffffff',
  colorBorder: '#e2e8f0',
  colorBorderSecondary: '#e2e8f0',
  colorText: '#0f172a',
  colorTextSecondary: '#475569',
  colorTextTertiary: '#94a3b8',
};

interface ThemeCtx {
  theme: ThemeMode;
  toggleTheme: () => void;
  setTheme: (t: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeCtx>({
  theme: 'dark',
  toggleTheme: () => {},
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

export default function ThemeProvider({ children }: { children: React.ReactNode }) {
  // 首帧读取 layout.js 内联脚本已设好的 data-theme，避免 React 与 CSS 主题不一致
  const [mode, setMode] = useState<ThemeMode>(() => {
    if (typeof document !== 'undefined') {
      const t = document.documentElement.getAttribute('data-theme');
      if (t === 'light' || t === 'dark') return t;
    }
    return 'dark';
  });

  // 同步到 <html data-theme> 与 localStorage（globals.css 的 CSS 变量靠它切换）
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', mode);
    try {
      localStorage.setItem('decksmith-theme', mode);
    } catch {
      /* 隐私模式忽略 */
    }
  }, [mode]);

  const toggleTheme = useCallback(() => setMode((m) => (m === 'dark' ? 'light' : 'dark')), []);

  const antdConfig =
    mode === 'dark'
      ? { algorithm: antdTheme.darkAlgorithm, token: { ...DARK_TOKENS, borderRadius: 8, fontFamily: FONT_FAMILY } }
      : { algorithm: antdTheme.defaultAlgorithm, token: { ...LIGHT_TOKENS, borderRadius: 8, fontFamily: FONT_FAMILY } };

  return (
    <ThemeContext.Provider value={{ theme: mode, toggleTheme, setTheme: setMode }}>
      <ConfigProvider theme={antdConfig}>{children}</ConfigProvider>
    </ThemeContext.Provider>
  );
}
