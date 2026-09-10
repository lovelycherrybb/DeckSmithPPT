/**
 * 引擎参数中文文案与动态渲染辅助。
 *
 * 文案来源：docs/contracts/style-guide.md（ppt-master 引擎参数指南）。
 * 换引擎时同步此表；不同引擎参数集不同，故一律按引擎 params_schema 实时渲染，不写死字段。
 */
import type { EngineInfo } from '@/lib/api';

export const PARAM_LABELS: Record<string, string> = {
  style: '视觉风格',
  mode: '叙事模式',
  format: '画布尺寸',
  notes: '演讲备注',
  animation: '动画',
  content_style: '内容风格',
};

export const PARAM_ENUM_LABELS: Record<string, Record<string, string>> = {
  style: {
    'swiss-minimal': '瑞士极简风',
    'soft-rounded': '圆角柔和风',
    glassmorphism: '毛玻璃风',
    'dark-tech': '深色科技风',
    blueprint: '蓝图线稿风',
    editorial: '杂志编辑风',
    'photo-editorial': '摄影编辑风',
    'data-journalism': '数据新闻风',
    brutalist: '粗野主义',
    chalkboard: '黑板手绘风',
    'ink-notes': '墨水笔记风',
    'ink-wash': '水墨风',
    memphis: '孟菲斯风',
    'paper-cut': '剪纸风',
    'pixel-art': '像素风',
    'sketch-notes': '速写笔记风',
    'vintage-poster': '复古海报风',
    zine: '小杂志拼贴风',
  },
  mode: {
    auto: '自动',
    pyramid: '金字塔',
    narrative: '叙事',
    instructional: '教学',
    showcase: '展示',
    briefing: '简报',
  },
  format: {
    ppt169: 'PPT 16:9',
    ppt43: 'PPT 4:3',
    xiaohongshu: '小红书',
    moments: '朋友圈/IG',
    story: '竖屏故事',
    wechat: '公众号头图',
    banner: '横版横幅',
    a4: 'A4 打印',
  },
  animation: { none: '关', auto: '自动' },
};

/** 条件依赖说明片段，如「（仅 style=magazine 时有效）」——标签展示与枚举解析都要先剔除 */
const DEPENDENCY_NOTE_RE = /（[^（）]*仅\s*[a-zA-Z_][\w.]*\s*=[^（）]+?时有效[^（）]*）/g;

export function stripDependencyNote(description: string): string {
  return description.replace(DEPENDENCY_NOTE_RE, '').trim();
}

/**
 * 从描述里解析「值=中文名」枚举对照表，如
 * "视觉风格：magazine=电子杂志风，swiss=瑞士国际主义" → { magazine:'电子杂志风', swiss:'瑞士国际主义' }
 * 引擎只要在契约描述里按此约定写，前端零改动显示中文选项。
 */
export function descEnumNames(description?: string): Record<string, string> {
  if (!description) return {};
  const text = stripDependencyNote(description);
  const map: Record<string, string> = {};
  const re = /([a-zA-Z][\w-]*)=([^，,。\s）]+)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) map[m[1]] = m[2];
  return map;
}

/** 参数名中文标签：映射表 → 描述清洗（去条件说明/值列表/括号注释）→ 原始 key */
export function paramLabel(key: string, description?: string): string {
  if (PARAM_LABELS[key]) return PARAM_LABELS[key];
  if (description) {
    // 先去掉括号说明（含条件依赖与「参考：…」这类内含冒号的注释），再按句/冒号截断，
    // 否则会出现「目标页数（默认根据内容自动，参考」这种半截标签
    const cleaned = stripDependencyNote(description)
      .replace(/（[^）]*）/g, '')
      .split('。')[0]
      .split('：')[0]
      .trim();
    if (cleaned) return cleaned;
  }
  return key;
}

/** 参数值中文标签：固定映射 → 引擎描述里的「值=中文名」→ 原样；布尔转开启/关闭 */
export function paramValueLabel(key: string, value: unknown, description?: string): string {
  if (typeof value === 'boolean') return value ? '开启' : '关闭';
  return (
    PARAM_ENUM_LABELS[key]?.[String(value)] ??
    descEnumNames(description)[String(value)] ??
    String(value)
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
  const props = (engine?.params_schema?.properties || {}) as Record<
    string,
    { description?: string }
  >;
  return Object.entries(params).map(([key, value]) => ({
    key,
    label: paramLabel(key, props[key]?.description),
    value: paramValueLabel(key, value, props[key]?.description),
  }));
}

/**
 * 解析参数条件依赖：描述里写「仅 style=magazine 时有效」→ 只有 style 选中该值才渲染。
 * 通用机制：新引擎的条件参数只要在 schema 描述里按此格式声明，前端零改动跟随。
 */
export function paramDependency(
  description?: string
): { dependsOn: string; equals: string } | null {
  const m = /仅\s*([a-zA-Z_][\w.]*)\s*=\s*([^\s时]+)\s*时有效/.exec(description || '');
  return m ? { dependsOn: m[1], equals: m[2] } : null;
}

/** 与基础表单字段（目标页数等）重复、不该出现在「引擎参数」区的键 */
export const BASE_DUP_PARAM_KEYS = ['source', 'files', 'prompt', 'pages'];
