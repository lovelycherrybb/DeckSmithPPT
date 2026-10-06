/**
 * 多页可拖拽 SVG 编辑器（步骤5 · 5d 增强）。
 * 与"预览"一致的整列滚动：把所有页**行内挂载**成一列，可滚动浏览；点任一页的元素即选中、
 * 拖动移动；底部一条共享属性栏改 文字/填充色/字号/删除。每次改动把该页序列化回草稿（onEdit）。
 *
 * 单一选中态跨全页共享（不像每页各一个工具条）。挂载前剥 <script>；高亮用 CSS outline（Chromium）。
 * DOM 是编辑期真值：编辑只改 DOM 并回传草稿，不回读草稿重挂（否则丢编辑态）。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, InputNumber, Space, Spin, Typography, message } from 'antd';
import { Trash2 } from 'lucide-react';

const { Text } = Typography;

interface PageRef { index: number; name: string; url: string }
interface Props {
  pages: PageRef[];
  draft: Record<number, { content: string }>;
  onEdit: (index: number, name: string, content: string) => void;
}

const GRAPHICS = new Set([
  'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'path',
  'text', 'tspan', 'image', 'g', 'use', 'foreignObject',
]);

function parseTranslate(t: string | null): { x: number; y: number; rest: string } {
  const m = /translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)/.exec(t || '');
  const rest = (t || '').replace(/translate\([^)]*\)\s*/, '');
  return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]), rest } : { x: 0, y: 0, rest };
}

function sanitize(svgText: string): string {
  const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
  const root = doc.querySelector('svg');
  if (!root) return '';
  root.querySelectorAll('script').forEach((s) => s.remove());
  // 只剥脚本，**保留原始 width/height 属性**：导出需要有限 px 值。显示缩放交给外层
  // `.rv-fit > svg` 的 CSS，不改元素本身——否则序列化回草稿会把宽度写成 100%，
  // finalize_svg 会报 "inline geometry width requires a finite px literal; got '100%'"。
  return root.outerHTML;
}

