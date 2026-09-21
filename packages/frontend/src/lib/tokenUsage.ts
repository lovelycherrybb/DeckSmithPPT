/**
 * 任务 token 消耗的展示格式化（口径见 ADR-016 / 契约 TokenUsage）。
 *
 * 三条展示红线（ADR-016 决策六）：
 * 1. null/缺失 = 「未记录」，渲染成「未记录」而不是 0；
 * 2. 分项缺报项显示「未回报」，让跨网关的数字差异可见，不被误当可比；
 * 3. 数字统一以 M（百万）为单位、最多两位小数（去尾零）——多轮会话读缓存动辄数百万，
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

/**
 * 精确整数 token 数 → 「N.NNM」（除以百万、四舍五入到两位、去尾零）。
 * 真 0 显示「0M」；非 0 但不足 0.005M（约 5,000）会四舍五入成 0，此时显示「<0.01M」，
 * 避免把「确实烧了一点」误读成「没烧」。
 */
export function formatTokenM(tokens: number): string {
  const rounded = Math.round((tokens / 1_000_000) * 100) / 100;
  if (rounded === 0) return tokens === 0 ? '0M' : '<0.01M';
  return `${rounded}M`;
}

/** 列表行用的短句：「token 消耗 3.47M」；未记录时「token 消耗 未记录」 */
export function tokenUsageSummary(usage: TokenUsage | null | undefined): string {
  return `token 消耗 ${tokenUsageTotal(usage)}`;
}

/** 总数（或「未记录」），供详情/列表分别取用 */
export function tokenUsageTotal(usage: TokenUsage | null | undefined): string {
  return usage?.total_tokens == null ? UNRECORDED : formatTokenM(usage.total_tokens);
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
      value: usage[key] == null ? '未回报' : formatTokenM(usage[key] as number),
    })),
    { key: 'total_tokens', label: '合计', value: tokenUsageTotal(usage) },
  ];
}
