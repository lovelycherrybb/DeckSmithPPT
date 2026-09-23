'use client';

import { useEffect, useState } from 'react';
import StudioLayout from '@/components/StudioLayout';
import WorkspaceTab from '@/components/tabs/WorkspaceTab';
import HistoryTab from '@/components/tabs/HistoryTab';
import SettingsTab from '@/components/tabs/SettingsTab';
import UsersTab from '@/components/tabs/UsersTab';
import { AuthProvider, useAuth } from '@/components/AuthProvider';

const PROTECTED_TABS = new Set(['history', 'settings', 'users']);
const ADMIN_TABS = new Set(['users']);

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
    if (t && PROTECTED_TABS.has(t) || t === 'workspace') setActiveTab(t);
  }, []);

  // 深链/登录态守卫：受保护标签未登录回落工作台；管理员专属标签非管理员也回落
  const effectiveTab =
    (PROTECTED_TABS.has(activeTab) && !authenticated) || (ADMIN_TABS.has(activeTab) && !isAdmin)
      ? 'workspace'
      : activeTab;

  const renderContent = () => {
    switch (effectiveTab) {
      case 'workspace':
        // key 让每次带项目跳入都重挂载，初始项目生效
        return <WorkspaceTab key={workspaceProjectId || 'fresh'} initialProjectId={workspaceProjectId} />;
      case 'history':
        return (
          <HistoryTab
            onOpenInWorkspace={(pid) => {
              setWorkspaceProjectId(pid);
              setActiveTab('workspace');
            }}
          />
        );
      case 'settings':
        return <SettingsTab />;
      case 'users':
        return <UsersTab />;
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
