/**
 * 统一 API 客户端：按 docs/contracts/openapi.yaml 的端点封装。
 * 类型来自契约生成（shared-types/api.d.ts，bash scripts/generate-types.sh 产出）。
 * 注意：requestJson 抛出的 Error.message 已是后端 detail（可读中文）。
 */
import type { components } from 'shared-types/api';

// ── 契约类型别名（与后端 schemas 同名） ──
export type EngineInfo = components['schemas']['EngineInfo'];
export type DeckRequest = components['schemas']['DeckRequest'];
export type Job = components['schemas']['Job'];
export type Project = components['schemas']['Project'];
export type ProjectDetail = components['schemas']['ProjectDetail'];
export type Draft = components['schemas']['Draft'];
export type DraftDetail = components['schemas']['DraftDetail'];
export type DraftVersion = components['schemas']['DraftVersion'];
export type Presentation = components['schemas']['Presentation'];
export type UploadResponse = components['schemas']['UploadResponse'];
export type SecurityCheckResult = components['schemas']['SecurityCheckResult'];
export type JobStatus = components['schemas']['JobStatus'];
export type TaskType = components['schemas']['Job']['task_type'];
export type PageItem = components['schemas']['PageItem'];
export type JobPages = components['schemas']['JobPages'];
export type TokenUsage = components['schemas']['TokenUsage'];
export type VersionInfo = components['schemas']['VersionOut'];
export type User = components['schemas']['User'];
export type UserRole = components['schemas']['UserRole'];
export type CurrentUser = components['schemas']['CurrentUser'];
export type LoginResult = components['schemas']['LoginResult'];
export type UserPreference = components['schemas']['UserPreference'];
export type SystemSettingsView = components['schemas']['SystemSettings'];
export type SystemSettingsField = components['schemas']['SystemSettingsField'];
export type CapacityInfo = components['schemas']['CapacityInfo'];

/** 后端 JobOut 的超集字段（进度信息，运行中任务附带） */
export interface ProgressInfo {
  stage: string;
  stage_label: string;
  pages_generated: number;
  elapsed_seconds: number;
  current_page?: string | null;
  last_log?: string | null;
}

export type JobWithProgress = Job & { progress?: ProgressInfo | null };

// ── 基础请求 ──
// API 走 next.config.mjs 的 rewrite 代理到后端，用相对路径即可
const API_BASE = '';

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    },
  });
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload?.detail || payload?.message || `请求失败 (${response.status})`
    );
  }
  return payload as T;
}

// ── 引擎 ──
export function listEngines(type?: 'draft' | 'output'): Promise<EngineInfo[]> {
  const q = type ? `?type=${type}` : '';
  return requestJson(`/api/engines${q}`);
}

// ── 版本 ──
/** 当前安装包版本；开发态后端返回 version:null（调用方据此不显示）。 */
export function getAppVersion(): Promise<VersionInfo> {
  return requestJson('/api/version');
}

// ── 身份与鉴权（v0.8）──
/** 当前身份三态：已登录 / 未登录有访客身份 / 纯匿名。恒 200。 */
export function getMe(): Promise<CurrentUser> {
  return requestJson('/api/auth/me');
}

