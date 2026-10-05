/**
 * 逐页修改工作台（ADR-023 · 5c/5d + 草稿载体模型）——大号操作界面。
 *
 * 三区：左=版本树导航；中=大幅单页（预览 / 拖拽编辑 / 编辑源码）；右=对话式改稿。
 * 关键：改动**先进本地草稿**（会话级、临时），手动/对话都攒进草稿，都不即时出版本；
 * 只有点「整体另存为新版本」才把草稿里所有改过的页一次性提交（submitRevision），
 * 后台重导出成功才落一个子文件。关掉/切版本 = 草稿丢弃重开（ADR-023 修订A、flows/04）。
 *
 * 对话式改稿走 Agent 的 revise_pages：AI 只把改好的页回传进草稿，不出版本、不导出。
 * 手动改支持拖拽（SvgCanvasEditor）与源码两种。真跑另存会 fork claude、烧额度。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Drawer, Empty, Input, Segmented, Space, Spin, Tag, Tooltip, message,
} from 'antd';
import { Download, MessageSquare, Layers } from 'lucide-react';
import SvgCanvasEditor from './SvgCanvasEditor';
import {
  getJob,
  getJobPages,
  listSubPresentations,
  presentationDownloadUrl,
  subPresentationDownloadUrl,
  submitRevision,
  revisePages,
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
  key: string;
  kind: 'root' | 'sub';
  label: string;
  taskId: string;
  depth: number;
  parentLabel?: string;
}

type Draft = Record<number, { name?: string; content: string }>;  // 只存改过的页

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export default function RevisionWorkbench({ presentation, open, onClose }: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [selected, setSelected] = useState<Node | null>(null);
  const [pages, setPages] = useState<{ index: number; name: string; url: string }[]>([]);
  const [pageIdx, setPageIdx] = useState(0);
  const [tab, setTab] = useState<'预览' | '拖拽编辑' | '编辑源码'>('预览');
  const [baseSvg, setBaseSvg] = useState('');           // 当前页从后端拉到的原始 SVG
  const [draft, setDraft] = useState<Draft>({});        // 本地草稿（仅改过的页）
  const [loadingPages, setLoadingPages] = useState(false);
  const [loadingSvg, setLoadingSvg] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [commitBusy, setCommitBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error?: boolean } | null>(null);
  const busy = chatBusy || commitBusy;

  const rebuildTree = useCallback((root: Presentation, list: SubPresentation[]) => {
    const byId = new Map(list.map((s) => [s.sub_presentation_id, s]));
    const nameOf = (id: string | null) => (!id ? root.file_name : byId.get(id)?.file_name);
    const depthOf = (s: SubPresentation) => {
      let d = 0; let cur = s.parent_sub_presentation_id;
      const seen = new Set<string>([s.sub_presentation_id]);
      while (cur && byId.has(cur) && !seen.has(cur)) { seen.add(cur); d += 1; cur = byId.get(cur)!.parent_sub_presentation_id; }
      return d;
    };
    const tree: Node[] = [{ key: root.presentation_id, kind: 'root', label: root.file_name, taskId: root.task_id, depth: 0 }];
    for (const s of [...list].sort((a, b) => a.created_at.localeCompare(b.created_at))) {
      tree.push({ key: s.sub_presentation_id, kind: 'sub', label: s.file_name, taskId: s.task_id, depth: depthOf(s), parentLabel: nameOf(s.parent_sub_presentation_id) });
    }
    setNodes(tree);
    return tree;
  }, []);

  // 打开：拉版本树，默认选中原始；关掉即丢弃草稿
  useEffect(() => {
    if (!open || !presentation) { setSelected(null); setPages([]); setDraft({}); return; }
    let alive = true;
    listSubPresentations(presentation.presentation_id)
      .then((rows) => { if (!alive) return; const t = rebuildTree(presentation, rows); setSelected(t[0]); })
      .catch(() => { if (alive) rebuildTree(presentation, []); });
    return () => { alive = false; };
  }, [open, presentation, rebuildTree]);

  // 切换选中版本 → 拉它的页墙，并**重置草稿**（切版本=丢弃未存的改动）
  useEffect(() => {
    setDraft({});
    if (!selected) { setPages([]); return; }
    let alive = true;
    setLoadingPages(true); setPages([]); setPageIdx(0); setStatus(null);
    getJobPages(selected.taskId)
      .then((d) => { if (alive) setPages((d.pages || []).map((p) => ({ index: p.index, name: p.name, url: p.url }))); })
      .catch(() => { if (alive) setPages([]); })
      .finally(() => { if (alive) setLoadingPages(false); });
    return () => { alive = false; };
  }, [selected]);

  const currentPage = pages[pageIdx] || null;
  const draftCount = Object.keys(draft).length;
  const editedHere = currentPage ? draft[currentPage.index] : undefined;
  const editValue = editedHere?.content ?? baseSvg;   // 当前页生效内容：草稿优先，否则原始

  // 进入编辑模式或换页 → 拉该页原始 SVG 作为 base（草稿若已有则以草稿为准，base 仅供未改页）
  useEffect(() => {
    if ((tab === '预览') || !currentPage) return;
    let alive = true;
    setLoadingSvg(true);
    fetch(currentPage.url).then((r) => r.text())
      .then((t) => { if (alive) setBaseSvg(t); })
      .catch(() => { if (alive) setBaseSvg(''); })
      .finally(() => { if (alive) setLoadingSvg(false); });
    return () => { alive = false; };
  }, [tab, currentPage]);

  const applyEdit = (index: number, name: string | undefined, content: string) => {
    setDraft((d) => ({ ...d, [index]: { name, content } }));
  };

  // 对话改稿：把当前页 + 指令交 AI，只回传改好的页并入草稿（不出版本）
  const sendInstruction = async () => {
    const text = instruction.trim();
    if (!text) { message.warning('先写一句要怎么改'); return; }
    if (!currentPage) { message.warning('没有选中页'); return; }
    setChatBusy(true); setStatus({ text: 'AI 正在改这一页…' });
    try {
      const out = await revisePages(
        [{ index: currentPage.index, name: currentPage.name, content: editValue }], text);
      for (const p of out) if (typeof p.index === 'number' && typeof p.content === 'string') applyEdit(p.index, p.name, p.content);
      setStatus({ text: `AI 已改好第 ${currentPage.index} 页并入草稿，可继续改其它页或另存。` });
      setInstruction('');
    } catch (e) {
      setStatus({ text: `改稿失败：${(e as Error).message}`, error: true });
    } finally {
      setChatBusy(false);
    }
  };

  // 唯一的"落版本"动作：把草稿里所有改过的页一次性提交 → 后台重导出 → 出一个子文件
  const commitDraft = useCallback(async () => {
    if (!selected) return;
    const list = Object.entries(draft).map(([idx, v]) => ({ index: Number(idx), name: v.name, content: v.content }));
    if (!list.length) { message.warning('草稿还没有改动'); return; }
    const srcKind = selected.kind;
    const srcKey = selected.key;
    setCommitBusy(true);
    setStatus({ text: `提交 ${list.length} 页改动，正在后台重导出…（会拉起 AI，可能数十秒到几分钟）` });
    try {
      const body = {
        ...(srcKind === 'root' ? { presentation_id: srcKey } : { sub_presentation_id: srcKey }),
        pages: list,
      };
      const job = await submitRevision({ agent: 'claude', ...body } as ReviseRequest);
      let st = await getJob(job.job_id); let guard = 0;
      while ((st.status === 'queued' || st.status === 'running') && guard < 600) {
        await sleep(2000); st = await getJob(job.job_id); guard += 1;
        setStatus({ text: `修改任务 ${st.status}：${st.status_detail || '处理中…'}` });
      }
      if (st.status === 'completed') {
        const rows = await listSubPresentations(presentation!.presentation_id);
        const tree = rebuildTree(presentation!, rows);
        setSelected(tree.find((n) => n.taskId === job.job_id) || tree[tree.length - 1]); // 切到新版本，草稿随之重置
        setStatus({ text: '已生成新版本，已切到它。' });
        message.success('新版本已生成');
      } else {
        setStatus({ text: `另存失败：${st.error || st.status}（详见任务 run.log）`, error: true });
        message.error('另存未成功');
      }
    } catch (e) {
      setStatus({ text: `提交失败：${(e as Error).message}`, error: true });
      message.error('提交失败');
    } finally {
      setCommitBusy(false);
    }
  }, [selected, draft, presentation, rebuildTree]);

  if (!presentation) return null;

  return (
    <Drawer
      title={`修改工作台 · ${presentation.file_name}`}
      placement="right" width="95%" open={open} onClose={onClose} destroyOnClose
      styles={{ body: { padding: 0, height: '100%', overflow: 'hidden' } }}
    >
      <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
        {/* 左：版本树 */}
        <div style={{ width: 260, borderRight: '1px solid var(--border)', overflowY: 'auto', padding: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>版本树（选中即在它上面改）</div>
          {nodes.map((n) => (
            <div key={n.key} onClick={() => !busy && setSelected(n)}
              style={{ padding: `6px 8px 6px ${8 + n.depth * 18}px`, borderRadius: 6, cursor: busy ? 'default' : 'pointer',
                background: selected?.key === n.key ? 'rgba(59,130,246,.12)' : 'transparent',
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}>
                <Tag color={n.kind === 'root' ? 'blue' : 'green'} style={{ marginRight: 6 }}>{n.kind === 'root' ? '原始' : '子'}</Tag>
                <Tooltip title={n.parentLabel ? `改自：${n.parentLabel}` : n.label}>{n.label}</Tooltip>
              </span>
              <a href={n.kind === 'root' ? presentationDownloadUrl(n.key) : subPresentationDownloadUrl(n.key)}
                 target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} title="下载此版本"><Download size={14} /></a>
            </div>
          ))}
        </div>

        {/* 中：单页 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
            <Segmented value={tab} onChange={(v) => setTab(v as typeof tab)} options={['预览', '拖拽编辑', '编辑源码']} />
            <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>
              {pages.length ? `第 ${pageIdx + 1} / ${pages.length} 页 · ${currentPage?.name}` : '0 页'}
            </span>
            <Button size="small" disabled={!currentPage || pageIdx <= 0} onClick={() => setPageIdx((i) => Math.max(0, i - 1))}>上一页</Button>
            <Button size="small" disabled={!currentPage || pageIdx >= pages.length - 1} onClick={() => setPageIdx((i) => Math.min(pages.length - 1, i + 1))}>下一页</Button>
            <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
              {draftCount > 0 && <Badge count={draftCount} showZero color="#fa8c16"><span style={{ paddingRight: 10, fontSize: 12, color: 'var(--text-muted)' }}>草稿改动</span></Badge>}
              <Button type="primary" icon={<Layers size={14} />} loading={commitBusy} disabled={draftCount === 0} onClick={commitDraft}>
                整体另存为新版本
              </Button>
            </div>
          </div>
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
            {loadingPages ? (<div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>)
              : !currentPage ? (<Empty description="该版本暂无可预览页，或引擎不支持逐页预览" />)
              : tab === '预览' ? (
                <iframe title="page" src={editedHere ? undefined : currentPage.url}
                  srcDoc={editedHere ? editedHere.content : undefined}
                  style={{ width: '100%', height: '100%', minHeight: 480, border: '1px solid var(--border)', borderRadius: 8, background: '#fff' }} />
              ) : tab === '拖拽编辑' ? (
                loadingSvg ? (<div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>)
                  : (<SvgCanvasEditor svgText={editValue} busy={busy}
                      onSave={(content) => applyEdit(currentPage.index, currentPage.name, content)} />)
              ) : (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>
                    编辑本页 SVG 源码（{currentPage.name}）——改动先进草稿，满意后点右上"整体另存"。
                  </div>
                  {loadingSvg && !editedHere ? <Spin /> : (
                    <TextArea value={editValue} onChange={(e) => applyEdit(currentPage.index, currentPage.name, e.target.value)}
                      style={{ flex: 1, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }} spellCheck={false} />
                  )}
                </div>
              )}
          </div>
        </div>

        {/* 右：对话改稿 */}
        <div style={{ width: 340, borderLeft: '1px solid var(--border)', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><MessageSquare size={15} /> <b>对话式改稿</b></div>
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
            说明要改<b>当前页</b>什么，AI 改好后只并入草稿（<b>不出版本</b>）。可反复改多页，最后统一"整体另存"。
          </div>
          <TextArea value={instruction} onChange={(e) => setInstruction(e.target.value)}
            placeholder="例如：把标题改成深蓝色并居中；右下角加页码…" autoSize={{ minRows: 4, maxRows: 10 }} disabled={busy} />
          <Button type="primary" loading={chatBusy} disabled={!instruction.trim() || !currentPage} onClick={sendInstruction} block>
            让 AI 改这一页（进草稿）
          </Button>
          {status && <Alert type={status.error ? 'error' : 'info'} showIcon message={<span style={{ fontSize: 12 }}>{status.text}</span>} />}
        </div>
      </div>
    </Drawer>
  );
}
