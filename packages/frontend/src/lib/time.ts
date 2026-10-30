/**
 * 时间展示口径（一处定死，别在各页面各写一套）。
 *
 * 规则：7 天内说"X 分钟前 / X 小时前 / X 天前"，更早的直接给日期。
 * 理由：管理列表里"3 小时前"比"2026-10-30 07:12:00"有用得多，
 * 但"400 天前"这种就不如给日期，反而省得读者心算。
 *
 * 入参是后端给的 ISO 字符串（UTC，带 +00:00），Date 会自动按本地时区显示。
 */

const DAY_MS = 24 * 60 * 60 * 1000;
/** 超过这个天数就不说"多少天前"，改给日期 */
const RELATIVE_MAX_DAYS = 7;

/**
 * @param iso 后端时间字符串；null / 空串 / 解析不出来一律返回 null，
 *            由调用方决定占位符（不许在这里编一个"看起来像"的时间）。
 */
export function humanTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const diff = Date.now() - t;
  if (diff < 0) return '刚刚'; // 时钟偏差或未来时间，不显示"负 X 小时前"
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days <= RELATIVE_MAX_DAYS) return `${days} 天前`;
  return new Date(t).toLocaleDateString('zh-CN');
}

/** 表格里空值的统一占位：不显示空白，也不显示 1970。 */
export function timeOrDash(iso: string | null | undefined, dash = '—'): string {
  return humanTime(iso) ?? dash;
}
