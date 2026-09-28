'use client';

import { useState, useEffect } from 'react';
import Link from 'next/link';
import { Avatar, Dropdown, Tabs, Tag } from 'antd';
import AppVersion from '@/components/AppVersion';
import { 
  History as HistoryIcon, 
  Settings as SettingsIcon,
  Users as UsersIcon,
  Sparkles,
  SlidersHorizontal,
  Sun,
  Moon,
  LogOut,
  KeyRound,
} from 'lucide-react';

import { useTheme } from '@/components/ThemeProvider';
import { useAuth } from '@/components/AuthProvider';
import { ChangePasswordModal, LoginModal } from '@/components/AuthModals';

// 角色中文名（下拉组头展示，让用户自证权限身份）
const ROLE_LABEL = { manager: '管理员', vip: '高级用户', normal: '普通用户' };

// 未登录进入这些标签会展示缺省页（自带「立即登录」CTA）；此时隐藏右上角登录，避免同屏两个登录按钮
const GUEST_GATE_TABS = new Set(['history', 'settings']);

export default function StudioLayout({ children, activeTab = 'workspace', onTabChange }) {
  const [currentTab, setCurrentTab] = useState(activeTab);
  const { theme, toggleTheme } = useTheme();
  const { authenticated, user, isAdmin, doLogout } = useAuth();
  const [loginOpen, setLoginOpen] = useState(false);
  const [pwdOpen, setPwdOpen] = useState(false);

  // 父级 activeTab 变化时（如 ?tab= 深链、历史「去编辑」切回工作台）同步高亮
  useEffect(() => {
    setCurrentTab(activeTab);
  }, [activeTab]);

  const handleTabChange = (key) => {
    // 未登录点受保护标签不再弹登录框拦截：直接切换，由页面渲染缺省页引导登录
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
    // 用户管理与系统设置：仅管理员可见（后端接口同样强制 require_admin，
    // 这里隐藏只是体验，不是权限）
    ...(isAdmin
      ? [
          {
            key: 'users',
            label: (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <UsersIcon size={15} />
                用户管理
              </span>
            ),
          },
          {
            key: 'system',
            label: (
              <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <SlidersHorizontal size={15} />
                系统设置
              </span>
            ),
          },
        ]
      : []),
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-dark)' }}>
      {/* 顶部导航 */}
      <header
        style={{
          background: 'var(--bg-card)',
          borderBottom: '1px solid var(--border)',
          padding: '0 32px',
          display: 'grid',
          gridTemplateColumns: '1fr auto 1fr',
          alignItems: 'center',
          height: 56,
          position: 'sticky',
          top: 0,
          zIndex: 100,
          boxShadow: 'var(--shadow)',
        }}
      >
        {/* 左侧：Logo */}
        <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: 12, textDecoration: 'none', justifySelf: 'start' }}>
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
          <AppVersion />
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

        {/* 右侧：操作区（深浅切换 + 账户；回首页交给左上角 Logo，不再放重复入口） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, justifySelf: 'end' }}>
          {/* 主题切换按钮 */}
          <button
            className="theme-toggle"
            onClick={toggleTheme}
            title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          >
            {theme === 'dark' ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          {/* 身份：已登录→头像下拉；未登录→登录按钮（占最右黄金角） */}
          {authenticated && user ? (
            <Dropdown
              menu={{
                items: [
                  {
                    key: 'who',
                    type: 'group',
                    label: (
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                        {user.nickname || user.username}
                        <Tag color={user.role === 'manager' ? 'gold' : user.role === 'vip' ? 'purple' : 'default'} style={{ margin: 0 }}>
                          {ROLE_LABEL[user.role] || user.role}
                        </Tag>
                      </span>
                    ),
                  },
                  { key: 'pwd', icon: <KeyRound size={14} />, label: '修改密码', onClick: () => setPwdOpen(true) },
                  { type: 'divider' },
                  { key: 'logout', icon: <LogOut size={14} />, label: '退出登录', onClick: () => doLogout() },
                ],
              }}
              trigger={['click']}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <Avatar size={28} style={{ background: 'var(--primary)', fontSize: 13 }}>
                  {(user.nickname || user.username).slice(0, 1).toUpperCase()}
                </Avatar>
                <span style={{ fontSize: 13, color: 'var(--text-primary)' }}>{user.nickname || user.username}</span>
              </div>
            </Dropdown>
          ) : GUEST_GATE_TABS.has(currentTab) ? null : (
            <button
              onClick={() => setLoginOpen(true)}
              style={{
                padding: '6px 14px',
                background: 'var(--primary)',
                color: '#fff',
                border: 'none',
                borderRadius: 6,
                fontSize: 13,
                cursor: 'pointer',
              }}
            >
              登录
            </button>
          )}
        </div>
      </header>

      {/* 主内容区 */}
      <main style={{ padding: '24px 32px' }}>
        {children}
      </main>

      <LoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
      <ChangePasswordModal open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </div>
  );
}
