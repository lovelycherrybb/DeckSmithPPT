/**
 * 模型对话面板（仅管理员，ADR-034）。
 *
 * 解决的一件事：让管理员在网页里直接问一句，看当前生效的这套模型服务到底答什么、
 * 会不会干活。「测试连接」只回"地址对不对、密钥填没填"，回答内容一个字都不取回来，
 * 所以那一项检查代替不了这里。
 *
 * 纪律：
 * 1. **不留记录**（ADR-034 决策 5）：对话内容只在组件内存着，关闭面板即清空；
 *    接着上一轮说靠 `resumeId`——它是 AI 给的标识，界面存着、下一轮带回去，服务端不留。
 * 2. **不粉饰能力面**：这个入口把管理员的原话直接交给 AI 工具，而这个工具在服务器上
 *    能读写文件、能执行命令（实测见 ADR-034 凭据 3、4，当前版本关不严）。
 *    所以这句话写在面板上，不藏在文档里。
 * 3. **体验口径**（2026-11-07 龚成定档）：思考过程默认收起；回答可复制；
 *    往上翻读前文时不许被自动滚底拽回去；失败那轮可按"那句话发出之前"的上下文重试；
 *    回答中可先写好下一句，本轮结束自动发出——一次只许排一条，排了就写明原因。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Divider,
  Drawer,
  Input,
  Space,
  Spin,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { Ban, ChevronDown, Copy, Eraser } from 'lucide-react';
import MarkdownView from '@/components/ui/MarkdownView';
import { ApiError, cancelAgentSession, chatWithAgent } from '@/lib/api';

const { Paragraph, Text } = Typography;

type Turn = {
  id: number;
  role: 'user' | 'ai';
  text: string;
  thinking?: string;
  /** AI 这一轮失败时的人话原因（不写英文枚举值、不写服务器路径） */
  error?: string;
  /** 重试凭据：这轮当时问的那句话，以及它发出之前的上下文标识（ADR-034 追记口径） */
  retryMessage?: string;
  retryFrom?: string | null;
};

function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.message || '请求失败，请稍后重试';
  return e instanceof Error ? e.message : '这次回答没有取回来，请重试';
}

/** 复制到剪贴板。页面不走 https 时浏览器不给 navigator.clipboard，退回老办法。 */
async function copyToClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const helper = document.createElement('textarea');
  helper.value = text;
  helper.style.position = 'fixed';
  helper.style.opacity = '0';
  document.body.appendChild(helper);
  helper.select();
  document.execCommand('copy');
  document.body.removeChild(helper);
}

