'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Divider,
  Layout,
  Segmented,
  Select,
  Space,
  Spin,
  Tag,
  Typography,
  message,
  Upload,
} from 'antd';
import {
  AlertCircle,
  ArrowDownToLine,
  ChevronRight,
  Download,
  Eye,
  FileText,
  History,
  RefreshCw,
  Save,
  Sparkles,
  UploadCloud,
} from 'lucide-react';

const { Header, Content } = Layout;
const { TextArea } = Input;
const { Title, Text } = Typography;

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const PAGE_SIZE = 12;
const HERO_IMAGE =
  'https://images.unsplash.com/photo-1552664730-d307ca884978?auto=format&fit=crop&w=1600&q=80';

const STATUS_META = {
  draft: { color: 'blue', label: '草稿' },
  running: { color: 'gold', label: '运行中' },
  completed: { color: 'green', label: '已完成' },
  failed: { color: 'red', label: '失败' },
  timeout: { color: 'orange', label: '超时' },
};

const SOURCE_OPTIONS = [
  { label: '上传文件', value: 'upload' },
  { label: '粘贴文本', value: 'text' },
];

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function requestJson(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
    },
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || `请求失败 (${response.status})`);
  }

  return payload;
}

async function uploadSourceFile(file) {
  const formData = new FormData();
  formData.append('file', file);
  const response = await fetch(apiUrl('/api/upload'), {
    method: 'POST',
    body: formData,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload?.detail || payload?.message || '上传失败');
  }
  return payload;
}

