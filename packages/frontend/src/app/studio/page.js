'use client';

import Link from 'next/link';
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
  Menu,
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
  FolderOpen,
  History,
  LayoutTemplate,
  RefreshCw,
  Save,
  Settings,
  Sparkles,
  UploadCloud,
} from 'lucide-react';
import { ClockCircleOutlined, FileTextOutlined, PlusOutlined } from "@ant-design/icons";

const { Header, Content, Sider } = Layout;
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
    content_style: job.content_style || '',
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
      {CONTENT_STYLE_OPTIONS.map((option) => (
        <button
          key={option.value}
          className={`content-style-card${value === option.value ? ' active' : ''}`}
          onClick={() => onChange(option.value)}
        >
          <strong>{option.label}</strong>
          <span>{option.description}</span>
        </button>
      ))}
    </div>
  );
}



// 进度显示组件
function ProgressDisplay({ progress }) {
  if (!progress) return null;
  
  const { stage, stage_label, pages_generated, elapsed_seconds, current_page, last_log } = progress;
  
  // 计算进度百分比（假设目标页数为10）
  const targetPages = 10;
  const progressPercent = Math.min(Math.round((pages_generated / targetPages) * 100), 100);
  
  // 格式化时间
  const formatTime = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return mins > 0 ? `${mins}分${secs}秒` : `${secs}秒`;
  };
  
  // 根据阶段设置不同的颜色和图标
  const getStageInfo = (stage) => {
    switch (stage) {
      case 'initializing':
        return { color: '#1890ff', icon: '⏳', text: '初始化中' };
      case 'generating_svg':
        return { color: '#52c41a', icon: '📄', text: '生成页面' };
      case 'exporting':
        return { color: '#722ed1', icon: '📦', text: '导出文件' };
      default:
        return { color: '#1890ff', icon: '⏳', text: '处理中' };
    }
  };
  
  const stageInfo = getStageInfo(stage);
  
  return (
    <div style={{ 
      marginTop: 16, 
      padding: 16, 
      backgroundColor: '#fafafa', 
      borderRadius: 8,
      border: '1px solid #f0f0f0'
    }}>
      <div style={{ 
        display: 'flex', 
        alignItems: 'center', 
        marginBottom: 12,
        gap: 8
      }}>
        <span style={{ fontSize: 16 }}>{stageInfo.icon}</span>
        <Text strong style={{ color: stageInfo.color }}>{stage_label}</Text>
        <Tag color={stageInfo.color} style={{ marginLeft: 'auto' }}>
          {stageInfo.text}
        </Tag>
      </div>
      
      <div style={{ marginBottom: 16 }}>
        <div style={{ 
          display: 'flex', 
          justifyContent: 'space-between', 
          marginBottom: 8,
          alignItems: 'center'
        }}>
          <Text>生成进度</Text>
          <Text strong style={{ fontSize: 18, color: stageInfo.color }}>
            {progressPercent}%
          </Text>
        </div>
        
        <div style={{ 
          height: 12, 
          backgroundColor: '#e8e8e8', 
          borderRadius: 6,
          overflow: 'hidden',
          position: 'relative'
        }}>
          <div style={{
            height: '100%',
            width: `${progressPercent}%`,
            backgroundColor: stageInfo.color,
            borderRadius: 6,
            transition: 'width 0.5s ease',
            position: 'relative',
            '&::after': {
              content: '""',
              position: 'absolute',
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.3), transparent)',
              animation: 'shimmer 2s infinite'
            }
          }} />
        </div>
      </div>
      
      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: 'repeat(3, 1fr)', 
        gap: 16,
        marginBottom: 16
      }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, fontWeight: 'bold', color: stageInfo.color }}>
            {pages_generated}
          </div>
          <div style={{ fontSize: 12, color: '#666' }}>
            已生成 / {targetPages}
          </div>
        </div>
        
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, fontWeight: 'bold', color: '#faad14' }}>
            {formatTime(elapsed_seconds)}
          </div>
          <div style={{ fontSize: 12, color: '#666' }}>
            已耗时
          </div>
        </div>
        
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 24, fontWeight: 'bold', color: '#722ed1' }}>
            {stage === 'generating_svg' ? pages_generated : 0}
          </div>
          <div style={{ fontSize: 12, color: '#666' }}>
            {stage === 'generating_svg' ? '当前页面' : '页面数'}
          </div>
        </div>
      </div>
      
      {current_page && (
        <div style={{ 
          marginBottom: 16,
          padding: 12,
          backgroundColor: '#fff',
          borderRadius: 6,
          border: '1px solid #d9d9d9'
        }}>
          <Text strong style={{ marginBottom: 4, display: 'block' }}>当前正在生成：</Text>
          <Text code style={{ fontSize: 14 }}>{current_page}</Text>
        </div>
      )}
      
      {last_log && (
        <div>
          <Text strong style={{ marginBottom: 8, display: 'block' }}>最近日志：</Text>
          <pre style={{ 
            backgroundColor: '#fff', 
            padding: 12, 
            borderRadius: 6,
            fontSize: 12,
            maxHeight: 120,
            overflow: 'auto',
            border: '1px solid #d9d9d9',
            fontFamily: 'Monaco, Consolas, "Courier New", monospace'
          }}>
            {last_log}
          </pre>
        </div>
      )}
    </div>
  );
}

