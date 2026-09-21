/**
 * 历史项目 Tab：所有项目的表格总览（编号/名称/草稿数/演示文件数/运行中任务数/时间）。
 * 操作列：「查看」= 页内只读展示草稿任务/生成任务/演示文件库（复用工作台面板，readOnly）；
 *         「操作」= 携带项目跳转工作台，进入编辑模式（展示效果与工作台完全一致）。
 */
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Button,
  Card,
  Empty,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  message,
} from 'antd';
import {
  Eye,
  FolderOpen,
  Pencil,
  RefreshCw,
} from 'lucide-react';
import {
  listEngines,
  listJobs,
  listProjectDrafts,
  listProjectPresentations,
  listProjects,
  type Draft,
  type EngineInfo,
  type JobWithProgress,
  type Presentation,
  type Project as ProjectSummary,
} from '@/lib/api';
import { usePollingJobs } from '@/hooks/usePollingJobs';
import DraftsPanel from '@/components/workspace/DraftsPanel';
import GenerationJobsPanel from '@/components/workspace/GenerationJobsPanel';
import PresentationsPanel from '@/components/workspace/PresentationsPanel';
import { collectPreviewableTaskIds } from '@/components/features/job/PagePreviewGrid';
import { formatDate } from '@/lib/formatDate';

interface Props {
  /** 「操作」：携带项目 ID 跳转工作台进入编辑模式 */
  onOpenInWorkspace?: (projectId: string) => void;
}

