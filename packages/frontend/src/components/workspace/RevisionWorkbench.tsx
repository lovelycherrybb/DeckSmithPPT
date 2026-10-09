/**
 * 逐页修改工作台（ADR-023 草稿模型）。
 * 左=版本列表（扁平、最新在上，带"改自"提示，避免深层缩进看不清）；
 * 中=整列可滚动预览(滚动/点击自动定位当前页，编辑作用于当前页)；右=对话改稿。
 * 改动全部先进本地草稿，只有"整体另存"一次性提交、后台重导出出一个子文件。
 * 另存用全局遮罩显示进度，成功后版本列表刷新并置顶选中新版本。
 */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Badge, Button, Drawer, Empty, Input, Segmented, Space, Spin, Tag, Typography, message,
} from 'antd';
import { Download, Layers, MessageSquare } from 'lucide-react';
import MultiPageSvgEditor from './MultiPageSvgEditor';
import {
  getJob, getJobPages, listSubPresentations, presentationDownloadUrl,
  subPresentationDownloadUrl, submitRevision, revisePages,
  type Presentation, type SubPresentation, type ReviseRequest,
} from '@/lib/api';

const { TextArea } = Input;
const { Text } = Typography;

interface Props { presentation: Presentation | null; open: boolean; onClose: () => void; }
interface Node { key: string; kind: 'root' | 'sub'; label: string; short: string; taskId: string; parentKey: string | null; createdAt: string; }
type Draft = Record<number, { name?: string; content: string }>;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
// 版本短标签：用 created_at（UTC ISO）按浏览器本地时区显示 MM-DD HH:MM。
// 不能再从文件名里的 UTC 时间戳截——那是 UTC，中国用户看着差 8 小时。
const localHM = (iso: string) => {
  try {
    return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
  } catch {
    return iso;
  }
};

