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
  Alert, Button, Drawer, Empty, Input, Popconfirm, Segmented, Space, Spin, Tag, Tooltip, Typography, message,
} from 'antd';
import { ArrowDown, ArrowUp, Download, Layers, MessageSquare, Plus, RotateCcw, Trash2 } from 'lucide-react';
import MultiPageSvgEditor from './MultiPageSvgEditor';
import {
  getJob, getJobPages, listSubPresentations, presentationDownloadUrl,
  subPresentationDownloadUrl, submitRevision, revisePages, cancelAgentSession,
  type Presentation, type SubPresentation, type ReviseRequest,
} from '@/lib/api';

const { TextArea } = Input;
const { Text } = Typography;

interface Props { presentation: Presentation | null; open: boolean; onClose: () => void; }
interface Node { key: string; kind: 'root' | 'sub'; label: string; short: string; taskId: string; parentKey: string | null; createdAt: string; }
/** 有序整稿草稿（ADR-027）：ref=null 即新增页；content=null 表示沿用源页服务端内容。 */
interface DeckItem { key: string; ref: number | null; name: string; content: string | null; url?: string }

// 新增页的空白骨架：已随导出闸验证（finalize_svg → quality_checker → svg_to_pptx 全绿，
// 见 ADR-027 层三验证）。留一行浅灰提示，用户随后用对话式改稿/编辑器填内容。
const BLANK_PAGE_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" data-pptx-page-role="content" lang="zh-CN" font-family="Microsoft YaHei, SimHei" font-size="20" fill="#1A1A1A">
  <rect id="bg" x="0" y="0" width="1280" height="720" fill="#FFFFFF" data-pptx-role="background"></rect>
  <text x="640" y="360" text-anchor="middle" font-size="24" fill="#999999">（空白页，请添加内容）</text>
