'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  Button,
  Layout,
  Menu,
  Space,
  Table,
  Tag,
  Select,
  DatePicker,
  Input,
  Card,
  Statistic,
  message,
} from 'antd';
import {
  ClockCircleOutlined,
  CheckCircleOutlined,
  SyncOutlined,
  FileTextOutlined,
  PlusOutlined,
  SearchOutlined,
  FilterOutlined,
  DownloadOutlined,
  EyeOutlined,
  EditOutlined,
  DeleteOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';

const { Header, Sider, Content } = Layout;
const { RangePicker } = DatePicker;

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');

const STATUS_META = {
  draft: { color: 'blue', label: '草稿', icon: <FileTextOutlined /> },
  running: { color: 'gold', label: '运行中', icon: <SyncOutlined spin /> },
  completed: { color: 'green', label: '已完成', icon: <CheckCircleOutlined /> },
  failed: { color: 'red', label: '失败', icon: <ClockCircleOutlined /> },
  timeout: { color: 'orange', label: '超时', icon: <ClockCircleOutlined /> },
};

const CONTENT_STYLE_OPTIONS = [
  { value: '', label: '全部风格' },
  { value: 'marketing_promo', label: '宣传材料' },
  { value: 'product_story', label: '产品介绍' },
  { value: 'briefing', label: '方案汇报' },
  { value: 'event_promo', label: '活动推广' },
  { value: 'investment_pitch', label: '招商介绍' },
];

export default function HistoryPage() {
  const [collapsed, setCollapsed] = useState(false);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filters, setFilters] = useState({
    status: '',
    engine: '',
    content_style: '',
    keyword: '',
    dateRange: null,
  });
  const [selectedRowKeys, setSelectedRowKeys] = useState([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [stats, setStats] = useState({
    total: 0,
    completed: 0,
    running: 0,
    draft: 0,
  });

  // Fetch jobs
  const fetchJobs = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.status) params.append('status', filters.status);
      if (filters.engine) params.append('engine', filters.engine);
      if (filters.content_style) params.append('content_style', filters.content_style);
      params.append('limit', '100');

      const response = await fetch(`${API_BASE}/api/jobs?${params}`);
      const data = await response.json();
      
      let filteredData = data;
      if (filters.keyword) {
        const keyword = filters.keyword.toLowerCase();
        filteredData = data.filter(job => 
          (job.title || '').toLowerCase().includes(keyword) ||
          (job.draft_content || '').toLowerCase().includes(keyword)
        );
      }
      
      if (filters.dateRange && filters.dateRange[0] && filters.dateRange[1]) {
        const startDate = filters.dateRange[0].startOf('day').valueOf();
        const endDate = filters.dateRange[1].endOf('day').valueOf();
        filteredData = filteredData.filter(job => {
          const jobDate = new Date(job.created_at).getTime();
          return jobDate >= startDate && jobDate <= endDate;
        });
      }

      setJobs(filteredData);
      
      // Calculate stats
      const stats = {
        total: data.length,
        completed: data.filter(j => j.status === 'completed').length,
        running: data.filter(j => j.status === 'running').length,
        draft: data.filter(j => j.status === 'draft').length,
      };
      setStats(stats);
    } catch (error) {
      message.error('获取任务列表失败');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, [filters.status, filters.engine, filters.content_style]);

  const handleDownload = (jobId) => {
    window.open(`${API_BASE}/api/jobs/${jobId}/download`, '_blank');
  };

  const handleView = (jobId) => {
    window.location.href = `/studio?job=${jobId}`;
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请选择要删除的记录');
      return;
    }
    
    setBatchLoading(true);
    try {
      // 这里应该调用批量删除API
      // await fetch(`${API_BASE}/api/jobs/batch-delete`, {
      //   method: 'POST',
      //   headers: { 'Content-Type': 'application/json' },
      //   body: JSON.stringify({ job_ids: selectedRowKeys }),
      // });
      
      message.success(`已删除 ${selectedRowKeys.length} 条记录`);
      setSelectedRowKeys([]);
      fetchJobs();
    } catch (error) {
      message.error('批量删除失败');
      console.error(error);
    } finally {
      setBatchLoading(false);
    }
  };

  const handleBatchDownload = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请选择要下载的记录');
      return;
    }
    
    selectedRowKeys.forEach(jobId => {
      window.open(`${API_BASE}/api/jobs/${jobId}/download`, '_blank');
    });
  };

  const columns = [
    {
      title: '任务信息',
      dataIndex: 'title',
      key: 'title',
      render: (text, record) => (
        <div>
          <div style={{ fontWeight: 500 }}>{text || '未命名'}</div>
          <div style={{ fontSize: 12, color: '#6b7280' }}>
            {CONTENT_STYLE_OPTIONS.find(o => o.value === record.content_style)?.label || '未指定'} • {record.engine || '默认引擎'}
          </div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status) => {
        const meta = STATUS_META[status] || { color: 'default', label: status };
        return <Tag color={meta.color} icon={meta.icon}>{meta.label}</Tag>;
      },
    },
    {
      title: '引擎',
      dataIndex: 'engine',
      key: 'engine',
      width: 120,
      render: (engine) => engine || 'ppt-master',
    },
    {
      title: '页数',
      dataIndex: 'pages',
      key: 'pages',
      width: 80,
      render: (pages) => pages ? `${pages} 页` : '-',
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (date) => date ? dayjs(date).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 200,
      render: (_, record) => (
        <Space>
          <Button
            type="link"
            icon={<EyeOutlined />}
            onClick={() => handleView(record.job_id)}
          >
            查看
          </Button>
          <Button
            type="link"
            icon={<DownloadOutlined />}
            onClick={() => handleDownload(record.job_id)}
            disabled={record.status !== 'completed'}
          >
            下载
          </Button>
        </Space>
      ),
    },
  ];

  const menuItems = [
    {
      key: 'home',
      icon: <FileTextOutlined />,
      label: <Link href="/">首页</Link>,
    },
    {
      key: 'studio',
      icon: <PlusOutlined />,
      label: <Link href="/studio">工作台</Link>,
    },
    {
      key: 'history',
      icon: <ClockCircleOutlined />,
      label: <Link href="/history">生成历史</Link>,
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
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
          borderBottom: '1px solid #e5e7eb'
        }}>
          <Link href="/" style={{ 
            fontSize: collapsed ? 16 : 18, 
            fontWeight: 600, 
            color: '#1a1a2e',
            textDecoration: 'none'
          }}>
            {collapsed ? 'DS' : 'DeckSmith'}
          </Link>
        </div>
        <Menu
          mode="inline"
          selectedKeys={['history']}
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
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 600 }}>生成历史</h2>
          <Link href="/studio">
            <Button type="primary" icon={<PlusOutlined />}>
              新建 PPT
            </Button>
          </Link>
        </Header>
        
        <Content style={{ padding: 24 }}>
          {/* Stats Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 24 }}>
            <Card size="small">
              <Statistic
                title="总生成次数"
                value={stats.total}
                prefix={<FileTextOutlined />}
              />
            </Card>
            <Card size="small">
              <Statistic
                title="已完成"
                value={stats.completed}
                prefix={<CheckCircleOutlined />}
                valueStyle={{ color: '#10b981' }}
              />
            </Card>
            <Card size="small">
              <Statistic
                title="运行中"
                value={stats.running}
                prefix={<SyncOutlined />}
                valueStyle={{ color: '#f59e0b' }}
              />
            </Card>
            <Card size="small">
              <Statistic
                title="草稿"
                value={stats.draft}
                prefix={<FileTextOutlined />}
                valueStyle={{ color: '#6b7280' }}
              />
            </Card>
          </div>

          {/* Filters */}
          <Card style={{ marginBottom: 24 }}>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div>
                <div style={{ marginBottom: 4, fontSize: 13, color: '#6b7280' }}>状态</div>
                <Select
                  style={{ width: 120 }}
                  placeholder="全部状态"
                  value={filters.status || undefined}
                  onChange={(value) => setFilters(prev => ({ ...prev, status: value || '' }))}
                  allowClear
                  options={[
                    { value: '', label: '全部状态' },
                    { value: 'draft', label: '草稿' },
                    { value: 'running', label: '运行中' },
                    { value: 'completed', label: '已完成' },
                    { value: 'failed', label: '失败' },
                    { value: 'timeout', label: '超时' },
                  ]}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4, fontSize: 13, color: '#6b7280' }}>内容风格</div>
                <Select
                  style={{ width: 140 }}
                  placeholder="全部风格"
                  value={filters.content_style || undefined}
                  onChange={(value) => setFilters(prev => ({ ...prev, content_style: value || '' }))}
                  allowClear
                  options={CONTENT_STYLE_OPTIONS}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4, fontSize: 13, color: '#6b7280' }}>时间范围</div>
                <RangePicker
                  value={filters.dateRange}
                  onChange={(dates) => setFilters(prev => ({ ...prev, dateRange: dates }))}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4, fontSize: 13, color: '#6b7280' }}>搜索</div>
                <Input
                  placeholder="搜索标题或内容"
                  prefix={<SearchOutlined />}
                  style={{ width: 200 }}
                  value={filters.keyword}
                  onChange={(e) => setFilters(prev => ({ ...prev, keyword: e.target.value }))}
                  onPressEnter={fetchJobs}
                />
              </div>
              <Button icon={<ReloadOutlined />} onClick={fetchJobs}>
                刷新
              </Button>
            </div>
          </Card>

          {/* 批量操作 */}
          {selectedRowKeys.length > 0 && (
            <div style={{ marginBottom: 16, display: 'flex', gap: 12 }}>
              <Button 
                icon={<DeleteOutlined />} 
                onClick={handleBatchDelete} 
                loading={batchLoading}
                danger
              >
                批量删除 ({selectedRowKeys.length})
              </Button>
              <Button 
                icon={<DownloadOutlined />} 
                onClick={handleBatchDownload}
              >
                批量下载 ({selectedRowKeys.length})
              </Button>
            </div>
          )}

          {/* Table */}
          <Card>
            <Table
              rowSelection={{
                selectedRowKeys,
                onChange: setSelectedRowKeys,
              }}
              columns={columns}
              dataSource={jobs}
              rowKey="job_id"
              loading={loading}
              pagination={{ pageSize: 10 }}
              locale={{ emptyText: '暂无生成记录' }}
            />
          </Card>
        </Content>
      </Layout>
    </Layout>
  );
}