export default function RevisionWorkbench({ presentation, open, onClose }: Props) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [pages, setPages] = useState<{ index: number; name: string; url: string }[]>([]);
  const [pageIdx, setPageIdx] = useState(0);
  const [tab, setTab] = useState<'预览' | '拖拽编辑' | '编辑源码'>('预览');
  const [baseSvg, setBaseSvg] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [loadingPages, setLoadingPages] = useState(false);
  const [loadingSvg, setLoadingSvg] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [commitBusy, setCommitBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error?: boolean } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLDivElement | null>>([]);

  const busy = chatBusy || commitBusy;
  const selected = useMemo(() => nodes.find((n) => n.key === selectedKey) || null, [nodes, selectedKey]);
  const currentPage = pages[pageIdx] || null;
  const draftCount = Object.keys(draft).length;
  const editedHere = currentPage ? draft[currentPage.index] : undefined;
  const editValue = editedHere?.content ?? baseSvg;

  // 给每个版本一个短序号：原始=起点，子版本按创建先后 #1/#2…（血缘用序号点，比长文件名清楚）
  const ordinalByKey = useMemo(() => {
    const ordered = [...nodes].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const m = new Map<string, string>();
    let i = 0;
    for (const n of ordered) { if (n.kind === 'root') m.set(n.key, '起点'); else { i += 1; m.set(n.key, `#${i}`); } }
    return m;
  }, [nodes]);
  // 扁平列表：原始置顶，其余按时间倒序（最新在上）——不再嵌套，深链也看得清
  const versionList = useMemo(() => {
    const root = nodes.find((n) => n.kind === 'root');
    const subs = nodes.filter((n) => n.kind === 'sub').sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return root ? [root, ...subs] : subs;
  }, [nodes]);

  const rebuild = useCallback((root: Presentation, list: SubPresentation[]) => {
    const ns: Node[] = [{ key: root.presentation_id, kind: 'root', label: root.file_name, short: localHM(root.created_at), taskId: root.task_id, parentKey: null, createdAt: root.created_at }];
    for (const s of list) ns.push({ key: s.sub_presentation_id, kind: 'sub', label: s.file_name, short: localHM(s.created_at), taskId: s.task_id, parentKey: s.parent_sub_presentation_id ?? root.presentation_id, createdAt: s.created_at });
    setNodes(ns);
    return ns;
  }, []);

  useEffect(() => {
    if (!open || !presentation) { setSelectedKey(null); setPages([]); setDraft({}); return; }
    let alive = true;
    listSubPresentations(presentation.presentation_id)
      .then((rows) => { if (!alive) return; const ns = rebuild(presentation, rows); setSelectedKey(ns[0].key); })
      .catch(() => { if (alive) rebuild(presentation, []); });
    return () => { alive = false; };
  }, [open, presentation, rebuild]);

  useEffect(() => {
    setDraft({}); setPageIdx(0); setStatus(null);
    if (!selected) { setPages([]); return; }
    let alive = true;
    setLoadingPages(true); setPages([]);
    getJobPages(selected.taskId)
      .then((d) => { if (alive) setPages((d.pages || []).map((p) => ({ index: p.index, name: p.name, url: p.url }))); })
      .catch(() => { if (alive) setPages([]); })
      .finally(() => { if (alive) setLoadingPages(false); });
    return () => { alive = false; };
  }, [selected]);

  useEffect(() => {
    // 当前页的源码不依赖页签：对话式改稿在「预览」页签也能发指令，
    // 必须提前把 baseSvg 拉好（曾因只在编辑页签才拉，导致预览页签发指令
    // 时 content 为空串、AI 收到空页并原样回传空页）。
    if (!currentPage) return;
    let alive = true; setLoadingSvg(true);
    fetch(currentPage.url).then((r) => r.text()).then((t) => { if (alive) setBaseSvg(t); })
      .catch(() => { if (alive) setBaseSvg(''); }).finally(() => { if (alive) setLoadingSvg(false); });
    return () => { alive = false; };
  }, [currentPage]);

  const onScroll = () => {
    const box = boxRef.current; if (!box) return;
    const center = box.scrollTop + box.clientHeight / 2;
    let best = pageIdx, bd = Infinity;
    itemRefs.current.forEach((el, i) => { if (!el) return; const c = el.offsetTop + el.offsetHeight / 2; const d = Math.abs(c - center); if (d < bd) { bd = d; best = i; } });
    if (best !== pageIdx) setPageIdx(best);
  };
  const gotoPage = (i: number) => { setPageIdx(i); itemRefs.current[i]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); };

  const applyEdit = (index: number, name: string | undefined, content: string) =>
    setDraft((d) => ({ ...d, [index]: { name, content } }));

  const sendInstruction = async () => {
    const text = instruction.trim();
    if (!text) { message.warning('先写一句要怎么改'); return; }
    if (!currentPage) { message.warning('没有选中页'); return; }
    if (!editValue.trim()) { message.warning('当前页源码还没加载好，稍等一两秒再发'); return; }
    setChatBusy(true); setStatus({ text: 'AI 正在改当前页…' });
    try {
      const out = await revisePages([{ index: currentPage.index, name: currentPage.name, content: editValue }], text);
      // 只并入有实质内容的页：防 AI 异常回传空内容把草稿页清空
      for (const p of out) if (typeof p.index === 'number' && typeof p.content === 'string' && p.content.trim()) applyEdit(p.index, p.name, p.content);
      setStatus({ text: 'AI 改好并并入草稿，可继续改别页或另存。' });
      setInstruction('');
    } catch (e) { setStatus({ text: `改稿失败：${(e as Error).message}`, error: true }); }
    finally { setChatBusy(false); }
  };

  const commitDraft = useCallback(async () => {
    if (!selected) return;
    const list = Object.entries(draft).map(([idx, v]) => ({ index: Number(idx), name: v.name, content: v.content }));
    if (!list.length) { message.warning('草稿还没有改动'); return; }
    setCommitBusy(true);
    setStatus({ text: `提交 ${list.length} 页改动，正在后台重导出…（拉起 AI，可能数十秒到几分钟）` });
    try {
      const body = selected.kind === 'root' ? { presentation_id: selected.key, pages: list } : { sub_presentation_id: selected.key, pages: list };
      const job = await submitRevision({ agent: 'claude', ...body } as ReviseRequest);
      let st = await getJob(job.job_id); let guard = 0;
      while ((st.status === 'queued' || st.status === 'running') && guard < 900) {
        await sleep(2000); st = await getJob(job.job_id); guard += 1;
        setStatus({ text: `修改任务 ${st.status}：${st.status_detail || '处理中…'}` });
      }
      if (st.status === 'completed') {
        const rows = await listSubPresentations(presentation!.presentation_id);
        const ns = rebuild(presentation!, rows);
        const fresh = ns.find((n) => n.taskId === job.job_id);
        setSelectedKey(fresh ? fresh.key : ns[ns.length - 1].key); // 新版本在列表最上方并选中
        message.success('新版本已生成');
      } else { setStatus({ text: `另存失败：${st.error || st.status}`, error: true }); message.error('另存未成功'); }
    } catch (e) { setStatus({ text: `提交失败：${(e as Error).message}`, error: true }); message.error('提交失败'); }
    finally { setCommitBusy(false); }
  }, [selected, draft, presentation, rebuild]);

  if (!presentation) return null;

  return (
    <Drawer title={`修改工作台 · ${presentation.file_name}`} placement="right" width="95%"
      open={open} onClose={onClose} destroyOnClose styles={{ body: { padding: 0, height: '100%', overflow: 'hidden' } }}>
      <div style={{ position: 'relative', display: 'flex', height: '100%', minHeight: 0 }}>
        {commitBusy && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 20, background: 'rgba(255,255,255,.72)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
            <Spin size="large" />
            <div>{status?.text || '正在重导出…'}</div>
            <Text type="secondary" style={{ fontSize: 12 }}>完成后版本列表会刷新并跳到新版本，请稍候</Text>
          </div>
        )}

        {/* 左：版本列表（扁平、最新在上） */}
        <div style={{ width: 260, borderRight: '1px solid var(--border)', overflowY: 'auto', padding: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>版本</div>
          {versionList.map((n) => (
            <div key={n.key} onClick={() => !busy && setSelectedKey(n.key)}
              style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 8px', borderRadius: 6, cursor: busy ? 'default' : 'pointer',
                background: selectedKey === n.key ? 'rgba(59,130,246,.12)' : 'transparent', marginBottom: 2 }}>
              <Tag color={n.kind === 'root' ? 'blue' : 'green'} style={{ marginInlineEnd: 0, flexShrink: 0 }}>
                {ordinalByKey.get(n.key) ?? (n.kind === 'root' ? '起点' : '子')}
              </Tag>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.short}</div>
                {n.kind === 'root' ? (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>最初生成的版本</div>
                ) : (
                  <a onClick={(e) => { e.stopPropagation(); if (n.parentKey) setSelectedKey(n.parentKey); }}
                     style={{ fontSize: 11, color: '#1677ff' }}>← 来自 {ordinalByKey.get(n.parentKey ?? '') ?? '起点'}</a>
                )}
              </div>
              <a href={n.kind === 'root' ? presentationDownloadUrl(n.key) : subPresentationDownloadUrl(n.key)}
                 target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()} title="下载此版本"><Download size={14} style={{ flexShrink: 0 }} /></a>
            </div>
          ))}
        </div>

        {/* 中：整列滚动预览 / 编辑当前页 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 16px', borderBottom: '1px solid var(--border)' }}>
            <Segmented value={tab} onChange={(v) => setTab(v as typeof tab)} options={['预览', '拖拽编辑', '编辑源码']} />
            <Text type="secondary" style={{ fontSize: 13 }}>{pages.length ? `当前 第 ${pageIdx + 1} / ${pages.length} 页 · ${currentPage?.name}` : '0 页'}</Text>
            <Space style={{ marginLeft: 'auto' }}>
              {draftCount > 0 && <Badge count={draftCount} showZero color="#fa8c16"><span style={{ paddingRight: 10, fontSize: 12 }}>草稿改动</span></Badge>}
              <Button type="primary" icon={<Layers size={14} />} disabled={draftCount === 0 || commitBusy} onClick={commitDraft}>整体另存为新版本</Button>
            </Space>
          </div>

          <div ref={tab === '预览' ? boxRef : undefined} onScroll={tab === '预览' ? onScroll : undefined}
            style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
            {loadingPages ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
              : !pages.length ? <Empty description="该版本暂无可预览页" />
              : tab === '预览' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {pages.map((p, i) => (
                    <div key={p.index} ref={(el) => { itemRefs.current[i] = el; }} onClick={() => gotoPage(i)}
                      style={{ outline: i === pageIdx ? '2px solid #3b82f6' : '1px solid var(--border)', outlineOffset: 2, borderRadius: 8, overflow: 'hidden', cursor: 'pointer', background: '#fff' }}>
                      <div style={{ padding: '4px 8px', fontSize: 12, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)' }}>
                        第 {i + 1} 页 · {p.name}{draft[p.index] ? ' ·（已改）' : ''}
                      </div>
                      <iframe title={p.name} src={draft[p.index] ? undefined : p.url} srcDoc={draft[p.index]?.content}
                        style={{ width: '100%', height: 420, border: 0, display: 'block', pointerEvents: 'none' }} />
                    </div>
                  ))}
                </div>
              ) : tab === '拖拽编辑' ? (
                <MultiPageSvgEditor pages={pages} draft={draft} onEdit={applyEdit} />
              ) : (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>编辑当前页（第 {pageIdx + 1} 页）源码——改动先进草稿</div>
                  {loadingSvg && !editedHere ? <Spin /> : (
                    <TextArea value={editValue} onChange={(e) => currentPage && applyEdit(currentPage.index, currentPage.name, e.target.value)}
                      style={{ flex: 1, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }} spellCheck={false} />
                  )}
                </div>
              )}
          </div>
        </div>

        {/* 右：对话改稿 */}
        <div style={{ width: 320, borderLeft: '1px solid var(--border)', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><MessageSquare size={15} /> <b>对话式改稿</b></div>
          <Text type="secondary" style={{ fontSize: 12 }}>对<b>当前页（第 {pageIdx + 1} 页）</b>说怎么改，AI 只回传进草稿、不出版本。</Text>
          <TextArea value={instruction} onChange={(e) => setInstruction(e.target.value)}
            placeholder="例如：标题改深蓝并居中；右下角加页码…" autoSize={{ minRows: 4, maxRows: 10 }} disabled={commitBusy} />
          <Button type="primary" loading={chatBusy} disabled={!instruction.trim() || !currentPage || commitBusy} onClick={sendInstruction} block>让 AI 改当前页</Button>
          {status && !commitBusy && <Alert type={status.error ? 'error' : 'info'} showIcon message={<span style={{ fontSize: 12 }}>{status.text}</span>} />}
        </div>
      </div>
    </Drawer>
  );
}
