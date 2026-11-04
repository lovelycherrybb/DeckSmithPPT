/**
 * 引擎参数中文文案与动态渲染辅助。
 *
 * 文案来源：docs/contracts/style-guide.md（ppt-master 引擎参数指南）。
 * 换引擎时同步此表；不同引擎参数集不同，故一律按引擎 params_schema 实时渲染，不写死字段。
 */
import type { EngineInfo } from '@/lib/api';

export const PARAM_LABELS: Record<string, string> = {
  style: '视觉风格',
  mode: '内容结构',
  format: '输出用途',
  notes: '演讲备注',
  animation: '动画',
  content_style: '内容类型',
};

/**
 * 选项中文名的来源已统一到契约 `x-value-labels`（AGENTS 规则 9：契约是唯一事实来源）。
 *
 * 原先这里另存一份 style/format 表，改契约界面不变——两份名字早晚漂移，
 * 且后端拼「暂不支持」句子时只能回落英文原值（用户会看到 swiss-minimal、ppt169）。
 * 2026-11-03 文案改造时把 18 个风格名与 8 个尺寸名收进 docs/contracts/engine.schema.json，
 * 本表清空，只留作契约漏标时的兜底位置；tests/contract/engine_param_wiring_test.py
 * 会检查每个枚举参数都拿得到中文名，漏标即判红，不靠这张表兜住。
 */
export const PARAM_ENUM_LABELS: Record<string, Record<string, string>> = {};

/**
 * 引擎参数在契约里的声明（本文件只用到这些字段）。
 *
 * **一份说明只干一件事**：`description` 给人看，选项中文名在 `x-value-labels`，
 * 条件显隐在 `x-when`。以前三件事全挤在 description 里，界面上就会把
 * 「中文说明见 docs/contracts/style-guide.md」这种开发者指路原样抛给用户
 * （2026-11-02 龚成指出）。
 */
export interface ParamSpec {
  type?: string;
  enum?: unknown[];
  description?: string;
  'x-hidden'?: boolean;
  'x-group'?: string;
  'x-section'?: string;
  'x-wide'?: boolean;
  'x-presets'?: unknown;
  'x-when'?: Record<string, string[]>;
  'x-value-labels'?: Record<string, string>;
  /** 整项当前兑现不了的原因（中文，给用户看）。见 ADR-033 */
  'x-unavailable'?: string;
  /** 只有其中某几个取值兑现不了：取值 → 中文原因。见 ADR-033 */
  'x-unavailable-values'?: Record<string, string>;
  items?: { enum?: unknown[] };
}

/** 参数名中文标签：固定表 → 描述去掉括号注释后按句/冒号截断 → 原始 key */
export function paramLabel(key: string, spec?: ParamSpec): string {
  if (PARAM_LABELS[key]) return PARAM_LABELS[key];
  const desc = spec?.description || '';
  // 去掉括号说明（如目标页数里的「粗略参考：15 分钟约 10 页」），否则会截出半截标签
  const cleaned = desc
    .replace(/（[^）]*）/g, '')
    .split('。')[0]
    .split('：')[0]
    .trim();
  return cleaned || key;
}

/**
 * 参数值中文标签：契约 x-value-labels → 前端旧文案表（视觉风格那 18 个还在它里面）→ 原样。
 * 布尔转开启/关闭。
 */
export function paramValueLabel(key: string, value: unknown, spec?: ParamSpec): string {
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  const text = String(value);
  return (
    spec?.['x-value-labels']?.[text] ?? PARAM_ENUM_LABELS[key]?.[text] ?? text
  );
}

/**
 * 按引擎 params_schema 实时渲染某任务实际提交的参数。
 * 只展示任务真正带了的 engine_params；schema 里有的键用其描述兜底出中文名。
 */
export function engineParamEntries(
  engine: EngineInfo | undefined,
  params: Record<string, unknown> | undefined | null
): Array<{ key: string; label: string; value: string }> {
  if (!params || typeof params !== 'object') return [];
  const props = (engine?.params_schema?.properties || {}) as Record<string, ParamSpec>;
  return Object.entries(params).map(([key, value]) => ({
    key,
    label: paramLabel(key, props[key]),
    value: paramValueLabel(key, value, props[key]),
  }));
}

