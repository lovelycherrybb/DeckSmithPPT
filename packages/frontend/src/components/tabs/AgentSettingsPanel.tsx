/**
 * Agent 设置（仅管理员，ADR-032）。
 *
 * 这块解决一件事：**换模型供应商（地址 / token / 模型名）不用重建容器**。
 * 生效方式是"写进 agent 的配置文件"——CLI 每接一个任务都新起进程并直接读那个文件，
 * 所以保存并启用后，**下一个任务就用新供应商，正在跑的任务不受打扰**。
 *
 * 三条界面纪律（都是被教训逼出来的，不是装饰）：
 * 1. **字段来自后端注册表**：本文件不认识任何 `ANTHROPIC_*` 变量名，也不写死 agent 名。
 *    加一个 agent 或加一项配置，这里自动长出来（与系统设置、引擎参数表单同一套原则）。
 * 2. **来源必须逐项说清**：一项值可能来自档案、来自配置文件里的外来键、
 *    或来自部署环境变量（.env 仍保留兼容）。分不清来源 = 管理员改完不知道成没成。
 * 3. **凭据不回显、不外传**：输入框留空表示"不改这一项"；界面里出现的都是掩码。
 */
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Divider,
  Input,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { KeyRound, Plug, Plus, Save, ToggleRight, Trash2 } from 'lucide-react';
import {
  ApiError,
  activateProvider,  createProvider,
  deleteProvider,
  getSettingsFile,
  listAgents,
  listProviders,
  probeProvider,
  saveSettingsFile,
  updateProvider,
  type AgentField,
  type AgentInfo,
  type AgentProviderProfile,
  type ProviderProbeResult,
  type SettingsFileView,
} from '@/lib/api';

const { Paragraph, Text } = Typography;

type DraftValues = Record<string, string>;

/** 档案表单的初值：secret 一律留空（留空＝不改），其余用后端回的原值。 */
function initialValues(agent: AgentInfo, profile?: AgentProviderProfile | null): DraftValues {
  const out: DraftValues = {};
  for (const field of agent.fields) {
    if (field.secret) {
      out[field.key] = '';
      continue;
    }
    out[field.key] = String(profile?.values?.[field.key] ?? '');
  }
  return out;
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.message || `请求失败（HTTP ${e.status}）`;
  return e instanceof Error ? e.message : '操作未成功，请重试';
}