</svg>
`;

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
/**
 * 未经编辑的新页在提交时去掉画在页面上的占位提示。
 *
 * 为什么要去：那行字是给编辑器看的界面提示，用户忘了填就会跟着成品交出去，
 * 等于把内部提示交付给业主（2026-11-03 界面文案体检清单第四节）。
 * 判定用全等：只剥掉"原样没动过"的新页，用户编辑过的页面一律原样提交。
 *
 * 未验事项：剥掉文字后该页只剩背景矩形，能否通过上游导出闸需要跑一次真实导出确认
 * （消耗付费额度，未获批准前不跑）。见当日交付说明。
 */
const BLANK_PAGE_WITHOUT_HINT = BLANK_PAGE_SVG.replace(
  /\s*<text[^>]*>（空白页，请添加内容）<\/text>/,
  ''
);
const stripBlankPageHint = (content: string) =>
  content === BLANK_PAGE_SVG ? BLANK_PAGE_WITHOUT_HINT : content;// 版本短标签：用 created_at（UTC ISO）按浏览器本地时区显示 MM-DD HH:MM。
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
  const [tab, setTab] = useState<'预览' | '拖动修改' | '编辑代码'>('预览');
  const [baseSvg, setBaseSvg] = useState('');
  const [deck, setDeck] = useState<DeckItem[]>([]);
  const [loadingPages, setLoadingPages] = useState(false);
  const [loadingSvg, setLoadingSvg] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [reviseSession, setReviseSession] = useState<string | null>(null);
  const [commitBusy, setCommitBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error?: boolean } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLDivElement | null>>([]);

  const busy = chatBusy || commitBusy;
  const selected = useMemo(() => nodes.find((n) => n.key === selectedKey) || null, [nodes, selectedKey]);
  const currentPage = deck[pageIdx] || null;
  // 草稿改动数 = 内容被改/新增的页 + 被删除的源页 + 调序（相对源顺序有错位记 1 处，ADR-027）
  const changedCount = deck.filter((it) => it.content !== null || it.ref === null).length;
  const deletedCount = Math.max(0, pages.length - deck.filter((it) => it.ref !== null).length);
  const refsInOrder = deck.filter((it) => it.ref !== null).map((it) => it.ref as number);
  const reordered = refsInOrder.join(',') !== refsInOrder.slice().sort((a, b) => a - b).join(',');
  const draftCount = changedCount + deletedCount + (reordered ? 1 : 0);
  const editValue = currentPage?.content ?? baseSvg;

  // 给每个版本一个短序号：原始=起点，子版本按创建先后 #1/#2…（血缘用序号点，比长文件名清楚）
  const ordinalByKey = useMemo(() => {
    const ordered = [...nodes].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const m = new Map<string, string>();
    let i = 0;
    for (const n of ordered) { if (n.kind === 'root') m.set(n.key, '原始版本'); else { i += 1; m.set(n.key, `#${i}`); } }
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
    if (!open || !presentation) { setSelectedKey(null); setPages([]); setDeck([]); return; }
    let alive = true;
    listSubPresentations(presentation.presentation_id)
      .then((rows) => { if (!alive) return; const ns = rebuild(presentation, rows); setSelectedKey(ns[0].key); })
      .catch(() => { if (alive) rebuild(presentation, []); });
    return () => { alive = false; };
  }, [open, presentation, rebuild]);

  useEffect(() => {
    setDeck([]); setPageIdx(0); setStatus(null);
    if (!selected) { setPages([]); return; }
    let alive = true;
    setLoadingPages(true); setPages([]);
    getJobPages(selected.taskId)
      .then((d) => {
        if (!alive) return;
        const list = (d.pages || []).map((p) => ({ index: p.index, name: p.name, url: p.url }));
        setPages(list);
        // 有序整稿草稿初始化为"全部引用源页"（ADR-027）；此后的增删改都落在这份清单上
        setDeck(list.map((p) => ({ key: `src-${p.index}`, ref: p.index, name: p.name, content: null, url: p.url })));
      })
      .catch(() => { if (alive) { setPages([]); setDeck([]); } })
      .finally(() => { if (alive) setLoadingPages(false); });
    return () => { alive = false; };
  }, [selected]);

  useEffect(() => {
    // 当前页源码回源：只对"仍是源页且未改过内容"的项有意义；新增页/已改页直接用草稿内容，
    // 不依赖页签（对话式改稿在「预览」页签也能发指令，曾因只在编辑页签才拉导致空页指令）。
    const cur = deck[pageIdx];
    if (!cur || !cur.url || cur.content !== null) return;
    let alive = true; setLoadingSvg(true);
    fetch(cur.url).then((r) => r.text()).then((t) => { if (alive) setBaseSvg(t); })
      .catch(() => { if (alive) setBaseSvg(''); }).finally(() => { if (alive) setLoadingSvg(false); });
    return () => { alive = false; };
  }, [deck, pageIdx]);

  const onScroll = () => {
    const box = boxRef.current; if (!box) return;
    const center = box.scrollTop + box.clientHeight / 2;
    let best = pageIdx, bd = Infinity;
    itemRefs.current.forEach((el, i) => { if (!el) return; const c = el.offsetTop + el.offsetHeight / 2; const d = Math.abs(c - center); if (d < bd) { bd = d; best = i; } });
    if (best !== pageIdx) setPageIdx(best);
  };
  const gotoPage = (i: number) => { setPageIdx(i); itemRefs.current[i]?.scrollIntoView({ behavior: 'smooth', block: 'center' }); };

  /** 把 pos 位页的内容写进整稿草稿（编辑器/源码编辑/AI 回传共用）。 */
  const applyContent = useCallback((pos: number, content: string) =>
    setDeck((arr) => arr.map((it, i) => (i === pos ? { ...it, content } : it))), []);

  // ── 页面增删（ADR-027）：全部是本地草稿操作，另存时才落盘 ──
  const insertPageAfter = () => {
    if (busy || !pages.length) return;
    const pos = pageIdx + 1;
    const item: DeckItem = { key: `new-${Date.now()}`, ref: null, name: 'newpage', content: BLANK_PAGE_SVG };
    setDeck((arr) => [...arr.slice(0, pos), item, ...arr.slice(pos)]);
    setPageIdx(pos); // 自动切到新页：紧接着就能对这一页说"帮我加目录"
  };
  const removePage = () => {
    if (busy || !currentPage) return;
    if (deck.length <= 1) { message.warning('至少要保留一页'); return; }
    const pos = pageIdx;
    setDeck((arr) => arr.filter((_, i) => i !== pos));
    setPageIdx(Math.max(0, Math.min(pos, deck.length - 2)));
  };
  /** 当前页在整稿清单里上移/下移一格（delta=±1）；调序就是改数组顺序，另存时由引擎物化。 */
  const movePage = (delta: number) => {
    const to = pageIdx + delta;
    if (busy || !currentPage || to < 0 || to >= deck.length) return;
    setDeck((arr) => {
      const next = [...arr];
      const [item] = next.splice(pageIdx, 1);
      next.splice(to, 0, item);
      return next;
    });
    setPageIdx(to);
    // 等重渲染把 ref 重排到新位置再滚动（50ms 足够，避免滚到旧元素）
    setTimeout(() => itemRefs.current[to]?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 50);
  };
  const resetDeck = () => {
    setDeck(pages.map((p) => ({ key: `src-${p.index}`, ref: p.index, name: p.name, content: null, url: p.url })));
    setPageIdx(0); setStatus(null);
  };

  const sendInstruction = async () => {
    const text = instruction.trim();
    if (!text) { message.warning('请先填写修改要求'); return; }
    if (!currentPage) { message.warning('请先选择要修改的页面'); return; }
    if (!editValue.trim()) { message.warning('当前页尚未加载完成，请稍后重试'); return; }
    setChatBusy(true);
    // 真流式进度：计时器 + AI 输出分片（字符数/补丁数）都汇到这一行状态文案上；
    // 会话 id 存下来供"取消"（后端杀进程组，SSE 会收到 error=已取消 收尾）
    const t0 = Date.now();
    const prog = { chars: 0, patches: 0 };
    const show = () => setStatus({
      text: `AI 正在修改当前页，已用时 ${Math.round((Date.now() - t0) / 1000)} 秒`,
    });
    show();
    const tick = window.setInterval(show, 1000);
    setReviseSession(null);
    try {
      const out = await revisePages(
        [{ index: pageIdx + 1, name: currentPage.name, content: editValue }], text,
        (acc) => { prog.chars = acc.length; prog.patches = (acc.match(/"search"/g) || []).length; show(); },
        (sid) => setReviseSession(sid)
      );
      // 只并入有实质内容的项：防 AI 异常回传空内容把草稿页清空
      const got = out.find((p) => p.index === pageIdx + 1 && typeof p.content === 'string' && p.content.trim())
        || out.find((p) => typeof p.content === 'string' && p.content.trim());
      if (got && typeof got.content === 'string') applyContent(pageIdx, got.content);
      setStatus({ text: 'AI 已完成修改，可继续修改其他页面，或点击「保存为新版本」。' });
      setInstruction('');
    } catch (e) {
      const msg = (e as Error).message;
      setStatus(msg.includes('已取消')
        ? { text: '已取消，当前页未发生变化。' }
        : { text: 'AI 修改未成功，请重试', error: true });
    } finally {
      window.clearInterval(tick);
      setReviseSession(null);
      setChatBusy(false);
    }
  };

  const cancelRevise = async () => {
    if (!reviseSession) { message.warning('取消功能即将可用，请稍后'); return; }
    try { await cancelAgentSession(reviseSession); }
    catch (e) { setStatus({ text: '取消未成功，请重试', error: true }); }
  };

  const commitDraft = useCallback(async () => {
    if (!selected) return;
    if (draftCount === 0) { message.warning('尚未做任何修改'); return; }
    setCommitBusy(true);
    setStatus({ text: `正在生成新版本（共 ${deck.length} 页），请稍候…` });
    try {
      // 有序整稿清单（ADR-027）：纯引用=保留源页；ref+content=就地替换；无 ref=新增页
      const deckPayload = deck.map((it) => (
        it.ref !== null
          ? (it.content !== null ? { ref: it.ref, name: it.name, content: it.content } : { ref: it.ref })
          : { name: it.name, content: stripBlankPageHint(it.content || '') }
      ));
      const body = selected.kind === 'root'
        ? { presentation_id: selected.key, deck: deckPayload }
        : { sub_presentation_id: selected.key, deck: deckPayload };
      const job = await submitRevision({ agent: 'claude', ...body } as ReviseRequest);
      let st = await getJob(job.job_id); let guard = 0;
      while ((st.status === 'queued' || st.status === 'running') && guard < 900) {
        await sleep(2000); st = await getJob(job.job_id); guard += 1;
        setStatus({ text: st.status === 'queued' ? '正在排队' : st.status === 'running' ? '正在生成新版本' : (st.status_detail || '正在生成新版本') });
      }
      if (st.status === 'completed') {
        const rows = await listSubPresentations(presentation!.presentation_id);
        const ns = rebuild(presentation!, rows);
        const fresh = ns.find((n) => n.taskId === job.job_id);
        setSelectedKey(fresh ? fresh.key : ns[ns.length - 1].key); // 新版本在列表最上方并选中
        message.success('新版本已生成');
      } else {
        // 失败必须带出后端给的原因，否则用户和排查的人都无从判断（2026-11-04 实测：
        // 上一版为去黑话把原因整段删掉，界面只剩"请重试"，连为什么被拒都查不出来）
        const why = st.error || st.status_detail || '保存新版本未成功，请重试';
        console.error('[保存新版本失败]', st.status, st.error, st.status_detail);
        setStatus({ text: why, error: true }); message.error(why);
      }
    } catch (e) {
      const why = e instanceof Error && e.message ? e.message : '开始生成新版本未成功，请重试';
      console.error('[保存新版本失败]', e);
      setStatus({ text: why, error: true }); message.error(why);
    }
    finally { setCommitBusy(false); }
  }, [selected, deck, pages, draftCount, presentation, rebuild]);

  if (!presentation) return null;

  return (
    <Drawer title={<div><div>修改这份 PPT</div><div style={{ fontSize: 12, fontWeight: 'normal', color: 'var(--text-muted)' }}>{presentation.file_name}</div></div>} placement="right" width="95%"
      open={open} onClose={onClose} destroyOnClose styles={{ body: { padding: 0, height: '100%', overflow: 'hidden' } }}>
      <div style={{ position: 'relative', display: 'flex', height: '100%', minHeight: 0 }}>
        {commitBusy && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 20, background: 'rgba(255,255,255,.72)', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12 }}>
            <Spin size="large" />
            <div>{status?.text || '正在生成新版本…'}</div>
            <Text type="secondary" style={{ fontSize: 12 }}>生成完成后会自动切换到新版本</Text>
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
                {ordinalByKey.get(n.key) ?? (n.kind === 'root' ? '原始版本' : '版本')}
              </Tag>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.short}</div>
                {n.kind === 'root' ? (
                  <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>最初生成的版本</div>
                ) : (
                  <a onClick={(e) => { e.stopPropagation(); if (n.parentKey) setSelectedKey(n.parentKey); }}
                     style={{ fontSize: 11, color: '#1677ff' }}>← 来自{ordinalByKey.get(n.parentKey ?? '') ?? '原始版本'}</a>
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
            <Segmented value={tab} onChange={(v) => setTab(v as typeof tab)} options={['预览', '拖动修改', '编辑代码']} />
            <Text type="secondary" style={{ fontSize: 13 }}>{deck.length ? `第 ${pageIdx + 1} 页 / 共 ${deck.length} 页` : '0 页'}</Text>
            <Space size={6} style={{ marginLeft: 'auto' }}>
              {draftCount > 0 && (
                <Text style={{ fontSize: 12, color: '#fa8c16', whiteSpace: 'nowrap' }}>本次共修改 {draftCount} 页</Text>
              )}
              <Tooltip title="在当前页后插入空白页">
                <Button icon={<Plus size={14} />} disabled={busy || !pages.length} onClick={insertPageAfter} />
              </Tooltip>
              <Tooltip title="当前页上移">
                <Button icon={<ArrowUp size={14} />} disabled={busy || pageIdx === 0} onClick={() => movePage(-1)} />
              </Tooltip>
              <Tooltip title="当前页下移">
                <Button icon={<ArrowDown size={14} />} disabled={busy || pageIdx >= deck.length - 1} onClick={() => movePage(1)} />
              </Tooltip>
              <Popconfirm title={`删除第 ${pageIdx + 1} 页？`} description="该页先被移除，点击「保存为新版本」后生效"
                onConfirm={removePage} disabled={busy || deck.length <= 1}>
                <Tooltip title="删除当前页">
                  <Button icon={<Trash2 size={14} />} danger disabled={busy || deck.length <= 1} />
                </Tooltip>
              </Popconfirm>
              {draftCount > 0 && (
                <Popconfirm title="放弃全部修改？" description="将清除本次所做的全部修改（新增、删除、调整顺序），回到初始状态"
                  onConfirm={resetDeck}>
                  <Tooltip title="放弃全部修改">
                    <Button icon={<RotateCcw size={14} />} disabled={busy} />
                  </Tooltip>
                </Popconfirm>
              )}
              <Button type="primary" icon={<Layers size={14} />} disabled={draftCount === 0 || commitBusy} onClick={commitDraft}>保存为新版本</Button>
            </Space>
          </div>

          <div ref={tab === '预览' ? boxRef : undefined} onScroll={tab === '预览' ? onScroll : undefined}
            style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: 16, background: 'var(--bg-subtle, #f6f7f9)' }}>
            {loadingPages ? <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
              : !deck.length ? <Empty description="该版本暂无可显示的页面" />
              : tab === '预览' ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                  {deck.map((it, i) => (
                    <div key={it.key} ref={(el) => { itemRefs.current[i] = el; }} onClick={() => gotoPage(i)}
                      style={{ outline: i === pageIdx ? '2px solid #3b82f6' : '1px solid var(--border)', outlineOffset: 2, borderRadius: 8, overflow: 'hidden', cursor: 'pointer', background: '#fff' }}>
                      <div style={{ padding: '4px 8px', fontSize: 12, color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span>第 {i + 1} 页</span>
                        {it.ref === null && <Tag color="green" style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}>新增</Tag>}
                        {it.ref !== null && it.content !== null && <Tag color="orange" style={{ marginInlineEnd: 0, fontSize: 11, lineHeight: '16px' }}>尚未保存的修改</Tag>}
                      </div>
                      <iframe title={`第 ${i + 1} 页`} src={it.content !== null ? undefined : it.url} srcDoc={it.content ?? undefined}
                        style={{ width: '100%', height: 420, border: 0, display: 'block', pointerEvents: 'none' }} />
                    </div>
                  ))}
                </div>
              ) : tab === '拖动修改' ? (
                <MultiPageSvgEditor
                  pages={deck.map((it, i) => ({ index: i + 1, name: it.name, url: it.url || '' }))}
                  draft={Object.fromEntries(deck.flatMap((it, i) => (it.content !== null ? [[i + 1, { content: it.content }]] : []))) as Record<number, { content: string }>}
                  onEdit={(index, _name, content) => applyContent(index - 1, content)}
                />
              ) : (
                <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>编辑第 {pageIdx + 1} 页的代码，修改结果先显示在预览中，不会生成新文件</div>
                  {loadingSvg && currentPage?.content === null ? <Spin /> : (
                    <TextArea value={editValue} onChange={(e) => applyContent(pageIdx, e.target.value)}
                      style={{ flex: 1, fontFamily: 'ui-monospace, Menlo, monospace', fontSize: 12 }} spellCheck={false} />
                  )}
                </div>
              )}
          </div>
        </div>

        {/* 右：对话改稿 */}
        <div style={{ width: 320, borderLeft: '1px solid var(--border)', padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}><MessageSquare size={15} /> <b>让 AI 修改</b></div>
          <Text type="secondary" style={{ fontSize: 12 }}>填写修改要求，结果先显示在预览区，点击「保存为新版本」后才会生成新文件。</Text>
          <TextArea value={instruction} onChange={(e) => setInstruction(e.target.value)}
            placeholder="例如：把标题改成深蓝色并居中，或在右下角添加页码" autoSize={{ minRows: 4, maxRows: 10 }} disabled={commitBusy} />
          <Button type="primary" loading={chatBusy} disabled={!instruction.trim() || !currentPage || commitBusy} onClick={sendInstruction} block>让 AI 改当前页</Button>
          {chatBusy && (
            <Button danger size="small" block onClick={cancelRevise}
              disabled={!reviseSession}>取消本次修改</Button>
          )}
          {status && !commitBusy && <Alert type={status.error ? 'error' : 'info'} showIcon message={<span style={{ fontSize: 12 }}>{status.text}</span>} />}
        </div>
      </div>
    </Drawer>
  );
}
