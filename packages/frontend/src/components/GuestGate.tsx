'use client';

/**
 * 未登录访问受保护标签（历史项目 / 设置）时的缺省页。
 * 说明该功能面向注册用户开放，并提供登录入口。
 * 登录成功后 authenticated 翻真，父级同一标签会自动渲染真实内容，无需显式跳转。
 */
import { useState } from 'react';
import { Button, Card } from 'antd';
import { Lock } from 'lucide-react';

import { LoginModal } from '@/components/AuthModals';

export default function GuestGate({ title, description }: { title: string; description: string }) {
  const [loginOpen, setLoginOpen] = useState(false);
  return (
    <div style={{ display: 'flex', justifyContent: 'center', paddingTop: 64 }}>
      <Card style={{ maxWidth: 460, width: '100%', textAlign: 'center' }}>
        <div
          style={{
            width: 56,
            height: 56,
            margin: '0 auto 16px',
            borderRadius: 14,
            background: 'var(--primary-bg)',
            color: 'var(--primary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Lock size={26} />
        </div>
        <div style={{ fontSize: 17, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>
          {title}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 20 }}>
          {description}
        </div>
        <Button type="primary" onClick={() => setLoginOpen(true)}>
          立即登录
        </Button>
      </Card>
      <LoginModal open={loginOpen} onClose={() => setLoginOpen(false)} />
    </div>
  );
}