export function login(username: string, password: string): Promise<LoginResult> {
  return requestJson('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function logout(): Promise<void> {
  return requestJson('/api/auth/logout', { method: 'POST' });
}

export function changePassword(oldPassword: string, newPassword: string): Promise<void> {
  return requestJson('/api/auth/password', {
    method: 'POST',
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  });
}

// ── 用户管理（仅管理员）──
export function listUsers(): Promise<User[]> {
  return requestJson('/api/users');
}

export function createUser(body: {
  username: string;
  nickname?: string;
  phone?: string;
  is_vip?: boolean;
}): Promise<User> {
  return requestJson('/api/users', { method: 'POST', body: JSON.stringify(body) });
}

export function updateUser(
  userId: string,
  body: { nickname?: string; phone?: string; is_vip?: boolean }
): Promise<User> {
  return requestJson(`/api/users/${userId}`, { method: 'PATCH', body: JSON.stringify(body) });
}

export function resetUserPassword(userId: string): Promise<void> {
  return requestJson(`/api/users/${userId}/reset-password`, { method: 'POST' });
}

export function setUserStatus(userId: string, status: 'enabled' | 'disabled'): Promise<User> {
  return requestJson(`/api/users/${userId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  });
}

// ── 用户偏好（本人）──
export function getMyPreferences(): Promise<UserPreference> {
  return requestJson('/api/settings/preferences');
}

export function saveMyPreferences(body: {
  default_engine?: string | null;
  default_pages?: number | null;
  auto_save?: boolean;
}): Promise<UserPreference> {
  return requestJson('/api/settings/preferences', { method: 'PUT', body: JSON.stringify(body) });
}

// ── 系统设置（仅管理员，ADR-020）──
// 表单元数据由后端注册表下发，前端按 type 通用渲染、不写死任何键名：
// 以后后端加设置项（含 agent CLI 那一组），这里和设置页都不用改。

export function getSystemSettings(): Promise<SystemSettingsView> {
  return requestJson('/api/admin/settings');
}

/**
 * 保存系统设置。只提交改动过的键（后端对未出现的键不动）；
 * 某键传 null = 删除覆盖、恢复 env 默认。
 * secret 项不提交即保持原值不变——绝不要把 masked 值回传回去。
 */
export function saveSystemSettings(
  values: Record<string, string | number | boolean | null>
): Promise<SystemSettingsView> {
  return requestJson('/api/admin/settings', {
    method: 'PUT',
    body: JSON.stringify({ values }),
  });
}

/** 只读容量提示（任意身份）：并发上限 + 当前在跑/排队数 */
export function getCapacity(): Promise<CapacityInfo> {
  return requestJson('/api/settings/capacity');
}

// ── 上传 ──
const UPLOAD_MAX_RETRIES = 2; // dev 代理偶发 500/断连时自动重试

export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('file', file);

  let lastError: ApiError | null = null;
  for (let attempt = 0; attempt <= UPLOAD_MAX_RETRIES; attempt++) {
    if (attempt > 0) {
      await new Promise((r) => setTimeout(r, 400 * attempt)); // 退避
    }
    try {
      const response = await fetch(`${API_BASE}/api/upload`, {
        method: 'POST',
        body: formData,
      });
      // 先取 text 再 parse：dev 代理偶发返回 HTML 500 页时，给用户可读错误而非 JSON 解析异常
      const text = await response.text();
      let payload: Record<string, unknown> | null = null;
      try {
        payload = text ? JSON.parse(text) : null;
      } catch {
        payload = null;
      }
      if (response.ok && payload) {
        return payload as unknown as UploadResponse;
      }
      // 可重试的状态：5xx / 非 JSON 响应（dev 代理抽风）；4xx 是业务错误不重试
      const retryable = response.status >= 500 || !payload;
      lastError = new ApiError(
        response.status,
        (payload as { detail?: string } | null)?.detail ||
          (retryable ? `上传失败（${response.status}），正在重试…` : `上传失败（${response.status}）`)
      );
      if (!retryable) throw lastError;
    } catch (e) {
      if (e instanceof ApiError) {
        if (e.status < 500) throw e; // 业务错误不重试
        lastError = e;
      } else {
        // 网络错误（代理进程重启等）：可重试
        lastError = new ApiError(0, '网络异常，正在重试…');
      }
    }
  }
  throw lastError || new ApiError(500, '上传失败，请稍后重试');
}

// ── Agent 文本能力（统一接口：丢任务给 AI、拿文本回来） ──
export type AgentKind = 'modify_draft' | 'suggest_title';

export function executeAgent(req: {
  kind: AgentKind;
  draft_id?: string;
  version?: string;
  prompt?: string;
  files?: Array<{ filename: string; path?: string }>;
}): Promise<{ text: string; kind: string }> {
  return requestJson('/api/agent/execute', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

/** Agent 拟题（基于已上传文件内容） */
export function suggestTitle(
  files: Array<{ filename: string; path?: string }>
): Promise<{ title: string }> {
  // 统一走 /api/agent/execute；单文件由后端短路返回文件名
  return executeAgent({ kind: 'suggest_title', files }).then(
    (r) => ({ title: r.text })
  );
}

/** Agent 修改草稿：返回修改后内容（不落库，用户确认后另调 saveDraftVersion） */
export function modifyDraft(
  draftId: string,
  currentVersion: string,
  prompt: string
): Promise<DraftVersion> {
  return executeAgent({
    kind: 'modify_draft',
    draft_id: draftId,
    version: currentVersion,
    prompt,
  }).then((r) => ({
    draft_id: draftId,
    version: currentVersion,
    content: r.text,
    created_at: new Date().toISOString(),
  }) as DraftVersion);
}

/** Agent 文本处理·流式：创建异步会话 + EventSource 消费增量。
 *  onDelta 收到"累计到目前为止"的文本；resolve 为清洗后的最终文本。 */
export async function executeAgentStream(
  req: {
    kind: AgentKind;
    draft_id?: string;
    version?: string;
    prompt?: string;
    files?: Array<{ filename: string; path?: string }>;
  },
  onDelta?: (accumulated: string) => void,
  onThinking?: (accumulated: string) => void
): Promise<string> {
  const session = await requestJson<{ session_id: string }>('/api/agent/execute-async', {
    method: 'POST',
    body: JSON.stringify(req),
  });

  return new Promise<string>((resolve, reject) => {
    const es = new EventSource(`/api/agent/sessions/${session.session_id}/stream`);
    let acc = '';
    let thinkAcc = '';
    let settled = false;
    // 看门狗：60 秒无任何事件 → 判定挂起，明确报错（而不是永久转圈）
    let lastEventAt = Date.now();
    const watchdog = setInterval(() => {
      if (!settled && Date.now() - lastEventAt > 60000) {
        finish(() => reject(new ApiError(504, 'AI 长时间无响应，请重试')));
      }
    }, 5000);
    const touch = () => {
      lastEventAt = Date.now();
    };
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearInterval(watchdog);
      es.close();
      fn();
    };
    es.onmessage = (e) => {
      try {
        touch();
        const ev = JSON.parse(e.data) as { type: string; data: string };
        if (ev.type === 'text') {
          acc += ev.data;
          onDelta?.(acc);
        } else if (ev.type === 'thinking') {
          thinkAcc += ev.data;
          onThinking?.(thinkAcc);
        } else if (ev.type === 'result') {
          finish(() => resolve(ev.data));
        } else if (ev.type === 'done') {
          finish(() => resolve(acc));
        } else if (ev.type === 'error') {
          finish(() => reject(new ApiError(502, ev.data || 'Agent 流式输出失败')));
        }
      } catch {
        /* 忽略非 JSON 心跳 */
      }
    };
    es.onerror = () => {
      touch(); // SSE 自动重连也算活动
      // 服务端正常关闭流也会触发 onerror：已拿到结果就不当失败
      if (!settled) finish(() => reject(new ApiError(0, '流式连接中断，请重试')));
    };
  });
}

/** Agent 修改草稿·流式：onDelta 收到累计输出，resolve 为清洗后的最终 Markdown */
export function modifyDraftStream(
  draftId: string,
  version: string,
  prompt: string,
  onDelta?: (accumulated: string) => void,
  onThinking?: (accumulated: string) => void
): Promise<string> {
  return executeAgentStream(
    { kind: 'modify_draft', draft_id: draftId, version, prompt },
    onDelta,
    onThinking
  );
}

/** Agent 拟题·流式 */
export function suggestTitleStream(
  files: Array<{ filename: string; path?: string }>,
  onDelta?: (accumulated: string) => void
): Promise<string> {
  return executeAgentStream({ kind: 'suggest_title', files }, onDelta);
}

// ── 任务 ──
/**
 * 提交入参：契约 DeckRequest 的实用化——
 * content_style/agent 在契约里带 default（后端有默认值），
 * openapi-typescript 会把它们生成必填，这里放宽回可选。
 */
export type SubmitDeckInput = Omit<DeckRequest, 'content_style' | 'agent'> & {
  content_style?: string;
  agent?: DeckRequest['agent'];
};

export function submitDeck(req: SubmitDeckInput): Promise<Job> {
  return requestJson('/api/decks', { method: 'POST', body: JSON.stringify(req) });
}

export function listJobs(params?: {
  project_id?: string;
  status?: JobStatus;
  task_type?: TaskType;
  limit?: number;
}): Promise<JobWithProgress[]> {
  const qs = new URLSearchParams();
  if (params?.project_id) qs.set('project_id', params.project_id);
  if (params?.status) qs.set('status', params.status);
  if (params?.task_type) qs.set('task_type', params.task_type);
  qs.set('limit', String(params?.limit ?? 100));
  return requestJson(`/api/jobs?${qs.toString()}`);
}

export function getJob(jobId: string): Promise<JobWithProgress> {
  return requestJson(`/api/jobs/${jobId}`);
}

export function retryJob(jobId: string): Promise<Job> {
  return requestJson(`/api/jobs/${jobId}/retry`, { method: 'POST' });
}

/**
 * 取消任务。queued 直接摘队；running 只有在引擎能力位含 cancel 时才可用，
 * 否则后端返回 409（绝不"静默假撤销"）。按钮可见性请一律用
 * `lib/jobStatus.engineCancellable(engine.capabilities)` 预判，别等 409。
 */
export function cancelJob(jobId: string): Promise<Job> {
  return requestJson(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
}

export function jobDownloadUrl(jobId: string): string {
  return `${API_BASE}/api/jobs/${jobId}/download`;
}

/** 逐页预览快照：只列已完成页（无预测总数、无 pending）；演示文件按 task_id 复用 */
export function getJobPages(jobId: string): Promise<JobPages> {
  return requestJson(`/api/jobs/${jobId}/pages`);
}

export function presentationDownloadUrl(presentationId: string): string {
  return `${API_BASE}/api/presentations/${presentationId}/download`;
}

// ── 项目 ──
export function listProjects(limit = 20): Promise<Project[]> {
  return requestJson(`/api/projects?limit=${limit}`);
}

export function getProject(projectId: string): Promise<ProjectDetail> {
  return requestJson(`/api/projects/${projectId}`);
}

export function listProjectDrafts(projectId: string): Promise<Draft[]> {
  return requestJson(`/api/projects/${projectId}/drafts`);
}

export function listProjectPresentations(projectId: string): Promise<Presentation[]> {
  return requestJson(`/api/projects/${projectId}/presentations`);
}

// ── 草稿 ──
export function getDraft(draftId: string): Promise<DraftDetail> {
  return requestJson(`/api/drafts/${draftId}`);
}

export function getDraftVersion(draftId: string, version: string): Promise<DraftVersion> {
  return requestJson(`/api/drafts/${draftId}/versions/${version}`);
}

// modifyDraft 已上移至「Agent 文本能力」节，统一走 /api/agent/execute

/** 保存草稿新版本（201；同秒重复保存后端返回 409） */
export function saveDraftVersion(draftId: string, content: string): Promise<DraftVersion> {
  return requestJson(`/api/drafts/${draftId}/versions`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}

/** 还原草稿到历史版本（只改 current_version 指向，版本不可变） */
export function restoreDraftVersion(draftId: string, version: string): Promise<Draft> {
  return requestJson(`/api/drafts/${draftId}/restore`, {
    method: 'POST',
    body: JSON.stringify({ version }),
  });
}

// ── 安全检查 ──
export function checkDraftContent(content: string): Promise<SecurityCheckResult> {
  return requestJson('/api/drafts/check', {
    method: 'POST',
    body: JSON.stringify({ content }),
  });
}
