/**
 * 子菜单③：生成任务——输出任务列表（轮询自动刷新）+ 重试 + 取消 + 下载 + 详情抽屉。
 * 监听 decksmith:open-job 事件（常驻进度浮窗/外部跳转打开详情）。
 *
 * 状态判据一律走 lib/jobStatus 的具名函数：引入 queued 之后，
 * `!== 'running'` 不再等于"已结束"，散着写必然出错。
 */
'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  List,
  Popconfirm,
  Segmented,
  Space,
  Tag,
} from 'antd';
import { AlertCircle, Download, Eye, RefreshCw, RotateCcw } from 'lucide-react';
import {
  getJob,
  jobDownloadUrl,
  type EngineInfo,
  type JobStatus,
  type JobWithProgress,
} from '@/lib/api';
import { StatusTag, contentStyleLabel } from '@/components/ui/StatusTag';
import {
  engineCancellable,
  isPending,
  isRetryable,
  isTerminal,
  queueDetailLabel,
} from '@/lib/jobStatus';
import ProgressDisplay from '@/components/features/job/ProgressDisplay';
import PagePreviewGrid from '@/components/features/job/PagePreviewGrid';
import { useJobPages } from '@/hooks/useJobPages';
import { formatDate } from '@/lib/formatDate';
import { engineParamEntries } from '@/lib/engineParams';
import { tokenUsagePartRows, tokenUsageSummary, tokenUsageTotal } from '@/lib/tokenUsage';

interface Props {
  jobs: JobWithProgress[];
  /** 输出引擎清单（含 params_schema 与 capabilities），用于按引擎渲染参数与取消可见性 */
  outputEngines: EngineInfo[];
  /** 生成任务 job_id → 演示文件名（任务完成后由 Presentation 提供） */
  fileNameByTaskId?: Record<string, string>;
  /** 只读模式（历史项目「查看」）：隐藏重试/取消等变更操作，保留下载/详情 */
  readOnly?: boolean;
  onRetry: (jobId: string) => void;
  onCancel: (jobId: string) => void;
  onRefresh: () => void;
}

/** 页签四档：「进行中」= 排队中 + 生成中（未结束的都算一个池子，见 lib/jobStatus） */
type FilterKey = 'all' | 'active' | 'completed' | 'failed';

