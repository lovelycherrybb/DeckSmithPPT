/**
 * 子菜单②：草稿任务——当前项目的所有草稿版本平铺展示。
 * 每个版本一行：主标题「2026年9月9日 第N版」（N=当天第几个版本，最早为 1）；
 * 副信息：任务 ID、版本号；操作：查看 / 编辑 / 生成演示文件。
 * 未落库的草稿任务（草稿实体只在 completed 时写入，失败则永远不会落库）
 * 以置顶行展示：running=「生成中」，failed/timeout=错误态+重试。
 *
 * 项目归属由本面板自己保证：两个数据源（drafts / jobs）都按 projectId 现切，
 * 调用方传全量轮询结果也不会串到别的项目（历史项目视图曾因此把某个项目的两条
 * 失败草稿任务显示在所有项目里，见 selectUnfinishedDraftJobs 的注释）。
 */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Collapse,
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
  cancelJob,
  getDraft,
  getDraftVersion,
  getMyPreferences,
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
  type UserPreference,
} from '@/lib/api';
import { isPending, isRetryable, queueDetailLabel } from '@/lib/jobStatus';
import { StatusTag, CONTENT_STYLE_LABELS } from '@/components/ui/StatusTag';
import MarkdownView from '@/components/ui/MarkdownView';
import MdEditor from '@/components/ui/MdEditor';
import { useAuth } from '@/components/AuthProvider';
import { formatDate } from '@/lib/formatDate';
import {
  paramLabel, paramValueLabel, dependencySatisfied, paramGroup, paramPresets,
  paramItemEnum, paramIsWide, groupBySection, FALLBACK_SECTION, BASE_DUP_PARAM_KEYS,
  paramUnavailableReason, paramUnavailableValues,
  type ParamSpec,
} from '@/lib/engineParams';
import { stylePreviewFor, stylePreviewModalStyles } from '@/lib/stylePreview';
import { tokenUsagePartRows, tokenUsageSummary, tokenUsageTotal } from '@/lib/tokenUsage';


/**
 * 生成弹窗的网格布局。三列等宽，`minmax(0,1fr)` 里那个 0 是必需的：
 * 不写它，网格列的最小宽度会等于内容宽度，长文案或长下拉会把列撑破、
 * 右列直接顶出弹窗边缘（560 宽那版就是这么露馅的）。
 */
const GEN_GRID_STYLE = {
  display: 'grid',
  gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
  gap: '0 16px',
} as const;