export default function HistoryTab({ onOpenInWorkspace }: Props) {
  const [messageApi, contextHolder] = message.useMessage();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [viewProjectId, setViewProjectId] = useState<string | null>(null);

  // ── 「查看」视图数据（只读，复用工作台面板）──
  const { jobs, refresh: refreshJobs } = usePollingJobs();
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [presentations, setPresentations] = useState<Presentation[]>([]);
  const [viewLoading, setViewLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setProjects(await listProjects(100));
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '获取项目列表失败');
    } finally {
      setLoading(false);
    }
  }, [messageApi]);

  // 进入「查看」时拉取该项目的草稿/演示文件/引擎清单
  const loadViewData = useCallback(async (projectId: string) => {
    setViewLoading(true);
    try {
      const [d, pres, eng] = await Promise.all([
        listProjectDrafts(projectId),
        listProjectPresentations(projectId),
        listEngines(),
      ]);
      setDrafts(d);
      setPresentations(pres);
      setEngines(eng);
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '获取项目数据失败');
    } finally {
      setViewLoading(false);
    }
  }, [messageApi]);

  useEffect(() => {
    load();
    listEngines().then(setEngines).catch(() => {});
  }, [load]);

  useEffect(() => {
    if (viewProjectId) loadViewData(viewProjectId);
  }, [viewProjectId, loadViewData]);

  const viewProject = projects.find((p) => p.project_id === viewProjectId);

  const outputEngines = useMemo(() => engines.filter((e) => e.engine_type === 'output'), [engines]);
  const viewOutputJobs = useMemo(
    () =>
      viewProjectId
        ? jobs.filter((j) => j.task_type === 'output' && j.project_id === viewProjectId)
        : [],
    [jobs, viewProjectId]
  );
  const fileNameByTaskId = useMemo(
    () => Object.fromEntries(presentations.map((p) => [p.task_id, p.file_name])),
    [presentations]
  );
  // 逐页预览入口集合：由引擎 capabilities（钩子实现派生）决定，非引擎名硬判
  const previewableTaskIds = useMemo(
    () => collectPreviewableTaskIds(jobs, outputEngines),
    [jobs, outputEngines]
  );

  const columns = [
    {
      title: '项目编号',
      dataIndex: 'project_id',
      width: 110,
      fixed: 'left' as const,
      render: (id: string) => (
        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{id.slice(0, 8)}</span>
      ),
    },
    { title: '项目名称', dataIndex: 'project_name', ellipsis: true },
    { title: '草稿数量', dataIndex: 'draft_count', width: 90, align: 'center' as const },
    { title: '演示文件数量', dataIndex: 'presentation_count', width: 110, align: 'center' as const },
    {
      title: '运行中任务',
      dataIndex: 'running_task_count',
      width: 100,
      align: 'center' as const,
      render: (n: number) =>
        n > 0 ? <Tag color="gold">{n} 个运行中</Tag> : <span style={{ color: 'var(--text-muted)' }}>0</span>,
    },
    { title: '创建时间', dataIndex: 'created_at', width: 150, render: (t: string) => formatDate(t) },
    { title: '更新时间', dataIndex: 'updated_at', width: 150, render: (t: string) => formatDate(t) },
    {
      title: '操作',
      key: 'actions',
      // 内容实测 138（两个 small 按钮 + Space 间距 8）+ 单元格左右 padding 8×2 = 154，
      // table-layout: fixed 不会为内容撑列，写窄了按钮就会画到格子外面 → 留 6px 余量取 160
      width: 160,
      fixed: 'right' as const,
      render: (_: unknown, p: ProjectSummary) => (
        <Space>
          <Button
            size="small"
            icon={<Eye size={13} />}
            onClick={() => setViewProjectId(p.project_id)}
          >
            查看
          </Button>
          <Button
            size="small"
            type="primary"
            ghost
            icon={<Pencil size={13} />}
            onClick={() => onOpenInWorkspace?.(p.project_id)}
          >
            操作
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      {contextHolder}
      <Card
        title="历史项目"
        extra={
          <Button size="small" icon={<RefreshCw size={13} />} onClick={load} loading={loading}>
            刷新
          </Button>
        }
      >
        <Table
          rowKey="project_id"
          size="small"
          loading={loading}
          dataSource={projects}
          columns={columns}
          pagination={false}
          // 固定列（项目编号左 / 操作右）依赖 scroll.x；窄窗口时在卡片内横向滚动，
          // 而不是让整张表冲出卡片边界
          scroll={{ x: 1000 }}
          locale={{ emptyText: <Empty description="暂无项目，提交草稿任务后自动创建" /> }}
          onRow={(p) => ({
            // 高亮走 class 而不是 tr 内联样式：固定列（项目编号/操作）单元格有自己不透明背景，
            // 内联在 tr 上的底色会被这两格盖出接缝
            className: viewProjectId === p.project_id ? 'row-active' : '',
          })}
        />
      </Card>

      {/* 查看：只读复用工作台面板 */}
      {viewProjectId && (
        <Card
          style={{ marginTop: 16 }}
          title={
            <Space>
              <FolderOpen size={15} color="#8b5cf6" />
              <span>
                {viewProject?.project_name || viewProjectId.slice(0, 8)}
                <Tag color="default" style={{ marginLeft: 8 }}>
                  只读
                </Tag>
              </span>
            </Space>
          }
          extra={
            <Space>
              <Button
                size="small"
                type="primary"
                ghost
                icon={<Pencil size={13} />}
                onClick={() => onOpenInWorkspace?.(viewProjectId)}
              >
                去编辑
              </Button>
              <Button size="small" onClick={() => setViewProjectId(null)}>
                关闭
              </Button>
            </Space>
          }
        >
          {viewLoading ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin />
            </div>
          ) : (
            <Tabs
              defaultActiveKey="drafts"
              items={[
                {
                  key: 'drafts',
                  label: '草稿任务',
                  children: (
                    <DraftsPanel
                      drafts={drafts}
                      jobs={jobs}
                      projects={projects}
                      loading={viewLoading}
                      outputEngines={outputEngines}
                      readOnly
                      onRefresh={() => loadViewData(viewProjectId)}
                      onGenerate={() => {}}
                    />
                  ),
                },
                {
                  key: 'jobs',
                  label: '生成任务',
                  children: (
                    <GenerationJobsPanel
                      jobs={viewOutputJobs}
                      outputEngines={outputEngines}
                      fileNameByTaskId={fileNameByTaskId}
                      readOnly
                      onRetry={() => {}}
                      onRefresh={() => {
                        refreshJobs();
                        loadViewData(viewProjectId);
                      }}
                    />
                  ),
                },
                {
                  key: 'presentations',
                  label: '演示文件库',
                  children: (
                    <PresentationsPanel
                      presentations={presentations}
                      loading={viewLoading}
                      previewableTaskIds={previewableTaskIds}
                      onRefresh={() => loadViewData(viewProjectId)}
                    />
                  ),
                },
              ]}
            />
          )}
        </Card>
      )}
    </div>
  );
}
