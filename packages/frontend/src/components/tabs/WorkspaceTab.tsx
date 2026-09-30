/**
 * 工作台 Tab：四个子菜单 + 常驻进度。
 *
 * ① 创建向导：上传/文本 → 草稿任务（一步步引导）
 * ② 草稿任务：当前项目草稿列表 + 版本历史 + AI 编辑 + 从任一草稿去生成
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
  cancelJob,
  retryJob,
  type Draft,
  type EngineInfo,
  type Project as ProjectSummary,
  type JobWithProgress,
  type Presentation,
} from '@/lib/api';
import { isPending } from '@/lib/jobStatus';
import { usePollingJobs } from '@/hooks/usePollingJobs';
import CreateWizard from '@/components/workspace/CreateWizard';
import DraftsPanel, { selectUnfinishedDraftJobs } from '@/components/workspace/DraftsPanel';
import GenerationJobsPanel from '@/components/workspace/GenerationJobsPanel';
import PresentationsPanel from '@/components/workspace/PresentationsPanel';
import RunningTasksDock from '@/components/workspace/RunningTasksDock';
import { useAuth } from '@/components/AuthProvider';
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

  // 身份一变（登录 / 退出 / 访客令牌换发）就重算可见项目集。
  // 为什么必须做：currentProjectId 是本组件 state，原地退出登录不会重挂载、没人清它；
  // 而创建向导提交时带着 `project_id: currentProjectId`，后端归属校验对无权访问的项目
  // 一律 404「项目不存在」（service.jobs._ensure_project）——现象就是用户"怎么点都生成不了"。
  // 判"当前项目还合法吗"的规则只有 refreshProjects 里那一条（不在可见列表就置空），
  // 这里复用，绝不另写第二套清空逻辑。
  const { user, visitorId } = useAuth();
  const identityKey = `${user?.user_id ?? 'anon'}|${visitorId ?? ''}`;
  const firstIdentityRef = useRef(true);
  useEffect(() => {
    if (firstIdentityRef.current) {
      firstIdentityRef.current = false;
      return;  // 挂载那次由上面的初始化 effect 负责，避免同一件事拉两遍
    }
    refreshProjects();
  }, [identityKey, refreshProjects]);

  // currentProjectId 确定后再拉取该项目的草稿和演示文件
  useEffect(() => {
    refreshDrafts();
    refreshPresentations();
  }, [currentProjectId, refreshDrafts, refreshPresentations]);

  // 任务从"未结束"→ 全部结束时刷新草稿/演示文件（新实体落库）。
  // 口径必须是 isPending（queued + running）：只数 running 会在"还剩排队任务"时
  // 就判定全部结束、提前刷一次。
  const activeCountRef = useRef(-1);
  useEffect(() => {
    const active = jobs.filter((j) => isPending(j.status)).length;
    if (activeCountRef.current > 0 && active === 0) {
      refreshDrafts();
      refreshPresentations();
    }
    activeCountRef.current = active;
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

  // 兜底：提交时后端回「项目不存在」（项目被删、或归属已随身份变化而不可见）。
  // 就地清掉选中、切回"提交即新建"，而不是把 404 原文甩给用户——那句话他读不懂，
  // 而且他会对着同一个已失效的项目反复点提交。
  const handleProjectInvalid = useCallback(() => {
    setCurrentProjectId(null);
    refreshProjects();
  }, [refreshProjects]);

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

  const handleCancel = useCallback(
    async (jobId: string) => {
      try {
        await cancelJob(jobId);
        messageApi.success('已取消');
        refreshJobs();
      } catch (e) {
        messageApi.error(e instanceof Error ? e.message : '取消失败');
      }
    },
    [refreshJobs, messageApi]
  );

  const draftEngines = useMemo(() => engines.filter((e) => e.engine_type === 'draft'), [engines]);
  const outputEngines = useMemo(() => engines.filter((e) => e.engine_type === 'output'), [engines]);

  // 常驻进度同口径：只看当前项目**未结束**的任务（排队中 + 生成中），未关联项目时为空
  // （否则停在 A 项目、甚至没选项目时，会被 B 项目的任务打扰）
  const projectRunningJobs = currentProjectId
    ? jobs.filter((j) => isPending(j.status) && j.project_id === currentProjectId)
    : [];
  // 生成任务与草稿/演示文件同口径：只看当前项目；未关联项目时为空
  const projectOutputJobs = currentProjectId
    ? jobs.filter((j) => j.task_type === 'output' && j.project_id === currentProjectId)
    : [];
  // 草稿任务同口径：DraftsPanel 的「未落库草稿任务」（failed/running）直接吃 jobs，
  // 不在这里按项目切就会把其他项目的失败任务显示出来（未关联项目时 drafts 为空、它们却漏出来）
  const projectDraftJobs = currentProjectId
    ? jobs.filter((j) => j.project_id === currentProjectId)
    : [];
  // 「草稿任务」角标 = 已落库草稿数 + 未落库草稿任务数（失败/生成中）。
  // 注意面板正文是按「版本」平铺的，一个草稿多版本时行数会多于草稿数，
  // 角标口径取"条目数"而非"版本行数"（版本数要逐个草稿拉详情，不为角标付这个代价）
  const draftBadgeCount =
    drafts.length + selectUnfinishedDraftJobs(projectDraftJobs, drafts).length;
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
    { key: 'drafts', label: '草稿任务', icon: FileText, badge: draftBadgeCount },
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
          onProjectInvalid={handleProjectInvalid}
        />
      )}
      {activeKey === 'drafts' && (
        <DraftsPanel
          drafts={drafts}
          jobs={projectDraftJobs}
          projects={projects}
          loading={draftsLoading}
          outputEngines={outputEngines}
          onRefresh={refreshDrafts}
          onGenerate={handleOutputSubmitted}
        />
      )}
      {activeKey === 'jobs' && (
        <GenerationJobsPanel jobs={projectOutputJobs} outputEngines={outputEngines} fileNameByTaskId={fileNameByTaskId} onRetry={handleRetry} onCancel={handleCancel} onRefresh={refreshJobs} />
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
        runningJobs={projectRunningJobs}
        projectNames={Object.fromEntries(projects.map((p) => [p.project_id, p.project_name]))}
        onOpenJob={async (jobId) => {
          setActiveKey('jobs');
          window.dispatchEvent(new CustomEvent('decksmith:open-job', { detail: jobId }));
        }}
        // 聚合胶囊（排队中 N 个 / 还有 N 个在生成）不指向某一条任务，只切到列表
        onOpenJobsList={() => setActiveKey('jobs')}
      />
    </div>
  );
}
