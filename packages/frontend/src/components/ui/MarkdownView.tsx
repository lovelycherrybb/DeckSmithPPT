/**
 * Markdown 渲染（只读查看）：react-markdown + remark-gfm。
 * 支持表格/删除线/任务列表等 GFM 扩展；样式见 globals.css 的 .md-view。
 */
'use client';

import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

export default function MarkdownView({ content }: { content: string }) {
  return (
    <div className="md-view">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
    </div>
  );
}
