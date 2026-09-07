'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Button,
  Card,
  Col,
  Dropdown,
  Input,
  Layout,
  Menu,
  Row,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ArrowLeft,
  FolderOpen,
  LayoutTemplate,
  Search,
  Settings,
  Sparkles,
} from 'lucide-react';
import { 
  ClockCircleOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  MoreOutlined,
  PlusOutlined,
} from '@ant-design/icons';

const { Header, Content, Sider } = Layout;
const { Title, Text } = Typography;

// 模拟项目数据
const PROJECTS = [
  {
    id: '1',
    name: '2024年工作总结',
    description: '年度工作成果汇报',
    status: 'completed',
    createdAt: '2024-01-15',
    updatedAt: '2024-01-20',
    pages: 12,
    size: '2.4MB',
  },
  {
    id: '2',
    name: '产品发布方案',
    description: '新产品发布策划方案',
    status: 'draft',
    createdAt: '2024-01-10',
    updatedAt: '2024-01-18',
    pages: 8,
    size: '1.8MB',
  },
  {
    id: '3',
    name: '市场分析报告',
    description: 'Q4市场趋势分析',
    status: 'completed',
    createdAt: '2024-01-05',
    updatedAt: '2024-01-15',
    pages: 15,
    size: '3.2MB',
  },
  {
    id: '4',
    name: '团队培训材料',
    description: '新员工入职培训',
    status: 'running',
    createdAt: '2024-01-12',
    updatedAt: '2024-01-19',
    pages: 20,
    size: '4.1MB',
  },
  {
    id: '5',
    name: '客户提案',
    description: '重要客户合作提案',
    status: 'draft',
    createdAt: '2024-01-08',
    updatedAt: '2024-01-16',
    pages: 10,
    size: '2.1MB',
  },
];

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

const STATUS_MAP = {
  draft: { color: 'blue', label: '草稿' },
  running: { color: 'gold', label: '生成中' },
  completed: { color: 'green', label: '已完成' },
  failed: { color: 'red', label: '失败' },
};

export default function ProjectsPage() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [selectedStatus, setSelectedStatus] = useState('all');

  const filteredProjects = PROJECTS.filter(project => {
    const matchesSearch = project.name.includes(searchText) || 
                         project.description.includes(searchText);
    const matchesStatus = selectedStatus === 'all' || 
                         project.status === selectedStatus;
    return matchesSearch && matchesStatus;
  });

  const columns = [
    {
      title: '项目名称',
      dataIndex: 'name',
      key: 'name',
      render: (text, record) => (
        <div>
          <div style={{ fontWeight: 500 }}>{text}</div>
          <div style={{ fontSize: 13, color: '#6b7280' }}>{record.description}</div>
        </div>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      render: status => {
        const { color, label } = STATUS_MAP[status] || {};
        return <Tag color={color}>{label}</Tag>;
      },
    },
    {
      title: '页数',
      dataIndex: 'pages',
      key: 'pages',
    },
    {
      title: '大小',
      dataIndex: 'size',
      key: 'size',
    },
    {
      title: '更新时间',
      dataIndex: 'updatedAt',
      key: 'updatedAt',
    },
    {
      title: '操作',
      key: 'actions',
      render: (_, record) => (
        <Dropdown
          menu={{
            items: [
              {
                key: 'view',
                icon: <EyeOutlined />,
                label: '查看',
              },
              {
                key: 'edit',
                icon: <EditOutlined />,
                label: '编辑',
              },
              {
                key: 'download',
                icon: <DownloadOutlined />,
                label: '下载',
              },
              {
                type: 'divider',
              },
              {
                key: 'delete',
                icon: <DeleteOutlined />,
                label: '删除',
                danger: true,
              },
            ],
            onClick: ({ key }) => {
              message.info(`点击了 ${key}`);
            },
          }}
        >
          <Button type="text" icon={<MoreOutlined size={16} />} />
        </Dropdown>
      ),
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
          selectedKeys={['projects']}
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
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <Link href="/studio">
              <Button icon={<ArrowLeft size={16} />} type="text">
                返回工作台
              </Button>
            </Link>
            <div>
              <Title level={4} style={{ margin: 0 }}>我的项目</Title>
              <Text type="secondary">管理您创建的所有PPT项目</Text>
            </div>
          </div>
          <Space>
            <Input
              placeholder="搜索项目..."
              prefix={<Search size={16} />}
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              style={{ width: 200 }}
            />
            <Button type="primary" icon={<PlusOutlined />}>
              新建项目
            </Button>
          </Space>
        </Header>
        <Content style={{ padding: 24 }}>
          {/* 状态筛选 */}
          <div style={{ marginBottom: 24 }}>
            <Space size="middle">
              {['all', 'draft', 'running', 'completed', 'failed'].map(status => (
                <Button
                  key={status}
                  type={selectedStatus === status ? 'primary' : 'default'}
                  onClick={() => setSelectedStatus(status)}
                >
                  {status === 'all' ? '全部' : STATUS_MAP[status]?.label || status}
                </Button>
              ))}
            </Space>
          </div>

          {/* 统计卡片 */}
          <Row gutter={16} style={{ marginBottom: 24 }}>
            <Col span={6}>
              <Card size="small">
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 600, color: '#1f6feb' }}>
                    {PROJECTS.length}
                  </div>
                  <div style={{ color: '#6b7280' }}>总项目数</div>
                </div>
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 600, color: '#10b981' }}>
                    {PROJECTS.filter(p => p.status === 'completed').length}
                  </div>
                  <div style={{ color: '#6b7280' }}>已完成</div>
                </div>
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 600, color: '#f59e0b' }}>
                    {PROJECTS.filter(p => p.status === 'running').length}
                  </div>
                  <div style={{ color: '#6b7280' }}>生成中</div>
                </div>
              </Card>
            </Col>
            <Col span={6}>
              <Card size="small">
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 600, color: '#3b82f6' }}>
                    {PROJECTS.filter(p => p.status === 'draft').length}
                  </div>
                  <div style={{ color: '#6b7280' }}>草稿</div>
                </div>
              </Card>
            </Col>
          </Row>

          {/* 项目表格 */}
          <Card>
            <Table
              columns={columns}
              dataSource={filteredProjects}
              rowKey="id"
              pagination={{ pageSize: 10 }}
            />
          </Card>
        </Content>
      </Layout>
    </Layout>
  );
}
