'use client';

import { useEffect, useState } from 'react';
import StudioLayout from '@/components/StudioLayout';
import WorkspaceTab from '@/components/tabs/WorkspaceTab';
import HistoryTab from '@/components/tabs/HistoryTab';
import SettingsTab from '@/components/tabs/SettingsTab';

export default function StudioPage() {
  const [activeTab, setActiveTab] = useState('workspace');
  // 历史项目「操作」跳入工作台时携带的项目
  const [workspaceProjectId, setWorkspaceProjectId] = useState<string | null>(null);

  // 支持 ?tab=workspace|history|settings 直达某个标签（放 useEffect 避免 SSR hydration 不一致）
  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    if (t === 'workspace' || t === 'history' || t === 'settings') setActiveTab(t);
  }, []);

  const renderContent = () => {
    switch (activeTab) {
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
      default:
        return <WorkspaceTab />;
    }
  };

  return (
    <StudioLayout activeTab={activeTab} onTabChange={setActiveTab}>
      {renderContent()}
    </StudioLayout>
  );
}
