/**
 * 逐页修改工作台（ADR-023 步骤5 · 5c）——大号操作界面。
 *
 * 三区布局：左=版本树导航（原始 + 各子版本，选中即预览/在其上改）；
 * 中=大幅单页查看（预览 / 编辑本页 SVG 源码两种模式）；右=对话式修改与保存。
 * 两条改法都走同一个后端动作 submitRevision（对话=instruction，手动=pages），
 * 提交后轮询该修改任务到终态再刷新版本树——产物隔离在引擎侧保证（只改工作副本）。
 *
 * 说明：这里的手动改是"本页 SVG 源码"级编辑（够用且稳）；拖拽可视化编辑器是后续增量。
 * 真跑出成品会 fork claude、烧付费额度，端到端验收需一次授权真跑。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Button, Drawer, Empty, Input, Segmented, Space, Spin, Tag, Tooltip, message,
} from 'antd';
import { Download, MessageSquare, Save } from 'lucide-react';
import SvgCanvasEditor from './SvgCanvasEditor';
import {
  getJob,
  getJobPages,
  listSubPresentations,
  presentationDownloadUrl,
  subPresentationDownloadUrl,
  submitRevision,
  type Presentation,
  type SubPresentation,
  type ReviseRequest,
} from '@/lib/api';

const { TextArea } = Input;

interface Props {
  presentation: Presentation | null;
  open: boolean;
  onClose: () => void;
}

interface Node {
  key: string;            // root 用 presentation_id；子用 sub_presentation_id
  kind: 'root' | 'sub';
  label: string;
  taskId: string;         // 该版本产出/修改任务的 task_id（逐页预览用它）
  depth: number;
  parentLabel?: string;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function RevisionWorkbench({ presentation, open, onClose }: Props) {
  const [subs, setSubs] = useState<SubPresentation[]>([]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [selected, setSelected] = useState<Node | null>(null);
  const [pages, setPages] = useState<{ index: number; name: string; url: string }[]>([]);
  const [pageIdx, setPageIdx] = useState(0);
  const [tab, setTab] = useState<'预览' | '编辑源码' | '拖拽编辑'>('预览');
  const [svgText, setSvgText] = useState('');
  const [loadingPages, setLoadingPages] = useState(false);
  const [loadingSvg, setLoadingSvg] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error?: boolean } | null>(null);

  // 版本树：原始在顶，子版本按 parent 缩进；重算成扁平可点列表
  const rebuildTree = useCallback((root: Presentation, list: SubPresentation[]) => {
    const byId = new Map(list.map((s) => [s.sub_presentation_id, s]));
    const nameOf = (id: string | null): string | undefined => {
      if (!id) return root.file_name;
      return byId.get(id)?.file_name;
    };
    const depthOf = (s: SubPresentation): number => {
      let d = 0;
      let cur = s.parent_sub_presentation_id;
      const seen = new Set<string>([s.sub_presentation_id]);
      while (cur && byId.has(cur) && !seen.has(cur)) {
        seen.add(cur);
        d += 1;
        cur = byId.get(cur)!.parent_sub_presentation_id;
      }
      return d;
    };
    const rootLabel = `原始版本 · ${root.file_name}`;
    const tree: Node[] = [{
      key: root.presentation_id, kind: 'root', label: rootLabel, taskId: root.task_id, depth: 0,
    }];
    for (const s of [...list].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
      tree.push({
        key: s.sub_presentation_id, kind: 'sub', label: s.file_name, taskId: s.task_id,
        depth: depthOf(s), parentLabel: nameOf(s.parent_sub_presentation_id),
      });
    }
    setNodes(tree);
    return tree;
  }, []);

  // 打开/换演示文件：拉子文件列表，默认选中原始
  useEffect(() => {
    if (!open || !presentation) {
      setSelected(null);
      setPages([]);
      return;
    }
    let alive = true;
    listSubPresentations(presentation.presentation_id)
      .then((rows) => {
        if (!alive) return;
        setSubs(rows);
        const tree = rebuildTree(presentation, rows);
        setSelected(tree[0]);
      })
      .catch(() => { if (alive) setSubs([]); });
    return () => { alive = false; };
  }, [open, presentation, rebuildTree]);

  // 选中版本变化 → 拉它的逐页页墙
  useEffect(() => {
    if (!selected) { setPages([]); return; }
    let alive = true;
    setLoadingPages(true);
    setPages([]);
    setPageIdx(0);
    getJobPages(selected.taskId)
      .then((d) => { if (alive) setPages((d.pages || []).map((p) => ({ index: p.index, name: p.name, url: p.url }))); })
      .catch(() => { if (alive) setPages([]); })
      .finally(() => { if (alive) setLoadingPages(false); });
    return () => { alive = false; };
  }, [selected]);

  const currentPage = pages[pageIdx] || null;

  // 进入"编辑源码/拖拽编辑"或换页 → 拉该页 SVG 文本（预览 URL 直接返回 SVG 源码）
  useEffect(() => {
    if ((tab !== '编辑源码' && tab !== '拖拽编辑') || !currentPage) { return; }
    let alive = true;
    setLoadingSvg(true);
    fetch(currentPage.url)
      .then((r) => r.text())
      .then((t) => { if (alive) setSvgText(t); })
      .catch(() => { if (alive) setSvgText(''); })
      .finally(() => { if (alive) setLoadingSvg(false); });
    return () => { alive = false; };
  }, [tab, currentPage]);

  const sourceRef = useRef<Node | null>(null);
  useEffect(() => { sourceRef.current = selected; }, [selected]);

  // 提交一次修改（对话或源码），轮询任务到终态，成功后刷新版本树并选中新版本
  const runRevision = useCallback(async (payload: {
    presentation_id?: string; sub_presentation_id?: string;
    instruction?: string; pages?: { index: number; name?: string; content: string }[];
  }) => {
    const src = sourceRef.current;
    if (!src) { message.warning('先在左侧选择要修改的版本'); return; }
    setBusy(true);
    setStatus({ text: '已提交修改，正在后台重导出…（首次会拉起 AI，可能数十秒到几分钟）' });
    try {
      const job = await submitRevision({ agent: 'claude', ...payload } as ReviseRequest);
      let st = await getJob(job.job_id);
      let guard = 0;
      while ((st.status === 'queued' || st.status === 'running') && guard < 600) {
        await sleep(2000);
        st = await getJob(job.job_id);
        guard += 1;
        setStatus({ text: `修改任务 ${st.status}：${st.status_detail || '处理中…'}` });
      }
      if (st.status === 'completed') {
        const rows = await listSubPresentations((presentation as Presentation).presentation_id);
        setSubs(rows);
        const tree = rebuildTree(presentation as Presentation, rows);
        const fresh = tree.find((n) => n.taskId === job.job_id) || tree[tree.length - 1];
        setSelected(fresh);
        setStatus({ text: '已生成新版本，已切到它。' });
        message.success('新版本已生成');
      } else {
        setStatus({ text: `修改失败：${st.error || st.status}（详见该任务 run.log）`, error: true });
        message.error('修改任务未成功');
      }
    } catch (e) {
      setStatus({ text: `提交失败：${(e as Error).message}`, error: true });
      message.error('提交失败');
    } finally {
      setBusy(false);
    }
  }, [presentation, rebuildTree]);

  const sendInstruction = () => {
    const text = instruction.trim();
    if (!text) { message.warning('先写一句要怎么改'); return; }
    const src = sourceRef.current!;
    runRevision(
      src.kind === 'root'
        ? { presentation_id: presentation!.presentation_id, instruction: text }
        : { sub_presentation_id: src.key, instruction: text },
    ).then(() => setInstruction(''));
  };

  const saveContent = (content: string) => {
    if (!currentPage) { message.warning('没有选中页'); return; }
    const src = sourceRef.current!;
    runRevision({
      ...(src.kind === 'root'
        ? { presentation_id: presentation!.presentation_id }
        : { sub_presentation_id: src.key }),
      pages: [{ index: currentPage.index, name: currentPage.name, content }],
    });
  };

  const saveSource = () => saveContent(svgText);

  if (!presentation) return null;

  return (
    <Drawer
      title={`修改工作台 · ${presentation.file_name}`}
      placement="right"
      width="95%"
      open={open}
      onClose={onClose}
      destroyOnClose
      styles={{ body: { padding: 0, height: '100%', overflow: 'hidden' } }}
    >
      <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
        {/* 左：版本树导航 */}
        <div style={{ width: 260, borderRight: '1px solid var(--border)', overflowY: 'auto', padding: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>版本树</div>
          {nodes.map((n) => (
            <div
              key={n.key}
              onClick={() => !busy && setSelected(n)}
              style={{
                paddingLeft: 8 + n.depth * 18,
                padding: `6px 8px 6px ${8 + n.depth * 18}px`,
                borderRadius: 6, cursor: busy ? 'default' : 'pointer',
                background: selected?.key === n.key ? 'rgba(59,130,246,.12)' : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
              }}
            >
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}>
                <Tag color={n.kind === 'root' ? 'blue' : 'green'} style={{ marginRight: 6 }}>
                  {n.kind === 'root' ? '原始' : '子'}
                </Tag>
                <Tooltip title={n.parentLabel ? `改自：${n.parentLabel}` : n.label}>
                  {n.kind === 'root' ? n.label.replace('原始版本 · ', '') : n.label}
                </Tooltip>
              </span>
              <a
                href={n.kind === 'root' ? presentationDownloadUrl(n.key) : subPresentationDownloadUrl(n.key)}
                target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
                title="下载此版本"
              ><Download size={14} /></a>
            </div>
          ))}
        </div>

        {/* 中：大幅单页查看 / 源码编辑 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
            <Segmented value={tab} onChange={(v) => setTab(v as '预览' | '编辑源码' | '拖拽编辑')} options={['预览', '拖拽编辑', '编辑源码']} />
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Button size="small" disabled={!currentPage || pageIdx <= 0} onClick={() => setPageIdx((i) => Math.max(0, i - 1))}>上一页</Button>
              <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
                {pages.length ? `第 ${pageIdx + 1} / ${pages.length} 页 · ${currentPage?.name}` : '0 页'}
              </span>
              <Button size="small" disabled={!currentPage || pageIdx >= pages.length - 1} onClick={() => setPageIdx((i) => Math.min(pages.length - 1, i + 1))}>下一页</Button>
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
            {loadingPages ? (
              <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
            ) : !currentPage ? (
              <Empty description="该版本暂无可预览页，或引擎不支持逐页预览" />
            ) : tab === '预览' ? (
              <iframe
                title="page"
                src={currentPage.url}
                style={{ width: '100%', height: '100%', minHeight: 480, border: '1px solid var(--border)', borderRadius: 8, background: '#fff' }}
              />
            ) : tab === '拖拽编辑' ? (
              loadingSvg ? (
                <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
              ) : (
                <SvgCanvasEditor svgText={svgText} onSave={saveContent} busy={busy} />
              )
            ) : (
              <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
                  直接编辑本页 SVG 源码（{currentPage.name}），保存后在后台重导出为新版本
                </div>
                {loadingSvg ? <Spin /> : (
                  <TextArea
                    value={svgText}
                    onChange={(e) => setSvgText(e.target.value)}
                    style={{ flex: 1, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }}
                    spellCheck={false}
                  />
                )}
                <div style={{ marginTop: 10, textAlign: 'right' }}>
                  <Button type="primary" icon={<Save size={14} />} loading={busy} onClick={saveSource} disabled={!svgText}>
                    保存本页为新版本
                  </Button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* 右：对话式修改 */}
        <div style={{ width: 340, borderLeft: '1px solid var(--border)', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><MessageSquare size={15} /> <b>对话式修改</b></div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            对<b>左侧选中的版本</b>说明要怎么改，AI 会在其工作副本上改稿并重新导出一版。
            {selected ? ` 当前：${selected.label}` : ''}
          </div>
          <TextArea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="例如：把第 3 页标题改成红色、正文左移；给封面加一句副标题…"
            autoSize={{ minRows: 4, maxRows: 10 }}
            disabled={busy}
          />
          <Button type="primary" loading={busy} disabled={!instruction.trim()} onClick={sendInstruction} block>
            提交修改并重导出
          </Button>
          {status && (
            <Alert type={status.error ? 'error' : 'info'} showIcon message={<span style={{ fontSize: 12 }}>{status.text}</span>} />
          )}
        </div>
      </div>
    </Drawer>
  );
}
