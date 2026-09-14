/**
 * 逐页预览页墙（引擎无关，数据驱动）。
 *
 * 诚实性约定（constraints 契约）：
 * - 只渲染 data.pages[]（已确定落盘的完成页），顶部文案「已生成 N 页」，无 /M、无进度条分母；
 * - 运行中只在末尾追加恰好一格转圈：generating → 「正在生成第 N+1 页」，导出阶段 → 「正在导出成品」；
 * - completed/failed/timeout 无末尾格。
 * 放大用 antd Image.PreviewGroup（自带左右翻页/缩放），零新依赖；
 * 深浅主题走 CSS 变量 token，不硬编码浅色。
 */
'use client';

import { useEffect, useRef, useState } from 'react';
import { Image, Spin, Tooltip, Typography } from 'antd';
import type { EngineInfo, JobPages, PageItem } from '@/lib/api';

const { Text } = Typography;

/** 元素首次进入视口后返回 true（用于缩略图懒加载，避免整墙 iframe 同时拉起 deck） */
function useInView<T extends HTMLElement>(rootMargin = '200px') {
  const ref = useRef<T | null>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    if (seen) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      },
      { rootMargin },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen, rootMargin]);
  return { ref, seen };
}

/**
 * 单文件 HTML 引擎（guizang_ppt）的页格：每格都是同一个 result.html，
 * 由后端注入的引导脚本按 ?slide=N 定位到该页；?static=1 让模板关掉 WebGL 背景
 * （__setLowPowerMode），缩略格不必各跑一份 shader。
 *
 * 沙箱口径：只给 allow-scripts、**不给 allow-same-origin** —— 产物是 agent 生成的
 * HTML，放开同源就等于让它读主应用的 localStorage。后端同时对预览响应下发
 * `Content-Security-Policy: sandbox allow-scripts`，双保险保持 opaque origin。
 */
function SlideIframeCell({ page }: { page: PageItem }) {
  const { ref, seen } = useInView<HTMLDivElement>();
  const base = `${page.url}${page.url.includes('?') ? '&' : '?'}slide=${page.index}`;
  return (
    <div
      ref={ref}
      style={{ ...cellBase, border: '1px solid var(--border)', cursor: 'pointer' }}
      onClick={() => window.open(base, '_blank', 'noopener')}
      title="点击在新标签放映"
    >
      <PageBadge index={page.index} />
      {seen ? (
        <iframe
          src={`${base}&static=1`}
          title={page.name}
          style={{ width: '100%', height: '100%', border: 'none', pointerEvents: 'none' }}
          sandbox="allow-scripts"
          loading="lazy"
        />
      ) : (
        <div style={{ width: '100%', height: '100%', display: 'grid', placeItems: 'center' }}>
          <Spin size="small" />
        </div>
      )}
    </div>
  );
}

/** 图片加载失败（产物目录被清理等）的占位图：仅该格降级，不整墙报错 */
const CLEANED_FALLBACK_SRC =
  'data:image/svg+xml;utf8,' +
  encodeURIComponent(
    `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180">
      <rect width="320" height="180" fill="#88888822"/>
      <text x="160" y="96" font-size="16" text-anchor="middle" fill="#888888">产物已清理</text>
    </svg>`
  );

const cellBase: React.CSSProperties = {
  position: 'relative',
  aspectRatio: '16 / 9',
  borderRadius: 8,
  overflow: 'hidden',
  background: 'var(--bg-card)',
};

function PageBadge({ index }: { index: number }) {
  return (
    <span
      style={{
        position: 'absolute',
        top: 6,
        left: 6,
        zIndex: 2,
        minWidth: 22,
        padding: '1px 6px',
        textAlign: 'center',
        fontSize: 12,
        borderRadius: 4,
        background: 'rgba(0,0,0,0.45)',
        color: '#fff',
      }}
    >
      {index}
    </span>
  );
}

function PageCard({ page }: { page: PageItem }) {
  if (page.kind === 'html') {
    // 单文件 HTML 引擎：整段 deck 由引导脚本定位到本页（见 SlideIframeCell）
    return <SlideIframeCell page={page} />;
  }
  return (
    <Tooltip title={page.name}>
      <div style={{ ...cellBase, border: '1px solid var(--border)' }}>
        <PageBadge index={page.index} />
        <Image
          src={page.url}
          alt={page.name}
          fallback={CLEANED_FALLBACK_SRC}
          wrapperStyle={{ width: '100%', height: '100%' }}
          style={{ objectFit: 'contain', width: '100%', height: '100%', cursor: 'zoom-in' }}
        />
      </div>
    </Tooltip>
  );
}

/** 末尾追加的「生成中/导出中」格（仅 running，恒定恰好一格；不代表某张具体页已存在） */
function InProgressCell({ data }: { data: JobPages }) {
  const exporting = !!data.stage && data.stage !== 'generating';
  return (
    <div
      style={{
        ...cellBase,
        border: '1px dashed var(--border)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
      }}
    >
      <Spin size="small" />
      <Text style={{ fontSize: 12, color: 'var(--text-muted)' }}>
        {exporting ? '正在导出成品' : `正在生成第 ${data.pages_generated + 1} 页`}
      </Text>
    </div>
  );
}

interface Props {
  data: JobPages;
  /** 嵌入深色面板时可略缩头部（文件库 Drawer 里隐藏「已生成 N 页」头部） */
  showHeader?: boolean;
}

export default function PagePreviewGrid({ data, showHeader = true }: Props) {
  const running = data.status === 'running';
  return (
    <div>
      {showHeader && (
        <div style={{ marginBottom: 8 }}>
          <Text strong style={{ fontSize: 13 }}>
            已生成 {data.pages_generated} 页
          </Text>
          {running && (
            <Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              （整页落盘后自动出现）
            </Text>
          )}
        </div>
      )}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))',
          gap: 12,
        }}
      >
        <Image.PreviewGroup>
          {data.pages.map((p) => (
            <PageCard key={`${p.index}-${p.name}`} page={p} />
          ))}
        </Image.PreviewGroup>
        {running && <InProgressCell data={data} />}
      </div>
      {data.supports_page_preview && data.pages.length === 0 && !running && (
        <Text type="secondary" style={{ fontSize: 12 }}>
          暂无可查看的页面（产物目录可能已被清理）
        </Text>
      )}
    </div>
  );
}

/**
 * 计算哪些任务的产物支持逐页预览：job 的 engine 在引擎清单中声明了 page_preview 能力。
 * 能力来自 GET /api/engines 的 capabilities（后端由钩子实现派生，非前端按引擎名硬判）。
 */
export function collectPreviewableTaskIds(
  jobs: Array<{ job_id: string; engine?: string | null; task_type?: string | null }>,
  engines: EngineInfo[]
): Set<string> {
  const previewEngines = new Set(
    engines.filter((e) => (e.capabilities ?? []).includes('page_preview')).map((e) => e.name)
  );
  const ids = new Set<string>();
  for (const j of jobs) {
    if (j.engine && previewEngines.has(j.engine)) ids.add(j.job_id);
  }
  return ids;
}
