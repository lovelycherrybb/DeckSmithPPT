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
  Sun,
  Moon,
  LogOut,
  KeyRound,
} from 'lucide-react';

import { useTheme } from '@/components/ThemeProvider';
import { useAuth } from '@/components/AuthProvider';
import { ChangePasswordModal, LoginModal } from '@/components/AuthModals';

// 未登录需跳登录才能进的标签（工作台对访客开放）
const AUTH_REQUIRED_TABS = new Set(['history', 'settings', 'users']);

// 角色中文名（下拉组头展示，让用户自证权限身份）
const ROLE_LABEL = { manager: '管理员', vip: '高级用户', normal: '普通用户' };

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
    // 菜单守卫：未登录点受保护标签 → 弹登录，不切换
    if (AUTH_REQUIRED_TABS.has(key) && !authenticated) {
      setLoginOpen(true);
      return;
    }
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
    // 用户管理：仅管理员可见（后端接口也强制 require_admin）
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
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
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
          ) : (
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
