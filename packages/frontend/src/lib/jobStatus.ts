/**
 * 任务状态判据的唯一真源（前端侧）。
 *
 * 三组集合与后端 `packages/backend/src/app/service/jobs.py` 的同名常量一一对应，
 * 值域来自 `docs/contracts/openapi.yaml` 的 JobStatus。
 *
 * 为什么必须集中在这里：引入 queued 之前，全站用 `status !== 'running'` 表达
 * "已结束"，那是等价的；引入排队态之后它就错了 —— 排队中的任务会被当成已结束的
 * 分支渲染（显示消耗、给下载入口、不转圈、甚至整条不显示）。这类判断散在 9 处，
 * 逐处记忆不可靠，只能收成一个具名函数。
 *
 * 契约测试 tests/contract/job_status_set_test.py 机器比对这三组与契约枚举完全一致。
 */
import type { JobStatus } from '@/lib/api';

/** 未结束：还在排队或正在生成。判"要不要继续轮询、要不要显示进度"用它。 */
export const PENDING_STATUSES: readonly JobStatus[] = ['queued', 'running'];

/** 终态，不可逆。判"消耗/下载/成品页墙这类事后信息该不该出现"用它。 */
export const TERMINAL_STATUSES: readonly JobStatus[] = [
  'completed',
  'failed',
  'timeout',
  'cancelled',
];

/** 可重试 = 未成功的终态。completed 不在内（已成功的不该有二次生成的入口）。 */
export const RETRYABLE_STATUSES: readonly JobStatus[] = ['failed', 'timeout', 'cancelled'];

type MaybeStatus = JobStatus | string | null | undefined;

const has = (set: readonly JobStatus[], s: MaybeStatus) =>
  !!s && (set as readonly string[]).includes(s);

export const isPending = (s: MaybeStatus) => has(PENDING_STATUSES, s);
export const isTerminal = (s: MaybeStatus) => has(TERMINAL_STATUSES, s);
export const isRetryable = (s: MaybeStatus) => has(RETRYABLE_STATUSES, s);

/** 正在生成（不含排队中）。专给"只有真在跑才该出现的动效"用，如页墙末尾的转圈格。
 *  别误用 isPending —— 排队中的任务此刻没有任何东西在产出。 */
export const isGenerating = (s: MaybeStatus) => s === 'running';

/** 该引擎能否中途停止生成（能力位派生，不按引擎名硬判） */
export const engineCancellable = (capabilities?: string[] | null) =>
  !!capabilities && capabilities.includes('cancel');

/** 该引擎的产物能否逐页修改（能力位派生，不按引擎名硬判）——决定是否显示「修改」入口 */
export const engineEditable = (capabilities?: string[] | null) =>
  !!capabilities && capabilities.includes('svg_edit');

/**
 * 排队位置的中文表述（兜底用）。
 * 后端给的是"读取时现算的派生值，仅供参考"（契约 Job.queue_position），
 * 所以文案不写成承诺；队首（0 个在前）说"即将开始"，不说"前方还有 0 个"。
 * 措辞与后端 service.jobs._queued_status_detail 一字不差（契约：界面与后端不得两套说法）。
 *
 * 注意：这句话**不知道并发闸有没有空位**，所以队首可能过于乐观。
 * 展示排队信息请优先用 queueDetailLabel()，它取的是后端写好的完整句子。
 */
export function queuePositionLabel(position?: number | null): string {
  if (position == null) return '排队中';
  if (position <= 0) return '即将开始';
  return `前方还有 ${position} 个任务`;
}

/**
 * 排队标签文案：取后端 status_detail 里的位序部分，取不到才退回 queuePositionLabel。
 *
 * 为什么不在前端重算第二遍：队首到底是"即将开始"还是"等待空位（N 个正在生成）",
 * 取决于并发闸的占用情况——running 数与闸门值都在后端。前端要自己判就得再拉一次
 * /api/settings/capacity，于是同一句话有了两个作者，迟早漂移。后端既然已经把
 * 完整句子写进 status_detail（契约 Job.status_detail 的 queued 语义），这里就
 * 只负责去掉与旁边那个「排队中」状态标签重复的前缀。
 */
export function queueDetailLabel(
  statusDetail?: string | null,
  position?: number | null,
): string {
  const tail = (statusDetail || '').split(' · ')[1]?.trim();
  return tail || queuePositionLabel(position);
}
