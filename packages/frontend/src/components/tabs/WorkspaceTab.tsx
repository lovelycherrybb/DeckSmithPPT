/**
 * 工作台 Tab：四个子菜单 + 常驻进度。
 *
 * ① 创建向导：上传/文本 → 草稿任务（一步步引导）
 * ② 草稿任务：全项目草稿列表 + 版本历史 + AI 编辑 + 从任一草稿去生成
 * ③ 生成任务：输出任务列表 + 轮询 + 重试 + 下载 + 详情
 * ④ 演示文件库：Presentation 列表 + 下载
 *
 * 数据流：引擎/任务/草稿由本 Tab 统一持有，子菜单间联动（①完成跳②，②提交生成跳③）。
 */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { message } from 'antd';
import { FileText, FolderOpen, Rocket, Wand2 } from 'lucide-react';
import {
  getJob,
  listEngines,
  listJobs,
  listProjectDrafts,
  listProjectPresentations,
  listProjects,
  retryJob,
  type Draft,
  type EngineInfo,
  type Project as ProjectSummary,
  type JobWithProgress,
  type Presentation,
} from '@/lib/api';
import { usePollingJobs } from '@/hooks/usePollingJobs';
import CreateWizard from '@/components/workspace/CreateWizard';
import DraftsPanel from '@/components/workspace/DraftsPanel';
import GenerationJobsPanel from '@/components/workspace/GenerationJobsPanel';
import PresentationsPanel from '@/components/workspace/PresentationsPanel';
import RunningTasksDock from '@/components/workspace/RunningTasksDock';
import { collectPreviewableTaskIds } from '@/components/features/job/PagePreviewGrid';

interface Props {
  /** 从历史项目「操作」跳入时携带的项目 ID */
  initialProjectId?: string | null;
}

