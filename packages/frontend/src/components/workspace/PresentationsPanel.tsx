/**
 * 子菜单④：演示文件库——当前项目的成品文件，文件夹式卡片视图（图标 + 文件名）。
 * 逐页预览入口：对引擎声明 page_preview 的成品显示（task_id 复用 /jobs/{id}/pages）。
 * 修改入口：对声明 svg_edit 的成品显示「修改」，打开版本树抽屉（ADR-023 步骤5 · 5b）。
 */
'use client';

import { useEffect, useState } from 'react';
import { Button, Card, Drawer, Empty, Space, Spin, Tag, Tooltip } from 'antd';
import { Download, Eye, Pencil, Presentation as PresentationIcon, RefreshCw } from 'lucide-react';
import {
  presentationDownloadUrl,
  listSubPresentations,
  subPresentationDownloadUrl,
  type Presentation,
  type SubPresentation,
} from '@/lib/api';
import { useJobPages } from '@/hooks/useJobPages';
import PagePreviewGrid from '@/components/features/job/PagePreviewGrid';
import { formatDate } from '@/lib/formatDate';

interface Props {
  presentations: Presentation[];
  loading: boolean;
  /** 产物支持逐页预览的任务 id 集合（父级按引擎 capabilities 派生，引擎无关） */
  previewableTaskIds?: Set<string>;
  /** 产物支持逐页修改的任务 id 集合（父级按 svg_edit 能力位派生，引擎无关） */
  editableTaskIds?: Set<string>;
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

/**
 * 修改抽屉（5b 只读版本树）：列出「原始版本 + 各子版本」并各自可下载。
 * 数据来自 GET /api/presentations/{id}/sub-presentations（扁平带 parent），
 * 前端按 parent_sub_presentation_id 算深度、缩进体现可分叉的版本树。
 * 逐页编辑器（拖拽/对话改、保存）为 5c，接入本抽屉。
 */
function RevisionDrawer({ task, onClose }: { task: Presentation | null; onClose: () => void }) {
  const [subs, setSubs] = useState<SubPresentation[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!task) {
      setSubs([]);
      return;
    }
    let alive = true;
    setLoading(true);
    listSubPresentations(task.presentation_id)
      .then((rows) => {
        if (alive) setSubs(rows);
      })
      .catch(() => {
        if (alive) setSubs([]);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [task]);

  // 按 parent 指针算缩进深度（父缺失/成环兜底为浅层，不炸）
  const byId = new Map(subs.map((s) => [s.sub_presentation_id, s]));
  const depthOf = (s: SubPresentation): number => {
    let d = 0;
    let cur = s.parent_sub_presentation_id;
    const seen = new Set<string>([s.sub_presentation_id]);
    while (cur && byId.has(cur) && !seen.has(cur)) {
      seen.add(cur);
      d += 1;
      cur = byId.get(cur)!.parent_sub_presentation_id;
    }
    return d;
  };

  const rows = [...subs].sort((a, b) => a.created_at.localeCompare(b.created_at));

  return (
    <Drawer
      title={task ? `修改 · ${task.file_name}` : '修改'}
      width={'52%'}
      open={!!task}
      onClose={onClose}
    >
      {task && (
        <Space direction="vertical" style={{ width: '100%' }} size={10}>
          {/* 原始版本（根） */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div>
              <Tag color="blue">原始版本</Tag>
              <Tooltip title={task.file_name}>
                <span style={{ fontWeight: 500 }}>{task.file_name}</span>
              </Tooltip>
              <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 12 }}>
                生成于 {formatDate(task.created_at)}
              </span>
            </div>
            <Button
              size="small"
              icon={<Download size={13} />}
              href={presentationDownloadUrl(task.presentation_id)}
              target="_blank"
            >
              下载
            </Button>
          </div>

          {loading ? (
            <div style={{ textAlign: 'center', padding: 20 }}>
              <Spin size="small" />
            </div>
          ) : rows.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="还没有修改版本。逐页编辑器接入后，这里会列出生成的子版本。"
            />
          ) : (
            rows.map((s) => (
              <div
                key={s.sub_presentation_id}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  paddingLeft: depthOf(s) * 22,
                }}
              >
                <div>
                  <Tag color="green">子版本</Tag>
                  <span>{s.file_name}</span>
                  <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 12 }}>
                    保存于 {formatDate(s.created_at)}
                    {s.parent_sub_presentation_id ? ' · 改自上一版' : ' · 改自原始版本'}
                  </span>
                </div>
                <Button
                  size="small"
                  icon={<Download size={13} />}
                  href={subPresentationDownloadUrl(s.sub_presentation_id)}
                  target="_blank"
                >
                  下载
                </Button>
              </div>
            ))
          )}
        </Space>
      )}
    </Drawer>
  );
}

export default function PresentationsPanel({
  presentations,
  loading,
  previewableTaskIds,
  editableTaskIds,
  onRefresh,
}: Props) {
  const [previewTask, setPreviewTask] = useState<Presentation | null>(null);
  const [revTask, setRevTask] = useState<Presentation | null>(null);
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
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
                  {previewableTaskIds?.has(p.task_id) && (
                    <Button size="small" icon={<Eye size={13} />} onClick={() => setPreviewTask(p)}>
                      预览
                    </Button>
                  )}
                  {editableTaskIds?.has(p.task_id) && (
                    <Button size="small" icon={<Pencil size={13} />} onClick={() => setRevTask(p)}>
                      修改
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
      <RevisionDrawer task={revTask} onClose={() => setRevTask(null)} />
    </Card>
  );
}
