'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Tabs } from 'antd';
import { 
  History as HistoryIcon, 
  Settings as SettingsIcon,
  Sparkles,
  Sun,
  Moon
} from 'lucide-react';

import { useTheme } from '@/components/ThemeProvider';

export default function StudioLayout({ children, activeTab = 'workspace', onTabChange }) {
  const [currentTab, setCurrentTab] = useState(activeTab);
  const { theme, toggleTheme } = useTheme();

  // 父级 activeTab 变化时（如 ?tab= 深链、历史「去编辑」切回工作台）同步高亮
  useEffect(() => {
    setCurrentTab(activeTab);
  }, [activeTab]);

  const handleTabChange = (key) => {
    setCurrentTab(key);
    if (onTabChange) onTabChange(key);
  };

  const tabItems = [
    {
      key: 'workspace',
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <Sparkles size={15} />
          工作台
        </span>
      ),
    },
    {
      key: 'history',
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <HistoryIcon size={15} />
          历史项目
        </span>
      ),
    },
    {
      key: 'settings',
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <SettingsIcon size={15} />
          设置
        </span>
      ),
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-dark)' }}>
      {/* 顶部导航 */}
      <header
        style={{
          background: 'var(--bg-card)',
          borderBottom: '1px solid var(--border)',
          padding: '0 32px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 56,
          position: 'sticky',
          top: 0,
          zIndex: 100,
          boxShadow: 'var(--shadow)',
        }}
      >
        {/* 左侧：Logo */}
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: 12, textDecoration: 'none' }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'linear-gradient(135deg, var(--primary), var(--primary-dark))',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 800,
              fontSize: 14,
            }}
          >
            DS
          </div>
          <span style={{ fontWeight: 700, fontSize: 16, color: 'var(--text-primary)' }}>DeckSmithPower</span>
        </Link>

        {/* 中间：Tab导航 */}
        <Tabs
          className="header-tabs"
          activeKey={currentTab}
          onChange={handleTabChange}
          items={tabItems}
          style={{ marginBottom: 0 }}
          tabBarStyle={{ marginBottom: 0, borderBottom: 'none' }}
        />

        {/* 右侧：操作区 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {/* 主题切换按钮 */}
          <button
            className="theme-toggle"
            onClick={toggleTheme}
            title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <Link href="/">
            <button
              style={{
                padding: '6px 12px',
                background: 'transparent',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                fontSize: 13,
                cursor: 'pointer',
                transition: 'all 150ms ease',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--bg-hover)';
                e.currentTarget.style.color = 'var(--text-primary)';
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent';
                e.currentTarget.style.color = 'var(--text-secondary)';
              }}
            >
              返回首页
            </button>
          </Link>
        </div>
      </header>

      {/* 主内容区 */}
      <main style={{ padding: '24px 32px' }}>
        {children}
      </main>
    </div>
  );
}
