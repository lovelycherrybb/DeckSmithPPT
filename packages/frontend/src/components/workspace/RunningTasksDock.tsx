/**
 * 常驻进度浮窗：右上角显示运行中任务锚点（跨子菜单可见）。
 * 点击切到「生成任务」并打开详情抽屉（通过 decksmith:open-job 事件）。
 */
'use client';

import { Badge, Button, Space, Tooltip } from 'antd';
import { Activity, FileText, Rocket } from 'lucide-react';
import type { JobWithProgress } from '@/lib/api';

interface Props {
  runningJobs: JobWithProgress[];
  projectNames: Record<string, string>;
  onOpenJob: (jobId: string) => void;
}

export default function RunningTasksDock({ runningJobs, projectNames, onOpenJob }: Props) {
  if (runningJobs.length === 0) return null;

  return (
    <div
      style={{
        position: 'fixed',
        right: 24,
        bottom: 24,
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
      }}
    >
      {runningJobs.map((job) => {
        const p = job.progress;
        const elapsed =
          p?.elapsed_seconds != null
            ? `${Math.floor(p.elapsed_seconds / 60)}分${p.elapsed_seconds % 60}秒`
            : null;
        const isDraft = job.task_type === 'draft';
        const displayName =
          job.title || (isDraft ? projectNames[job.project_id || ''] || '草稿生成中' : '未命名任务');

        // 悬停内容：按任务类型分支（草稿无页数概念）
        const tooltip = (
          <div>
            <div>{displayName}</div>
            <div style={{ fontSize: 12, opacity: 0.8 }}>
              {p?.stage_label || job.status_detail || '引擎执行中'}
              {isDraft
                ? null
                : p?.pages_generated != null
                  ? ` · 已生成 ${p.pages_generated} 页`
                  : ''}
              {elapsed ? ` · ${elapsed}` : ''}
            </div>
            <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>点击查看详情</div>
          </div>
        );

        // 胶囊内容：草稿显示「项目名/草稿生成中」，输出显示页数
        const pillLabel = isDraft ? (
          <span style={{ fontSize: 13 }}>
            {displayName.slice(0, 12)} · 草稿生成中
          </span>
        ) : (
          <span style={{ fontSize: 13 }}>
            {job.title?.slice(0, 10) || job.job_id.slice(0, 8)}
            {p?.pages_generated != null && ` (${p.pages_generated}页)`}
          </span>
        );

        return (
          <Tooltip key={job.job_id} title={tooltip}>
            <Button
              shape="round"
              onClick={() => onOpenJob(job.job_id)}
              icon={
                <Badge dot status="processing">
                  {isDraft ? <FileText size={14} /> : <Rocket size={14} />}
                </Badge>
              }
              style={{
                boxShadow: '0 4px 12px rgba(0,0,0,0.35)',
                background: 'var(--bg-card)',
              }}
            >
              <Space size={4}>
                <Activity size={13} color={isDraft ? '#8b5cf6' : '#faad14'} />
                {pillLabel}
              </Space>
            </Button>
          </Tooltip>
        );
      })}
    </div>
  );
}
