/**
 * 进度展示：running 任务的状态明细 + 引擎 progress（页数/耗时/阶段）+ 逐页预览页墙。
 */
'use client';

import { Typography } from 'antd';
import type { ProgressInfo } from '@/lib/api';
import { useJobPages } from '@/hooks/useJobPages';
import { useAuth } from '@/components/AuthProvider';
import PagePreviewGrid from '@/components/features/job/PagePreviewGrid';

const { Text } = Typography;

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return mins > 0 ? `${mins}分${secs}秒` : `${secs}秒`;
}

/** 从内部页文件名里取页码（如 page_03.svg、03_title.svg 都取到 3），取不到返回 null */
function pageNumberOf(name: string): number | null {
  const m = /(\d+)/.exec(name);
  return m ? Number(m[1]) : null;
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
  // 技术细节门控：原始日志与内部文件名只对高级用户/管理员显示（与生成设置同一口径，
  // AGENTS 规则 8/11）；普通用户看到这些只会以为页面出了错。
  const { user } = useAuth();
  const canSeeDiagnostics = user?.role === 'vip' || user?.role === 'manager';

  if (!progress) return null;
  const { stage_label, pages_generated = 0, elapsed_seconds = 0, current_page, last_log } = progress;
  const pageNo = current_page ? pageNumberOf(current_page) : null;

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
        <Text strong>{stage_label || '正在生成'}</Text>
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
          <Text strong>{pageNo === null ? '正在生成下一页…' : `正在生成第 ${pageNo} 页`}</Text>
        </div>
      )}
      {canSeeDiagnostics && (current_page || last_log) && (
        <div
          style={{
            padding: 12,
            backgroundColor: 'var(--bg-card)',
            borderRadius: 6,
            border: '1px solid var(--border)',
          }}
        >
          <Text type="secondary" style={{ fontSize: 12 }}>技术详情（供排查）</Text>
          {current_page && (
            <pre style={{ margin: '6px 0 0', fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {current_page}
            </pre>
          )}
          {last_log && (
            <pre style={{ margin: '6px 0 0', fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {last_log}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
