'use client';

import dynamic from 'next/dynamic';

// 使用 dynamic import 禁用 SSR，避免 Ant Design 的 hydration 问题
const HomePageContent = dynamic(() => import('../components/HomePageContent'), {
  ssr: false,
  loading: () => (
    <div style={{ 
      display: 'flex', 
      justifyContent: 'center', 
      alignItems: 'center', 
      height: '100vh',
      fontFamily: 'Inter, sans-serif'
    }}>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 24, fontWeight: 600, marginBottom: 8 }}>DeckSmithPower</div>
        <div style={{ color: '#6b7280' }}>加载中...</div>
      </div>
    </div>
  ),
});

export default function Page() {
  return <HomePageContent />;
}