export default function MultiPageSvgEditor({ pages, draft, onEdit }: Props) {
  const [html, setHtml] = useState<Record<number, string>>({});   // 每页初始挂载 HTML
  const [loading, setLoading] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const selRef = useRef<Element | null>(null);
  const [sel, setSel] = useState<{ tag: string; pageIndex: number } | null>(null);
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

  const emitPage = (el: Element) => {
    const block = el.closest('[data-page-index]');
    if (!block) return;
    const svg = block.querySelector('svg');
    if (svg) onEdit(Number((block as HTMLElement).dataset.pageIndex), (block as HTMLElement).dataset.pageName || '', svg.outerHTML);
  };

  const select = (el: Element) => {
    selRef.current?.removeAttribute('data-rv-sel');
    selRef.current = el;
    el.setAttribute('data-rv-sel', '1');
    const block = el.closest('[data-page-index]');
    setSel({ tag: el.tagName, pageIndex: block ? Number((block as HTMLElement).dataset.pageIndex) : -1 });
  };
  const clearSel = () => { selRef.current?.removeAttribute('data-rv-sel'); selRef.current = null; setSel(null); };

  const onPointerDown = (e: React.PointerEvent) => {
    let el = e.target as Element | null;
    while (el && !GRAPHICS.has(el.tagName) && !el.hasAttribute?.('data-page-index')) el = el.parentElement;
    if (!el || el.hasAttribute?.('data-page-index')) { clearSel(); return; }
    select(el);
    const block = el.closest('[data-page-index]');
    const svg = block?.querySelector('svg') as SVGSVGElement | null;
    const vb = svg?.viewBox?.baseVal; const rect = svg?.getBoundingClientRect();
    const scale = vb && vb.width && rect && rect.width ? vb.width / rect.width : 1;
    const start = { x: e.clientX, y: e.clientY };
    const { x: bx, y: by, rest } = parseTranslate(el.getAttribute('transform'));
    (e.target as Element).setPointerCapture?.(e.pointerId);
    e.preventDefault();
    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - start.x) * scale, dy = (ev.clientY - start.y) * scale;
      el!.setAttribute('transform', `translate(${(bx + dx).toFixed(1)} ${(by + dy).toFixed(1)}) ${rest}`.trim());
    };
    const up = () => {
      document.removeEventListener('pointermove', move); document.removeEventListener('pointerup', up);
      rerender(); emitPage(el!);
    };
    document.addEventListener('pointermove', move); document.addEventListener('pointerup', up);
  };

  const selEl = selRef.current;
  const selT = selEl ? parseTranslate(selEl.getAttribute('transform')) : null;
  const isText = !!selEl && (selEl.tagName === 'text' || selEl.tagName === 'tspan');
  const setAttr = (k: string, v: string) => { if (selEl) { selEl.setAttribute(k, v); rerender(); emitPage(selEl); } };
  const setTranslate = (axis: 'x' | 'y', v: number) => {
    if (!selEl || !selT) return;
    selEl.setAttribute('transform', `translate(${axis === 'x' ? v : selT.x} ${axis === 'y' ? v : selT.y}) ${selT.rest}`.trim());
    rerender(); emitPage(selEl);
  };
  const setText = (v: string) => { if (selEl) { selEl.textContent = v; rerender(); emitPage(selEl); } };
  const del = () => { if (selEl) { const blk = selEl.closest('[data-page-index]'); selEl.remove(); clearSel(); if (blk) { const s = blk.querySelector('svg'); if (s) onEdit(Number((blk as HTMLElement).dataset.pageIndex), (blk as HTMLElement).dataset.pageName || '', s.outerHTML); } } };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <style>{`.rv-multi [data-rv-sel]{outline:2px solid #ff3b30;outline-offset:1px;cursor:move} .rv-fit > svg{max-width:100%;height:auto;display:block}`}</style>
      <div ref={containerRef} className="rv-multi" onPointerDown={onPointerDown}
        style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
        {loading ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div> : pages.map((p) => (
          <div key={p.index} data-page-index={p.index} data-page-name={p.name}
            style={{ background: '#fff', border: '1px solid var(--border)', borderRadius: 8, marginBottom: 16, overflow: 'hidden' }}>
            <div style={{ padding: '4px 10px', fontSize: 12, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>
              第 {pages.findIndex((x) => x.index === p.index) + 1} 页 · {p.name}{draft[p.index] ? ' ·（已改）' : ''}
            </div>
            <div className="rv-fit" dangerouslySetInnerHTML={{ __html: html[p.index] || '' }} />
          </div>
        ))}
      </div>
      <div style={{ borderTop: '1px solid var(--border)', padding: '8px 12px', background: 'var(--bg-card, #fff)' }}>
        {sel && selT ? (
          <Space wrap size={8} align="center">
            <Text type="secondary" style={{ fontSize: 12 }}>选中 &lt;{sel.tag}&gt; · 第 {pages.findIndex((x) => x.index === sel.pageIndex) + 1} 页</Text>
            X <InputNumber size="small" value={selT.x} onChange={(v) => setTranslate('x', Number(v) || 0)} style={{ width: 74 }} />
            Y <InputNumber size="small" value={selT.y} onChange={(v) => setTranslate('y', Number(v) || 0)} style={{ width: 74 }} />
            {isText && <Input size="small" placeholder="文字内容" defaultValue={selEl!.textContent || ''}
              onPressEnter={(e) => setText((e.target as HTMLInputElement).value)} onBlur={(e) => setText(e.target.value)} style={{ width: 180 }} />}
            <input type="color" value={((selEl as Element)?.getAttribute('fill') || '#000000').slice(0, 7)}
              onChange={(e) => setAttr('fill', e.target.value)} style={{ width: 28, height: 24, border: 'none', background: 'none' }} title="填充色" />
            {isText && <InputNumber size="small" min={1} value={parseFloat(selEl!.getAttribute('font-size') || '16')}
              onChange={(v) => setAttr('font-size', String(Number(v) || 16))} style={{ width: 74 }} />}
            <Button size="small" danger icon={<Trash2 size={13} />} onClick={del}>删除</Button>
          </Space>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>点击任意页里的元素可选中并拖动；改动实时并入草稿，最后统一"整体另存"。</Text>
        )}
      </div>
    </div>
  );
}
