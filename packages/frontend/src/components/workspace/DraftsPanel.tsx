/**
 * 子菜单②：草稿任务——所有草稿版本平铺展示。
 * 每个版本一行：主标题「2026年9月9日 第N版」（N=当天第几个版本，最早为 1）；
 * 副信息：任务 ID、版本号；操作：查看 / 编辑 / 生成演示文件。
 * 未落库的草稿任务（草稿实体只在 completed 时写入，失败则永远不会落库）
 * 以置顶行展示：running=「生成中」，failed/timeout=错误态+重试。
 */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  message,
  Modal,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
} from 'antd';
import {
  Bot,
  Eye,
  FileText,
  RefreshCw,
  Rocket,
  RotateCcw,
  Save,
  Undo2,
} from 'lucide-react';
import {
  ApiError,
  getDraft,
  getDraftVersion,
  modifyDraft,
  modifyDraftStream,
  restoreDraftVersion,
  retryJob,
  saveDraftVersion,
  submitDeck,
  type DeckRequest,
  type Draft,
  type DraftDetail,
  type EngineInfo,
  type JobWithProgress,
  type Project,
} from '@/lib/api';
import { StatusTag } from '@/components/ui/StatusTag';
import MarkdownView from '@/components/ui/MarkdownView';
import MdEditor from '@/components/ui/MdEditor';
import { formatDate } from '@/lib/formatDate';
import { paramLabel, paramValueLabel, paramDependency, BASE_DUP_PARAM_KEYS } from '@/lib/engineParams';


interface Props {
  drafts: Draft[];
  jobs: JobWithProgress[];
  projects: Project[];
  loading: boolean;
  outputEngines: EngineInfo[];
  /** 只读模式（历史项目「查看」）：隐藏编辑/生成等变更操作，仅保留查看 */
  readOnly?: boolean;
  onRefresh: () => void;
  onGenerate: (job: JobWithProgress) => void;
}

/** 平铺行：草稿的某个版本 */
interface FlatVersionRow {
  draft: Draft;
  version: string;
  rank: number | null;
  day: string | null;
  label: string; // 「2026年9月9日 第N版」
  content: string;
  createdAt?: string;
  isCurrent: boolean;
}

// 未落库的草稿任务（草稿实体只在 completed 时写入）：running=生成中；
// failed/timeout 若被过滤掉就会「凭空消失」，必须保留展示供用户重试。
// 提取成导出函数是为了让工作台的「草稿任务」badge 与面板列表数同一件事——
// 面板平铺了它们而角标只数已落库草稿，就会出现「角标 0、点进去两条」的落差。
export function selectUnfinishedDraftJobs(
  jobs: JobWithProgress[],
  drafts: Draft[]
): JobWithProgress[] {
  return jobs.filter(
    (j) =>
      j.task_type === 'draft' &&
      ['running', 'failed', 'timeout'].includes(j.status) &&
      !drafts.some((d) => d.task_id === j.job_id)
  );
}

// ── 版本号工具：14 位时间戳 ──
function versionDay(v: string): string | null {
  if (!/^\d{14}$/.test(v)) return null;
  return `${v.slice(0, 4)}年${parseInt(v.slice(4, 6), 10)}月${parseInt(v.slice(6, 8), 10)}日`;
}