export default function AgentSettingsPanel() {
  const [messageApi, contextHolder] = message.useMessage();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [profiles, setProfiles] = useState<AgentProviderProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [lastActivate, setLastActivate] = useState<string[] | null>(null);

  // 档案编辑弹窗
  const [editing, setEditing] = useState<AgentProviderProfile | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editValues, setEditValues] = useState<DraftValues>({});
  const [savingProfile, setSavingProfile] = useState(false);

  // 探针结果
  const [probe, setProbe] = useState<ProviderProbeResult | null>(null);
  /** 探测中的配置 id：只让被点的那一行转圈，不能让整表一起转（2026-11-04 龚成指出） */
  const [probingId, setProbingId] = useState<string | null>(null);
  /** 配置文件区默认收起：整屏文字压到一屏标题加一句说明，需要改高级项时才展开 */
  const [fileSectionOpen, setFileSectionOpen] = useState(false);

  // 配置文件视图
  const [fileAgent, setFileAgent] = useState<string>('');
  const [fileName, setFileName] = useState<string>('');
  const [fileView, setFileView] = useState<SettingsFileView | null>(null);
  const [fileDraft, setFileDraft] = useState('');
  const [savingFile, setSavingFile] = useState(false);
  /** 只用于"让下方文件视图重新拉一次"的计数器。启用档案会重写磁盘上的文件，
   *  下方不跟着刷新的话，管理员看到的是"还不存在"，而磁盘上其实已经有了。 */
  const [fileNonce, setFileNonce] = useState(0);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [agentRes, providerRes] = await Promise.all([listAgents(), listProviders()]);
      setAgents(agentRes.agents);
      setProfiles(providerRes.profiles);
      if (!fileAgent && agentRes.agents.length > 0) {
        setFileAgent(agentRes.agents[0].id);
        setFileName(agentRes.agents[0].files[0]?.name ?? '');
      }
      setFileNonce((n) => n + 1);
      setLoadError(null);
    } catch (e) {
      setLoadError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, [fileAgent]);

  useEffect(() => {
    void reload();
    // 只在挂载时拉一次：reload 依赖 fileAgent，放进去会每次选文件都全量重拉
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const agentById = useMemo(() => {
    const map: Record<string, AgentInfo> = {};
    agents.forEach((a) => { map[a.id] = a; });
    return map;
  }, [agents]);

  const activeAgent = agentById[fileAgent] ?? agents[0];

  // ── 档案操作 ──
  /** 关弹窗时必须清掉表单值：antd 的 Modal 关闭后仍挂在 DOM 里，
   *  刚敲进去的凭据要是不清，页面被人翻 DOM 就能读到。 */
  const closeEditor = () => {
    setEditOpen(false);
    setEditing(null);
    setEditName('');
    setEditValues({});
    setProbe(null);
  };

  const openCreate = () => {
    const agent = agents[0];
    if (!agent) return;
    setEditing(null);
    setEditName('');
    setEditValues(initialValues(agent));
    setEditOpen(true);
    setProbe(null);
  };

  const openEdit = (profile: AgentProviderProfile) => {
    const agent = agentById[profile.agent_id];
    if (!agent) return;
    setEditing(profile);
    setEditName(profile.name);
    setEditValues(initialValues(agent, profile));
    setEditOpen(true);
    setProbe(null);
  };

  const submitProfile = async () => {
    const agentId = editing?.agent_id ?? agents[0]?.id;
    const agent = agentById[agentId];
    if (!agent || !editName.trim()) {
      messageApi.error('请填写配置名称，便于切换时识别');
      return;
    }
    // 只提交"填过的"字段：secret 留空＝保持原值不动，别的字段留空＝显式清空
    const values: DraftValues = {};
    for (const field of agent.fields) {
      const raw = (editValues[field.key] ?? '').trim();
      if (field.secret && !raw) continue;
      values[field.key] = raw;
    }
    setSavingProfile(true);
    try {
      if (editing) {
        const out = await updateProvider(editing.id, { agent_id: agentId, name: editName, values });
        (out.warnings ?? []).forEach((w) => messageApi.warning(w));
        messageApi.success(out.is_active ? '已保存并写入配置文件，下一次生成时生效' : '已保存');
      } else {
        await createProvider({ agent_id: agentId, name: editName, values });
        messageApi.success('已新建配置，尚未启用；点击「启用」后才会写入配置文件');
      }
      closeEditor();
      await reload();
    } catch (e) {
      messageApi.error(describeError(e));
    } finally {
      setSavingProfile(false);
    }
  };

  const doActivate = async (profile: AgentProviderProfile) => {
    try {
      const res = await activateProvider(profile.id);
      setLastActivate(res.warnings ?? []);
      if (res.gaps?.length) {
        messageApi.warning(`已启用「${profile.name}」，但缺这些项：${res.gaps.join('、')}`);
      } else {
        messageApi.success(`已启用「${profile.name}」，${res.restart_required ? '需重启服务后生效' : '下一次生成时生效'}`);
      }
      await reload();
    } catch (e) {
      messageApi.error(describeError(e));
    }
  };

  const doDelete = async (profile: AgentProviderProfile) => {
    try {
      await deleteProvider(profile.id);
      messageApi.success(`已删除配置「${profile.name}」`);
      await reload();
    } catch (e) {
      messageApi.error(describeError(e));
    }
  };

  const doProbe = async (profile: AgentProviderProfile) => {
    setProbingId(profile.id);
    setProbe(null);
    try {
      const res = await probeProvider(profile.id);
      setProbe(res);
      messageApi[res.ok ? 'success' : 'warning'](
        res.ok ? '检查通过：服务地址、密钥、模型名称均有效' : '检查未通过，下方列出每一项的具体原因');
    } catch (e) {
      messageApi.error(describeError(e));
    } finally {
      setProbingId(null);
    }
  };

  // ── 配置文件 ──
  const loadFile = useCallback(async (agentId: string, name: string) => {
    if (!agentId || !name) return;
    try {
      const view = await getSettingsFile(agentId, name);
      setFileView(view);
      setFileDraft(view.raw ?? '');
    } catch (e) {
      messageApi.error(describeError(e));
    }
  }, [messageApi]);

  useEffect(() => {
    if (fileAgent && fileName) void loadFile(fileAgent, fileName);
  }, [fileAgent, fileName, fileNonce, loadFile]);

  const submitFile = async () => {
    if (!fileView) return;
    setSavingFile(true);
    try {
      const after = await saveSettingsFile(fileView.agent_id, fileView.name, fileDraft,
        fileView.sha256 ?? null);
      setFileView(after);
      setFileDraft(after.raw ?? '');
      messageApi.success(`已写回 ${after.name}${after.backup ? `（改动前的备份：${after.backup}）` : ''}`);
    } catch (e) {
      messageApi.error(describeError(e));
    } finally {
      setSavingFile(false);
    }
  };

  const columns: ColumnsType<AgentProviderProfile> = [
    {
      title: '配置名称',
      dataIndex: 'name',
      width: 180,
      render: (name: string, row) => (
        <Space size={6}>
          <Text strong style={{ fontSize: 13 }}>{name}</Text>
          {row.is_active ? <Badge status="processing" text="使用中" /> : null}
        </Space>
      ),
    },
    {
      title: '服务地址',
      key: 'base_url',
      width: 240,
      render: (_, row) => {
        const agent = agentById[row.agent_id];
        const urlField = agent?.fields.find((f) => f.key === 'base_url');
        const url = urlField ? String(row.values?.[urlField.key] ?? '') : '';
        return url
          ? <Text style={{ fontSize: 12 }}>{url}</Text>
          : <Text type="secondary" style={{ fontSize: 12 }}>未填写时使用官方地址</Text>;
      },
    },
    {
      title: '模型名称',
      key: 'models',
      width: 200,
      render: (_, row) => {
        const agent = agentById[row.agent_id];
        const modelField = agent?.fields.find((f) => f.key === 'model');
        const value = modelField ? String(row.values?.[modelField.key] ?? '') : '';
        return value
          ? <Text style={{ fontSize: 12 }}>{value}</Text>
          : <Text type="secondary" style={{ fontSize: 12 }}>未填写</Text>;
      },
    },
    {
      title: '密钥',
      key: 'secrets',
      width: 150,
      render: (_, row) => {
        const states = Object.entries(row.secret_states ?? {}).filter(([, s]) => s.configured);
        if (!states.length) return <Tag>未填写</Tag>;
        // 只说"配了哪一种、尾号是什么"，绝不显示可用值
        return (
          <Space direction="vertical" size={2}>
            {states.map(([key, s]) => (
              <Text key={key} type="secondary" style={{ fontSize: 12 }}>
                {(row.agent_id && agentById[row.agent_id]?.fields.find((f) => f.key === key)?.label) || key}
                ：{s.masked || '已填写'}
              </Text>
            ))}
          </Space>
        );
      },
    },
    {
      title: '问题提醒',
      key: 'gaps',
      width: 220,
      render: (_, row) => (row.gaps?.length
        ? <Tag color="red">缺（{row.gaps.join('、')}），启用后可能当场报错</Tag>
        : <Text type="secondary" style={{ fontSize: 12 }}>—</Text>),
    },
    {
      // 全站口径：操作列钉在列尾、按内容写足宽度，并给 Table 配 scroll.x（否则固定列不生效）
      title: '操作',
      key: 'actions',
      fixed: 'right',
      width: 300,
      render: (_, row) => (
        <Space size={4} wrap>
          <Tooltip title={row.is_active ? '该配置正在使用中，无需重复启用' : undefined}>
            <span style={{ display: 'inline-block' }}>
              <Button size="small" icon={<ToggleRight size={14} />} disabled={row.is_active}
                      onClick={() => void doActivate(row)}>
                {row.is_active ? '使用中' : '启用'}
              </Button>
            </span>
          </Tooltip>
          <Button size="small" onClick={() => openEdit(row)}>编辑</Button>
          <Button size="small" icon={<Plug size={14} />} loading={probingId === row.id}
                  onClick={() => void doProbe(row)}>
            测试连接
          </Button>
          <Popconfirm title={`删除配置「${row.name}」？`} description="正在使用的配置不能删除，请先启用其他配置"
                      okText="删除" cancelText="取消" onConfirm={() => void doDelete(row)}>
            <Button size="small" danger icon={<Trash2 size={14} />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  /** 按注册表声明的 type 出控件：secret 用密码框且留空＝不改，其余用文本框。 */
  const renderField = (field: AgentField) => {
    const common = {
      style: { width: 360 },
      placeholder: field.secret ? '未填写则保持原值不变' : '',
    };
    const onInput = (value: string) =>
      setEditValues((prev) => ({ ...prev, [field.key]: value }));
    if (field.secret) {
      return <Input.Password {...common} value={editValues[field.key] ?? ''}
                             onChange={(e) => onInput(e.target.value)} />;
    }
    return <Input {...common} value={editValues[field.key] ?? ''}
                  onChange={(e) => onInput(e.target.value)} />;
  };

  if (loading && agents.length === 0) {
    return <Card variant="borderless"><Spin /></Card>;
  }

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>
      {contextHolder}
      {loadError && (
        <Alert type="error" showIcon message="模型来源设置加载失败" description={loadError}
               style={{ marginBottom: 16 }} />
      )}
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {agents.map((agent) => {
          const rows = profiles.filter((p) => p.agent_id === agent.id);
          return (
            <Card
              key={agent.id}
              title={`${agent.label} 的模型服务配置`}
              extra={<Text type="secondary" style={{ fontSize: 12 }}>
                AI 工具版本 {agent.cli_version || '未知'}
              </Text>}
            >
              <Paragraph style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                此处可保存多套配置，<strong>同一时间仅启用一套</strong>。点击「启用」后，该配置写入
                配置文件，<strong>下一次生成时生效，无需重启服务</strong>，进行中的任务不受影响。
                未启用任何配置时，沿用服务器上原有的默认设置。
              </Paragraph>
              <Divider style={{ margin: '12px 0' }} />
              <Table<AgentProviderProfile>
                rowKey="id"
                size="small"
                columns={columns}
                dataSource={rows}
                pagination={false}
                scroll={{ x: 1300 }}
                locale={{ emptyText: '还没有任何配置。点击「新建配置」，填写服务地址、密钥与模型名称，保存后点击「启用」。' }}
              />
              <Space style={{ marginTop: 12 }}>
                <Button type="primary" icon={<Plus size={14} />} onClick={openCreate}>新建配置</Button>
                <Button icon={<KeyRound size={14} />} onClick={() => { void reload(); }}>刷新</Button>
              </Space>
              {lastActivate && lastActivate.length > 0 && (
                <Alert
                  style={{ marginTop: 12 }}
                  type="warning"
                  showIcon
                  message="启用成功了，但这些地方要确认"
                  description={<ul style={{ margin: 0, paddingLeft: 18 }}>
                    {lastActivate.map((w) => <li key={w}>{w}</li>)}
                  </ul>}
                />
              )}
              {probe && (
                <Alert
                  style={{ marginTop: 12 }}
                  type={probe.ok ? 'success' : 'error'}
                  showIcon
                  message={probe.consumed_quota
                    ? `本次检查已向 AI 服务发起真实请求，可能产生一次费用。${probe.note ?? ''}`
                    : '本次检查未发起调用，不消耗积分'}
                  description={<Space direction="vertical" size={2}>
                    {(probe.checks ?? []).map((c) => (
                      <Text key={c.key} type={c.pass ? 'secondary' : 'danger'} style={{ fontSize: 12 }}>
                        {c.pass ? '✓' : '✗'} {c.label}：{c.detail || '（暂无更多信息，可联系技术支持）'}
                      </Text>
                    ))}
                  </Space>}
                />
              )}
            </Card>
          );
        })}

        {activeAgent && (
          <Card title="配置文件（系统实际使用的设置）"
                extra={<Button size="small" type="link"
                               onClick={() => setFileSectionOpen((v) => !v)}>
                        {fileSectionOpen ? '收起' : '展开'}
                      </Button>}>
            <Paragraph style={{ fontSize: 12, color: 'var(--text-muted)' }}>
              此处用于修改上方清单未涵盖的设置项；清单已涵盖的项目请回清单修改。
            </Paragraph>
            {fileSectionOpen && (<>
            <Space style={{ marginBottom: 12 }}>
              {/* antd 的 Button.Group 已弃用（会在控制台报 error），改用 Space.Compact */}
              <Space.Compact>
                {activeAgent.files.map((f) => (
                  <Button key={f.name} size="small"
                          type={fileName === f.name ? 'primary' : 'default'}
                          onClick={() => { setFileName(f.name); void loadFile(activeAgent.id, f.name); }}>
                    {f.name}{f.editable ? '' : '（只读）'}
                  </Button>
                ))}
              </Space.Compact>
            </Space>
            {fileView && (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  路径：{fileView.path}
                  {fileView.exists ? '' : (fileView.editable
                    ? '（尚未生成：启用任意一套配置后，系统会自动创建该文件）'
                    : '（该文件尚不存在。它由 AI 工具自身生成，本页面不会代为创建）')}
                </Text>
                {fileView.parse_error && (
                  <Alert type="error" showIcon message="配置文件内容存在错误，暂时无法安全修改"
                         description={fileView.parse_error + '。请先修复该文件，或重新启用一套配置生成新文件。'} />
                )}
                {fileView.foreign_env_keys.length > 0 && (
                  <Alert type="warning" showIcon message="以下设置项不通过本页面维护，但仍会生效"
                         description={fileView.foreign_env_keys.join('、')} />
                )}
                {fileView.shadow_env_keys.length > 0 && (
                  <Alert type="info" showIcon message="以下项目前由部署时的配置提供"
                         description={`${fileView.shadow_env_keys.join('、')}：本配置中未填写，当前仍沿用部署时的配置值。`
                           + '若在此配置中填写同名项，将以这里的值为准。'} />
                )}
                {fileView.masked && (
                  <Alert type="warning" showIcon message="出于安全考虑，以下显示的密钥以星号代替，与文件真实内容不一致"
                         description={`以下项目以星号显示：${fileView.masked_keys.join('、')}。保存时系统会保留原有密钥，不会把星号写入文件。`} />
                )}
                <Input.TextArea
                  rows={12}
                  value={fileDraft}
                  readOnly={!fileView.editable}
                  onChange={(e) => setFileDraft(e.target.value)}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
                />
                <Space>
                  {/* 置灰必须讲清原因，否则用户只会以为按钮坏了（2026-11-04 实测：无改动时按钮不可点且无任何说明） */}
                  <Tooltip title={!fileView.editable ? '该文件不支持在本页面修改'
                    : fileDraft === (fileView.raw ?? '') ? '内容未发生改动，无需保存' : undefined}>
                    <span style={{ display: 'inline-block' }}>
                      <Button type="primary" icon={<Save size={14} />} loading={savingFile}
                              disabled={!fileView.editable || fileDraft === (fileView.raw ?? '')}
                              onClick={() => void submitFile()}>
                        保存这个文件
                      </Button>
                    </span>
                  </Tooltip>
                  <Button onClick={() => void loadFile(fileView.agent_id, fileView.name)}>放弃未保存的修改</Button>
                  {fileView.note && <Text type="secondary" style={{ fontSize: 12 }}>{fileView.note}</Text>}
                </Space>
              </Space>
            )}
            </>
            )}
          </Card>
        )}
      </Space>

      <Modal
        open={editOpen}
        title={editing ? `编辑配置「${editing.name}」` : '新建配置'}
        onCancel={closeEditor}
        onOk={() => void submitProfile()}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
        confirmLoading={savingProfile}
        width={720}
      >
        {agents[0] && (
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div>
              <Text strong style={{ fontSize: 13 }}>配置名称</Text>
              <Input value={editName} onChange={(e) => setEditName(e.target.value)}
                     placeholder="例如：公司专用通道、备用方案一" style={{ width: 360, marginTop: 4 }} />
            </div>
            <Divider style={{ margin: '4px 0' }} />
            {(agentById[editing?.agent_id ?? agents[0].id]?.fields ?? []).map((field) => (
              <div key={field.key}>
                <Space size={6}>
                  <Text strong style={{ fontSize: 13 }}>{field.label}</Text>
                  {field.required_for_gateway ? <Tag color="orange">使用第三方服务时必填</Tag> : null}
                  {field.secret ? <Tag icon={<KeyRound size={11} />}>密钥</Tag> : null}
                </Space>
                <div><Text type="secondary" style={{ fontSize: 12 }}>{field.description}</Text></div>
                <div style={{ marginTop: 4 }}>
                  {renderField(field)}
                </div>
                {field.secret && editing?.secret_states?.[field.key]?.configured && (
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    当前已保存的密钥：{editing.secret_states[field.key].masked}
                  </Text>
                )}
              </div>
            ))}
          </Space>
        )}
      </Modal>
    </div>
  );
}