interface Props {
  /**
   * 当前项目 ID。面板内所有数据一律按它现切：`null`（未关联项目）时两块都为空。
   * 之所以由面板来切而不是让调用方先切好——那是个没说出口的约定，
   * 漏一次就把别的项目的任务显示到本项目里（历史项目视图正是这么漏的）。
   */
  projectId: string | null;
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
//
// projectId 是这个函数原本缺的那半个条件：只按 task_type 与状态筛，喂进来的 jobs
// 又是跨项目的全量轮询结果，就会把 A 项目的失败草稿任务显示在 B 项目里（且因为
// 失败任务永不清理，它们长期挂在所有项目上）。项目归属必须和状态判据同层生效。
export function selectUnfinishedDraftJobs(
  jobs: JobWithProgress[],
  drafts: Draft[],
  projectId: string | null
): JobWithProgress[] {
  if (!projectId) return [];
  return jobs.filter(
    (j) =>
      j.project_id === projectId &&
      j.task_type === 'draft' &&
      // 未落库且"还没有正常结束"的草稿任务都要看得见：排队中、生成中、
      // 失败/超时/已取消（后三者供重试）。
      // 原先这里是一份 ['running','failed','timeout'] 白名单 —— 引入 queued 之后，
      // 刚提交的草稿任务会被整个过滤掉：用户点了提交、列表空空，像没提交成功。
      (isPending(j.status) || isRetryable(j.status)) &&
      !drafts.some((d) => d.task_id === j.job_id)
  );
}

// ── 版本号工具：14 位时间戳 ──
function versionDay(v: string): string | null {
  if (!/^\d{14}$/.test(v)) return null;
  return `${v.slice(0, 4)}年${parseInt(v.slice(4, 6), 10)}月${parseInt(v.slice(6, 8), 10)}日`;
}

/** 14 位时间戳版本号 → 「YYYY-MM-DD」；非该形状返回 null（界面不回显时间戳原值） */
function versionDate(v: string | null | undefined): string | null {
  if (!v || !/^\d{14}$/.test(v)) return null;
  return `${v.slice(0, 4)}-${v.slice(4, 6)}-${v.slice(6, 8)}`;
}

/** 版本号展示文案：显示「第 N 版（YYYY-MM-DD）」，不显示时间戳原值 */
function versionDisplay(v: string | null | undefined, rank?: number | null): string {
  const date = versionDate(v);
  if (rank != null) return date ? `第 ${rank} 版（${date}）` : `第 ${rank} 版`;
  return date ? `版本（${date}）` : '版本未知';
}

export default function DraftsPanel({
  projectId,
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
  // 引擎参数门控：仅高级用户/管理员可编辑；普通用户与访客只读禁用（后端仍独立复核）
  const { user } = useAuth();
  const canEditParams = user?.role === 'vip' || user?.role === 'manager';

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
  /* 风格悬停预览：只存「当前鼠标/键盘走到哪个枚举值」，与表单里真正选中的值互不相干。
     null 表示不预览（弹窗回到应用主题）。 */
  const [previewValue, setPreviewValue] = useState<string | null>(null);
  const stylePreview = stylePreviewFor(previewValue);
  const closeGenerateModal = () => {
    setGenerateTarget(null);
    setPreviewValue(null);
  };
  // 本人偏好：作为生成弹窗的初始值（默认引擎/页数）。未登录/访客取不到则回落默认。
  const [prefs, setPrefs] = useState<UserPreference | null>(null);
  useEffect(() => {
    getMyPreferences().then(setPrefs).catch(() => setPrefs(null));
  }, []);
  const [genForm] = Form.useForm();
  const [genSubmitting, setGenSubmitting] = useState(false);

  // ── 数据辅助 ──
  const projectNameMap = useMemo(
    () => new Map(projects.map((p) => [p.project_id, p.project_name])),
    [projects]
  );
  const jobByTaskId = useMemo(() => new Map(jobs.map((j) => [j.job_id, j])), [jobs]);

  // 已落库草稿同样按项目切（调用方走的是项目内接口，这里只是让"归属"只有一处口径）
  const projectDrafts = useMemo(
    () => (projectId ? drafts.filter((d) => d.project_id === projectId) : []),
    [drafts, projectId]
  );

  // 未落库的草稿任务：过滤条件（含项目归属）与工作台 badge 共用 selectUnfinishedDraftJobs
  const unfinishedJobs = useMemo(
    () => selectUnfinishedDraftJobs(jobs, projectDrafts, projectId),
    [jobs, projectDrafts, projectId]
  );

  // 版本详情缓存：平铺需要每个草稿的版本列表
  const [detailCache, setDetailCache] = useState<Record<string, DraftDetail | undefined>>({});
  useEffect(() => {
    const missing = projectDrafts.filter((d) => detailCache[d.draft_id] === undefined);
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
  }, [projectDrafts]); // eslint-disable-line react-hooks/exhaustive-deps

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
          label: day || '未命名草稿',
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
        label: day ? `${day} 第${idx + 1}版` : `第 ${idx + 1} 版`,
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
        messageApi.error(e instanceof Error ? e.message : '草稿内容加载失败，请重试');
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
      messageApi.success('AI 已完成修改，确认后点击「保存为新版本」');
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
      messageApi.success('已保存为新版本');
      await refreshEditDetail();
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        messageApi.warning('保存过于频繁，请稍后重试');
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
      messageApi.success('已回到该版本');
      await refreshEditDetail();
      const detail = await getDraft(editing.draft.draft_id);
      setDraftText(detail.content || '');
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '回到该版本未成功');
    }
  };

  // ── 重试失败的草稿任务（后端新建同参数任务，原任务保留 failed 记录） ──
  const [retrying, setRetrying] = useState<string | null>(null);
  const handleRetry = async (jobId: string) => {
    setRetrying(jobId);
    try {
      const job = await retryJob(jobId);
      messageApi.success('已重新提交生成');
      onRefresh();
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '重新生成未成功，请稍后重试');
    } finally {
      setRetrying(null);
    }
  };

  // ── 取消排队中的草稿任务 ──
  // 草稿类引擎（ppt_master_draft / claude_draft）都显式退出了 cancel 钩子：它们把
  // claude 跑在阻塞调用里、没有可杀的 pid，所以"生成中"阶段没有取消入口（不是漏做，
  // 是不做假撤销）。因此这里只给 queued 一个「取消排队」。
  const [cancelling, setCancelling] = useState<string | null>(null);
  const handleCancel = async (jobId: string) => {
    setCancelling(jobId);
    try {
      await cancelJob(jobId);
      messageApi.success('已取消排队');
      onRefresh();
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '取消失败');
    } finally {
      setCancelling(null);
    }
  };

  const handleGenerateSubmit = async () => {
    if (!generateTarget) return;
    try {
      const values = await genForm.validateFields();
      setGenSubmitting(true);
      // 清洗引擎参数：条件不成立的参数不下发；基础「目标页数」按引擎声明回填进 engine_params。
      // 空值一律不下发（不是下发 null）——"没选"必须传成"这个键不存在"，引擎才知道该让技能自己判断；
      // 下发 null 会被当成一次真实选择。
      const rawParams = (values.engine_params || {}) as Record<string, unknown>;
      const engineParams: Record<string, unknown> = {};
      for (const [key, value] of Object.entries(rawParams)) {
        if (value === undefined || value === null || value === '') continue;
        if (Array.isArray(value) && value.length === 0) continue; // 多选清空＝没选，不下发空数组
        if (!dependencySatisfied(genEngineProps[key], rawParams)) continue;
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
      closeGenerateModal();
      genForm.resetFields();
      onGenerate(job);
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      messageApi.error(e instanceof Error ? e.message : '开始生成未成功，请重试');
    } finally {
      setGenSubmitting(false);
    }
  };

  // 生成弹窗打开时写入初始值：优先用本人偏好（默认引擎/页数），未设则回落
  // 引擎取列表首个、页数留空由引擎决定（与设置页占位文案语义一致）
  useEffect(() => {
    if (generateTarget) {
      const prefEngine =
        prefs?.default_engine && outputEngines.some((e) => e.name === prefs.default_engine)
          ? prefs.default_engine
          : outputEngines[0]?.name;
      genForm.setFieldsValue({
        engine: prefEngine,
        pages: prefs?.default_pages ?? undefined,
        title: undefined,
      });
    }
  }, [generateTarget, outputEngines, genForm, prefs]);

  // 引擎参数（动态 schema 渲染）
  const selectedGenEngine = Form.useWatch('engine', genForm) || outputEngines[0]?.name || '';
  const genEngineInfo = outputEngines.find((e) => e.name === selectedGenEngine);
  const genEngineProps = (genEngineInfo?.params_schema?.properties || {}) as Record<
    string,
    ParamSpec
  >;
  // 引擎参数当前值（驱动条件参数显隐，如「仅 style=magazine 时有效」的主题色）
  const watchedEngineParams = (Form.useWatch('engine_params', genForm) || {}) as Record<string, unknown>;
  const genParamEntries = Object.entries(genEngineProps).filter(([key, value]) => {
    if (value['x-hidden']) return false; // 契约标记为隐藏的纯工程旋钮（如 route_profile）不进表单
    if (BASE_DUP_PARAM_KEYS.includes(key)) return false; // 目标页数等已有基础字段，不重复渲染
    return dependencySatisfied(value, watchedEngineParams);
  });
  // 契约里标了 x-group=advanced 的（自由文本那几项）收进「高级」折叠区，别在常规区吓人
  const genNormalEntries = genParamEntries.filter(([, v]) => paramGroup(v) === 'normal');
  const genAdvancedEntries = genParamEntries.filter(([, v]) => paramGroup(v) === 'advanced');
  // 常规项按契约 x-section 切节；漏标的进「其它参数」兜底节，保证一项都不会不渲染
  const genParamSections = groupBySection(genNormalEntries);

  /**
   * 单个引擎参数控件。常规区与「高级」折叠区共用同一个渲染器——两处形状必须一致，
   * 复制一份就会漂移（以后新增控件类型只改这里）。
   * 控件类型全部由契约决定，不按参数名硬编码：数组型走多选、带 x-presets 的走
   * "可选可写"、枚举走单选、布尔走开关，其余落到文本框。
   */
  const renderEngineParam = ([key, value]: [string, (typeof genEngineProps)[string]]) => {
    const label = paramLabel(key, value);
    /* 这个枚举参数里有没有配了外观的值（目前只有视觉风格那三个）。按值查外观表，
       不按参数名判断，所以别的枚举参数不受影响、以后加风格也不用改这里。 */
    const previewableEnum = !!value.enum?.length && value.enum.some((v) => stylePreviewFor(v));
    const presets = paramPresets(value);
    const itemEnum = paramItemEnum(value);
    /* 当前兑现不了的项，原因一律从契约读：整项标了就整项锁住，只标某几个取值就只锁那几个。
       这里不按参数名写死判断——按名字写死等于给「前端不硬编码引擎参数」那条口径开个常例
       （ADR-033 决策 1）。置灰只是界面层，后端提交期还会再拦一道（ADR-033 决策 3），
       因为界面挡不住直接调接口的人。 */
    const unavailableReason = paramUnavailableReason(value);
    const valueReasons = paramUnavailableValues(value);
    const paramDisabled = !canEditParams || !!unavailableReason;
    const tipLines = [value.description, unavailableReason].filter(Boolean) as string[];
    for (const [v, why] of Object.entries(valueReasons)) {
      tipLines.push(`「${paramValueLabel(key, v, value)}」${why}`);
    }
    /** 下拉候选项：被标了暂不支持的取值照常显示，但选不动，并在名字后面写明 */
    const optionOf = (v: unknown) => {
      const why = valueReasons[String(v)];
      const text = paramValueLabel(key, v, value);
      return { value: v, label: why ? `${text}（暂不支持）` : text, disabled: !!why };
    };
    return (
      <Form.Item
        key={key}
        name={['engine_params', key]}
        label={
          unavailableReason ? (
            <Space size={4}>
              {label}
              <Tag>暂不支持</Tag>
            </Space>
          ) : (
            label
          )
        }
        /* 长说明挂到标签后面的问号上：说明文字平铺在框里会把弹窗撑得老长，
           而它本来就是"给用户看的解释"，不该占版面。暂不支持的原因也走同一个问号。 */
        tooltip={<span style={{ whiteSpace: 'pre-line' }}>{tipLines.join('\n\n')}</span>}
        style={paramIsWide(value) ? { gridColumn: '1 / -1' } : undefined}
      >
        {itemEnum.length ? (
          <Select
            mode="multiple"
            disabled={paramDisabled}
            allowClear
            options={itemEnum.map(optionOf)}
            placeholder={`选择${label}（可多选）`}
          />
        ) : presets.length ? (
          /* 预设句只是"点一下就替你写进去"的便利，不是把选择面锁死成三档：
             输入框照样能自己写，写什么就原样下发什么。 */
          <AutoComplete
            disabled={paramDisabled}
            allowClear
            options={presets.map((p) => ({ value: p, label: p }))}
            placeholder="未填写表示不指定，可点击示例，也可自行填写"
          />
        ) : value.enum?.length ? (
          <Select
            disabled={paramDisabled}
            allowClear
            options={value.enum.map(optionOf)}
            placeholder={`选择${label}`}
            onOpenChange={(open) => { if (!open) setPreviewValue(null); }}
            /* 鼠标划过哪个风格，就把整个弹窗换成那个风格的样子；悬停只是预览，
               不改动已选中的值，关掉下拉就退回应用主题。 */
            optionRender={
              previewableEnum
                ? (option) => (
                    <div
                      onMouseEnter={() =>
                        setPreviewValue(stylePreviewFor(option.value) ? String(option.value) : null)
                      }
                    >
                      {option.label}
                    </div>
                  )
                : undefined
            }
          />
        ) : value.type === 'boolean' ? (
          <Select
            disabled={paramDisabled}
            allowClear
            options={[
              { value: true, label: '开启' },
              { value: false, label: '关闭' },
            ]}
            placeholder={`选择${label}`}
          />
        ) : value.type === 'integer' ? (
          <InputNumber disabled={paramDisabled} min={1} style={{ width: '100%' }} placeholder={`输入${label}`} />
        ) : (
          <Input disabled={paramDisabled} placeholder={`输入${label}`} />
        )}
      </Form.Item>
    );
  };

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
          <Empty description="当前项目暂无草稿。首次生成草稿时会自动创建项目" />
        ) : (
          <List
            dataSource={[
              ...unfinishedJobs.map((j) => ({ key: `gen-${j.job_id}`, kind: 'generating' as const, job: j })),
              ...flatRows.map((r) => ({ key: `v-${r.draft.draft_id}-${r.version}`, kind: 'version' as const, row: r })),
            ]}
            renderItem={(item) =>
              item.kind === 'generating' ? (
                // 未落库的草稿任务：排队中 / 生成中 / 失败·超时·已取消（均可重试）
                (() => {
                  const job = item.job;
                  const retryable = isRetryable(job.status);
                  const queued = job.status === 'queued';
                  return (
                    <List.Item
                      actions={
                        retryable
                          ? !readOnly && [
                              <Button
                                key="retry"
                                size="small"
                                icon={<RotateCcw size={13} />}
                                loading={retrying === job.job_id}
                                onClick={() => handleRetry(job.job_id)}
                              >
                                重新生成
                              </Button>,
                            ]
                          : queued && !readOnly
                            ? [
                                <Button
                                  key="cancel"
                                  size="small"
                                  loading={cancelling === job.job_id}
                                  onClick={() => handleCancel(job.job_id)}
                                >
                                  取消排队
                                </Button>,
                              ]
                            : [
                                <Button
                                  key="gen"
                                  size="small"
                                  type="primary"
                                  icon={<Rocket size={13} />}
                                  disabled
                                >
                                  {queued ? '排队中…' : '生成中…'}
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
                          retryable ? (
                            <>
                              {job.error || job.status_detail || '生成未成功，可点击「重新生成」再次尝试'}
                              {/* 未成功任务烧掉的 token 也是事实（终态才落库，排队/生成中不显示） */}
                              {`｜${tokenUsageSummary(job.token_usage)}`}
                            </>
                          ) : (
                            queued
                              ? queueDetailLabel(job.status_detail, job.queue_position)
                              : job.status_detail || '正在生成'
                          )
                        }
                      />
                    </List.Item>
                  );
                })()
              ) : (
                (() => {
                  const row = item.row;
                  // token 消耗挂在「生成该草稿的任务」上（与任务编号同源）：手工保存的版本不额外烧钱
                  const genJob = row.draft.task_id ? jobByTaskId.get(row.draft.task_id) : undefined;
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
                            <span>{versionDisplay(row.version, row.rank)}</span>
                            <span>{tokenUsageSummary(genJob?.token_usage)}</span>
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
            {(() => {
              const genJob = viewing.draft.task_id ? jobByTaskId.get(viewing.draft.task_id) : undefined;
              return (
                <Descriptions size="small" column={2} style={{ marginBottom: 16 }}>
                  <Descriptions.Item label="版本号">{versionDisplay(viewing.version, viewing.rank)}</Descriptions.Item>
                  <Descriptions.Item label="任务编号（供报障核对）">
                    <span style={{ fontFamily: 'monospace' }}>{viewing.draft.task_id?.slice(0, 8) || '-'}</span>
                  </Descriptions.Item>
                  <Descriptions.Item label="消耗">{tokenUsageTotal(genJob?.token_usage)}</Descriptions.Item>
                  {tokenUsagePartRows(genJob?.token_usage).map((p) => (
                    <Descriptions.Item key={p.key} label={p.label}>
                      {p.value}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              );
            })()}
            <MarkdownView content={viewing.content} />
          </>
        )}
      </Drawer>

      {/* 编辑抽屉 */}
      <Drawer
        title={editing ? '编辑草稿' : '草稿'}
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
                          <Tag color="blue">
                            {versionDisplay(
                              editDetail.current_version,
                              [...(editDetail.versions || [])]
                                .map((x) => x.version)
                                .sort()
                                .indexOf(editDetail.current_version) + 1 || null
                            )}
                          </Tag>
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
                  children: (() => {
                    const asc = [...(editDetail.versions || [])].sort((a, b) =>
                      a.version.localeCompare(b.version)
                    );
                    const rankOf = (ver: string) =>
                      asc.findIndex((x) => x.version === ver) + 1;
                    return (
                      <List
                        size="small"
                        dataSource={[...asc].reverse()}
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
                                {v.version === editDetail.current_version ? '当前' : '使用此版本'}
                              </Button>,
                            ]}
                          >
                            <List.Item.Meta
                              title={versionDisplay(v.version, rankOf(v.version))}
                              description={`${(v.content || '').slice(0, 60)}${
                                (v.content || '').length > 60 ? '…' : ''
                              }｜${formatDate(v.created_at)}`}
                            />
                          </List.Item>
                        )}
                      />
                    );
                  })(),
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
              placeholder="输入修改要求，例如：把所有标题改成英文"
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
              让 AI 修改
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
        onCancel={closeGenerateModal}
        footer={null}
        width={920}
        /* 悬停某个风格时整弹窗换成那个风格的样子。变量挂在 content 上，弹窗内的标题、表单、
           控件都从它继承取色；关掉下拉即退回应用主题。 */
        styles={stylePreviewModalStyles(stylePreview)}
      >
        <Form form={genForm} layout="vertical">
          {/* 基础三项一行放完。minmax(0,1fr) 是关键：不写这个 0，网格列会被长内容撑破，
              右列控件就会顶出弹窗边缘（560 宽那版正是这么露馅的）。 */}
          <div style={GEN_GRID_STYLE}>
            <Form.Item name="engine" label="生成方式" rules={[{ required: true }]}>
              <Select
                options={outputEngines.map((e) => ({
                  value: e.name,
                  label: e.display_name || e.name,
                }))}
              />
            </Form.Item>
            <Form.Item name="title" label="PPT 标题">
              <Input placeholder="未填写时使用项目名称" />
            </Form.Item>
            <Form.Item
              name="pages"
              label="目标页数"
              extra={(() => {
                const desc = genEngineProps.pages?.description || '';
                const inner = /（([^）]*)）/.exec(desc)?.[1];
                return inner ? `可留空。${inner}` : '可留空，由系统决定页数；填写时须为不小于 1 的整数';
              })()}
              rules={[{ type: 'integer', min: 1, message: '页数须为整数，且不小于 1' }]}
            >
              <InputNumber
                min={1}
                precision={0}
                style={{ width: '100%' }}
                placeholder="未填写时由系统决定"
              />
            </Form.Item>
          </div>
          {genParamEntries.length > 0 && (
            <>
              <Divider orientation="left" plain>
                生成设置
              </Divider>
              {!canEditParams && (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message="当前账号暂不支持修改这些设置，将按默认方式生成。如需自定义，请开通高级用户或联系管理员"
                />
              )}
              {/* 小节标题与顺序全部来自契约 x-section，前端不重排、不改名。
                  整份都没有小节声明的引擎（如 ppt_master）只渲染一个网格、不顶兜底标题，
                  免得界面凭空多出一个"其它参数"。 */}
              {genParamSections.map((section, idx) => (
                <div key={section.title}>
                  {/* 小节标题刻意**不用分隔条**：它和上层「引擎参数」那条线同权重时，
                     人会误以为两者同级（2026-11-02 龚成指出）。约定成一句：
                     有横线＝大分界，纯文字＝小节。 */}
                  {!(genParamSections.length === 1 && section.title === FALLBACK_SECTION) && (
                    <div
                      style={{
                        fontSize: 12,
                        color: 'var(--text-muted)',
                        letterSpacing: 0.5,
                        margin: idx === 0 ? '0 0 10px' : '20px 0 10px',
                      }}
                    >
                      {section.title}
                    </div>
                  )}
                  <div style={GEN_GRID_STYLE}>
                    {section.items.map(renderEngineParam)}
                  </div>
                </div>
              ))}
              {genAdvancedEntries.length > 0 && (
                <Collapse
                  ghost
                  items={[
                    {
                      key: 'advanced',
                      label: '高级',
                      children: (
                        <div style={GEN_GRID_STYLE}>
                          {genAdvancedEntries.map(renderEngineParam)}
                        </div>
                      ),
                    },
                  ]}
                />
              )}
            </>
          )}
          <Form.Item style={{ marginTop: 20 }}>
            <Space>
              <Button onClick={closeGenerateModal}>取消</Button>
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
          message="生成需要一些时间，页数越多耗时越长。进度可在「生成任务」中查看。"
        />
      </Modal>
    </div>
  );
}
