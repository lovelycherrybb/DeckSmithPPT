/**
 * 通用组件：状态标签 / 内容风格文案 / 引擎参数工具。
 * 从旧 studio/page.js 抽出，全站复用（消灭 4 页复制粘贴）。
 */
import { Tag } from 'antd';
import type { JobStatus } from '@/lib/api';

const STATUS_META: Record<JobStatus, { color: string; label: string }> = {
  queued: { color: 'blue', label: '排队中' },
  running: { color: 'gold', label: '运行中' },
  completed: { color: 'green', label: '已完成' },
  failed: { color: 'red', label: '失败' },
  timeout: { color: 'orange', label: '超时' },
  // cancelled 的中文文案与配色属于前端那一批的设计（撤销按钮、排队中文案一并敲）；
  // 这里先补齐键位是为了让 `Record<JobStatus, …>` 的穷尽检查过编译 ——
  // 少了它 `next build` 直接红，容器镜像就构建不出来。
  cancelled: { color: 'default', label: '已撤销' },
};

export function StatusTag({ status }: { status?: JobStatus | string }) {
  const meta = STATUS_META[status as JobStatus] || { color: 'default', label: status || '-' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

const CONTENT_STYLE_LABELS: Record<string, string> = {
  marketing_promo: '宣传材料',
  product_story: '产品介绍',
  briefing: '方案汇报',
  event_promo: '活动推广',
  investment_pitch: '招商介绍',
};

export function contentStyleLabel(value?: string | null): string {
  return CONTENT_STYLE_LABELS[value || ''] || value || '未选择';
}

export function summarizeEngineParams(params: Record<string, unknown> | undefined): string {
  if (!params) return '未选择';
  const rows: string[] = [];
  if (params.style) rows.push(`风格：${params.style}`);
  if (params.mode) rows.push(`模式：${params.mode}`);
  if (params.format) rows.push(`画布：${params.format}`);
  if (typeof params.notes === 'boolean') rows.push(`备注：${params.notes ? '开启' : '关闭'}`);
  if (params.animation) rows.push(`动画：${params.animation}`);
  return rows.length ? rows.join(' / ') : '未选择';
}
