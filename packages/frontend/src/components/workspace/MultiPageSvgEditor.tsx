/**
 * 多页可拖拽 SVG 编辑器（步骤5 · 5d 增强）。
 * 与"预览"一致的整列滚动：把所有页行内挂载成一列，可滚动浏览；点任一页元素即选中，
 * 覆盖层画选择框 + 缩放手柄，可就地拖动移动 / 缩放；底部属性栏改文字/填充/字号/删除。
 *
 * 三条铁律（ADR-025，都是踩坑换来的）：
 * 1) 上游只读 + 保真重导出：编辑只改真实 SVG DOM，产出仍是逐页 SVG 字符串走原脚本重导出。
 * 2) <tspan> 等不支持 transform：拖拽落到最近的、支持 transform 的祖先（<text>/<g>）。
 * 3) React 重渲染会整棵替换注入子树 → 绝不跨帧持有节点引用：每帧按"页号 + 结构路径"从实时
 *    DOM 重新解析目标再改；覆盖层是 React 渲染的独立层（不受注入子树替换影响），拖拽中途用 ref
 *    命令式刷新，松手再 commitPage 落 html+草稿。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, InputNumber, Space, Spin, Typography } from 'antd';
import { Trash2 } from 'lucide-react';

const { Text } = Typography;

interface PageRef { index: number; name: string; url: string }
interface Box { x: number; y: number; w: number; h: number }
interface Props {
  pages: PageRef[];
  draft: Record<number, { content: string }>;
  onEdit: (index: number, name: string, content: string) => void;
}

const GRAPHICS = new Set([
  'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'path',
  'text', 'tspan', 'image', 'g', 'use', 'foreignObject',
]);
// Chromium 里不支持 transform 的节点：拖拽位移要上提到最近的、支持 transform 的祖先。
const NO_TRANSFORM = new Set(['tspan', 'textpath', 'ref']);
// 本切片支持"几何缩放"的类型（导出最干净）；path/g 等复杂图形暂只支持移动。
const RESIZABLE = new Set(['rect', 'image', 'circle', 'ellipse', 'text', 'tspan']);
const HANDLES = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'] as const;
type HandleId = typeof HANDLES[number];
const CURSOR: Record<HandleId, string> = {
  nw: 'nwse-resize', se: 'nwse-resize', ne: 'nesw-resize', sw: 'nesw-resize',
  n: 'ns-resize', s: 'ns-resize', e: 'ew-resize', w: 'ew-resize',
};
const HS = 9; // 手柄边长（用户单位）

function parseTranslate(t: string | null): { x: number; y: number; rest: string } {
  const m = /translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)/.exec(t || '');
  const rest = (t || '').replace(/translate\([^)]*\)\s*/, '');
  return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]), rest } : { x: 0, y: 0, rest };
}
// 解析 translate + rotate + 其余（scale/mirror 等），供旋转手柄在保留既有位移/缩放下改角度。
function parseFullTransform(t: string | null): { tx: number; ty: number; rot: number; rest: string } {
  const s = t || '';
  const tr = /translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)/.exec(s);
  const ro = /rotate\(\s*(-?[\d.]+)/.exec(s);
  const rest = s.replace(/translate\([^)]*\)/, '').replace(/rotate\([^)]*\)/, '').trim();
  return { tx: tr ? parseFloat(tr[1]) : 0, ty: tr ? parseFloat(tr[2]) : 0, rot: ro ? parseFloat(ro[1]) : 0, rest };
}
// 从 root 到 node 的"元素子节点下标路径"，用于重渲染后按同一结构重新定位到等价节点。
function pathFrom(root: Element, node: Element): number[] {
  const p: number[] = [];
  let cur: Element | null = node;
  while (cur && cur !== root) {
    const parent = cur.parentElement;
    if (!parent) return [];
    p.unshift(Array.prototype.indexOf.call(parent.children, cur));
    cur = parent;
  }
  return cur === root ? p : [];
}
function resolvePath(root: Element, p: number[]): Element | null {
  let cur: Element | null = root;
  for (const i of p) { cur = cur ? cur.children[i] ?? null : null; if (!cur) return null; }
  return cur;
}
function sanitize(svgText: string): string {
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  const root = doc.querySelector('svg');
  if (!root) return '';
  root.querySelectorAll('script').forEach((s) => s.remove());
  // 只剥脚本，保留原始 width/height：导出需有限 px 值；显示缩放交给 .rv-fit > svg 的 CSS。
  return root.outerHTML;
}
// 屏幕坐标 → 该页 SVG 用户坐标（精确，兼容响应式缩放与任意祖先 transform）。
function toUser(svg: SVGSVGElement, cx: number, cy: number): { x: number; y: number } {
  const m = svg.getScreenCTM();
  if (!m) return { x: cx, y: cy };
  const pt = svg.createSVGPoint(); pt.x = cx; pt.y = cy;
  const p = pt.matrixTransform(m.inverse());
  return { x: p.x, y: p.y };
}
// 元素在"该页 SVG 用户坐标"下的外接框（覆盖层定位用，与祖先缩放/镜像无关地正确）。
function userBBox(el: Element, svg: SVGSVGElement): Box {
  const r = el.getBoundingClientRect();
  const a = toUser(svg, r.left, r.top);
  const b = toUser(svg, r.right, r.bottom);
  return { x: a.x, y: a.y, w: b.x - a.x, h: b.y - a.y };
}
function handlePos(id: HandleId, bb: Box): { x: number; y: number } {
  const cx = bb.x + bb.w / 2, cy = bb.y + bb.h / 2;
  const map: Record<HandleId, [number, number]> = {
    nw: [bb.x, bb.y], n: [cx, bb.y], ne: [bb.x + bb.w, bb.y], e: [bb.x + bb.w, cy],
    se: [bb.x + bb.w, bb.y + bb.h], s: [cx, bb.y + bb.h], sw: [bb.x, bb.y + bb.h], w: [bb.x, cy],
  };
  const [x, y] = map[id]; return { x, y };
}
// 按住 Shift：等比缩放。角点沿"锚点→原角点"的对角线投影取统一比例（角点不会自由跑偏），
// 锚定在对侧角/边（对侧不动，邻边居中）。
function constrainUniform(orig: Box, nb: Box, handle: HandleId): Box {
  let s: number;
  if (handle.length === 2) { // 角手柄
    const ax = handle.includes('w') ? orig.x + orig.w : orig.x;
    const ay = handle.includes('n') ? orig.y + orig.h : orig.y;
    const c0x = handle.includes('w') ? orig.x : orig.x + orig.w;
    const c0y = handle.includes('n') ? orig.y : orig.y + orig.h;
    const c1x = handle.includes('w') ? nb.x : nb.x + nb.w;
    const c1y = handle.includes('n') ? nb.y : nb.y + nb.h;
    const vx = c0x - ax, vy = c0y - ay, wx = c1x - ax, wy = c1y - ay;
    s = (vx * vx + vy * vy) ? (wx * vx + wy * vy) / (vx * vx + vy * vy) : 1;
  } else if (handle === 'e' || handle === 'w') {
    s = nb.w / orig.w;
  } else {
    s = nb.h / orig.h;
  }
  s = Math.max(s, 0.01);
  const w = orig.w * s, h = orig.h * s;
  const x = handle.includes('w') ? orig.x + orig.w - w : handle.includes('e') ? orig.x : orig.x + (orig.w - w) / 2;
  const y = handle.includes('n') ? orig.y + orig.h - h : handle.includes('s') ? orig.y : orig.y + (orig.h - h) / 2;
  return { x, y, w, h };
}
// 缩放手柄的"锚点"= 与拖拽角相对的那个角/边中点（缩放时它保持不动）。
function anchorCorner(bb: Box, handle: HandleId): { x: number; y: number } {
  return {
    x: handle.includes('w') ? bb.x + bb.w : handle.includes('e') ? bb.x : bb.x + bb.w / 2,
    y: handle.includes('n') ? bb.y + bb.h : handle.includes('s') ? bb.y : bb.y + bb.h / 2,
  };
}
// 元素当前 rotate 角度（度）。
function elRotDeg(el: Element): number {
  const m = /rotate\(\s*(-?[\d.]+)/.exec(el.getAttribute('transform') || '');
  return m ? parseFloat(m[1]) : 0;
}
// 元素"局部外接框"的 4 个角，映射到该页 viewBox 坐标（含自身 rotate）→ 得到随元素一起转的定向四边形。
function orientedCorners(el: Element, svg: SVGSVGElement): Array<[number, number]> {
  const gb = (el as unknown as SVGGraphicsElement).getBBox();
  const em = (el as unknown as SVGGraphicsElement).getScreenCTM();
  const sm = svg.getScreenCTM();
  if (!em || !sm) { const b = userBBox(el, svg); return [[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]]; }
  const toVB = sm.inverse().multiply(em); // 元素局部 → viewBox
  const pts: Array<[number, number]> = [[gb.x, gb.y], [gb.x + gb.width, gb.y], [gb.x + gb.width, gb.y + gb.height], [gb.x, gb.y + gb.height]];
  return pts.map(([x, y]) => { const p = svg.createSVGPoint(); p.x = x; p.y = y; const q = p.matrixTransform(toVB); return [q.x, q.y] as [number, number]; });
}
// 定向四边形 → 8 个缩放手柄坐标（角点 + 边中点）。corners 顺序：tl,tr,br,bl。
function quadHandlePos(id: HandleId, c: Array<[number, number]>): [number, number] {
  const mid = (a: [number, number], b: [number, number]): [number, number] => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const map: Record<HandleId, [number, number]> = {
    nw: c[0], ne: c[1], se: c[2], sw: c[3],
    n: mid(c[0], c[1]), e: mid(c[1], c[2]), s: mid(c[2], c[3]), w: mid(c[3], c[0]),
  };
  return map[id];
}
// 把视口(鼠标)位移按元素旋转角反旋回元素局部轴，供旋转后仍"跟手"地移动/缩放。
function unrotate(dx: number, dy: number, deg: number): [number, number] {
  if (!deg) return [dx, dy];
  const r = -deg * Math.PI / 180, cs = Math.cos(r), sn = Math.sin(r);
  return [dx * cs - dy * sn, dx * sn + dy * cs];
}
// 变换目标：点中 <tspan> 时，移动/缩放/旋转作用于其所在 <text>。
function xformTarget(el: Element): Element {
  return el.tagName === 'tspan' ? (el.closest('text') || el) : el;
}
// 定向四边形的顶边中点 + 旋转手柄位置（沿顶边外法线外推 22）。
function quadRot(c: Array<[number, number]>): { mid: [number, number]; rot: [number, number] } {
  const mid: [number, number] = [(c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2];
  const cx = (c[0][0] + c[1][0] + c[2][0] + c[3][0]) / 4, cy = (c[0][1] + c[1][1] + c[2][1] + c[3][1]) / 4;
  let nx = -(c[1][1] - c[0][1]), ny = c[1][0] - c[0][0];
  const nl = Math.hypot(nx, ny) || 1; nx /= nl; ny /= nl;
  if ((mid[0] + nx - cx) * (mid[0] - cx) + (mid[1] + ny - cy) * (mid[1] - cy) < 0) { nx = -nx; ny = -ny; }
  return { mid, rot: [mid[0] + nx * 22, mid[1] + ny * 22] };
}

const SVG_NS = 'http://www.w3.org/2000/svg';
// 把 <text> 读成多行文本：有 <tspan> 就按行拼（deck 的多行就是每行一个带 dy 的 tspan），否则整段。
function textToLines(el: Element): string {
  const ts = [...el.children].filter((c) => c.tagName === 'tspan');
  return ts.length ? ts.map((t) => t.textContent || '').join('\n') : (el.textContent || '');
}
// 把多行文本写回 <text>：清掉子节点，按行重建 <tspan x dy>（沿用原行距），保持可被导出识别。
function linesToText(el: Element, lines: string[]): void {
  const x = el.getAttribute('x') || '0';
  const fs = parseFloat(el.getAttribute('font-size') || window.getComputedStyle(el).fontSize || '16');
  const existing = [...el.children].filter((c) => c.tagName === 'tspan');
  let lh = Math.round(fs * 1.2);
  for (const t of existing) { const d = parseFloat(t.getAttribute('dy') || '0'); if (d) { lh = d; break; } }
  while (el.firstChild) el.removeChild(el.firstChild);
  lines.forEach((ln, i) => {
    const ts = document.createElementNS(SVG_NS, 'tspan');
    ts.setAttribute('x', x);
    ts.setAttribute('dy', i === 0 ? '0' : String(lh));
    ts.textContent = ln;
    el.appendChild(ts);
  });
}

export default function MultiPageSvgEditor({ pages, draft, onEdit }: Props) {
  const [html, setHtml] = useState<Record<number, string>>({});
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<SVGSVGElement | null>(null);
  const selRef = useRef<Element | null>(null);
  const selPageRef = useRef<number>(-1);
  const selPathRef = useRef<number[]>([]);
  const lastClickRef = useRef<{ key: string; time: number }>({ key: '', time: 0 });
  const [sel, setSel] = useState<{ tag: string; pageIndex: number } | null>(null);
  const [selVB, setSelVB] = useState('');
  const [selPAR, setSelPAR] = useState('xMidYMid meet');
  const [selBox, setSelBox] = useState<Box | null>(null);
  const [selQuad, setSelQuad] = useState<Array<[number, number]> | null>(null); // 随元素旋转的选择框四角（viewBox 坐标）
  // 双击就地改字：记住目标节点(页号+路径)、屏幕位置/初值，以及原文样式（做成"直接改这段字"的感觉）
  const [editing, setEditing] = useState<{
    pageIndex: number; path: number[]; x: number; y: number; w: number; h: number; value: string;
    fontSize: number; fontFamily: string; color: string; fontWeight: string; anchor: string;
  } | null>(null);
  const editingRef = useRef<typeof editing>(null);
  editingRef.current = editing;
  const [, force] = useState(0);
  const rerender = useCallback(() => force((n) => n + 1), []);

  const pageKey = pages.map((p) => `${p.index}:${p.url}`).join('|');

  // 载入每页：草稿优先，否则拉原始 SVG；仅在换版本（pages 变）时重挂
  useEffect(() => {
    let alive = true;
    setLoading(true);
    const acc: Record<number, string> = {};
    Promise.all(pages.map(async (p) => {
      const src = draft[p.index]?.content ?? (await fetch(p.url).then((r) => r.text()).catch(() => ''));
      acc[p.index] = sanitize(src || '');
    })).then(() => {
      if (!alive) return;
      setHtml(acc); selRef.current = null; setSel(null); setLoading(false);
    });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pageKey]);

  const liveSvg = (pageIndex: number): SVGSVGElement | null => {
    if (pageIndex < 0) return null;
    const lb = containerRef.current?.querySelector(`[data-page-index="${pageIndex}"]`);
    return (lb?.querySelector('svg') as SVGSVGElement | null) ?? null;
  };
  // 选中节点的"实时等价节点"（重渲染后按路径重新解析，绝不攥旧引用）
  const liveSel = (): Element | null => {
    const svg = liveSvg(selPageRef.current);
    return (svg && selPathRef.current.length ? resolvePath(svg, selPathRef.current) : null) || selRef.current;
  };
  // 把某页当前 SVG 落回 html 状态 + 回传草稿（编辑期唯一"提交"口径，避免重渲染回弹）
  const commitPage = useCallback((pageIndex: number) => {
    const lb = pageIndex >= 0 ? containerRef.current?.querySelector(`[data-page-index="${pageIndex}"]`) : null;
    const ls = lb?.querySelector('svg');
    if (!ls) return;
    const content = ls.outerHTML;
    setHtml((h) => ({ ...h, [pageIndex]: content }));
    onEdit(pageIndex, (lb as HTMLElement).dataset.pageName || '', content);
  }, [onEdit]);

  // 命令式刷新覆盖层框 + 手柄（拖拽中途用，避免逐帧 setState）——随元素旋转画定向四边形。
  const paintOverlay = () => {
    const svg = liveSvg(selPageRef.current);
    const node = liveSel();
    const ov = overlayRef.current;
    if (!svg || !node || !ov) return;
    const c = orientedCorners(xformTarget(node), svg);
    const box = ov.querySelector('[data-box]');
    if (box) box.setAttribute('points', c.map((p) => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' '));
    ov.querySelectorAll<SVGRectElement>('[data-h]').forEach((h) => {
      const p = quadHandlePos(h.dataset.h as HandleId, c);
      h.setAttribute('x', String(p[0] - HS / 2)); h.setAttribute('y', String(p[1] - HS / 2));
    });
    // 旋转手柄：顶边中点沿外法线外推
    const { mid, rot: rp } = quadRot(c);
    const rot = ov.querySelector('[data-rot]');
    const rline = ov.querySelector('[data-rotline]');
    if (rot) { rot.setAttribute('cx', String(rp[0])); rot.setAttribute('cy', String(rp[1])); }
    if (rline) { rline.setAttribute('x1', String(mid[0])); rline.setAttribute('y1', String(mid[1])); rline.setAttribute('x2', String(rp[0])); rline.setAttribute('y2', String(rp[1])); }
  };

  const select = (el: Element) => {
    selRef.current?.removeAttribute('data-rv-sel');
    const block = el.closest('[data-page-index]');
    const svg = block?.querySelector('svg') as SVGSVGElement | null;
    selPageRef.current = block ? Number((block as HTMLElement).dataset.pageIndex) : -1;
    selPathRef.current = svg ? pathFrom(svg, el) : [];
    selRef.current = el;
    el.setAttribute('data-rv-sel', '1');
    if (svg) { setSelVB(svg.getAttribute('viewBox') || ''); setSelPAR(svg.getAttribute('preserveAspectRatio') || 'xMidYMid meet'); }
    setSelBox(svg ? userBBox(el, svg) : null);
    setSelQuad(svg ? orientedCorners(xformTarget(el), svg) : null);
    setSel({ tag: el.tagName, pageIndex: selPageRef.current });
  };
  const clearSel = () => {
    selRef.current?.removeAttribute('data-rv-sel'); selRef.current = null;
    selPageRef.current = -1; selPathRef.current = []; setSel(null); setSelBox(null);
  };

  // 撤销/重做：每次改动前压入该页"改前"的整页 SVG。
  type Snap = { pageIndex: number; name: string; content: string };
  const undoStackRef = useRef<Snap[]>([]);
  const redoStackRef = useRef<Snap[]>([]);
  const clipRef = useRef<{ pageIndex: number; outer: string } | null>(null);
  const pageSnap = (pageIndex: number): Snap | null => {
    const lb = pageIndex >= 0 ? containerRef.current?.querySelector(`[data-page-index="${pageIndex}"]`) : null;
    const ls = lb?.querySelector('svg');
    return ls ? { pageIndex, name: (lb as HTMLElement).dataset.pageName || '', content: ls.outerHTML } : null;
  };
  const applySnap = (s: Snap) => { setHtml((h) => ({ ...h, [s.pageIndex]: s.content })); onEdit(s.pageIndex, s.name, s.content); };
  const pushUndo = (pageIndex: number) => {
    const s = pageSnap(pageIndex); if (!s) return;
    undoStackRef.current.push(s); if (undoStackRef.current.length > 80) undoStackRef.current.shift();
    redoStackRef.current = [];
  };
  const undo = () => { const s = undoStackRef.current.pop(); if (!s) return; const cur = pageSnap(s.pageIndex); if (cur) redoStackRef.current.push(cur); applySnap(s); clearSel(); };
  const redo = () => { const s = redoStackRef.current.pop(); if (!s) return; const cur = pageSnap(s.pageIndex); if (cur) undoStackRef.current.push(cur); applySnap(s); clearSel(); };

  // 对象操作：删除 / 再制 / 复制·剪切·粘贴 / 方向键微调 / 层级
  const transformTarget = (n: Element): Element => (n.tagName === 'tspan' ? (n.closest('text') || n) : n);
  const deleteSel = () => { const n = liveSel(); if (!n) return; const pi = selPageRef.current; pushUndo(pi); n.remove(); clearSel(); commitPage(pi); };
  const cloneSel = (off: number) => {
    const n = liveSel(); if (!n || !n.parentElement) return;
    const pi = selPageRef.current; pushUndo(pi);
    const c = n.cloneNode(true) as Element; c.removeAttribute('data-rv-sel');
    const t = parseTranslate(n.getAttribute('transform'));
    c.setAttribute('transform', `translate(${(t.x + off).toFixed(1)} ${(t.y + off).toFixed(1)}) ${t.rest}`.trim());
    n.parentElement.insertBefore(c, n.nextSibling);
    commitPage(pi); rerender();
  };
  const copySel = () => { const n = liveSel(); if (n) clipRef.current = { pageIndex: selPageRef.current, outer: n.outerHTML }; };
  const cutSel = () => { copySel(); deleteSel(); };
  const pasteSel = () => {
    const clip = clipRef.current; if (!clip) return;
    const svg = liveSvg(clip.pageIndex); if (!svg) return;
    pushUndo(clip.pageIndex);
    const parsed = new DOMParser().parseFromString(`<svg xmlns="${SVG_NS}">${clip.outer}</svg>`, 'image/svg+xml').documentElement.firstElementChild;
    if (!parsed) return;
    const c = document.importNode(parsed, true);
    const t = parseTranslate(c.getAttribute('transform'));
    c.setAttribute('transform', `translate(${(t.x + 16).toFixed(1)} ${(t.y + 16).toFixed(1)}) ${t.rest}`.trim());
    svg.appendChild(c); commitPage(clip.pageIndex); rerender();
  };
  const nudge = (dx: number, dy: number) => {
    const n = liveSel(); if (!n) return;
    const svg = liveSvg(selPageRef.current); if (!svg) return;
    const target = transformTarget(n); pushUndo(selPageRef.current);
    const t = parseTranslate(target.getAttribute('transform'));
    target.setAttribute('transform', `translate(${(t.x + dx).toFixed(1)} ${(t.y + dy).toFixed(1)}) ${t.rest}`.trim());
    commitPage(selPageRef.current); setSelBox(userBBox(target, svg)); rerender();
  };
  const zOrder = (front: boolean) => {
    const n = liveSel(); if (!n || !n.parentElement) return;
    const pi = selPageRef.current; pushUndo(pi);
    if (front) n.parentElement.appendChild(n); else n.parentElement.insertBefore(n, n.parentElement.firstChild);
    commitPage(pi); rerender();
  };

  const actRef = useRef({ undo, redo, deleteSel, duplicate: () => cloneSel(16), copySel, cutSel, pasteSel, nudge, front: () => zOrder(true), back: () => zOrder(false) });
  actRef.current = { undo, redo, deleteSel, duplicate: () => cloneSel(16), copySel, cutSel, pasteSel, nudge, front: () => zOrder(true), back: () => zOrder(false) };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return; // 输入框里交给浏览器原生
      const a = actRef.current; const mod = e.ctrlKey || e.metaKey; const k = e.key.toLowerCase();
      if (mod && k === 'z' && !e.shiftKey) { e.preventDefault(); a.undo(); }
      else if ((mod && k === 'z' && e.shiftKey) || (mod && k === 'y')) { e.preventDefault(); a.redo(); }
      else if (mod && k === 'd') { e.preventDefault(); a.duplicate(); }
      else if (mod && k === 'c') { a.copySel(); }
      else if (mod && k === 'x') { e.preventDefault(); a.cutSel(); }
      else if (mod && k === 'v') { e.preventDefault(); a.pasteSel(); }
      else if (mod && e.key === ']') { e.preventDefault(); a.front(); }
      else if (mod && e.key === '[') { e.preventDefault(); a.back(); }
      else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); a.deleteSel(); }
      else if (e.key.startsWith('Arrow')) {
        e.preventDefault(); const step = e.shiftKey ? 10 : 2;
        if (e.key === 'ArrowLeft') a.nudge(-step, 0); else if (e.key === 'ArrowRight') a.nudge(step, 0);
        else if (e.key === 'ArrowUp') a.nudge(0, -step); else a.nudge(0, step);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // 重渲染（含 commitPage 改 html、以及打开改字浮层）后，把高亮/覆盖层重新贴到等价节点上。
  // 依赖含 editing：打开输入框会触发重渲染替换注入子树，需在此重新贴上 data-rv-sel，
  // 让 .rv-multi.editing [data-rv-sel]{visibility:hidden} 能真正藏住原文（做出"直接编辑"感）。
  useEffect(() => {
    if (!sel) return;
    const svg = liveSvg(sel.pageIndex);
    const node = svg && selPathRef.current.length ? resolvePath(svg, selPathRef.current) : null;
    selRef.current?.removeAttribute('data-rv-sel');
    if (node) { node.setAttribute('data-rv-sel', '1'); selRef.current = node; }
    if (svg) { setSelVB(svg.getAttribute('viewBox') || ''); setSelPAR(svg.getAttribute('preserveAspectRatio') || 'xMidYMid meet'); }
    if (node && svg) { setSelBox(userBBox(node, svg)); setSelQuad(orientedCorners(xformTarget(node), svg)); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, html, editing]);

  // 把缩放后的几何写回"图形"元素（局部坐标系）。文字走 startResize 里的 scale transform 分支。
  const applyGeom = (el: Element, nb: Box, lb0: { x: number; y: number; width: number; height: number }) => {
    const tag = el.tagName;
    if (tag === 'rect' || tag === 'image') {
      el.setAttribute('x', nb.x.toFixed(1)); el.setAttribute('y', nb.y.toFixed(1));
      el.setAttribute('width', nb.w.toFixed(1)); el.setAttribute('height', nb.h.toFixed(1));
    } else if (tag === 'circle') {
      el.setAttribute('cx', (nb.x + nb.w / 2).toFixed(1)); el.setAttribute('cy', (nb.y + nb.h / 2).toFixed(1));
      el.setAttribute('r', (Math.min(nb.w, nb.h) / 2).toFixed(1));
    } else if (tag === 'ellipse') {
      el.setAttribute('cx', (nb.x + nb.w / 2).toFixed(1)); el.setAttribute('cy', (nb.y + nb.h / 2).toFixed(1));
      el.setAttribute('rx', (nb.w / 2).toFixed(1)); el.setAttribute('ry', (nb.h / 2).toFixed(1));
    }
  };

  const startResize = (e: PointerEvent, hid: HandleId) => {
    const svg = liveSvg(selPageRef.current);
    const node = liveSel();
    if (!svg || !node) return;
    const isText = node.tagName === 'text' || node.tagName === 'tspan';
    const gb = (node as unknown as SVGGraphicsElement).getBBox();
    const lb0 = { x: gb.x, y: gb.y, width: gb.width, height: gb.height };
    const RB0 = userBBox(node, svg); // viewBox 坐标当前外接框（文字等比缩放 + 锚定用）
    const f0 = parseFloat(node.getAttribute('font-size') || svg.getAttribute('font-size') || '16');
    const A0 = anchorCorner(RB0, hid);
    // 图形几何编辑：局部→视口真实缩放比（两个 getScreenCTM 比值，抵消 CSS 响应式缩放）
    const es = (node as unknown as SVGGraphicsElement).getScreenCTM?.();
    const ss = svg.getScreenCTM?.();
    const sx = es && ss && es.a && ss.a ? Math.abs(es.a / ss.a) : 1;
    const sy = es && ss && es.d && ss.d ? Math.abs(es.d / ss.d) : 1;
    const startUser = toUser(svg, e.clientX, e.clientY);

    e.preventDefault(); e.stopPropagation();
    let pushed = false;
    const move = (ev: PointerEvent) => {
      if (!pushed) { pushUndo(selPageRef.current); pushed = true; }
      const t = liveSel() || node;
      const u = toUser(svg, ev.clientX, ev.clientY);
      const ddx = u.x - startUser.x, ddy = u.y - startUser.y;
      let x = RB0.x, y = RB0.y, w = RB0.w, h = RB0.h;
      if (hid.includes('e')) w = RB0.w + ddx;
      if (hid.includes('s')) h = RB0.h + ddy;
      if (hid.includes('w')) { x = RB0.x + ddx; w = RB0.w - ddx; }
      if (hid.includes('n')) { y = RB0.y + ddy; h = RB0.h - ddy; }
      if (w < 1) w = 1; if (h < 1) h = 1;
      if (isText) {
        // 导出侧禁止对 <text> 用 scale/matrix（只允许 translate / 单个 rotate），所以文字只能"等比"缩放：
        // 按对角线投影取统一比例 → 改 font-size（相对当前），再用 translate 把锚点拉回原位。
        const nb = constrainUniform(RB0, { x, y, w, h }, hid);
        const s = Math.max(0.02, nb.w / RB0.w);
        t.setAttribute('font-size', (f0 * s).toFixed(2));
        if (t.tagName === 'text') {
          const bb = userBBox(t, svg);
          const ac = anchorCorner(bb, hid);
          const cur = parseTranslate(t.getAttribute('transform'));
          t.setAttribute('transform', `translate(${(cur.x + (A0.x - ac.x)).toFixed(1)} ${(cur.y + (A0.y - ac.y)).toFixed(1)}) ${cur.rest}`.trim());
        }
      } else {
        const ldx = ddx / sx, ldy = ddy / sy;
        let gx = lb0.x, gy = lb0.y, gw = lb0.width, gh = lb0.height;
        if (hid.includes('e')) gw = lb0.width + ldx;
        if (hid.includes('s')) gh = lb0.height + ldy;
        if (hid.includes('w')) { gx = lb0.x + ldx; gw = lb0.width - ldx; }
        if (hid.includes('n')) { gy = lb0.y + ldy; gh = lb0.height - ldy; }
        if (gw < 1) gw = 1; if (gh < 1) gh = 1;
        if (ev.shiftKey) { const c = constrainUniform({ x: lb0.x, y: lb0.y, w: lb0.width, h: lb0.height }, { x: gx, y: gy, w: gw, h: gh }, hid); gx = c.x; gy = c.y; gw = c.w; gh = c.h; }
        applyGeom(t, { x: gx, y: gy, w: gw, h: gh }, lb0);
      }
      paintOverlay();
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      if (!pushed) return;
      commitPage(selPageRef.current);
      const s = liveSvg(selPageRef.current), n = liveSel();
      if (s && n) setSelBox(userBBox(n, s));
      rerender();
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  };

  // 旋转：绕自身中心改 rotate 角度。图形保留 translate+rotate；文字只写纯 rotate（位移走 x/y，避开 text 混合 transform 被导出拒）。
  const startRotate = (e: PointerEvent) => {
    const svg0 = liveSvg(selPageRef.current);
    const raw = liveSel();
    if (!svg0 || !raw) return;
    const node = raw.tagName === 'tspan' ? (raw.closest('text') || raw) : raw;
    const isText = node.tagName === 'text';
    const rotPath = pathFrom(svg0, node);
    const bb = userBBox(node, svg0);
    const ccx = bb.x + bb.w / 2, ccy = bb.y + bb.h / 2;
    const gb = (node as unknown as SVGGraphicsElement).getBBox();
    const rcx = gb.x + gb.width / 2, rcy = gb.y + gb.height / 2;
    const tf = parseFullTransform(node.getAttribute('transform'));
    const p0 = toUser(svg0, e.clientX, e.clientY);
    const startAngle = Math.atan2(p0.y - ccy, p0.x - ccx) * 180 / Math.PI;
    e.preventDefault(); e.stopPropagation();
    let pushed = false;
    const move = (ev: PointerEvent) => {
      if (!pushed) { pushUndo(selPageRef.current); pushed = true; }
      const s = liveSvg(selPageRef.current);
      const t = (s && rotPath.length ? resolvePath(s, rotPath) : null) || node;
      const p = toUser(s || svg0, ev.clientX, ev.clientY);
      let deg = tf.rot + (Math.atan2(p.y - ccy, p.x - ccx) * 180 / Math.PI - startAngle);
      if (ev.shiftKey) deg = Math.round(deg / 15) * 15; // 按住 Shift 吸附 15°
      t.setAttribute('transform', isText
        ? `rotate(${deg.toFixed(1)} ${rcx.toFixed(1)} ${rcy.toFixed(1)})`
        : `translate(${tf.tx} ${tf.ty}) rotate(${deg.toFixed(1)} ${rcx.toFixed(1)} ${rcy.toFixed(1)}) ${tf.rest}`.trim());
      paintOverlay();
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      if (!pushed) return;
      commitPage(selPageRef.current);
      const s = liveSvg(selPageRef.current), n = liveSel();
      if (s && n) setSelBox(userBBox(n, s));
      rerender();
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  };

  const onPointerDown = (e: PointerEvent) => {
    // 正在就地改字时，点画布任意处 = 先保存这次改动（pointerdown 的 preventDefault 会挡住 input 的
    // 原生 blur，所以这里主动提交，实现"点别处自动保存"）。
    if (editingRef.current) { commitEdit(); }
    // 命中旋转手柄 → 旋转
    if ((e.target as Element).closest?.('[data-rot]')) { startRotate(e); return; }
    // 命中缩放手柄 → 走缩放；否则走选中 + 移动
    const handleEl = (e.target as Element).closest?.('[data-h]');
    if (handleEl) { startResize(e, handleEl.getAttribute('data-h') as HandleId); return; }

    let el = e.target as Element | null;
    while (el && !GRAPHICS.has(el.tagName) && !el.hasAttribute?.('data-page-index')) el = el.parentElement;
    if (!el || el.hasAttribute?.('data-page-index')) { clearSel(); return; }

    const block = el.closest('[data-page-index]');
    const pageIndex = block ? Number((block as HTMLElement).dataset.pageIndex) : -1;
    const svg = block?.querySelector('svg') as SVGSVGElement | null;
    const path = svg ? pathFrom(svg, el) : [];

    // 双击就地改字：pointerdown 里 preventDefault 会抑制原生 dblclick，所以自己按
    // "同一文字节点 400ms 内二次按下"判定，命中就弹输入框、跳过选中/移动。
    const isText = el.tagName === 'text' || el.tagName === 'tspan';
    const key = pageIndex + ':' + path.join(',');
    const now = Date.now();
    if (isText && key === lastClickRef.current.key && now - lastClickRef.current.time < 400) {
      lastClickRef.current = { key: '', time: 0 };
      const textEl = el.tagName === 'tspan' ? (el.closest('text') || el) : el; // 编辑整段文字（含多行）
      const tPath = svg ? pathFrom(svg, textEl) : [];
      const r = textEl.getBoundingClientRect();
      const cs = window.getComputedStyle(textEl);
      // 输入框是屏幕像素定位，字号要换算成"屏幕上的大小"：SVG 用户字号 × (渲染宽/视口宽)。
      const vbW = svg?.viewBox?.baseVal?.width || 0;
      const scale = vbW ? (svg!.getBoundingClientRect().width / vbW) : 1;
      const userFontSize = parseFloat(cs.fontSize || textEl.getAttribute('font-size') || svg?.getAttribute('font-size') || '16');
      const color = cs.fill && cs.fill !== 'none' ? cs.fill : (cs.color || '#111111');
      setEditing({
        pageIndex, path: tPath, x: r.left, y: r.top, w: Math.max(120, r.width + 16), h: Math.max(24, r.height + 6),
        value: textToLines(textEl), fontSize: userFontSize * scale, fontFamily: cs.fontFamily, color, fontWeight: cs.fontWeight,
        anchor: textEl.getAttribute('text-anchor') || svg?.getAttribute('text-anchor') || 'start',
      });
      e.preventDefault();
      return;
    }
    lastClickRef.current = { key, time: now };
    select(el);

    const start = { x: e.clientX, y: e.clientY };
    let dragEl: Element = el;
    while (dragEl && NO_TRANSFORM.has(dragEl.tagName)) dragEl = dragEl.parentElement as Element;
    if (!dragEl) dragEl = el;
    const isTextMove = dragEl.tagName === 'text'; // 文字用 x/y 位移，把 transform 让给 rotate（text 混合 transform 会被导出拒）
    const { x: bx, y: by, rest } = parseTranslate(dragEl.getAttribute('transform'));
    const tx0 = parseFloat(dragEl.getAttribute('x') || '0'), ty0 = parseFloat(dragEl.getAttribute('y') || '0');
    const dragPath = svg ? pathFrom(svg, dragEl) : [];
    const liveDrag = (): Element | null => { const s = liveSvg(pageIndex); return s && dragPath.length ? resolvePath(s, dragPath) : dragEl; };

    e.preventDefault();
    let pushed = false;
    const move = (ev: PointerEvent) => {
      if (!svg) return;
      if (!pushed) { pushUndo(pageIndex); pushed = true; }
      const u0 = toUser(svg, start.x, start.y), u1 = toUser(svg, ev.clientX, ev.clientY);
      const dx = u1.x - u0.x, dy = u1.y - u0.y;
      const t = liveDrag() || dragEl;
      if (isTextMove) {
        const [ldx, ldy] = unrotate(dx, dy, elRotDeg(t)); // 旋转后仍按鼠标方向移动：把视口位移反旋回文字局部轴
        t.setAttribute('x', (tx0 + ldx).toFixed(1)); t.setAttribute('y', (ty0 + ldy).toFixed(1));
        for (const c of Array.from(t.children)) if (c.tagName === 'tspan' && c.hasAttribute('x')) c.setAttribute('x', (parseFloat(c.getAttribute('x') || '0') + ldx).toFixed(1));
      } else {
        t.setAttribute('transform', `translate(${(bx + dx).toFixed(1)} ${(by + dy).toFixed(1)}) ${rest}`.trim());
      }
      paintOverlay();
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      if (!pushed) return; // 纯点击（未移动）不提交，免得把没改的页标成"已改"
      commitPage(pageIndex);
      const s = liveSvg(pageIndex), n = liveSel();
      if (s && n) setSelBox(userBBox(n, s));
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  };

  // 原生监听容器：对 dangerouslySetInnerHTML 注入的 SVG 子节点，原生 pointerdown 比合成事件稳。
  // 再兜一层 dragstart 拦截 + 容器 user-select:none，避免真鼠标在可选文字上触发原生选中/图片拖拽抢手势。
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const blockDrag = (ev: Event) => ev.preventDefault();
    el.addEventListener('pointerdown', onPointerDown);
    el.addEventListener('dragstart', blockDrag);
    return () => {
      el.removeEventListener('pointerdown', onPointerDown);
      el.removeEventListener('dragstart', blockDrag);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [html]);

  const selEl = selRef.current;
  const selT = selEl ? parseTranslate(selEl.getAttribute('transform')) : null;
  const isText = !!selEl && (selEl.tagName === 'text' || selEl.tagName === 'tspan');
  const setAttr = (k: string, v: string) => { const n = liveSel(); if (n) { pushUndo(selPageRef.current); n.setAttribute(k, v); commitPage(selPageRef.current); rerender(); } };
  const setTranslate = (axis: 'x' | 'y', v: number) => {
    const n = liveSel(); if (!n || !selT) return;
    pushUndo(selPageRef.current);
    n.setAttribute('transform', `translate(${axis === 'x' ? v : selT.x} ${axis === 'y' ? v : selT.y}) ${selT.rest}`.trim());
    commitPage(selPageRef.current); rerender();
  };
  const setText = (v: string) => { const n = liveSel(); if (n) { pushUndo(selPageRef.current); n.textContent = v; commitPage(selPageRef.current); rerender(); } };

  // 双击就地改字（检测在 onPointerDown 里做）：把输入框内容写回目标文字节点。
  const commitEdit = (val?: string) => {
    const ed = editingRef.current;
    if (!ed) return;
    editingRef.current = null; // 先清，防止 blur 再触发一次重复提交
    const svg = liveSvg(ed.pageIndex);
    const node = svg && ed.path.length ? resolvePath(svg, ed.path) : null;
    if (node) { pushUndo(ed.pageIndex); linesToText(node, (val ?? ed.value).split('\n')); commitPage(ed.pageIndex); }
    setEditing(null); rerender();
  };
  const cancelEdit = () => {
    if (!editingRef.current) return;
    editingRef.current = null;
    setEditing(null);
  };

  const canResize = !!sel && RESIZABLE.has(sel.tag);
  const canRotate = !!sel; // 文字与图形都可旋转（文字位移走 x/y、transform 只放 rotate，避开混合 transform）

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <style>{`.rv-multi{user-select:none;-webkit-user-select:none;touch-action:none} .rv-multi [data-rv-sel]{outline:2px solid #ff3b30;outline-offset:1px;cursor:move} .rv-multi.editing [data-rv-sel]{visibility:hidden} .rv-fit > svg{max-width:100%;height:auto;display:block} .rv-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;overflow:visible} .rv-overlay [data-h]{pointer-events:all;fill:#fff;stroke:#3b82f6;stroke-width:1.2} .rv-overlay [data-rot]{pointer-events:all;fill:#3b82f6;stroke:#fff;stroke-width:1.2}`}</style>
      <div ref={containerRef} className={'rv-multi' + (editing ? ' editing' : '')}
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
        {loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : pages.map((p) => (
          <div key={p.index} data-page-index={p.index} data-page-name={p.name}
            style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 16, overflow: 'hidden' }}>
            <div style={{ padding: '4px 10px', fontSize: 12, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>
              第 {pages.findIndex((x) => x.index === p.index) + 1} 页 · {p.name}{draft[p.index] ? ' ·（已改）' : ''}
            </div>
            <div style={{ position: 'relative' }}>
              <div className="rv-fit" dangerouslySetInnerHTML={{ __html: html[p.index] || '' }} />
              {sel && sel.pageIndex === p.index && selQuad && selVB && (
                <svg ref={overlayRef} className="rv-overlay" viewBox={selVB} preserveAspectRatio={selPAR}>
                  <polygon data-box points={selQuad.map((q) => `${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(' ')}
                    fill="none" stroke="#3b82f6" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                  {canResize && HANDLES.map((hid) => {
                    const hp = quadHandlePos(hid, selQuad);
                    return <rect key={hid} data-h={hid} x={hp[0] - HS / 2} y={hp[1] - HS / 2} width={HS} height={HS} style={{ cursor: CURSOR[hid] }} />;
                  })}
                  {canRotate && (() => { const { mid, rot } = quadRot(selQuad); return (
                    <>
                      <line data-rotline x1={mid[0]} y1={mid[1]} x2={rot[0]} y2={rot[1]} stroke="#3b82f6" strokeWidth={1} vectorEffect="non-scaling-stroke" />
                      <circle data-rot cx={rot[0]} cy={rot[1]} r={5} style={{ cursor: 'grab' }} />
                    </>); })()}
                </svg>
              )}
            </div>
          </div>
        ))}
      </div>
      {editing && (
        <textarea
          autoFocus
          value={editing.value}
          onChange={(e) => setEditing({ ...editing, value: e.target.value })}
          onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); } }}
          onBlur={() => commitEdit()}
          rows={Math.max(1, editing.value.split('\n').length)}
          style={{
            position: 'fixed', left: editing.x - 2, top: editing.y - 2, width: editing.w, minHeight: editing.h,
            fontFamily: editing.fontFamily, fontSize: editing.fontSize, fontWeight: editing.fontWeight,
            color: editing.color, lineHeight: 1.2,
            textAlign: editing.anchor === 'middle' ? 'center' : editing.anchor === 'end' ? 'right' : 'left',
            background: '#fff', border: 'none', borderBottom: '2px solid #2563eb',
            outline: 'none', padding: '0 2px', margin: 0, boxSizing: 'border-box', caretColor: '#2563eb',
            boxShadow: '0 1px 6px rgba(0,0,0,.18)', zIndex: 60, userSelect: 'text', resize: 'none', overflow: 'hidden',
            whiteSpace: 'pre-wrap', letterSpacing: 'normal',
          }}
        />
      )}
      <div style={{ borderTop: '1px solid var(--border)', padding: '8px 12px', background: 'var(--bg-card, #fff)' }}>
        {sel && selT ? (
          <Space wrap size={8} align="center">
            <Text type="secondary" style={{ fontSize: 12 }}>选中 &lt;{sel.tag}&gt; · 第 {pages.findIndex((x) => x.index === sel.pageIndex) + 1} 页{canResize ? (isText ? ' ·（文字等比缩放）' : ' ·（拖角/边缩放，Shift 等比）') : ' ·（仅可移动）'}</Text>
            X <InputNumber size="small" value={selT.x} onChange={(v) => setTranslate('x', Number(v) || 0)} style={{ width: 74 }} />
            Y <InputNumber size="small" value={selT.y} onChange={(v) => setTranslate('y', Number(v) || 0)} style={{ width: 74 }} />
            {isText && <Input size="small" placeholder="文字内容" defaultValue={selEl!.textContent || ''}
              onPressEnter={(e) => setText((e.target as HTMLInputElement).value)} onBlur={(e) => setText(e.target.value)} style={{ width: 180 }} />}
            <input type="color" value={((selEl as Element)?.getAttribute('fill') || '#000000').slice(0, 7)}
              onChange={(e) => setAttr('fill', e.target.value)} style={{ width: 28, height: 24, border: 'none', background: 'none' }} title="填充色" />
            {isText && <InputNumber size="small" min={1} value={parseFloat(selEl!.getAttribute('font-size') || '16')}
              onChange={(v) => setAttr('font-size', String(Number(v) || 16))} style={{ width: 74 }} />}
            <Button size="small" onClick={copySel} title="Ctrl/Cmd+C">复制</Button>
            <Button size="small" onClick={pasteSel} title="Ctrl/Cmd+V">粘贴</Button>
            <Button size="small" onClick={() => cloneSel(16)} title="Ctrl/Cmd+D">再制</Button>
            <Button size="small" onClick={() => zOrder(true)} title="Ctrl/Cmd+] 上移一层">上移</Button>
            <Button size="small" onClick={() => zOrder(false)} title="Ctrl/Cmd+[ 下移一层">下移</Button>
            <Button size="small" danger icon={<Trash2 size={13} />} onClick={deleteSel} title="Delete 键">删除</Button>
          </Space>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>点击任意页里的元素可选中并拖动；可缩放的元素会显示四角/四边手柄。</Text>
        )}
      </div>
    </div>
  );
}
