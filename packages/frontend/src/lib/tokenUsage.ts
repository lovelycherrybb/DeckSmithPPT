/**
 * 任务消耗的展示格式化。
 *
 * 底层口径仍是 token（见 ADR-016 / 契约 TokenUsage，后端/契约/引擎不动），
 * 但对用户一律以「积分」呈现，token 不暴露。
 *
 * 换算单点：CREDITS_DIVISOR 控制「多少 token 记作 1 积分」。
 * 当前 = 1_000_000，即 3,470,000 token 显示为「3.47 积分」（等效 1 积分 = 100 万 token）。
 * 若要真 1:1（3,470,000 token = 3,470,000 积分），把 CREDITS_DIVISOR 改成 1 即可，
 * 其余展示逻辑不变。
 *
 * 三条展示红线（ADR-016 决策六）：
 * 1. null/缺失 = 「未记录」，渲染成「未记录」而不是 0；
 * 2. 分项缺报项显示「未回报」，让跨网关的数字差异可见，不被误当可比；
 * 3. 数字统一以「积分」为单位、最多两位小数（去尾零）——多轮会话读缓存动辄数百万 token，
 *    千分位长串不好读；底层仍是精确整数，这里只是展示层换算。
 */
import type { TokenUsage } from '@/lib/api';

/** 分项定义：顺序即详情展示顺序，label 与 vc_design/03-任务 的「新增输入/写缓存/读缓存/输出」一致 */
export const TOKEN_USAGE_PARTS: ReadonlyArray<{
  key: keyof Omit<TokenUsage, 'total_tokens'>;
  label: string;
}> = [
  { key: 'input_tokens', label: '新增输入' },
  { key: 'cache_creation_input_tokens', label: '写缓存' },
  { key: 'cache_read_input_tokens', label: '读缓存' },
  { key: 'output_tokens', label: '输出' },
];

export const UNRECORDED = '未记录';

/** 积分换算分母：多少 token 记作 1 积分（暂定 100 万，见文件头说明，单点可调） */
const CREDITS_DIVISOR = 1_000_000;

/**
 * 精确整数 token 数 → 「N.NN 积分」（除以 CREDITS_DIVISOR、四舍五入到两位、去尾零）。
 * 真 0 显示「0 积分」；非 0 但不足半档（当前约 5,000 token）会四舍五入成 0，
 * 此时显示「<0.01 积分」，避免把「确实消耗了一点」误读成「没消耗」。
 */
export function formatCredits(tokens: number): string {
  const rounded = Math.round((tokens / CREDITS_DIVISOR) * 100) / 100;
  if (rounded === 0) return tokens === 0 ? '0 积分' : '<0.01 积分';
  return `${rounded} 积分`;
}

/** 列表行用的短句：「消耗 3.47 积分」；未记录时「消耗 未记录」 */
export function tokenUsageSummary(usage: TokenUsage | null | undefined): string {
  return `消耗 ${tokenUsageTotal(usage)}`;
}

/** 总数（或「未记录」），供详情/列表分别取用 */
export function tokenUsageTotal(usage: TokenUsage | null | undefined): string {
  return usage?.total_tokens == null ? UNRECORDED : formatCredits(usage.total_tokens);
}

/** 详情分项行：label + 值（缺报显示「未回报」），并附派生总数校验行 */
export function tokenUsagePartRows(
  usage: TokenUsage | null | undefined
): Array<{ key: string; label: string; value: string }> {
  if (!usage) return [];
  return [
    ...TOKEN_USAGE_PARTS.map(({ key, label }) => ({
      key,
      label,
      value: usage[key] == null ? '未回报' : formatCredits(usage[key] as number),
    })),
    { key: 'total_tokens', label: '合计', value: tokenUsageTotal(usage) },
  ];
}