export default function AgentChatPanel({ open, onClose }: {
  open: boolean;
  onClose: () => void;
}) {
  const [messageApi, contextHolder] = message.useMessage();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  /** 排队中的下一句：一次只许排一条（2026-11-07 定档） */
  const [queued, setQueued] = useState<string | null>(null);
  /** AI 给的对话标识：下一轮带回去就能接着说。关掉面板即清零（不留记录） */
  const [resumeId, setResumeId] = useState<string | null>(null);
  /** 这次处理的标识，只供「停止回答」用 */
  const [currentHandle, setCurrentHandle] = useState<string | null>(null);
  /** 展开了思考过程的那几轮（默认收起） */
  const [thinkingOpen, setThinkingOpen] = useState<Set<number>>(new Set());

  const listRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const resumeRef = useRef<string | null>(null);
  const queuedRef = useRef<string | null>(null);
  /** 是否有一轮在跑：用 ref 不用 state，收尾时排队那条才接得上（state 会拿到旧值） */
  const runningRef = useRef(false);
  /** 只有浏览者停在底部才自动跟滚；往上翻了就不打扰（2026-11-07 定档） */
  const stickToBottom = useRef(true);

  const adopt = useCallback((id: string | null) => {
    resumeRef.current = id;
    setResumeId(id);
  }, []);

  // 新内容把列表滚到底——但仅当人还停在底部
  useEffect(() => {
    const box = listRef.current;
    if (box && stickToBottom.current) box.scrollTop = box.scrollHeight;
  }, [turns, busy]);

  const onListScroll = useCallback(() => {
    const box = listRef.current;
    if (!box) return;
    stickToBottom.current = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
  }, []);

  /** 关闭即清空：对话不留记录是 ADR-034 决策 5 定下的口径，不是没做保存 */
  const resetAll = useCallback(() => {
    setTurns([]);
    setDraft('');
    setQueued(null);
    queuedRef.current = null;
    adopt(null);
    setCurrentHandle(null);
    setThinkingOpen(new Set());
    stickToBottom.current = true;
  }, [adopt]);

  const close = () => {
    // 关掉即不留记录：在跑的那一轮也一并叫停，免得它背着人在服务器上继续花积分
    if (runningRef.current && currentHandle) {
      void cancelAgentSession(currentHandle).catch(() => undefined);
    }
    resetAll();
    onClose();
  };

  const stop = async () => {
    if (!currentHandle) return;
    try {
      await cancelAgentSession(currentHandle);
    } catch (e) {
      setTurns((prev) => prev.map((t, i) => (
        i === prev.length - 1 && t.role === 'ai'
          ? { ...t, error: describeError(e) }
          : t
      )));
    }
  };

  const runTurn = async (text: string, resumeAt: string | null) => {
    if (runningRef.current) return; // 一条会话同时只许一轮在跑，排队那条在收尾时自动接上
    runningRef.current = true;
    const aiId = nextId.current++;
    setTurns((prev) => [...prev, {
      id: aiId, role: 'ai', text: '',
      retryMessage: text, retryFrom: resumeAt,
    }]);
    setBusy(true);
    adopt(resumeAt);

    const patch = (fn: (t: Turn) => Turn) =>
      setTurns((prev) => prev.map((t) => (t.id === aiId ? fn(t) : t)));

    try {
      await chatWithAgent(text, resumeAt, {
        onDelta: (accumulated) => patch((t) => ({ ...t, text: accumulated })),
        onThinking: (accumulated) => patch((t) => ({ ...t, thinking: accumulated })),
        onSession: (handle) => setCurrentHandle(handle),
        onResume: (id) => adopt(id),
      });
      patch((t) => ({ ...t, text: t.text || '（AI 没有返回内容）' }));
    } catch (e) {
      patch((t) => ({ ...t, error: describeError(e) }));
    } finally {
      setBusy(false);
      setCurrentHandle(null);
      runningRef.current = false;
      const next = queuedRef.current;
      if (next) {
        queuedRef.current = null;
        setQueued(null);
        void runTurn(next, resumeRef.current);
      }
    }
  };

  /** 统一入口：闲时直接问，忙时排队（只排一条），排满了由占位提示说明原因 */
  const submit = (raw: string) => {
    const text = raw.trim();
    if (!text || queuedRef.current) return;
    if (busy) {
      queuedRef.current = text;
      setQueued(text);
      setDraft('');
      return;
    }
    const userId = nextId.current++;
    setTurns((prev) => [...prev, { id: userId, role: 'user', text }]);
    stickToBottom.current = true;
    setDraft('');
    void runTurn(text, resumeRef.current);
  };

  /** 重试：退回那一轮发问之前的上下文，把原话再问一遍（不是接着失败那轮继续） */
  const retry = (turn: Turn) => {
    if (busy || !turn.retryMessage) return;
    const text = turn.retryMessage;
    setTurns((prev) => prev.filter((t) => t.id !== turn.id));
    stickToBottom.current = true;
    void runTurn(text, turn.retryFrom ?? null);
  };

  const withdrawQueued = () => {
    const text = queuedRef.current;
    queuedRef.current = null;
    setQueued(null);
    if (text) setDraft(text); // 撤回的字放回输入框，不丢
  };

  const copyAnswer = async (text: string) => {
    try {
      await copyToClipboard(text);
      messageApi.success('已复制');
    } catch {
      messageApi.error('复制未成功，请手动划选');
    }
  };

  const toggleThinking = (id: number) => {
    setThinkingOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <Drawer
      title="模型对话"
      placement="right"
      width={680}
      open={open}
      onClose={close}
    >
      {contextHolder}
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert
          type="warning"
          showIcon
          message="这段对话会把你的原话直接交给服务器上的 AI 工具"
          description="该工具能读写服务器上的文件、执行命令，当前版本无法关闭这些能力。请只问你要问的内容，不要把命令本身写进去。这段对话不会保存，关闭面板后即清空；每次发起真实请求会消耗积分。"
        />

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {resumeId ? '接着上一轮说' : '尚未开始，第一句话会开一段新对话'}
          </Text>
          <Button
            size="small"
            icon={<Eraser size={14} />}
            disabled={busy || turns.length === 0}
            onClick={resetAll}
          >
            新对话
          </Button>
        </div>

        <div
          ref={listRef}
          onScroll={onListScroll}
          style={{
            maxHeight: 'calc(100vh - 340px)',
            minHeight: 200,
            overflowY: 'auto',
            padding: '12px 14px',
            border: '1px solid var(--border)',
            borderRadius: 8,
            background: 'var(--bg-subtle, #f6f7f9)',
          }}
        >
          {turns.length === 0 && !busy && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              这里还没有内容。先问一句，例如「用三句话说明你能做什么」。
            </Text>
          )}
          {turns.map((t) => (
            <div key={t.id} style={{ marginBottom: 14 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {t.role === 'user' ? '你' : 'AI'}
                </Text>
                {t.role === 'ai' && t.text && (
                  <Button
                    type="text"
                    size="small"
                    icon={<Copy size={13} />}
                    onClick={() => void copyAnswer(t.text)}
                  >
                    复制
                  </Button>
                )}
              </div>
              {t.role === 'user' ? (
                <div style={{ fontSize: 13, whiteSpace: 'pre-wrap', marginTop: 2 }}>{t.text}</div>
              ) : (
                <>
                  {t.thinking && (
                    <>
                      {/* 思考过程默认收起：它是推理过程，不是正文（2026-11-07 定档） */}
                      <button
                        type="button"
                        onClick={() => toggleThinking(t.id)}
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 2,
                          border: 'none', background: 'none', padding: 0,
                          color: 'var(--text-muted)', fontSize: 12, cursor: 'pointer',
                          marginTop: 2,
                        }}
                      >
                        <ChevronDown
                          size={12}
                          style={{ transform: thinkingOpen.has(t.id) ? undefined : 'rotate(-90deg)' }}
                        />
                        思考过程
                      </button>
                      {thinkingOpen.has(t.id) && (
                        <>
                          <div style={{
                            fontSize: 12, color: 'var(--text-muted)', whiteSpace: 'pre-wrap',
                            maxHeight: 160, overflowY: 'auto', marginTop: 4,
                          }}>
                            {t.thinking}
                          </div>
                          <Divider style={{ margin: '8px 0' }} />
                        </>
                      )}
                    </>
                  )}
                  {t.text && (
                    <div style={{ fontSize: 13, marginTop: 2 }}>
                      <MarkdownView content={t.text} />
                    </div>
                  )}
                  {t.error && (
                    <>
                      <Paragraph type="danger" style={{ fontSize: 13, marginBottom: 4 }}>
                        {t.error}
                      </Paragraph>
                      {t.retryMessage && (
                        <Button size="small" disabled={busy} onClick={() => retry(t)}>
                          重试
                        </Button>
                      )}
                    </>
                  )}
                  {!t.text && !t.error && busy && (
                    <Space size={6} style={{ marginTop: 4 }}>
                      <Spin size="small" />
                      <Text type="secondary" style={{ fontSize: 12 }}>AI 正在回答</Text>
                    </Space>
                  )}
                </>
              )}
            </div>
          ))}
        </div>

        <Input.TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              submit(draft);
            }
          }}
          placeholder={queued ? '已有一条在排队，等本轮结束'
            : busy ? 'AI 正在回答，可先写下一条，发送后自动排队'
              : '写下要问的内容，回车发送，Shift 加回车换行'}
          autoSize={{ minRows: 2, maxRows: 6 }}
          disabled={!!queued}
        />
        {queued && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              已排队，本轮回答结束后自动发送
            </Text>
            <Button size="small" onClick={withdrawQueued}>撤回</Button>
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          {/* 灰着必须讲清原因，否则只会以为按钮坏了（2026-11-04 全站定下的口径） */}
          <Tooltip title={busy && !currentHandle ? '本轮正在开始，稍候即可停止'
            : !busy ? '现在没有在进行的回答' : undefined}>
            <span style={{ display: 'inline-block' }}>
              <Button icon={<Ban size={14} />} disabled={!busy || !currentHandle}
                      onClick={() => void stop()}>
                停止回答
              </Button>
            </span>
          </Tooltip>
          <Button
            type="primary"
            disabled={!!queued || !draft.trim()}
            onClick={() => submit(draft)}
          >
            发送
          </Button>
        </div>
      </Space>
    </Drawer>
  );
}