export default function WorkspaceTab({ initialProjectId = null }: Props) {
  const [messageApi, contextHolder] = message.useMessage();
  const [activeKey, setActiveKey] = useState('create');

  // 全局数据（子菜单共享）
  const [engines, setEngines] = useState<EngineInfo[]>([]);
  const { jobs, refresh: refreshJobs } = usePollingJobs();

  // 项目（聚合根）：由首次草稿任务创建；工作台内沿用当前项目
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(initialProjectId);

  const refreshProjects = useCallback(async () => {
    try {
      const projs = await listProjects(50);
      setProjects(projs);
      // 项目由首次草稿任务创建；刷新/首次进入保持无选中，提交草稿后才采纳新项目
      setCurrentProjectId((cur) =>
        cur && projs.some((p) => p.project_id === cur) ? cur : null
      );
      return projs;
    } catch {
      return [];
    }
  }, []);

  // 草稿数据（②持有，任务完成后刷新；仅当前项目）
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftsLoading, setDraftsLoading] = useState(false);

  // 草稿任务 = 当前项目下的草稿；无项目即为空（项目由首次草稿任务创建）
  const refreshDrafts = useCallback(async () => {
    if (!currentProjectId) {
      setDrafts([]);
      return;
    }
    setDraftsLoading(true);
    try {
      const list = await listProjectDrafts(currentProjectId);
      setDrafts([...list].sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || '')));
    } catch {
      setDrafts([]);
    } finally {
      setDraftsLoading(false);
    }
  }, [currentProjectId]);

  // 演示文件（④持有；仅当前项目）
  const [presentations, setPresentations] = useState<Presentation[]>([]);
  const [presentationsLoading, setPresentationsLoading] = useState(false);

  const refreshPresentations = useCallback(async () => {
    if (!currentProjectId) {
      setPresentations([]);
      return;
    }
    setPresentationsLoading(true);
    try {
      const list = await listProjectPresentations(currentProjectId);
      setPresentations([...list].sort((a, b) => (b.created_at || '').localeCompare(a.created_at || '')));
    } catch {
      setPresentations([]);
    } finally {
      setPresentationsLoading(false);
    }
  }, [currentProjectId]);

  useEffect(() => {
    listEngines().then(setEngines).catch(() => messageApi.error('获取引擎列表失败'));
    refreshProjects().then(() => refreshDrafts());
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // currentProjectId 确定后再拉取该项目的草稿和演示文件
  useEffect(() => {
    refreshDrafts();
    refreshPresentations();
  }, [currentProjectId, refreshDrafts, refreshPresentations]);

  // 任务从 running → 全部结束时刷新草稿/演示文件（新实体落库）
  const runningCountRef = useRef(-1);
  useEffect(() => {
    const running = jobs.filter((j) => j.status === 'running').length;
    if (runningCountRef.current > 0 && running === 0) {
      refreshDrafts();
      refreshPresentations();
    }
    runningCountRef.current = running;
  }, [jobs, refreshDrafts, refreshPresentations]);

  // ── 跨子菜单动作 ──
  // ①创建完成 → 切到②草稿任务
  const handleDraftJobSubmitted = useCallback(() => {
    setActiveKey('drafts');
    // 提交后刷新项目；若刚才是「新建项目」（currentProjectId 为 null），采纳最新项目为当前项目
    refreshProjects().then((projs) => {
      if (!currentProjectId && projs.length > 0) {
        setCurrentProjectId(projs[0].project_id);
      }
    });
    refreshDrafts();
    refreshJobs();
  }, [refreshProjects, refreshDrafts, refreshJobs, currentProjectId]);

  // ②「去生成」提交成功 → 切到③生成任务
  const handleOutputSubmitted = useCallback(
    (job: JobWithProgress) => {
      setActiveKey('jobs');
      refreshJobs();
      messageApi.success(`输出任务已提交：${job.job_id.slice(0, 8)}`);
    },
    [refreshJobs, messageApi]
  );

  const handleRetry = useCallback(
    async (jobId: string) => {
      try {
        const job = await retryJob(jobId);
        messageApi.success(`重试任务已创建：${job.job_id.slice(0, 8)}`);
        refreshJobs();
      } catch (e) {
        messageApi.error(e instanceof Error ? e.message : '重试失败');
      }
    },
    [refreshJobs, messageApi]
  );

  const draftEngines = useMemo(() => engines.filter((e) => e.engine_type === 'draft'), [engines]);
  const outputEngines = useMemo(() => engines.filter((e) => e.engine_type === 'output'), [engines]);

  const runningJobs = jobs.filter((j) => j.status === 'running');
  // 生成任务与草稿/演示文件同口径：只看当前项目；未关联项目时为空
  const projectOutputJobs = currentProjectId
    ? jobs.filter((j) => j.task_type === 'output' && j.project_id === currentProjectId)
    : [];
  const outputJobsCount = projectOutputJobs.length;
  // 生成任务标题展示用：job_id → 演示文件名（任务完成后落库 Presentation 提供）
  const fileNameByTaskId = useMemo(
    () => Object.fromEntries(presentations.map((p2) => [p2.task_id, p2.file_name])),
    [presentations]
  );
  // 逐页预览入口集合：由 GET /api/engines 的 capabilities（引擎钩子实现派生）决定，非引擎名硬判
  const previewableTaskIds = useMemo(
    () => collectPreviewableTaskIds(jobs, outputEngines),
    [jobs, outputEngines]
  );
  const currentProject = projects.find((p) => p.project_id === currentProjectId);

  const subtabs = [
    { key: 'create', label: '创建向导', icon: Wand2, badge: 0 },
    { key: 'drafts', label: '草稿任务', icon: FileText, badge: drafts.length },
    { key: 'jobs', label: '生成任务', icon: Rocket, badge: outputJobsCount },
    { key: 'presentations', label: '演示文件库', icon: FolderOpen, badge: presentations.length },
  ];

  return (
    <div>
      {contextHolder}

      {/* 子标签：居中胶囊容器（对齐设计稿 .sub-tabs） */}
      <div className="subtabs">
        {subtabs.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              className={`subtab${activeKey === t.key ? ' active' : ''}`}
              onClick={() => setActiveKey(t.key)}
            >
              <Icon size={15} />
              {t.label}
              {t.badge > 0 && <span className="subtab-badge">{t.badge}</span>}
            </button>
          );
        })}
      </div>

      {/* 当前项目信息行（对齐设计稿 .project-info） */}
      <div className="project-info">
        当前项目：
        {currentProject ? (
          <>
            <span className="name">{currentProject.project_name}</span>
            <span className="id">{currentProject.project_id.slice(0, 8)}</span>
          </>
        ) : (
          <span style={{ color: 'var(--text-muted)' }}>未关联（提交时自动创建）</span>
        )}
      </div>

      {activeKey === 'create' && (
        <CreateWizard
          draftEngines={draftEngines}
          currentProjectId={currentProjectId}
          onSubmitted={handleDraftJobSubmitted}
        />
      )}
      {activeKey === 'drafts' && (
        <DraftsPanel
          drafts={drafts}
          jobs={jobs}
          projects={projects}
          loading={draftsLoading}
          outputEngines={outputEngines}
          onRefresh={refreshDrafts}
          onGenerate={handleOutputSubmitted}
        />
      )}
      {activeKey === 'jobs' && (
        <GenerationJobsPanel jobs={projectOutputJobs} outputEngines={outputEngines} fileNameByTaskId={fileNameByTaskId} onRetry={handleRetry} onRefresh={refreshJobs} />
      )}
      {activeKey === 'presentations' && (
        <PresentationsPanel
          presentations={presentations}
          loading={presentationsLoading}
          previewableTaskIds={previewableTaskIds}
          onRefresh={refreshPresentations}
        />
      )}

      {/* 常驻进度：运行中任务锚点（跨子菜单可见） */}
      <RunningTasksDock
        runningJobs={runningJobs}
        projectNames={Object.fromEntries(projects.map((p) => [p.project_id, p.project_name]))}
        onOpenJob={async (jobId) => {
          setActiveKey('jobs');
          window.dispatchEvent(new CustomEvent('decksmith:open-job', { detail: jobId }));
        }}
      />
    </div>
  );
}
