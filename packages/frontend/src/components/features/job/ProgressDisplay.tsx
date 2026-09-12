/**
 * 进度展示：running 任务的状态明细 + 引擎 progress（页数/耗时/阶段）+ 逐页预览页墙。
 */
'use client';

import { Typography } from 'antd';
import type { ProgressInfo } from '@/lib/api';
import { useJobPages } from '@/hooks/useJobPages';
import PagePreviewGrid from '@/components/features/job/PagePreviewGrid';

const { Text } = Typography;

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return mins > 0 ? `${mins}分${secs}秒` : `${secs}秒`;
}

export default function ProgressDisplay({
  progress,
  jobId,
}: {
  progress?: ProgressInfo | null;
  jobId?: string | null;
}) {
  // 轮询瘦身：列表轮询只带计数，整页列表只在页数/阶段变化时按需拉
  const watchKey = `${progress?.pages_generated ?? 0}-${progress?.stage ?? ''}`;
  const { data: pages } = useJobPages(jobId ?? null, watchKey);

  if (!progress) return null;
  const { stage_label, pages_generated = 0, elapsed_seconds = 0, current_page, last_log } = progress;

  return (
    <div
      style={{
        marginTop: 16,
        padding: 16,
        backgroundColor: 'var(--bg-input)',
        borderRadius: 8,
        border: '1px solid var(--border)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <span className="progress-dot" />
        <Text strong>{stage_label || '引擎执行中'}</Text>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16, marginBottom: 16 }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, fontWeight: 'bold', color: 'var(--info)' }}>{pages_generated}</div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>已生成页面</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, fontWeight: 'bold', color: 'var(--warning)' }}>
            {formatTime(elapsed_seconds)}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>已耗时</div>
        </div>
      </div>
      {pages && pages.supports_page_preview && (
        <div style={{ marginBottom: 16 }}>
          <PagePreviewGrid data={pages} />
        </div>
      )}
      {current_page && (
        <div
          style={{
            marginBottom: 12,
            padding: 12,
            backgroundColor: 'var(--bg-card)',
            borderRadius: 6,
            border: '1px solid var(--border)',
          }}
        >
          <Text strong style={{ display: 'block', marginBottom: 4 }}>
            当前正在生成：
          </Text>
          <Text code style={{ fontSize: 14 }}>
            {current_page}
          </Text>
        </div>
      )}
      {last_log && (
        <div>
          <Text strong style={{ display: 'block', marginBottom: 8 }}>
            最近日志：
          </Text>
          <pre
            style={{
              backgroundColor: 'var(--bg-card)',
              padding: 12,
              borderRadius: 6,
              fontSize: 12,
              maxHeight: 120,
              overflow: 'auto',
              border: '1px solid var(--border)',
              margin: 0,
              fontFamily: 'Monaco, Consolas, "Courier New", monospace',
            }}
          >
            {last_log}
          </pre>
        </div>
      )}
    </div>
  );
}