function JobDetailDrawer({ open, job, draftText, onDraftTextChange, onClose, onSaveDraft, onConfirm, onDownload, saving, confirming }) {
  if (!job) return null;

  return (
    <Drawer
      title={job.title || '未命名任务'}
      width={640}
      open={open}
      onClose={onClose}
      extra={
        <Space>
          <Button icon={<Save size={16} />} onClick={onSaveDraft} loading={saving} disabled={job.status !== 'draft'}>
            保存草稿
          </Button>
          <Button type="primary" icon={<Sparkles size={16} />} onClick={onConfirm} loading={confirming} disabled={job.status !== 'draft'}>
            确认生成
          </Button>
          <Button icon={<Download size={16} />} onClick={onDownload} disabled={job.status !== 'completed'}>
            下载 PPTX
          </Button>
        </Space>
      }
    >
      <Descriptions column={1} size="small" style={{ marginBottom: 16 }}>
        <Descriptions.Item label="状态">{statusTag(job.status)}</Descriptions.Item>
        <Descriptions.Item label="引擎">{job.engine || '-'}</Descriptions.Item>
        <Descriptions.Item label="内容风格">{summarizeContentStyle(job.content_style)}</Descriptions.Item>
        <Descriptions.Item label="页数">{job.pages ? `${job.pages} 页` : '-'}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{job.created_at ? new Date(job.created_at).toLocaleString() : '-'}</Descriptions.Item>
      </Descriptions>

      {job.status === 'draft' && (
        <>
          <Divider orientation="left">草稿编辑</Divider>
          <TextArea
            rows={10}
            value={draftText}
            onChange={(e) => onDraftTextChange(e.target.value)}
            placeholder="在此编辑草稿内容..."
          />
        </>
      )}

      {job.status === 'draft' && job.draft_content && (
        <>
          <Divider orientation="left">草稿预览</Divider>
          <div className="preview-box">{job.draft_content}</div>
        </>
      )}

      {job.status === 'running' && job.progress && (
        <ProgressDisplay progress={job.progress} />
      )}
      
      {job.status === 'failed' && job.error && (
        <Alert type="error" message="生成失败" description={job.error} showIcon icon={<AlertCircle size={16} />} style={{ marginTop: 16 }} />
      )}
    </Drawer>
  );
}