function statusTag(status) {
  const meta = STATUS_META[status] || { color: 'default', label: status || '-' };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function getEngineSchema(engines, name) {
  return engines.find((engine) => engine.name === name)?.params_schema || {};
}

function buildEngineDefaults(schema) {
  const defaults = {};
  const props = schema?.properties || {};
  Object.entries(props).forEach(([key, value]) => {
    if (key === 'source') return;
    if (value?.default !== undefined) {
      defaults[key] = value.default;
      return;
    }
    if (value?.enum?.length) {
      defaults[key] = value.enum[0];
      return;
    }
    if (value?.type === 'boolean') {
      defaults[key] = false;
    }
  });
  return defaults;
}

function pickEngineParams(schema, values) {
  const props = schema?.properties || {};
  const result = {};
  Object.keys(props).forEach((key) => {
    if (key === 'source') return;
    if (values[key] !== undefined) {
      result[key] = values[key];
    }
  });
  return result;
}

function summarizeEngineParams(params) {
  const rows = [];
  if (params.style) rows.push(`风格：${params.style}`);
  if (params.mode) rows.push(`模式：${params.mode}`);
  if (params.format) rows.push(`画布：${params.format}`);
  if (typeof params.notes === 'boolean') rows.push(`备注：${params.notes ? '开启' : '关闭'}`);
  if (params.animation) rows.push(`动画：${params.animation}`);
  return rows.length ? rows.join(' / ') : '未选择';
}

function summarizeContentStyle(value) {
  const map = {
    marketing_promo: '宣传材料',
    product_story: '产品介绍',
    briefing: '方案汇报',
    event_promo: '活动推广',
    investment_pitch: '招商介绍',
  };
  return map[value] || value || '未选择';
}

function normalizeJob(job) {
  return {
    ...job,
    engine_params: job.engine_params || {},
    title: job.title || '',
    pages: job.pages || 0,
    engine: job.engine || '',
    agent: job.agent || '',
    draft_content: job.draft_content || '',
  };
}

const CONTENT_STYLE_OPTIONS = [
  { value: 'marketing_promo', label: '宣传材料', description: '更像营销落地页或对外宣传稿' },
  { value: 'product_story', label: '产品介绍', description: '突出卖点、价值和使用场景' },
  { value: 'briefing', label: '方案汇报', description: '更偏正式汇报和结构化表达' },
  { value: 'event_promo', label: '活动推广', description: '更有动员感和传播感' },
  { value: 'investment_pitch', label: '招商介绍', description: '更关注规模、机会和合作空间' },
];

function ContentStylePicker({ value, onChange }) {
  return (
    <div className="content-style-grid">
      {CONTENT_STYLE_OPTIONS.map((item) => {
        const active = value === item.value;
        return (
          <button
            key={item.value}
            type="button"
            className={`content-style-card${active ? ' active' : ''}`}
            onClick={() => onChange?.(item.value)}
          >
            <strong>{item.label}</strong>
            <span>{item.description}</span>
          </button>
        );
      })}
    </div>
  );
}

function renderEngineFields(schema) {
  const props = schema?.properties || {};
  return Object.entries(props)
    .filter(([key]) => key !== 'source')
    .map(([name, meta]) => {
      const labelMap = {
        style: '风格',
        mode: '模式',
        format: '画布',
        notes: '演讲备注',
        animation: '动画',
      };
      const label = labelMap[name] || name;

      let control = <Input placeholder={meta?.description || label} />;
      let itemProps = {};

      if (meta?.enum?.length) {
        control = (
          <Select
            options={meta.enum.map((value) => ({ value, label: value }))}
            placeholder={meta?.description || label}
          />
        );
      } else if (meta?.type === 'boolean') {
        control = <Segmented options={[{ label: '开', value: true }, { label: '关', value: false }]} />;
        itemProps = { valuePropName: 'value' };
      } else if (meta?.type === 'integer' || meta?.type === 'number') {
        control = <InputNumber style={{ width: '100%' }} min={0} />;
      }

      return (
        <Form.Item
          key={name}
          className="param-item"
          name={name}
          label={label}
          tooltip={meta?.description}
          valuePropName={meta?.type === 'boolean' ? 'value' : undefined}
          {...itemProps}
        >
          {control}
        </Form.Item>
      );
    });
}

function JobDetailDrawer({
  open,
  job,
  draftText,
  onDraftTextChange,
  onClose,
  onSaveDraft,
  onConfirm,
  onDownload,
  saving,
  confirming,
}) {
  const engineSummary = summarizeEngineParams(job?.engine_params || {});

  return (
    <Drawer
      title={job ? `任务 ${job.job_id}` : '任务详情'}
      width={860}
      open={open}
      onClose={onClose}
      destroyOnClose={false}
      extra={
        job ? (
          <Space>
            <Button onClick={onClose}>关闭</Button>
            <Button icon={<ArrowDownToLine size={16} />} onClick={onDownload} disabled={job.status !== 'completed'}>
              下载
            </Button>
            <Button icon={<Save size={16} />} onClick={onSaveDraft} loading={saving} disabled={job.status !== 'draft'}>
              保存草稿
            </Button>
            <Button type="primary" icon={<Sparkles size={16} />} onClick={onConfirm} loading={confirming} disabled={job.status !== 'draft'}>
              确认生成
            </Button>
          </Space>
        ) : null
      }
    >
      {!job ? (
        <Spin />
      ) : (
        <div className="drawer-grid">
          <Descriptions bordered size="small" column={2}>
            <Descriptions.Item label="标题">{job.title || '-'}</Descriptions.Item>
            <Descriptions.Item label="状态">{statusTag(job.status)}</Descriptions.Item>
            <Descriptions.Item label="引擎">{job.engine || '-'}</Descriptions.Item>
            <Descriptions.Item label="页数">{job.pages || '-'}</Descriptions.Item>
            <Descriptions.Item label="Agent">{job.agent || '-'}</Descriptions.Item>
            <Descriptions.Item label="更新时间">{job.updated_at || '-'}</Descriptions.Item>
            <Descriptions.Item label="参数摘要" span={2}>
              {engineSummary}
            </Descriptions.Item>
          </Descriptions>

          {job.status === 'draft' ? (
            <div className="drawer-editor">
              <Title level={5} style={{ margin: 0 }}>
                草稿内容
              </Title>
              <TextArea
                value={draftText}
                onChange={(event) => onDraftTextChange(event.target.value)}
                autoSize={{ minRows: 18, maxRows: 28 }}
                spellCheck={false}
              />
            </div>
          ) : (
            <Alert
              type={job.status === 'failed' ? 'error' : 'info'}
              icon={<AlertCircle size={16} />}
              message={job.status === 'draft' ? '草稿可编辑' : '当前任务只读'}
              description={
                job.status === 'completed'
                  ? '任务已完成，可直接下载结果。'
                  : job.error || '该任务当前不可编辑。'
              }
              showIcon
            />
          )}
        </div>
      )}
    </Drawer>
  );
}

function App() {
  const [messageApi, contextHolder] = message.useMessage();
  const [form] = Form.useForm();
  const watchedValues = Form.useWatch([], form) || {};
  const engineName = Form.useWatch('engine', form) || 'ppt_master';
  const sourceMode = Form.useWatch('sourceMode', form) || 'upload';

  const [engines, setEngines] = useState([]);
  const [engineLoading, setEngineLoading] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [jobsFilter, setJobsFilter] = useState('all');
  const [jobsOffset, setJobsOffset] = useState(0);
  const [jobsHasMore, setJobsHasMore] = useState(true);
  const [currentJob, setCurrentJob] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [drawerSaving, setDrawerSaving] = useState(false);
  const [drawerConfirming, setDrawerConfirming] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState(null);

  const selectedSchema = useMemo(() => getEngineSchema(engines, engineName), [engines, engineName]);
  const engineOptions = useMemo(
    () => engines.map((engine) => ({ value: engine.name, label: engine.display_name || engine.name })),
    [engines]
  );

  useEffect(() => {
    void loadEngines();
    void loadJobs({ reset: true, nextFilter: 'all' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const defaults = buildEngineDefaults(selectedSchema);
    form.setFieldsValue(defaults);
  }, [form, selectedSchema, engineName]);

  async function loadEngines() {
    setEngineLoading(true);
    try {
      const data = await requestJson('/api/engines');
      setEngines(data);
      const first = data[0];
      if (first?.name) {
        form.setFieldsValue({ engine: first.name });
      }
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setEngineLoading(false);
    }
  }

  async function loadJobs({ reset = false, nextFilter = jobsFilter } = {}) {
    setJobsLoading(true);
    try {
      const offset = reset ? 0 : jobsOffset;
      const params = new URLSearchParams();
      if (nextFilter !== 'all') {
        params.set('status', nextFilter);
      }
      params.set('limit', String(PAGE_SIZE));
      params.set('offset', String(offset));
      const data = await requestJson(`/api/jobs?${params.toString()}`);
      const normalized = data.map(normalizeJob);
      setJobs((prev) => (reset ? normalized : [...prev, ...normalized]));
      setJobsHasMore(normalized.length === PAGE_SIZE);
      setJobsOffset(offset + normalized.length);
      if (reset) {
        setJobsFilter(nextFilter);
      }
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setJobsLoading(false);
    }
  }

  async function openJob(jobId) {
    setDrawerOpen(true);
    setDrawerSaving(false);
    setDrawerConfirming(false);
    setCurrentJob(null);
    try {
      const data = normalizeJob(await requestJson(`/api/jobs/${jobId}`));
      setCurrentJob(data);
      setDraftText(data.draft_content || '');
    } catch (error) {
      messageApi?.error(error.message);
      setDrawerOpen(false);
    }
  }

  async function handleUpload(file) {
    setUploading(true);
    try {
      const data = await uploadSourceFile(file);
      setUploadedFile(data);
      messageApi?.success(`已上传 ${data.filename}`);
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setUploading(false);
    }
    return false;
  }

  async function handleCreate(values) {
    if (sourceMode === 'upload' && !uploadedFile?.path) {
      messageApi?.error('请先上传材料');
      return;
    }
    if (sourceMode === 'text' && !values.sourceText?.trim()) {
      messageApi?.error('请填写文本内容');
      return;
    }

    const payload = {
      title: values.title,
      pages: values.pages,
      engine: values.engine,
      agent: values.agent,
      content_style: values.content_style || 'marketing_promo',
      content: sourceMode === 'text' ? values.sourceText : null,
      source:
        sourceMode === 'upload'
          ? { type: 'file_path', value: uploadedFile.path }
          : { type: 'text', value: values.sourceText },
      engine_params: pickEngineParams(selectedSchema, values),
    };

    setCreating(true);
    try {
      const job = normalizeJob(await requestJson('/api/decks', { method: 'POST', body: JSON.stringify(payload) }));
      messageApi?.success('草稿已生成');
      await loadJobs({ reset: true, nextFilter: jobsFilter });
      await openJob(job.job_id);
      setDraftText(job.draft_content || '');
      form.resetFields(['sourceText']);
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveDraft() {
    if (!currentJob) return;
    setDrawerSaving(true);
    try {
      const job = normalizeJob(
        await requestJson(`/api/jobs/${currentJob.job_id}/draft`, {
          method: 'PATCH',
          body: JSON.stringify({ content: draftText }),
        })
      );
      setCurrentJob(job);
      setDraftText(job.draft_content || draftText);
      messageApi?.success('草稿已保存');
      await loadJobs({ reset: true, nextFilter: jobsFilter });
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setDrawerSaving(false);
    }
  }

  async function handleConfirm() {
    if (!currentJob) return;
    setDrawerConfirming(true);
    try {
      const job = normalizeJob(
        await requestJson(`/api/jobs/${currentJob.job_id}/confirm`, {
          method: 'POST',
          body: JSON.stringify({
            content: draftText,
            engine_params: currentJob.engine_params || {},
          }),
        })
      );
      setCurrentJob(job);
      messageApi?.success('已进入正式生成');
      await loadJobs({ reset: true, nextFilter: jobsFilter });
    } catch (error) {
      messageApi?.error(error.message);
    } finally {
      setDrawerConfirming(false);
    }
  }

  function handleDownload() {
    if (!currentJob) return;
    window.open(apiUrl(`/api/jobs/${currentJob.job_id}/download`), '_blank', 'noopener,noreferrer');
  }

  function scrollToWorkspace() {
    window.setTimeout(() => {
      document.getElementById('workspace')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
  }

  const recentCards = jobs.slice(0, 6);

  const createPanel = (
    <div className="panel-grid">
      <div className="section-stack">
        <div className="section">
          <div className="section-head">
            <div>
              <h3>开始创作</h3>
              <p>先确定视觉风格和内容风格，再上传材料或粘贴文本。</p>
            </div>
            <Button icon={<RefreshCw size={16} />} onClick={() => void loadEngines()} loading={engineLoading}>
              刷新风格
            </Button>
          </div>

          <Form
            form={form}
            layout="vertical"
            onFinish={handleCreate}
            initialValues={{ pages: 12, agent: 'claude', sourceMode: 'upload', content_style: 'marketing_promo' }}
          >
            <div className="form-grid">
              <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]} className="span-2">
                <Input placeholder="例如：城市更新项目宣传页" />
              </Form.Item>

              <Form.Item name="pages" label="页数" rules={[{ required: true, message: '请输入页数' }]}>
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>

              <Form.Item name="engine" label="视觉引擎" rules={[{ required: true, message: '请选择引擎' }]}>
                <Select options={engineOptions} loading={engineLoading} />
              </Form.Item>

              <Form.Item name="agent" label="Agent">
                <Select
                  options={[
                    { value: 'claude', label: 'claude' },
                    { value: 'codex', label: 'codex' },
                    { value: 'hermes', label: 'hermes' },
                  ]}
                />
              </Form.Item>

              <Form.Item name="sourceMode" label="素材来源">
                <Segmented options={SOURCE_OPTIONS} block />
              </Form.Item>
            </div>

            <div className="source-panel">
              {sourceMode === 'upload' ? (
                <div className="upload-box">
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Upload beforeUpload={handleUpload} showUploadList={false} accept=".doc,.docx,.pdf,.ppt,.pptx,.md,.txt">
                      <Button icon={<UploadCloud size={16} />} loading={uploading}>
                        上传材料
                      </Button>
                    </Upload>
                    <Text type="secondary">支持 docx、pdf、pptx、md、txt 等文件，上传后转成服务器路径。</Text>
                    {uploadedFile ? (
                      <div className="upload-meta">
                        <span>{uploadedFile.filename}</span>
                        <span className="mono">{uploadedFile.path}</span>
                        <span>{uploadedFile.size} bytes</span>
                      </div>
                    ) : (
                      <div className="upload-meta">
                        <span>尚未上传文件</span>
                      </div>
                    )}
                  </Space>
                </div>
              ) : (
                <Form.Item
                  name="sourceText"
                  label="文本内容"
                  rules={[{ required: sourceMode === 'text', message: '请输入文本内容' }]}
                >
                  <TextArea rows={8} placeholder="粘贴原始材料或提纲" />
                </Form.Item>
              )}
            </div>

            <Divider style={{ margin: '16px 0' }} />

            <div className="section-head" style={{ marginBottom: 12 }}>
              <div>
                <h3>内容风格</h3>
                <p>先把拆解后的 Markdown 再转一次，决定它更像什么。</p>
              </div>
            </div>
            <Form.Item name="content_style" style={{ marginBottom: 16 }}>
              <ContentStylePicker />
            </Form.Item>

            <div className="engine-box">
              <div className="section-head" style={{ marginBottom: 0 }}>
                <div>
                  <h3>视觉风格参数</h3>
                  <p>来自 `style-guide` 的控制项。</p>
                </div>
              </div>
              {selectedSchema ? (
                <div className="param-grid">{renderEngineFields(selectedSchema)}</div>
              ) : (
                <Empty description="请选择一个引擎后再配置参数" />
              )}
            </div>

            <Divider style={{ margin: '16px 0' }} />

            <div className="toolbar">
              <Button type="primary" htmlType="submit" icon={<Sparkles size={16} />} loading={creating}>
                生成草稿
              </Button>
              <Text type="secondary">先得到可编辑草稿，再进入确认和下载。</Text>
            </div>
          </Form>
        </div>
      </div>

      <div className="section-stack">
        <div className="section">
          <div className="section-head">
            <div>
              <h3>预期产出</h3>
              <p>这里展示生成方向，不展示任务状态。</p>
            </div>
          </div>
          <div className="preview-card">
            <div className="preview-card-top">
              <span className="preview-kicker">DeckSmithPPT</span>
              <strong>{watchedValues.title || '未命名作品'}</strong>
            </div>
            <div className="preview-card-body">
              <div className="preview-block">
                <span>内容风格</span>
                <strong>{summarizeContentStyle(watchedValues.content_style)}</strong>
              </div>
              <div className="preview-block">
                <span>视觉风格</span>
                <strong>{summarizeEngineParams({ ...buildEngineDefaults(selectedSchema), ...watchedValues })}</strong>
              </div>
              <div className="preview-block">
                <span>转化方式</span>
                <strong>拆解素材 &gt; 重写结构 &gt; 生成草稿</strong>
              </div>
            </div>
          </div>
        </div>

        <div className="section">
          <div className="section-head">
            <div>
              <h3>草稿示意</h3>
              <p>提交后这里会变成可编辑的 Markdown 预览。</p>
            </div>
          </div>
          <div className="preview-box mono">{watchedValues.sourceText || uploadedFile?.path || '等待输入材料'}</div>
        </div>
      </div>
    </div>
  );

  const recentSection = (
    <div className="section">
      <div className="history-toolbar">
        <Space wrap>
          <Segmented
            value={jobsFilter}
            onChange={(value) => {
              setJobsFilter(value);
              setJobsOffset(0);
              setJobs([]);
              void loadJobs({ reset: true, nextFilter: value });
            }}
            options={[
              { label: '全部', value: 'all' },
              { label: '草稿', value: 'draft' },
              { label: '运行中', value: 'running' },
              { label: '已完成', value: 'completed' },
              { label: '失败', value: 'failed' },
              { label: '超时', value: 'timeout' },
            ]}
          />
          <Text type="secondary">最近作品只保留简洁信息，点开看详情。</Text>
        </Space>

        <Space>
          <Button icon={<RefreshCw size={16} />} onClick={() => void loadJobs({ reset: true, nextFilter: jobsFilter })} loading={jobsLoading}>
            刷新
          </Button>
          <Button
            icon={<ChevronRight size={16} />}
            onClick={() => void loadJobs({ reset: false, nextFilter: jobsFilter })}
            disabled={!jobsHasMore}
            loading={jobsLoading}
          >
            更多
          </Button>
        </Space>
      </div>

      <div className="recent-grid">
        {recentCards.length ? (
          recentCards.map((job) => (
            <article key={job.job_id} className="recent-card">
              <div className="recent-card-head">
                <div>
                  <strong>{job.title || '未命名任务'}</strong>
                  <p>{job.engine || 'ppt_master'} · {job.pages || 0} 页</p>
                </div>
                {statusTag(job.status)}
              </div>
              <p className="recent-card-body">
                {job.draft_content ? `${job.draft_content.slice(0, 120)}${job.draft_content.length > 120 ? '…' : ''}` : '暂无草稿内容'}
              </p>
              <div className="recent-card-actions">
                <Button icon={<Eye size={16} />} onClick={() => openJob(job.job_id)}>
                  继续编辑
                </Button>
                <Button
                  icon={<Download size={16} />}
                  onClick={() => window.open(apiUrl(`/api/jobs/${job.job_id}/download`), '_blank', 'noopener,noreferrer')}
                  disabled={job.status !== 'completed'}
                >
                  下载
                </Button>
              </div>
            </article>
          ))
        ) : (
          <Empty description="暂无最近作品" />
        )}
      </div>
    </div>
  );

  return (
    <Layout className="app-shell">
      {contextHolder}
      <Header className="app-header">
        <div className="brand">
          <div className="brand-mark">
            <FileText size={20} />
          </div>
          <div>
            <h1 className="brand-title">DeckSmithPPT</h1>
            <p className="brand-subtitle">把材料变成一份能直接改的 PPT 草稿</p>
          </div>
        </div>
        <div className="compact-row">
          <Button icon={<History size={16} />} onClick={() => scrollToWorkspace('history')}>
            查看历史
          </Button>
          <Button type="primary" icon={<Sparkles size={16} />} onClick={() => scrollToWorkspace('create')}>
            开始生成
          </Button>
        </div>
      </Header>

      <Content className="shell-body">
        <div
          className="hero-cover"
          style={{
            backgroundImage: `linear-gradient(135deg, rgba(15, 23, 42, 0.78), rgba(15, 23, 42, 0.35)), url(${HERO_IMAGE})`,
          }}
        >
          <div className="hero-copy-panel">
            <p className="hero-eyebrow">DeckSmithPPT</p>
            <Title level={1} className="hero-title">
              把材料变成可编辑的 PPT 草稿
            </Title>
            <p className="hero-copy">
              上传文件、粘贴文本，或者直接选引擎和风格参数，先生成草稿，再保存、确认、下载。
            </p>
            <div className="compact-row">
              <Button type="primary" icon={<Sparkles size={16} />} onClick={() => scrollToWorkspace('create')}>
                开始生成
              </Button>
              <Button icon={<History size={16} />} onClick={() => scrollToWorkspace('history')}>
                查看历史
              </Button>
            </div>
            <div className="hero-pills">
              <span>上传材料</span>
              <span>草稿编辑</span>
              <span>确认生成</span>
            </div>
          </div>
        </div>

        <div className="section" style={{ marginTop: 16 }}>
          <div className="section-head">
            <div>
              <h3>使用流程</h3>
              <p>先定引擎和参数，再生成草稿，最后确认成片。</p>
            </div>
          </div>
          <div className="feature-row">
            <div className="feature-item">
              <span>01</span>
              <strong>上传或粘贴</strong>
              <p>支持文件路径和直接文本。</p>
            </div>
            <div className="feature-item">
              <span>02</span>
              <strong>选视觉与内容风格</strong>
              <p>视觉风格和内容风格分开控制。</p>
            </div>
            <div className="feature-item">
              <span>03</span>
              <strong>编辑并确认</strong>
              <p>草稿可保存，确认后进入正式生成。</p>
            </div>
          </div>
        </div>

        <div id="workspace" className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">工作区</h2>
              <p className="panel-subtitle">入口放轻一点，具体操作在下面。</p>
            </div>
          </div>
          {createPanel}
        </div>

        <div className="panel">
          <div className="panel-head">
            <div>
              <h2 className="panel-title">最近作品</h2>
              <p className="panel-subtitle">只保留简洁信息，便于继续编辑。</p>
            </div>
          </div>
          {recentSection}
        </div>
      </Content>

      <JobDetailDrawer
        open={drawerOpen}
        job={currentJob}
        draftText={draftText}
        onDraftTextChange={setDraftText}
        onClose={() => setDrawerOpen(false)}
        onSaveDraft={handleSaveDraft}
        onConfirm={handleConfirm}
        onDownload={handleDownload}
        saving={drawerSaving}
        confirming={drawerConfirming}
      />
    </Layout>
  );
}

export default function Page() {
  return <App />;
}
