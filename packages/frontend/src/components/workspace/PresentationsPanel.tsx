/**
 * 子菜单④：演示文件库——当前项目的成品文件，文件夹式卡片视图（图标 + 文件名）。
 * 逐页预览入口：仅对引擎声明了 page_preview 能力的成品显示（task_id 复用 /jobs/{id}/pages）。
 */
'use client';

import { useState } from 'react';
import { Button, Card, Drawer, Empty, Spin, Tooltip } from 'antd';
import { Download, Eye, Presentation as PresentationIcon, RefreshCw } from 'lucide-react';
import { presentationDownloadUrl, type Presentation } from '@/lib/api';
import { useJobPages } from '@/hooks/useJobPages';
import PagePreviewGrid from '@/components/features/job/PagePreviewGrid';
import { formatDate } from '@/lib/formatDate';

interface Props {
  presentations: Presentation[];
  loading: boolean;
  /** 产物支持逐页预览的任务 id 集合（父级按引擎 capabilities 派生，引擎无关） */
  previewableTaskIds?: Set<string>;
  onRefresh: () => void;
}

// 文件类型 → 图标主色（PPTX 橙、HTML 青，贴近系统里文件图标的观感）
const FORMAT_COLOR: Record<string, string> = {
  pptx: '#d24726',
  html: '#0891b2',
};

function PagesDrawer({ task, onClose }: { task: Presentation | null; onClose: () => void }) {
  const { data, loading } = useJobPages(task ? task.task_id : null, task?.presentation_id);
  return (
    <Drawer
      title={task ? `逐页预览 · ${task.file_name}` : '逐页预览'}
      width={'70%'}
      open={!!task}
      onClose={onClose}
    >
      {loading && !data ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : data && data.supports_page_preview ? (
        <PagePreviewGrid data={data} />
      ) : (
        <Empty description="暂无可预览内容：该引擎不支持逐页预览，或产物/任务信息不可用" />
      )}
    </Drawer>
  );
}

export default function PresentationsPanel({
  presentations,
  loading,
  previewableTaskIds,
  onRefresh,
}: Props) {
  const [previewTask, setPreviewTask] = useState<Presentation | null>(null);
  return (
    <Card
      title="演示文件库"
      extra={
        <Button size="small" icon={<RefreshCw size={13} />} onClick={onRefresh}>
          刷新
        </Button>
      }
    >
      {loading ? (
        <div style={{ textAlign: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : presentations.length === 0 ? (
        <Empty description="当前项目暂无演示文件，生成任务完成后会出现在这里" />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))',
            gap: 16,
          }}
        >
          {presentations.map((p) => {
            const color = FORMAT_COLOR[String(p.file_format).toLowerCase()] || '#64748b';
            return (
              <div
                key={p.presentation_id}
                style={{
                  border: '1px solid var(--border)',
                  borderRadius: 10,
                  padding: '20px 14px 14px',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: 10,
                  background: 'var(--bg-card)',
                  transition: 'box-shadow .2s, border-color .2s, transform .2s',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.boxShadow = 'var(--shadow-lg)';
                  e.currentTarget.style.borderColor = color;
                  e.currentTarget.style.transform = 'translateY(-2px)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.boxShadow = 'none';
                  e.currentTarget.style.borderColor = 'var(--border)';
                  e.currentTarget.style.transform = 'none';
                }}
              >
                <PresentationIcon size={52} color={color} strokeWidth={1.4} />
                <Tooltip title={p.file_name}>
                  <div
                    style={{
                      width: '100%',
                      textAlign: 'center',
                      fontSize: 13,
                      fontWeight: 500,
                      color: 'var(--text-primary)',
                      lineHeight: 1.4,
                      display: '-webkit-box',
                      WebkitLineClamp: 2,
                      WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                      wordBreak: 'break-all',
                    }}
                  >
                    {p.file_name}
                  </div>
                </Tooltip>
                <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                  {String(p.file_format).toUpperCase()} · {formatDate(p.created_at)}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  {previewableTaskIds?.has(p.task_id) && (
                    <Button size="small" icon={<Eye size={13} />} onClick={() => setPreviewTask(p)}>
                      预览
                    </Button>
                  )}
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    icon={<Download size={13} />}
                    href={presentationDownloadUrl(p.presentation_id)}
                    target="_blank"
                  >
                    下载
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      <PagesDrawer task={previewTask} onClose={() => setPreviewTask(null)} />
    </Card>
  );
}