/**
 * 参数条件依赖：契约 `x-when` 写明"依赖哪个参数、它取哪些值时本项才成立"，
 * 如 `{"format": ["ppt169","ppt43"]}`——命中任一值即成立，多个键之间是"且"。
 * 以前这条藏在 description 的「仅 X=Y 时有效」里，人话和机器约定挤在同一字段，
 * 界面上就把开发者指路抛给了用户。
 */
export function dependencySatisfied(
  spec: ParamSpec | undefined,
  current: Record<string, unknown>
): boolean {
  const when = spec?.['x-when'];
  if (!when) return true;
  return Object.entries(when).every(([dependsOn, equals]) =>
    equals.map(String).includes(String(current[dependsOn] ?? ''))
  );
}

/** 参数分组：契约里 `x-group: "advanced"` 的进「高级」折叠区，其余进常规区 */
export function paramGroup(value: { 'x-group'?: string }): 'advanced' | 'normal' {
  return value['x-group'] === 'advanced' ? 'advanced' : 'normal';
}

/** 参数所属小节名（契约 `x-section`）；没标的归到兜底小节，绝不因漏标而不渲染 */
export const FALLBACK_SECTION = '其他设置';
export function paramSection(value: { 'x-section'?: string }): string {
  return value['x-section'] || FALLBACK_SECTION;
}

/** 是否占整行（契约 `x-wide`）——要写字的长文本项用，避免挤在三列里 */
export function paramIsWide(value: { 'x-wide'?: boolean }): boolean {
  return value['x-wide'] === true;
}

/** 按契约声明顺序把参数切成小节，顺序完全由契约决定，前端不重排 */
export function groupBySection<T extends { 'x-section'?: string }>(
  entries: Array<[string, T]>
): Array<{ title: string; items: Array<[string, T]> }> {
  const out: Array<{ title: string; items: Array<[string, T]> }> = [];
  for (const e of entries) {
    const title = paramSection(e[1]);
    const last = out[out.length - 1];
    if (last && last.title === title) last.items.push(e);
    else out.push({ title, items: [e] });
  }
  return out;
}

/** 预设句（自由文本参数的候选写法，供输入框给下拉建议） */
export function paramPresets(value: { 'x-presets'?: unknown }): string[] {
  return Array.isArray(value['x-presets'])
    ? (value['x-presets'] as unknown[]).map((v) => String(v))
    : [];
}

/** 数组型参数的候选值（如配图来源多选） */
export function paramItemEnum(value: { items?: { enum?: unknown[] } }): unknown[] {
  return value.items?.enum ?? [];
}

/** 与基础表单字段（目标页数等）重复、不该出现在「引擎参数」区的键 */
export const BASE_DUP_PARAM_KEYS = ['source', 'files', 'prompt', 'pages'];

/**
 * 参数当前兑现不了时，原因文案一律从契约读（`x-unavailable` / `x-unavailable-values`）。
 *
 * 为什么不按参数名在前端写死：本仓已定口径「前端不硬编码引擎参数、照 schema 动态渲染，
 * 改契约即改界面」，写 `key === 'narration_audio'` 等于给这条规矩开个常例
 * （ADR-033 决策 1）。
 * 为什么不用现成的 `x-hidden`：那条是「纯工程旋钮，压根不给用户看」；今天要的是相反
 * 形状——让用户看得见功能存在、但明确知道现在选不了（ADR-033 决策 2）。
 */
export function paramUnavailableReason(spec?: ParamSpec): string | undefined {
  return spec?.['x-unavailable'] || undefined;
}

/** 取值级的「暂不支持」对照表：取值 → 中文原因。没标就是空表，整项照常可选 */
export function paramUnavailableValues(spec?: ParamSpec): Record<string, string> {
  return spec?.['x-unavailable-values'] || {};
}

/** 哪些取值被标了暂不支持（后端拒绝逻辑要覆盖的就是这一组，见 ADR-033 决策 5） */
export function unavailableValueKeys(spec?: ParamSpec): string[] {
  return Object.keys(paramUnavailableValues(spec));
}
