'use client';

import { useEffect, useState } from 'react';
import StudioLayout from '@/components/StudioLayout';
import WorkspaceTab from '@/components/tabs/WorkspaceTab';
import HistoryTab from '@/components/tabs/HistoryTab';
import SettingsTab from '@/components/tabs/SettingsTab';
import UsersTab from '@/components/tabs/UsersTab';
import SystemSettingsTab from '@/components/tabs/SystemSettingsTab';
import GuestGate from '@/components/GuestGate';
import { AuthProvider, useAuth } from '@/components/AuthProvider';

// 管理员专属标签：非管理员（含未登录）直接回落工作台，不给缺省页
const ADMIN_TABS = new Set(['users', 'system']);
// ?tab= 深链允许直达的标签
const VALID_TABS = new Set(['workspace', 'history', 'settings', 'users', 'system']);

export default function StudioPage() {
  return (
    <AuthProvider>
      <StudioInner />
    </AuthProvider>
  );
}

function StudioInner() {
  const { authenticated, isAdmin } = useAuth();
  const [activeTab, setActiveTab] = useState('workspace');
  // 历史项目「操作」跳入工作台时携带的项目
  const [workspaceProjectId, setWorkspaceProjectId] = useState<string | null>(null);

  // 支持 ?tab=workspace|history|settings|users 直达某个标签（放 useEffect 避免 SSR hydration 不一致）
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    if (t && VALID_TABS.has(t)) setActiveTab(t);
  }, []);

  // 管理员专属标签：非管理员（含未登录）回落工作台。
  // 历史/设置不再弹回工作台——未登录时进入标签并展示缺省页（见 renderContent）。
  const effectiveTab = ADMIN_TABS.has(activeTab) && !isAdmin ? 'workspace' : activeTab;

  const renderContent = () => {
    switch (effectiveTab) {
      case 'workspace':
        // key 让每次带项目跳入都重挂载，初始项目生效
        return <WorkspaceTab key={workspaceProjectId || 'fresh'} initialProjectId={workspaceProjectId} />;
      case 'history':
        if (!authenticated) {
          return (
            <GuestGate
              title="登录后查看历史项目"
              description="历史项目面向注册用户开放，登录即可查看你创建过的项目与演示文件。"
            />
          );
        }
        return (
          <HistoryTab
            onOpenInWorkspace={(pid) => {
              setWorkspaceProjectId(pid);
              setActiveTab('workspace');
            }}
          />
        );
      case 'settings':
        if (!authenticated) {
          return (
            <GuestGate
              title="登录后查看个人设置"
              description="个人设置面向注册用户开放，登录即可保存默认输出引擎、默认页数等偏好。"
            />
          );
        }
        return <SettingsTab />;
      case 'users':
        return <UsersTab />;
      case 'system':
        // 系统设置：ADMIN_TABS 已把非管理员弹回工作台，这里不再判权限（后端也强制）
        return <SystemSettingsTab />;
      default:
        return <WorkspaceTab />;
    }
  };

  return (
    <StudioLayout activeTab={effectiveTab} onTabChange={setActiveTab}>
      {renderContent()}
    </StudioLayout>
  );
}