export default function DraftsPanel({
  drafts,
  jobs,
  projects,
  loading,
  outputEngines,
  readOnly = false,
  onRefresh,
  onGenerate,
}: Props) {
  const [messageApi, contextHolder] = message.useMessage();

  // ── 编辑抽屉 ──
  const [editing, setEditing] = useState<{ draft: Draft; version: string } | null>(null);
  const [editDetail, setEditDetail] = useState<DraftDetail | null>(null);
  const [editLoading, setEditLoading] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [aiPrompt, setAiPrompt] = useState('');
  const [aiBusy, setAiBusy] = useState(false);
  const [aiStream, setAiStream] = useState(''); // AI 修改的流式输出（实时展示）
  const [aiThinking, setAiThinking] = useState(''); // AI 思考过程（流式展示）
  const streamRef = useRef<HTMLPreElement>(null);
  const [saving, setSaving] = useState(false);

  // 流式输出自动滚到底部
  useEffect(() => {
    if (streamRef.current) streamRef.current.scrollTop = streamRef.current.scrollHeight;
  }, [aiStream, aiThinking]);

  // ── 查看（只读） ──
  const [viewing, setViewing] = useState<FlatVersionRow | null>(null);

  // ── 去生成弹窗（指定版本） ──
  const [generateTarget, setGenerateTarget] = useState<{ draft: Draft; version: string; label: string } | null>(null);
  const [genForm] = Form.useForm();
  const [genSubmitting, setGenSubmitting] = useState(false);

  // ── 数据辅助 ──
  const projectNameMap = useMemo(
    () => new Map(projects.map((p) => [p.project_id, p.project_name])),
    [projects]
  );
  const jobByTaskId = useMemo(() => new Map(jobs.map((j) => [j.job_id, j])), [jobs]);

  // 未落库的草稿任务：过滤条件与工作台 badge 共用 selectUnfinishedDraftJobs
  const unfinishedJobs = useMemo(
    () => selectUnfinishedDraftJobs(jobs, drafts),
    [jobs, drafts]
  );

  // 版本详情缓存：平铺需要每个草稿的版本列表
  const [detailCache, setDetailCache] = useState<Record<string, DraftDetail | undefined>>({});
  useEffect(() => {
    const missing = drafts.filter((d) => detailCache[d.draft_id] === undefined);
    if (missing.length === 0) return;
    let cancelled = false;
    (async () => {
      const results = await Promise.all(
        missing.map((d) => getDraft(d.draft_id).catch(() => undefined))
      );
      if (cancelled) return;
      setDetailCache((prev) => {
        const next = { ...prev };
        missing.forEach((d, i) => {
          next[d.draft_id] = results[i];
        });
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [drafts]); // eslint-disable-line react-hooks/exhaustive-deps

  // 平铺行：草稿的每个版本一行；第N版按「项目内全局顺延」——
  // 合并项目下所有草稿的版本按时间排序统一编号（新草稿接在已有版本之后，不会重复出现第1版）
  const flatRows = useMemo<FlatVersionRow[]>(() => {
    const rows: FlatVersionRow[] = [];
    const all: Array<{ draft: Draft; version: string; content: string; createdAt?: string }> = [];
    for (const d of drafts) {
      const detail = detailCache[d.draft_id];
      const versions = detail?.versions || [];
      if (versions.length === 0) {
        // 详情未加载：退化为单行占位（不编号）
        const cur = d.current_version || '';
        const day = versionDay(cur);
        rows.push({
          draft: d,
          version: cur,
          rank: null,
          day,
          label: day || `草稿 ${d.draft_id.slice(0, 8)}`,
          content: '',
          isCurrent: false, // 统一在排序后只标全局最新一版
        });
        continue;
      }
      for (const v of versions) {
        all.push({ draft: d, version: v.version, content: v.content || '', createdAt: v.created_at });
      }
    }
    // 全项目按版本时间升序统一编号
    all.sort((a, b) => a.version.localeCompare(b.version));
    all.forEach((item, idx) => {
      const day = versionDay(item.version);
      rows.push({
        draft: item.draft,
        version: item.version,
        rank: idx + 1,
        day,
        label: day ? `${day} 第${idx + 1}版` : `版本 ${item.version}`,
        content: item.content,
        createdAt: item.createdAt,
        isCurrent: false, // 统一在排序后只标全局最新一版
      });
    });
    rows.sort((a, b) => b.version.localeCompare(a.version));
    // 「当前版本」全列表唯一：时间戳最新的那一版（跨任务编号也只标一个）
    if (rows.length > 0) rows[0].isCurrent = true;
    return rows;
  }, [drafts, detailCache]);

  // ── 操作 ──
  /** 编辑：加载指定版本内容（保存时总是产生新版本） */
  const openEdit = useCallback(
    async (row: FlatVersionRow) => {
      setEditing({ draft: row.draft, version: row.version });
      setEditLoading(true);
      setDraftText('');
      setAiPrompt('');
      try {
        const detail = await getDraft(row.draft.draft_id);
        setEditDetail(detail);
        // 编辑的是所选版本：取该版本内容（缓存里有就直接用）
        const cached = detail.versions?.find((v) => v.version === row.version);
        const content =
          cached?.content ??
          (await getDraftVersion(row.draft.draft_id, row.version).then((v) => v.content).catch(() => ''));
        setDraftText(content || '');
      } catch (e) {
        messageApi.error(e instanceof Error ? e.message : '加载草稿失败');
        setEditing(null);
      } finally {
        setEditLoading(false);
      }
    },
    [messageApi]
  );

  const refreshEditDetail = useCallback(async () => {
    if (!editing) return;
    const detail = await getDraft(editing.draft.draft_id);
    setEditDetail(detail);
    setEditing({ draft: editing.draft, version: detail.current_version });
    // 同步平铺列表的版本缓存（否则新保存的版本不会出现在列表）
    setDetailCache((prev) => ({ ...prev, [editing.draft.draft_id]: detail }));
    onRefresh();
  }, [editing, onRefresh]);

  const handleAiModify = async () => {
    if (!editing || !aiPrompt.trim()) return;
    setAiBusy(true);
    setAiStream('');
    try {
      // 流式：思考过程 + 正文增量实时展示在下方输出区，resolve 为清洗后的最终 Markdown
      const finalText = await modifyDraftStream(
        editing.draft.draft_id,
        editing.version,
        aiPrompt,
        (acc) => setAiStream(acc),
        (acc) => setAiThinking(acc)
      );
      setDraftText(finalText);
      setAiPrompt('');
      messageApi.success('AI 已修改（尚未保存），确认后点「保存为新版本」');
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : 'AI 修改失败');
    } finally {
      setAiBusy(false);
      setAiThinking('');
      setAiStream('');
    }
  };

  const handleSaveVersion = async () => {
    if (!editing) return;
    setSaving(true);
    try {
      const v = await saveDraftVersion(editing.draft.draft_id, draftText);
      messageApi.success(`已保存新版本 ${v.version}`);
      await refreshEditDetail();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        messageApi.warning('同一秒内已保存过，请稍候再试');
      } else {
        messageApi.error(e instanceof Error ? e.message : '保存失败');
      }
    } finally {
      setSaving(false);
    }
  };

  const handleRestore = async (version: string) => {
    if (!editing) return;
    try {
      await restoreDraftVersion(editing.draft.draft_id, version);
      messageApi.success(`已还原到版本 ${version}`);
      await refreshEditDetail();
      const detail = await getDraft(editing.draft.draft_id);
      setDraftText(detail.content || '');
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '还原失败');
    }
  };

  // ── 重试失败的草稿任务（后端新建同参数任务，原任务保留 failed 记录） ──
  const [retrying, setRetrying] = useState<string | null>(null);
  const handleRetry = async (jobId: string) => {
    setRetrying(jobId);
    try {
      const job = await retryJob(jobId);
      messageApi.success(`已重新提交，新任务 ${job.job_id.slice(0, 8)}`);
      onRefresh();
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '重试失败');
    } finally {
      setRetrying(null);
    }
  };

  const handleGenerateSubmit = async () => {
    if (!generateTarget) return;
    try {
      const values = await genForm.validateFields();
      setGenSubmitting(true);
      // 清洗引擎参数：条件不成立的参数不下发；基础「目标页数」按引擎声明回填进 engine_params
      const rawParams = (values.engine_params || {}) as Record<string, unknown>;
      const engineParams: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(rawParams)) {
        const dep = paramDependency(genEngineProps[key]?.description);
        if (dep && String(rawParams[dep.dependsOn] ?? '') !== dep.equals) continue;
        engineParams[key] = value;
      }
      if (genEngineProps.pages && values.pages != null) engineParams.pages = values.pages;
      const job = await submitDeck({
        engine: values.engine as DeckRequest['engine'],
        engine_type: 'output',
        project_id: generateTarget.draft.project_id,
        title: values.title || undefined,
        draft_id: generateTarget.draft.draft_id,
        version: generateTarget.version,
        pages: values.pages,
        engine_params: engineParams,
      });
      setGenerateTarget(null);
      genForm.resetFields();
      onGenerate(job);
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      messageApi.error(e instanceof Error ? e.message : '提交失败');
    } finally {
      setGenSubmitting(false);
    }
  };

  // 生成弹窗打开时写入默认值
  useEffect(() => {
    if (generateTarget) {
      genForm.setFieldsValue({
        engine: outputEngines[0]?.name,
        pages: 5,
        title: undefined,
      });
    }
  }, [generateTarget, outputEngines, genForm]);

  // 引擎参数（动态 schema 渲染）
  const selectedGenEngine = Form.useWatch('engine', genForm) || outputEngines[0]?.name || '';
  const genEngineInfo = outputEngines.find((e) => e.name === selectedGenEngine);
  const genEngineProps = (genEngineInfo?.params_schema?.properties || {}) as Record<string, {
    enum?: unknown[];
    type?: string;
    description?: string;
    'x-hidden'?: boolean;
  }>;
  // 引擎参数当前值（驱动条件参数显隐，如「仅 style=magazine 时有效」的主题色）
  const watchedEngineParams = (Form.useWatch('engine_params', genForm) || {}) as Record<string, unknown>;
  const genParamEntries = Object.entries(genEngineProps).filter(([key, value]) => {
    if (value['x-hidden']) return false; // 契约标记为隐藏的纯工程旋钮（如 route_profile）不进表单
    if (BASE_DUP_PARAM_KEYS.includes(key)) return false; // 目标页数等已有基础字段，不重复渲染
    const dep = paramDependency(value.description);
    if (dep) return String(watchedEngineParams[dep.dependsOn] ?? '') === dep.equals;
    return true;
  });

  return (
    <div>
      {contextHolder}
      <Card
        title="草稿任务"
        extra={
          <Button
            size="small"
            icon={<RefreshCw size={13} />}
            onClick={() => {
              setDetailCache({});
              onRefresh();
            }}
          >
            刷新
          </Button>
        }
      >
        {loading ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin />
          </div>
        ) : flatRows.length === 0 && unfinishedJobs.length === 0 ? (
          <Empty description="当前项目暂无草稿——首次生成草稿时会自动创建项目" />
        ) : (
          <List
            dataSource={[
              ...unfinishedJobs.map((j) => ({ key: `gen-${j.job_id}`, kind: 'generating' as const, job: j })),
              ...flatRows.map((r) => ({ key: `v-${r.draft.draft_id}-${r.version}`, kind: 'version' as const, row: r })),
            ]}
            renderItem={(item) =>
              item.kind === 'generating' ? (
                // 未落库的草稿任务：生成中 / 失败（可重试） / 超时（可重试）
                (() => {
                  const job = item.job;
                  const failed = job.status === 'failed' || job.status === 'timeout';
                  const pName = projectNameMap.get(job.project_id || '') || job.project_id?.slice(0, 8) || '-';
                  return (
                    <List.Item
                      actions={
                        failed
                          ? !readOnly && [
                              <Button
                                key="retry"
                                size="small"
                                icon={<RotateCcw size={13} />}
                                loading={retrying === job.job_id}
                                onClick={() => handleRetry(job.job_id)}
                              >
                                重试
                              </Button>,
                            ]
                          : [
                              <Button key="gen" size="small" type="primary" icon={<Rocket size={13} />} disabled>
                                生成中…
                              </Button>,
                            ]
                      }
                    >
                      <List.Item.Meta
                        title={
                          <Space>
                            <span>{job.title || '未命名草稿'}</span>
                            <StatusTag status={job.status} />
                          </Space>
                        }
                        description={
                          failed
                            ? `所属项目 ${pName} · ${job.error || job.status_detail || '生成失败，详见任务日志'}`
                            : `所属项目 ${pName} · ${job.status_detail || '引擎执行中'}`
                        }
                      />
                    </List.Item>
                  );
                })()
              ) : (
                (() => {
                  const row = item.row;
                  const pName = projectNameMap.get(row.draft.project_id) || row.draft.project_id.slice(0, 8);
                  return (
                    <List.Item
                      actions={[
                        <Button key="view" size="small" icon={<Eye size={13} />} onClick={() => setViewing(row)}>
                          查看
                        </Button>,
                        !readOnly && (
                          <Button key="edit" size="small" icon={<FileText size={13} />} onClick={() => openEdit(row)}>
                            编辑
                          </Button>
                        ),
                        !readOnly && (
                          <Button
                            key="gen"
                            size="small"
                            type="primary"
                            icon={<Rocket size={13} />}
                            onClick={() => {
                              setGenerateTarget({ draft: row.draft, version: row.version, label: row.label });
                              // PPT 标题默认 = 项目标题（用户可改；清空则后端回退草稿标题）
                              genForm.setFieldsValue({
                                title: projectNameMap.get(row.draft.project_id) || '',
                              });
                            }}
                          >
                            生成演示文件
                          </Button>
                        ),
                      ]}
                    >
                      <List.Item.Meta
                        title={
                          <Space>
                            <span style={{ fontWeight: 600 }}>{row.label}</span>
                            {row.isCurrent && <Tag color="purple">当前版本</Tag>}
                          </Space>
                        }
                        description={
                          <Space split={<Divider type="vertical" />} style={{ fontSize: 12 }}>
                            <span>
                              任务编号 <span style={{ fontFamily: 'monospace' }}>{row.draft.task_id?.slice(0, 8) || '-'}</span>
                            </span>
                            <span>
                              版本号 <span style={{ fontFamily: 'monospace' }}>{row.version}</span>
                            </span>
                            <span>{formatDate(row.createdAt || row.draft.updated_at)}</span>
                          </Space>
                        }
                      />
                    </List.Item>
                  );
                })()
              )
            }
          />
        )}
      </Card>

      {/* 查看抽屉（只读，Markdown 渲染） */}
      <Drawer
        title={viewing?.label || '查看草稿'}
        width={'70%'}
        open={!!viewing}
        onClose={() => setViewing(null)}
        footer={
          <div style={{ textAlign: 'right' }}>
            <Button
              type="primary"
              onClick={() => {
                if (viewing) openEdit(viewing);
                setViewing(null);
              }}
            >
              去编辑
            </Button>
          </div>
        }
      >
        {viewing && (
          <>
            <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="版本号">
                <span style={{ fontFamily: 'monospace' }}>{viewing.version}</span>
              </Descriptions.Item>
              <Descriptions.Item label="任务编号">
                <span style={{ fontFamily: 'monospace' }}>{viewing.draft.task_id?.slice(0, 8) || '-'}</span>
              </Descriptions.Item>
            </Descriptions>
            <MarkdownView content={viewing.content} />
          </>
        )}
      </Drawer>

      {/* 编辑抽屉 */}
      <Drawer
        title={editing ? `编辑草稿（基于版本 ${editing.version}）` : '草稿'}
        width={'70%'}
        styles={{ body: { display: 'flex', flexDirection: 'column', overflow: 'hidden' } }}
        open={!!editing}
        onClose={() => setEditing(null)}
        extra={
          <Button icon={<Save size={14} />} loading={saving} onClick={handleSaveVersion}>
            保存为新版本
          </Button>
        }
      >
        {editLoading ? (
          <div style={{ textAlign: 'center', padding: 60 }}>
            <Spin />
          </div>
        ) : (
          editDetail && (
            <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', paddingRight: 4 }}>
            <Tabs
              className="draft-edit-tabs"
              style={{ flex: 1, minHeight: 0 }}
              items={[
                {
                  key: 'edit',
                  label: '编辑',
                  children: (
                    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                      <Descriptions size="small" column={2} style={{ marginBottom: 12, flexShrink: 0 }}>
                        <Descriptions.Item label="当前版本">
                          <Tag color="blue">{editDetail.current_version}</Tag>
                        </Descriptions.Item>
                        <Descriptions.Item label="更新时间">
                          {formatDate(editDetail.updated_at)}
                        </Descriptions.Item>
                      </Descriptions>
                      <div style={{ flex: 1, minHeight: 260 }}>
                        <MdEditor value={draftText} onChange={setDraftText} height="100%" />
                      </div>
                    </div>
                  ),
                },
                {
                  key: 'versions',
                  label: `版本历史（${editDetail.versions?.length || 0}）`,
                  children: (
                    <List
                      size="small"
                      dataSource={[...(editDetail.versions || [])].sort((a, b) =>
                        b.version.localeCompare(a.version)
                      )}
                      renderItem={(v) => (
                        <List.Item
                          actions={[
                            <Button
                              key="restore"
                              size="small"
                              icon={<Undo2 size={12} />}
                              onClick={() => handleRestore(v.version)}
                              disabled={v.version === editDetail.current_version}
                            >
                              {v.version === editDetail.current_version ? '当前' : '还原'}
                            </Button>,
                          ]}
                        >
                          <List.Item.Meta
                            title={`版本 ${v.version}`}
                            description={`${(v.content || '').slice(0, 60)}${
                              (v.content || '').length > 60 ? '…' : ''
                            } · ${formatDate(v.created_at)}`}
                          />
                        </List.Item>
                      )}
                    />
                  ),
                },
              ]}
            />
            </div>
          )
        )}

        {/* AI 修改：固定在抽屉底部 */}
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, marginTop: 8 }}>
          <Space.Compact style={{ width: '100%', marginBottom: 8 }}>
            <Input
              placeholder="告诉 AI 怎么改，例如：把所有标题改成英文"
              value={aiPrompt}
              onChange={(e) => setAiPrompt(e.target.value)}
              onPressEnter={handleAiModify}
              prefix={<Bot size={14} color="#8b5cf6" />}
            />
            <Button
              type="primary"
              loading={aiBusy}
              onClick={handleAiModify}
              disabled={!aiPrompt.trim()}
            >
              AI 修改
            </Button>
          </Space.Compact>
          {(aiBusy || aiThinking) && (
            <pre
              ref={streamRef}
              style={{
                background: 'var(--primary-bg)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                padding: 10,
                maxHeight: 180,
                overflow: 'auto',
                fontSize: 12,
                fontFamily: 'Monaco, Consolas, monospace',
                whiteSpace: 'pre-wrap',
                margin: '0 0 8px',
                color: 'var(--text-primary)',
              }}
            >
              {aiThinking && (
                <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>💭 {aiThinking}</span>
              )}
              {aiStream && <span>✍ {aiStream.slice(-3000)}</span>}
              {aiBusy && !aiStream && !aiThinking ? '正在连接 AI…' : ''}
            </pre>
          )}
        </div>
      </Drawer>

      {/* 去生成弹窗（指定版本，就地不打断流程） */}
      <Modal
        title={`生成演示文件（${generateTarget?.label || ''}）`}
        open={!!generateTarget}
        onCancel={() => setGenerateTarget(null)}
        footer={null}
        width={560}
      >
        <Form form={genForm} layout="vertical">
          <Form.Item name="engine" label="输出引擎" rules={[{ required: true }]}>
            <Select
              options={outputEngines.map((e) => ({
                value: e.name,
                label: e.display_name || e.name,
              }))}
            />
          </Form.Item>
          <Form.Item name="title" label="PPT 标题">
            <Input placeholder="默认取项目标题" />
          </Form.Item>
          <Form.Item
            name="pages"
            label="目标页数"
            extra={(() => {
              const desc = genEngineProps.pages?.description || '';
              const inner = /（([^）]*)）/.exec(desc)?.[1];
              return inner ? `可留空。${inner}` : '可留空（由引擎决定），也可填任意页数';
            })()}
            rules={[{ type: 'integer', min: 1, message: '页数须为不小于 1 的整数' }]}
          >
            <InputNumber
              min={1}
              precision={0}
              style={{ width: '100%' }}
              placeholder="留空则由引擎决定"
            />
          </Form.Item>
          {genParamEntries.length > 0 && (
            <>
              <Divider orientation="left" plain>
                引擎参数
              </Divider>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                {genParamEntries.map(([key, value]) => {
                  const label = paramLabel(key, value.description);
                  return (
                    <Form.Item
                      key={key}
                      name={['engine_params', key]}
                      label={label}
                    >
                      {value.enum?.length ? (
                        <Select
                          options={value.enum.map((v) => ({
                            value: v,
                            label: paramValueLabel(key, v, value.description),
                          }))}
                          placeholder={`选择${label}`}
                        />
                      ) : value.type === 'boolean' ? (
                        <Select
                          options={[
                            { value: true, label: '开启' },
                            { value: false, label: '关闭' },
                          ]}
                          placeholder={`选择${label}`}
                        />
                      ) : value.type === 'integer' ? (
                        <InputNumber min={1} style={{ width: '100%' }} placeholder={`输入${label}`} />
                      ) : (
                        <Input placeholder={`输入${label}`} />
                      )}
                    </Form.Item>
                  );
                })}
              </div>
            </>
          )}
          <Form.Item style={{ marginTop: 20 }}>
            <Space>
              <Button onClick={() => setGenerateTarget(null)}>取消</Button>
              <Button
                type="primary"
                icon={<Rocket size={14} />}
                loading={genSubmitting}
                onClick={handleGenerateSubmit}
              >
                开始生成
              </Button>
            </Space>
          </Form.Item>
        </Form>
        <Alert
          type="info"
          showIcon
          message="真实引擎生成一份 PPTX 约 20-30 分钟，可稍后在「生成任务」查看。"
        />
      </Modal>
    </div>
  );
}
