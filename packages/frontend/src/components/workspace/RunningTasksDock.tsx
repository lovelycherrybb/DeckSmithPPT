/**
 * 常驻进度浮窗：右下角显示**未结束**任务的锚点（跨子菜单可见）。
 * 点击切到「生成任务」并打开详情抽屉（通过 decksmith:open-job 事件）。
 *
 * 排队中的任务也要有回执，但**不逐条画胶囊**：并发闸只管"同时在跑几个"，
 * 队列长度可以是几十。真浏览器复验时 60 条排队任务把整屏右侧铺满过——
 * 所以生成中的按条显示（数量被闸门压着），排队的聚合成一条「排队中 N 个」。
 */
'use client';

import { Badge, Button, Space, Tooltip } from 'antd';
import { Activity, Clock, FileText, Rocket } from 'lucide-react';
import type { JobWithProgress } from '@/lib/api';
import { isGenerating } from '@/lib/jobStatus';

/** 生成中的胶囊上限：闸门理论上会压住，这里只是防呆（闸门被调到很大时不铺满屏） */
const MAX_PILLS = 6;

interface Props {
  runningJobs: JobWithProgress[];
  projectNames: Record<string, string>;
  onOpenJob: (jobId: string) => void;
  /** 只切到「生成任务」列表、不打开某一条详情（给聚合的排队胶囊用） */
  onOpenJobsList?: () => void;
}

export default function RunningTasksDock({
  runningJobs,
  projectNames,
  onOpenJob,
  onOpenJobsList,
}: Props) {
  if (runningJobs.length === 0) return null;

  const generating = runningJobs.filter((j) => isGenerating(j.status));
  const queued = runningJobs.filter((j) => j.status === 'queued');
  const shown = generating.slice(0, MAX_PILLS);
  const hiddenGenerating = generating.length - shown.length;

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
      {shown.map((job) => {
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

      {/* 生成中但被胶囊上限挡掉的：折成一条计数，不铺屏 */}
      {hiddenGenerating > 0 && (
        <Button
          shape="round"
          onClick={() => onOpenJobsList?.()}
          style={{ boxShadow: '0 4px 12px rgba(0,0,0,0.35)', background: 'var(--bg-card)' }}
        >
          <Space size={4}>
            <Activity size={13} color="#faad14" />
            <span style={{ fontSize: 13 }}>还有 {hiddenGenerating} 个在生成</span>
          </Space>
        </Button>
      )}

      {/* 排队中的一律聚合成一条：队列长度只受"单身份未完成任务上限"约束，
          逐条画会把整屏右侧铺满（真浏览器复验时 60 条就铺满了） */}
      {queued.length > 0 && (
        <Tooltip
          title={
            <div>
              <div>{`排队中 ${queued.length} 个`}</div>
              <div style={{ fontSize: 12, opacity: 0.8 }}>
                {`下一个是「${(queued.find((j) => (j.queue_position ?? 1e9) === Math.min(...queued.map((x) => x.queue_position ?? 1e9)))?.title || '未命名任务').slice(0, 16)}」`}
              </div>
              <div style={{ fontSize: 12, opacity: 0.6, marginTop: 4 }}>点击去列表取消排队</div>
            </div>
          }
        >
          <Button
            shape="round"
            onClick={() => onOpenJobsList?.()}
            icon={
              <Badge dot status="default">
                <Clock size={14} />
              </Badge>
            }
            style={{ boxShadow: '0 4px 12px rgba(0,0,0,0.35)', background: 'var(--bg-card)' }}
          >
            <Space size={4}>
              <Activity size={13} color="#8c8c8c" />
              <span style={{ fontSize: 13 }}>{`排队中 ${queued.length} 个`}</span>
            </Space>
          </Button>
        </Tooltip>
      )}
    </div>
  );
}