function App() {
  const [messageApi, contextHolder] = message.useMessage();
  const [engines, setEngines] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [currentJob, setCurrentJob] = useState(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [drawerSaving, setDrawerSaving] = useState(false);
  const [drawerConfirming, setDrawerConfirming] = useState(false);
  const [sourceType, setSourceType] = useState('upload');
  const [uploadedSource, setUploadedSource] = useState(null);
  const [uploading, setUploading] = useState(false);

  const [form] = Form.useForm();
  const selectedEngine = Form.useWatch('engine', form) || engines[0]?.name || '';

  const engineSchema = useMemo(() => getEngineSchema(engines, selectedEngine), [engines, selectedEngine]);
  const engineDefaults = useMemo(() => buildEngineDefaults(engineSchema), [engineSchema]);

  useEffect(() => {
    async function load() {
      try {
        const data = await requestJson('/api/engines');
        setEngines(data);
        if (data.length > 0) {
          form.setFieldsValue({ engine: data[0].name });
        }
      } catch (error) {
        messageApi.error('获取引擎列表失败');
        console.error(error);
      }
    }
    load();
  }, [form, messageApi]);

  useEffect(() => {
    fetchJobs();
  }, []);

  useEffect(() => {
    form.setFieldsValue(engineDefaults);
  }, [engineDefaults, form]);

  
  // 进度轮询逻辑
  useEffect(() => {
    let pollInterval = null;
    
    // 检查是否有运行中的任务
    const hasRunningJobs = jobs.some(job => job.status === 'running');
    
    if (hasRunningJobs) {
      // 每 2 秒轮询一次任务状态
      pollInterval = setInterval(() => {
        fetchJobs();
      }, 2000);
    }
    
    return () => {
      if (pollInterval) {
        clearInterval(pollInterval);
      }
    };
  }, [jobs]);

const fetchJobs = async () => {
    setLoading(true);
    try {
      const data = await requestJson('/api/jobs?limit=100');
      setJobs(data.map(normalizeJob));
    } catch (error) {
      messageApi.error('获取任务列表失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleUpload = async (file) => {
    setUploading(true);
    try {
      const result = await uploadSourceFile(file);
      setUploadedSource(result);
      messageApi.success('文件上传成功');
    } catch (error) {
      messageApi.error(error.message || '上传失败');
    } finally {
      setUploading(false);
    }
    return false;
  };

  const handleCreate = async () => {
    try {
      const values = await form.validateFields();
      setSubmitting(true);

      const payload = {
        engine: values.engine,
        content_style: values.content_style,
        engine_params: pickEngineParams(engineSchema, values),
      };

      if (sourceType === 'upload' && uploadedSource) {
        payload.engine_params.source = {
          type: 'file_path',
          value: uploadedSource.path,
        };
      } else if (sourceType === 'text' && values.source_text) {
        payload.engine_params.source = {
          type: 'text',
          value: values.source_text,
        };
      }

      const job = await requestJson('/api/decks', {
        method: 'POST',
        body: JSON.stringify(payload),
      });

      messageApi.success('任务已提交');
      form.resetFields();
      setUploadedSource(null);
      fetchJobs();
      openJob(job.job_id);
    } catch (error) {
      if (error.errorFields) return;
      messageApi.error(error.message || '提交失败');
      console.error(error);
    } finally {
      setSubmitting(false);
    }
  };

  const openJob = async (jobId) => {
    try {
      const job = await requestJson(`/api/jobs/${jobId}`);
      setCurrentJob(normalizeJob(job));
      setDraftText(job.draft_content || '');
      setDrawerOpen(true);
    } catch (error) {
      messageApi.error('获取任务详情失败');
      console.error(error);
    }
  };

  const handleSaveDraft = async () => {
    if (!currentJob) return;
    setDrawerSaving(true);
    try {
      await requestJson(`/api/jobs/${currentJob.job_id}/draft`, {
        method: 'PATCH',
        body: JSON.stringify({ content: draftText }),
      });
      messageApi.success('草稿已保存');
    } catch (error) {
      messageApi.error(error.message || '保存失败');
      console.error(error);
    } finally {
      setDrawerSaving(false);
    }
  };

  const handleConfirm = async () => {
    if (!currentJob) return;
    setDrawerConfirming(true);
    try {
      const job = await requestJson(`/api/jobs/${currentJob.job_id}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ content: draftText }),
      });
      setCurrentJob(normalizeJob(job));
      messageApi.success('已确认生成');
      fetchJobs();
    } catch (error) {
      messageApi.error(error.message || '确认失败');
      console.error(error);
    } finally {
      setDrawerConfirming(false);
    }
  };

  const handleDownload = () => {
    if (!currentJob) return;
    window.open(apiUrl(`/api/jobs/${currentJob.job_id}/download`), '_blank');
  };

  const scrollToWorkspace = (id) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const createPanel = (
    <div className="panel-grid">
      <div>
        <Form form={form} layout="vertical" initialValues={engineDefaults}>
          <Form.Item name="engine" label="引擎">
            <Select
              options={engines.map((engine) => ({
                value: engine.name,
                label: engine.name,
              }))}
            />
          </Form.Item>

          <Form.Item label="内容风格" name="content_style">
            <ContentStylePicker />
          </Form.Item>

          <Form.Item label="素材来源">
            <Segmented
              options={SOURCE_OPTIONS}
              value={sourceType}
              onChange={setSourceType}
              style={{ marginBottom: 12 }}
            />
            {sourceType === 'upload' ? (
              <Upload
                beforeUpload={handleUpload}
                showUploadList={false}
                accept=".txt,.md,.docx,.pdf"
              >
                <Button icon={<UploadCloud size={16} />} loading={uploading}>
                  选择文件
                </Button>
              </Upload>
            ) : (
              <Form.Item name="source_text" noStyle>
                <TextArea rows={4} placeholder="直接粘贴文本内容" />
              </Form.Item>
            )}
            {uploadedSource && (
              <div style={{ marginTop: 8 }}>
                <Tag color="green">已上传</Tag>
                <span>{uploadedSource.filename || uploadedSource.path}</span>
              </div>
            )}
          </Form.Item>

          <Form.Item label="引擎参数">
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name="style" label="视觉风格">
                <Select
                  options={(engineSchema.properties?.style?.enum || []).map((value) => ({
                    value,
                    label: value,
                  }))}
                  placeholder="选择视觉风格"
                />
              </Form.Item>
              <Form.Item name="mode" label="叙事模式">
                <Select
                  options={(engineSchema.properties?.mode?.enum || []).map((value) => ({
                    value,
                    label: value,
                  }))}
                  placeholder="选择叙事模式"
                />
              </Form.Item>
              <Form.Item name="format" label="画布尺寸">
                <Select
                  options={(engineSchema.properties?.format?.enum || []).map((value) => ({
                    value,
                    label: value,
                  }))}
                  placeholder="选择画布尺寸"
                />
              </Form.Item>
              <Form.Item name="notes" label="演讲备注" valuePropName="checked">
                <Select
                  options={[
                    { value: true, label: '开启' },
                    { value: false, label: '关闭' },
                  ]}
                  placeholder="是否生成备注"
                />
              </Form.Item>
            </div>
          </Form.Item>

          <Form.Item>
            <Button
              type="primary"
              icon={<Sparkles size={16} />}
              onClick={handleCreate}
              loading={submitting}
              block
            >
              提交生成
            </Button>
          </Form.Item>
        </Form>
      </div>

      <div className="preview-card">
        <div className="preview-card-top">
          <span className="preview-kicker">预览</span>
          <h3 className="preview-title">任务提交预览</h3>
        </div>
        <div className="preview-meta">
          <p>引擎：{selectedEngine || '未选择'}</p>
          <p>内容风格：{summarizeContentStyle(Form.useWatch('content_style', form))}</p>
          <p>参数：{summarizeEngineParams(pickEngineParams(engineSchema, form.getFieldsValue()))}</p>
        </div>
      </div>
    </div>
  );

  const recentSection = (
    <div>
      {loading ? (
        <Spin />
      ) : jobs.length > 0 ? (
        <div className="recent-grid">
          {jobs.slice(0, PAGE_SIZE).map((job) => (
            <div key={job.job_id} className="recent-card">
              <div className="recent-card-head">
                <div>
                  <strong>{job.title || '未命名'}</strong>
                  <p>{job.engine} · {summarizeContentStyle(job.content_style)}</p>
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
            </div>
          ))}
        </div>
      ) : (
        <Empty description="暂无最近作品" />
      )}
    </div>
  );

  const [collapsed, setCollapsed] = useState(false);

  const menuItems = [
    {
      type: 'group',
      label: '创作',
      children: [
        {
          key: 'studio',
          icon: <Sparkles />,
          label: <Link href="/studio">新建 PPT</Link>,
        },
        {
          key: 'templates',
          icon: <LayoutTemplate />,
          label: <Link href="/studio/templates">模板库</Link>,
          extra: <Tag color="blue">12</Tag>,
        },
      ],
    },
    {
      type: 'group',
      label: '管理',
      children: [
        {
          key: 'projects',
          icon: <FolderOpen />,
          label: <Link href="/studio/projects">我的项目</Link>,
          extra: <Tag color="green">5</Tag>,
        },
        {
          key: 'history',
          icon: <ClockCircleOutlined />,
          label: <Link href="/history">生成历史</Link>,
          extra: <Tag color="orange">23</Tag>,
        },
      ],
    },
    {
      type: 'group',
      label: '设置',
      children: [
        {
          key: 'settings',
          icon: <Settings />,
          label: <Link href="/studio/settings">账户设置</Link>,
        },
      ],
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      {contextHolder}
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={setCollapsed}
        style={{ background: '#fff' }}
      >
        <div style={{ 
          height: 64, 
          display: 'flex', 
          alignItems: 'center', 
          justifyContent: 'center',
          borderBottom: '1px solid #e5e7eb',
          padding: '0 16px'
        }}>
          <Link href="/" style={{ 
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            textDecoration: 'none',
            color: 'inherit'
          }}>
            <div style={{
              width: 40,
              height: 40,
              background: 'linear-gradient(135deg, #1f6feb, #0f9d8f)',
              borderRadius: 10,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontWeight: 700,
              fontSize: 18
            }}>
              D
            </div>
            {!collapsed && (
              <div>
                <div style={{ fontSize: 18, fontWeight: 700, lineHeight: 1.2 }}>DeckSmithPPT</div>
                <div style={{ fontSize: 12, color: 'rgba(23, 32, 51, 0.64)', marginTop: 2 }}>AI PPT 生成助手</div>
              </div>
            )}
          </Link>
        </div>
        <Menu
          mode="inline"
          selectedKeys={['studio']}
          items={menuItems}
          style={{ borderRight: 0 }}
        />
      </Sider>
      <Layout>
        <Header style={{ 
          background: '#fff', 
          padding: '0 24px', 
          borderBottom: '1px solid #e5e7eb',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between'
        }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>DeckSmithPPT</h1>
            <p style={{ margin: 0, fontSize: 13, color: '#6b7280' }}>把材料变成一份能直接改的 PPT 草稿</p>
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
                <Link href="/history">
                  <Button icon={<History size={16} />}>
                    查看历史
                  </Button>
                </Link>
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

          {/* 使用技巧 */}
          <div className="panel" style={{ marginTop: 16 }}>
            <div className="panel-head">
              <div>
                <h2 className="panel-title">使用技巧</h2>
                <p className="panel-subtitle">提高生成效率的小贴士</p>
              </div>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
              <div style={{ padding: 16, background: '#f8fbff', borderRadius: 8, border: '1px solid #e5e7eb' }}>
                <h4 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>上传材料</h4>
                <p style={{ margin: 0, fontSize: 13, color: '#6b7280' }}>支持TXT、DOCX、PDF等格式，文件大小不超过10MB</p>
              </div>
              <div style={{ padding: 16, background: '#f8fbff', borderRadius: 8, border: '1px solid #e5e7eb' }}>
                <h4 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>风格选择</h4>
                <p style={{ margin: 0, fontSize: 13, color: '#6b7280' }}>先选视觉风格，再选内容风格，组合效果更佳</p>
              </div>
              <div style={{ padding: 16, background: '#f8fbff', borderRadius: 8, border: '1px solid #e5e7eb' }}>
                <h4 style={{ margin: '0 0 8px', fontSize: 14, fontWeight: 600 }}>草稿编辑</h4>
                <p style={{ margin: 0, fontSize: 13, color: '#6b7280' }}>生成草稿后可保存修改，满意后再确认生成最终PPT</p>
              </div>
            </div>
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
    </Layout>
  );
}

export default function Page() {
  return <App />;
}