export default function GenerationJobsPanel({
  jobs,
  outputEngines,
  fileNameByTaskId = {},
  readOnly = false,
  onRetry,
  onCancel,
  onRefresh,
}: Props) {
  const [filter, setFilter] = useState<FilterKey>('all');
  const [drawerJob, setDrawerJob] = useState<JobWithProgress | null>(null);

  // 只有声明了 page_preview 能力的引擎才有「逐页产物」可数；单文件 HTML 引擎
  // 无逐页落盘，渲染「0 页」会被读成卡死。按 capabilities 派生，不按引擎名硬编码。
  const pagePreviewEngines = useMemo(
    () =>
      new Set(
        outputEngines
          .filter((e) => (e.capabilities || []).includes('page_preview'))
          .map((e) => e.name),
      ),
    [outputEngines],
  );

  // 能中途停止生成的引擎（能力位派生，与 page_preview 同一手法）。
  // 草稿类引擎显式退出了 cancel 钩子 → 它们的任务在"生成中"阶段没有任何取消入口，
  // 只能等跑完。这是契约决定的（不支持就隐藏按钮，而不是点了报 409）。
  const cancellableEngines = useMemo(
    () => new Set(outputEngines.filter((e) => engineCancellable(e.capabilities)).map((e) => e.name)),
    [outputEngines],
  );

  // 任务标题优先用生成的演示文件名（项目名_草稿版本号.pptx），未完成时退回任务标题
  const jobTitle = (job: JobWithProgress) =>
    fileNameByTaskId[job.job_id] || job.title || '未命名';

  // 抽屉内容跟随父级任务列表轮询刷新（running 时 2s 一拉），不再是打开时的静态快照；
  // 列表里暂时查不到该任务时退回打开时的快照，避免抽屉闪空
  const drawer = drawerJob
    ? jobs.find((j) => j.job_id === drawerJob.job_id) ?? drawerJob
    : null;

  // 终态（completed/failed/timeout/cancelled）抽屉的成品页墙；running 的页墙由
  // ProgressDisplay 内部按需拉，不双拉同一任务；queued 不拉（还没有产物可列）
  const { data: drawerPages } = useJobPages(
    drawer && isTerminal(drawer.status) ? drawer.job_id : null,
    drawer?.status
  );

  // 外部请求打开任务详情（浮窗/URL 跳转）
  useEffect(() => {
    const handler = async (e: Event) => {
      const jobId = (e as CustomEvent<string>).detail;
      try {
        const job = await getJob(jobId);
        setDrawerJob(job);
      } catch {
        // 任务可能不存在
      }
    };
    window.addEventListener('decksmith:open-job', handler);
    return () => window.removeEventListener('decksmith:open-job', handler);
  }, []);

  const outputJobs = jobs.filter((j) => j.task_type === 'output');
  const filtered = outputJobs.filter((j) => {
    if (filter === 'all') return true;
    if (filter === 'active') return isPending(j.status);
    if (filter === 'failed') return j.status === 'failed' || j.status === 'timeout';
    return j.status === (filter as JobStatus);
  });

  // 取消按钮：排队中随时可取消（还没开始、没有成本，不弹确认）；
  // 生成中要引擎支持中断才给入口
  const cancelLabel = (job: JobWithProgress) =>
    job.status === 'queued' ? '取消排队' : '停止生成';
  const canCancel = (job: JobWithProgress) =>
    !readOnly &&
    isPending(job.status) &&
    (job.status === 'queued' || cancellableEngines.has(job.engine || ''));

  const openDetail = async (jobId: string) => {
    try {
      const job = await getJob(jobId);
      setDrawerJob(job);
    } catch {
      // ignore
    }
  };

  return (
    <Card
      title="生成任务"
      extra={
        <Space>
          <Segmented
            options={[
              { label: '全部', value: 'all' },
              { label: '进行中', value: 'active' },
              { label: '已完成', value: 'completed' },
              { label: '失败', value: 'failed' },
            ]}
            value={filter}
            onChange={(v) => setFilter(v as FilterKey)}
          />
          <Button size="small" icon={<RefreshCw size={13} />} onClick={onRefresh}>
            刷新
          </Button>
        </Space>
      }
    >
      {filtered.length === 0 ? (
        <Empty description={outputJobs.length === 0 ? '暂无生成任务，从「草稿任务」去生成' : '没有符合条件的任务'} />
      ) : (
        <List
          dataSource={filtered}
          renderItem={(job) => (
            <List.Item
              actions={[
                ...(canCancel(job)
                  ? job.status === 'queued'
                    ? [
                        <Button key="cancel" size="small" onClick={() => onCancel(job.job_id)}>
                          {cancelLabel(job)}
                        </Button>,
                      ]
                    : [
                        // 已经在生成了：停下来会丢掉已完成的部分、已消耗的额度也不退，
                        // 所以必须二次确认，且把代价写在确认里
                        <Popconfirm
                          key="cancel"
                          title="停止这次生成？"
                          description="停止后本次生成不会保存已完成的部分，已消耗的额度也不退回。"
                          okText="停止生成"
                          cancelText="继续等"
                          onConfirm={() => onCancel(job.job_id)}
                        >
                          <Button size="small">{cancelLabel(job)}</Button>
                        </Popconfirm>,
                      ]
                  : []),
                ...(!readOnly && isRetryable(job.status)
                  ? [
                      <Button key="retry" size="small" icon={<RotateCcw size={13} />} onClick={() => onRetry(job.job_id)}>
                        重试
                      </Button>,
                    ]
                  : []),
                ...(job.status === 'completed'
                  ? [
                      <Button
                        key="dl"
                        size="small"
                        type="primary"
                        icon={<Download size={13} />}
                        href={jobDownloadUrl(job.job_id)}
                        target="_blank"
                      >
                        下载
                      </Button>,
                    ]
                  : []),
                <Button key="detail" size="small" icon={<Eye size={13} />} onClick={() => openDetail(job.job_id)}>
                  详情
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    <span>{jobTitle(job)}</span>
                    <StatusTag status={job.status} />
                    {/* 排队中：只报位置，不报页数/耗时——此刻还没有任何产出，显示 0 会被读成卡死 */}
                    {job.status === 'queued' && (
                      <Tag style={{ fontSize: 12 }}>{queueDetailLabel(job.status_detail, job.queue_position)}</Tag>
                    )}
                    {job.status === 'running' && (
                      <Tag color="gold" style={{ fontSize: 12 }}>
                        {pagePreviewEngines.has(job.engine || '') &&
                          `${job.progress?.pages_generated ?? 0} 页 · `}
                        {job.progress?.elapsed_seconds != null
                          ? `${Math.floor((job.progress.elapsed_seconds || 0) / 60)}分${(job.progress.elapsed_seconds || 0) % 60}秒`
                          : '执行中'}
                      </Tag>
                    )}
                  </Space>
                }
                description={
                  <>
                    {`${job.engine || '-'} · ${contentStyleLabel(job.content_style)} · ${formatDate(job.created_at)}`}
                    {/* 只有终态才显示消耗：用量要到结束才落库，「未记录」对跑完仍无数据的
                        任务才有信息量；排队中显示它会让人以为已经花过钱 */}
                    {isTerminal(job.status) && ` · ${tokenUsageSummary(job.token_usage)}`}
                  </>
                }
              />
            </List.Item>
          )}
        />
      )}

      {/* 任务详情抽屉 */}
      <Drawer
        title={drawer ? jobTitle(drawer) : '任务详情'}
        width={'70%'}
        open={!!drawer}
        onClose={() => setDrawerJob(null)}
        extra={
          <Space>
            {drawer && canCancel(drawer) && (
              drawer.status === 'queued' ? (
                <Button size="small" onClick={() => onCancel(drawer.job_id)}>
                  {cancelLabel(drawer)}
                </Button>
              ) : (
                <Popconfirm
                  title="停止这次生成？"
                  description="停止后本次生成不会保存已完成的部分，已消耗的额度也不退回。"
                  okText="停止生成"
                  cancelText="继续等"
                  onConfirm={() => onCancel(drawer.job_id)}
                >
                  <Button size="small">{cancelLabel(drawer)}</Button>
                </Popconfirm>
              )
            )}
            {drawer && isRetryable(drawer.status) && (
              <Button size="small" onClick={() => onRetry(drawer.job_id)}>
                重试
              </Button>
            )}
            {drawer?.status === 'completed' && (
              <Button
                size="small"
                type="primary"
                icon={<Download size={14} />}
                href={jobDownloadUrl(drawer.job_id)}
                target="_blank"
              >
                下载
              </Button>
            )}
          </Space>
        }
      >
        {drawer && (
          <>
            <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
              <Descriptions.Item label="任务 ID">
                <span style={{ fontFamily: 'monospace' }}>{drawer.job_id}</span>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusTag status={drawer.status} />
              </Descriptions.Item>
              <Descriptions.Item label="引擎">{drawer.engine || '-'}</Descriptions.Item>
              <Descriptions.Item label="创建时间">{formatDate(drawer.created_at)}</Descriptions.Item>
              {isTerminal(drawer.status) && (
                <Descriptions.Item label="消耗">
                  {tokenUsageTotal(drawer.token_usage)}
                </Descriptions.Item>
              )}
              {isTerminal(drawer.status) &&
                tokenUsagePartRows(drawer.token_usage).map((p) => (
                  <Descriptions.Item key={p.key} label={p.label}>
                    {p.value}
                  </Descriptions.Item>
                ))}
              {drawer.status_detail && (
                <Descriptions.Item label="状态明细">{drawer.status_detail}</Descriptions.Item>
              )}
            </Descriptions>
            {(() => {
              const engine = outputEngines.find((e) => e.name === drawer.engine);
              const entries = engineParamEntries(engine, drawer.engine_params);
              return (
                <>
                  <Divider orientation="left" plain style={{ margin: '8px 0 12px' }}>
                    生成参数（{engine?.display_name || drawer.engine || '-'}）
                  </Divider>
                  <Descriptions column={2} size="small" style={{ marginBottom: 16 }}>
                    {drawer.pages != null && (
                      <Descriptions.Item label="目标页数">{drawer.pages} 页</Descriptions.Item>
                    )}
                    {entries.map((e) => (
                      <Descriptions.Item key={e.key} label={e.label}>
                        {e.value}
                      </Descriptions.Item>
                    ))}
                  </Descriptions>
                </>
              );
            })()}
            {drawer.status === 'queued' && (
              // 排队中不画进度、也不给"已生成 0 页"——那会被读成坏掉了。
              // 位置信息不在这里重复一遍：上方「状态明细」已经带了后端现算的那句完整话，
              // 这里再拼一次就成了同一事实的两个作者。
              <span style={{ fontSize: 13, color: 'rgba(0,0,0,0.45)' }}>
                任务在排队，尚未开始生成。
              </span>
            )}
            {drawer.status === 'running' && <ProgressDisplay progress={drawer.progress} jobId={drawer.job_id} />}
            {drawerPages && drawerPages.supports_page_preview && (
              <div style={{ marginTop: 16 }}>
                <PagePreviewGrid data={drawerPages} />
              </div>
            )}
            {drawer.error && (
              <Alert
                type={drawer.status === 'cancelled' ? 'warning' : 'error'}
                showIcon
                icon={<AlertCircle size={16} />}
                message={drawer.status === 'cancelled' ? '任务已取消' : '任务失败'}
                description={drawer.error}
                style={{ marginTop: 16 }}
              />
            )}
          </>
        )}
      </Drawer>
    </Card>
  );
}
