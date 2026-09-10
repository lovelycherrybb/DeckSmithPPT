'use client';

import { useEffect, useRef } from 'react';
import Link from 'next/link';
import { useTheme } from '@/components/ThemeProvider';

export default function HomePageContent() {
  const pageRef = useRef(null);
  const glowRef = useRef(null);
  const dsRef = useRef(null);
  const { theme, toggleTheme } = useTheme();

  useEffect(() => {
    const page = pageRef.current;
    const glow = glowRef.current;
    const ds = dsRef.current;

    if (!page || !glow || !ds) return;

    const handleMouseMove = (e) => {
      const x = e.clientX;
      const y = e.clientY;
      const w = window.innerWidth;
      const h = window.innerHeight;
      
      glow.style.background = `radial-gradient(600px circle at ${x}px ${y}px, rgba(139,92,246,0.15), transparent 40%)`;
      
      const rotateY = ((x / w) - 0.5) * 8;
      const rotateX = ((y / h) - 0.5) * -5;
      ds.style.transform = `perspective(500px) rotateX(${rotateX}deg) rotateY(${rotateY}deg)`;
    };

    page.addEventListener('mousemove', handleMouseMove);
    return () => page.removeEventListener('mousemove', handleMouseMove);
  }, []);

  const isDark = theme === 'dark';

  return (
    <div
      ref={pageRef}
      style={{
        minHeight: '100vh',
        background: isDark ? '#0a0a0a' : '#f8fafc',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative',
        overflow: 'hidden',
        transition: 'background 300ms ease',
      }}
    >
      {/* 主题切换按钮 */}
      <button
        onClick={toggleTheme}
        style={{
          position: 'fixed',
          top: 24,
          right: 24,
          width: 44,
          height: 44,
          borderRadius: 12,
          border: `1px solid ${isDark ? '#1f1f1f' : '#e2e8f0'}`,
          background: isDark ? '#111111' : '#ffffff',
          color: isDark ? '#9ca3af' : '#475569',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'all 200ms ease',
          zIndex: 100,
        }}
        title={isDark ? '切换到浅色主题' : '切换到深色主题'}
      >
        {isDark ? (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="5"/>
            <line x1="12" y1="1" x2="12" y2="3"/>
            <line x1="12" y1="21" x2="12" y2="23"/>
            <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
            <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
            <line x1="1" y1="12" x2="3" y2="12"/>
            <line x1="21" y1="12" x2="23" y2="12"/>
            <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
            <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
          </svg>
        ) : (
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
        )}
      </button>

      <div
        ref={glowRef}
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          pointerEvents: 'none',
        }}
      />
      <div style={{ textAlign: 'center', position: 'relative', zIndex: 10 }}>
        <div
          ref={dsRef}
          style={{
            fontSize: 180,
            fontWeight: 900,
            lineHeight: 1,
            letterSpacing: -8,
            marginBottom: 20,
            transition: 'transform 0.15s ease-out',
          }}
        >
          <span style={{ color: '#8b5cf6' }}>D</span>
          <span style={{ color: isDark ? '#f3f4f6' : '#0f172a' }}>S</span>
        </div>
        <div style={{ 
          fontSize: 20, 
          color: isDark ? '#9ca3af' : '#64748b', 
          letterSpacing: 4, 
          marginBottom: 12 
        }}>
          材料 × AI = 演示文件
        </div>
        <div style={{ 
          fontSize: 14, 
          color: isDark ? '#6b7280' : '#94a3b8', 
          letterSpacing: 8, 
          marginBottom: 40 
        }}>
          DECKSMITHPOWER
        </div>
        <Link href="/studio">
          <button style={{ 
            padding: '16px 48px', 
            background: '#8b5cf6', 
            color: '#fff', 
            border: 'none', 
            fontSize: 16, 
            fontWeight: 600, 
            cursor: 'pointer',
            transition: 'all 200ms ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = '#7c3aed';
            e.currentTarget.style.transform = 'translateY(-2px)';
            e.currentTarget.style.boxShadow = '0 8px 24px rgba(139, 92, 246, 0.3)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = '#8b5cf6';
            e.currentTarget.style.transform = 'translateY(0)';
            e.currentTarget.style.boxShadow = 'none';
          }}
          >
            开始使用
          </button>
        </Link>
      </div>
    </div>
  );
}
