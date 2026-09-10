/**
 * 专业 Markdown 编辑器（@uiw/react-md-editor 封装）：
 * 工具栏 + 编辑/预览分栏（live）+ 全屏；SSR 关闭以避免 window 依赖。
 */
'use client';

import dynamic from 'next/dynamic';
import { Spin } from 'antd';

const MDEditor = dynamic(() => import('@uiw/react-md-editor'), {
  ssr: false,
  loading: () => (
    <div style={{ textAlign: 'center', padding: 60 }}>
      <Spin tip="编辑器加载中…" />
    </div>
  ),
});

interface Props {
  value: string;
  onChange: (value: string) => void;
  height?: number | string;
}

export default function MdEditor({ value, onChange, height = 520 }: Props) {
  return (
    <div data-color-mode="light" style={{ height }}>
      <MDEditor
        value={value}
        onChange={(v) => onChange(v || '')}
        height="100%" // 必须走 height prop：@uiw 内部会用它覆盖容器高度（默认 200px）
        style={{ height: '100%' }}
        preview="live"
        visibleDragbar={false}
      />
    </div>
  );
}
