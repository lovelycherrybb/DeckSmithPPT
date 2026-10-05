/**
 * 轻量可视化 SVG 编辑器（步骤5 · 5d）。把当页 SVG 行内挂载，支持：
 * 点选元素 → 拖拽移动；改文字内容 / 填充色 / 字号；删除元素；导出改后源码。
 *
 * 定位：够用、稳的"第一版"，非 Figma 级——不做旋转/缩放手柄/对齐辅助线/多选。
 * 挂载前剥掉 <script>（行内注入自身产物的防御性处理）。高亮依赖 CSS outline，
 * 以 Chromium（本产品运行环境）为准。序列化出的源码交回工作台，走同一条保存/重导出。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Input, InputNumber, Space, Tooltip, Typography, message } from 'antd';
import { Save, Trash2 } from 'lucide-react';

const { Text } = Typography;

interface Props {
  svgText: string;
  onSave: (content: string) => void;
  busy?: boolean;
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

export default function SvgCanvasEditor({ svgText, onSave, busy }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const selRef = useRef<Element | null>(null);
  const [selTag, setSelTag] = useState<string | null>(null);
  const [, force] = useState(0);
  const rerender = useCallback(() => force((n) => n + 1), []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
    const root = doc.querySelector('svg');
    if (!root) { host.innerHTML = ''; svgRef.current = null; return; }
    root.querySelectorAll('script').forEach((s) => s.remove());
    root.removeAttribute('width'); root.removeAttribute('height');
    root.style.width = '100%'; root.style.height = 'auto';
    root.style.display = 'block';
    host.innerHTML = root.outerHTML;
    svgRef.current = host.querySelector('svg');
    selRef.current = null; setSelTag(null);
  }, [svgText]);

  const clearSel = useCallback(() => {
    selRef.current?.removeAttribute('data-rv-sel');
    selRef.current = null;
    setSelTag(null);
  }, []);

  const onPointerDown = (e: React.PointerEvent) => {
    let el = e.target as Element | null;
    while (el && !GRAPHICS.has(el.tagName) && el !== svgRef.current) el = el.parentElement;
    if (!el || el === svgRef.current) { clearSel(); return; }
    selRef.current?.removeAttribute('data-rv-sel');
    selRef.current = el;
    el.setAttribute('data-rv-sel', '1');
    setSelTag(el.tagName);

    const svg = svgRef.current!;
    const vb = svg.viewBox?.baseVal;
    const rect = svg.getBoundingClientRect();
    const scale = vb && vb.width && rect.width ? vb.width / rect.width : 1;
    const start = { x: e.clientX, y: e.clientY };
    const { x: bx, y: by, rest } = parseTranslate(el.getAttribute('transform'));
    (e.target as Element).setPointerCapture?.(e.pointerId);
    e.preventDefault();

    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - start.x) * scale;
      const dy = (ev.clientY - start.y) * scale;
      el!.setAttribute('transform', `translate(${(bx + dx).toFixed(1)} ${(by + dy).toFixed(1)}) ${rest}`.trim());
    };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      rerender();
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  };

  const sel = selRef.current;
  const selT = sel ? parseTranslate(sel.getAttribute('transform')) : null;
  const isText = !!sel && (sel.tagName === 'text' || sel.tagName === 'tspan');

  const setAttr = (name: string, value: string) => { if (sel) { sel.setAttribute(name, value); rerender(); } };
  const setTranslate = (axis: 'x' | 'y', v: number) => {
    if (!sel || !selT) return;
    const nx = axis === 'x' ? v : selT.x;
    const ny = axis === 'y' ? v : selT.y;
    sel.setAttribute('transform', `translate(${nx} ${ny}) ${selT.rest}`.trim());
    rerender();
  };
  const setText = (v: string) => { if (sel) { sel.textContent = v; rerender(); } };
  const del = () => { sel?.remove(); clearSel(); };

  const exportSvg = () => {
    if (!svgRef.current) { message.error('编辑器未就绪'); return; }
    onSave(svgRef.current.outerHTML);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <style>{`.rv-svg-host svg [data-rv-sel]{outline:2px solid #ff3b30;outline-offset:1px;cursor:move} .rv-svg-host svg{background:#fff}`}</style>
      <div
        ref={hostRef}
        className="rv-svg-host"
        onPointerDown={onPointerDown}
        style={{ flex: 1, minHeight: 0, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8, background: '#fafafa' }}
      />
      <Space style={{ marginTop: 8, flexWrap: 'wrap' }} align="center">
        {sel && selT ? (
          <>
            <Text type="secondary" style={{ fontSize: 12 }}>选中 &lt;{selTag}&gt;</Text>
            X <InputNumber size="small" value={selT.x} onChange={(v) => setTranslate('x', Number(v) || 0)} style={{ width: 74 }} />
            Y <InputNumber size="small" value={selT.y} onChange={(v) => setTranslate('y', Number(v) || 0)} style={{ width: 74 }} />
            {isText && (
              <Input
                size="small"
                placeholder="文字内容"
                defaultValue={sel!.textContent || ''}
                onPressEnter={(e) => setText((e.target as HTMLInputElement).value)}
                onBlur={(e) => setText(e.target.value)}
                style={{ width: 180 }}
              />
            )}
            <Tooltip title="填充色">
              <input
                type="color"
                value={((sel as Element)?.getAttribute('fill') || '#000000').slice(0, 7)}
                onChange={(e) => setAttr('fill', e.target.value)}
                style={{ width: 28, height: 24, border: 'none', background: 'none' }}
              />
            </Tooltip>
            {isText && (
              <InputNumber
                size="small" min={1} value={parseFloat(sel!.getAttribute('font-size') || '16')}
                onChange={(v) => setAttr('font-size', String(Number(v) || 16))} style={{ width: 74 }}
              />
            )}
            <Button size="small" danger icon={<Trash2 size={13} />} onClick={del}>删除</Button>
          </>
        ) : (
          <Text type="secondary" style={{ fontSize: 12 }}>点击画布里的元素可选中，拖动可移动；未选中时无操作。</Text>
        )}
        <Button type="primary" size="small" icon={<Save size={13} />} loading={busy} onClick={exportSvg} style={{ marginLeft: 'auto' }}>
          应用为本页并保存新版本
        </Button>
      </Space>
    </div>
  );
}
