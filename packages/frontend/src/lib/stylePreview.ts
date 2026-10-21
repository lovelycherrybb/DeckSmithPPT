/**
 * 视觉风格的「悬停预览」外观表与拼装函数。
 *
 * 用途：在「生成演示文件」弹窗里，鼠标悬停到某个视觉风格上时，把整个弹窗换成那个风格的样子，
 * 让人在提交之前就能感到那个风格的脾气。悬停只是预览，不改动已选中的值。
 *
 * 为什么只覆盖 CSS 变量、不动 antd 主题令牌：globals.css 里弹窗与表单控件（含输入框、下拉框、
 * 分隔线、标签）的颜色和圆角全部是 `var(--xxx) !important`，所以只要把变量注到 `.ant-modal-content`
 * 这一个元素上，整弹窗（含其中的 antd 控件）就都跟着变，不需要再套一层 ConfigProvider。
 *
 * 一处有意的例外：下拉浮层是挂在 body 下的门户节点，不在弹窗子树内，因此它**不跟着换**——
 * 你正在挑的那张列表保持应用原主题，只有弹窗本体变风格。
 *
 * 字体只用 macOS 与 Windows 都自带的族（Helvetica/PingFang/雅黑、等宽族、楷体/宋体）。
 * 产品是离线交付到客户机的，禁止引外部字体与网络图片，否则到没网的机器上会整片回落。
 *
 * 底纹一律纯 CSS。杂志风曾试过用上游随包的那五张主题背景图，实测不行，三条原因：那五张是给
 * 「截图合成衬底」用的，边缘自带暗角与投影，铺满弹窗会在角落出现脏影；它们是 16:9 横帧，塞进
 * 接近正方形的弹窗会被横向切掉约四成，横构图的疏密节奏正好丢掉；而真实效果的灵魂是一块全屏
 * WebGL 流体画布加幻灯片上层半透明带模糊的遮罩——静态图没有流动感，且「只糊背景不糊内容」
 * 这件事 CSS 在自己的背景层上做不到。所以改回用几团大半径柔光渐变近似那个色向，色值仍取上游
 * 该主题自己的 ink-rgb，不另发明颜色。
 *
 * 外观依据取自 docs/contracts/style-guide.md 的「特点」列与上游技能的主题文件，不另发明说法。
 * 数据本体在 stylePreview.json（纯数据，便于契约检查闸直接读）。
 */
import type { CSSProperties } from 'react';
import raw from './stylePreview.json';

export interface StylePreview {
  /** 暗底还是亮底：决定文字与控件的明暗取向 */
  dark: boolean;
  /**
   * 整弹窗字体族。可选——「主题色」类条目只换配色不换字体（上游规则：主题只管颜色），
   * 缺省就不碰字体。
   */
  fontFamily?: string;
  /** 覆盖到弹窗内容元素上的 CSS 变量集合 */
  cssVars: Record<string, string>;
  /**
   * 少数风格需要变量够不着的手法（厚边框、硬投影、玻璃模糊）。
   * 只允许挂在弹窗内容元素上的属性； globals.css 里带感叹号强制的项（如背景、圆角）
   * 一律走 cssVars，不放这里，否则会被那条强制规则盖掉。
   */
  contentStyle?: Record<string, string>;
}

export const STYLE_PREVIEWS = raw as Record<string, StylePreview>;

/** 取某枚举值的外观条目；没配条目就返回 null（调用方据此不切换，不做半吊子回落） */
export function stylePreviewFor(value: unknown): StylePreview | null {
  if (typeof value !== 'string') return null;
  return STYLE_PREVIEWS[value] ?? null;
}

/**
 * 拼成 antd Modal 的 styles 入参。只给 content 挂变量与字体，
 * header/body 由 globals.css 的 var() 规则从 content 继承下来。
 */
export function stylePreviewModalStyles(
  preview: StylePreview | null
): Record<string, CSSProperties> | undefined {
  if (!preview) return undefined;
  // CSS 自定义属性不在 React.CSSProperties 的键类型里，这里按 CSSProperties 收口
  const content = {
    ...preview.cssVars,
    ...preview.contentStyle,
    ...(preview.fontFamily ? { fontFamily: preview.fontFamily } : {}),
    transition: 'background-color 140ms ease, color 140ms ease, border-color 140ms ease',
  } as unknown as CSSProperties;
  return { content };
}
