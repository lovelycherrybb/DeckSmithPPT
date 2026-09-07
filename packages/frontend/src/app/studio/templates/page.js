'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Button,
  Card,
  Col,
  Input,
  Layout,
  Menu,
  Row,
  Space,
  Tag,
  Typography,
} from 'antd';
import {
  ArrowLeft,
  Download,
  Eye,
  FolderOpen,
  LayoutTemplate,
  Search,
  Settings,
  Sparkles,
} from 'lucide-react';
import { ClockCircleOutlined } from '@ant-design/icons';

const { Header, Content, Sider } = Layout;
const { Title, Text } = Typography;

// 模拟模板数据
const TEMPLATES = [
  {
    id: 1,
    name: '商务汇报模板',
    description: '专业的商务汇报风格，适合工作汇报、项目总结',
    category: '商务',
    style: '简约',
    pages: 12,
    downloads: 1234,
    preview: '/templates/business.jpg',
  },
  {
    id: 2,
    name: '产品发布模板',
    description: '炫酷的产品发布风格，适合新品发布、功能介绍',
    category: '产品',
    style: '科技',
    pages: 15,
    downloads: 856,
    preview: '/templates/product.jpg',
  },
  {
    id: 3,
    name: '教育培训模板',
    description: '清晰的教育培训风格，适合课程讲解、知识分享',
    category: '教育',
    style: '活泼',
    pages: 18,
    downloads: 2341,
    preview: '/templates/education.jpg',
  },
  {
    id: 4,
    name: '数据分析模板',
    description: '专业的数据分析风格，适合数据报告、趋势分析',
    category: '数据',
    style: '专业',
    pages: 10,
    downloads: 567,
    preview: '/templates/data.jpg',
  },
  {
    id: 5,
    name: '创意设计模板',
    description: '独特的创意设计风格，适合设计提案、创意展示',
    category: '设计',
    style: '创意',
    pages: 14,
    downloads: 432,
    preview: '/templates/creative.jpg',
  },
  {
    id: 6,
    name: '营销策划模板',
    description: '吸引人的营销策划风格，适合营销方案、活动策划',
    category: '营销',
    style: '活力',
    pages: 16,
    downloads: 789,
    preview: '/templates/marketing.jpg',
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

export default function TemplatesPage() {
  const [collapsed, setCollapsed] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [selectedCategory, setSelectedCategory] = useState('all');

  const categories = ['all', '商务', '产品', '教育', '数据', '设计', '营销'];

  const filteredTemplates = TEMPLATES.filter(template => {
    const matchesSearch = template.name.includes(searchText) || 
                         template.description.includes(searchText);
    const matchesCategory = selectedCategory === 'all' || 
                           template.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

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
          selectedKeys={['templates']}
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
              <Title level={4} style={{ margin: 0 }}>模板库</Title>
              <Text type="secondary">选择模板，快速开始创作</Text>
            </div>
          </div>
          <Space>
            <Input
              placeholder="搜索模板..."
              prefix={<Search size={16} />}
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              style={{ width: 250 }}
            />
          </Space>
        </Header>
        <Content style={{ padding: 24 }}>
          {/* 分类筛选 */}
          <div style={{ marginBottom: 24 }}>
            <Space size="middle">
              {categories.map(category => (
                <Button
                  key={category}
                  type={selectedCategory === category ? 'primary' : 'default'}
                  onClick={() => setSelectedCategory(category)}
                >
                  {category === 'all' ? '全部' : category}
                </Button>
              ))}
            </Space>
          </div>

          {/* 模板网格 */}
          <Row gutter={[24, 24]}>
            {filteredTemplates.map(template => (
              <Col key={template.id} xs={24} sm={12} lg={8} xl={6}>
                <Card
                  hoverable
                  cover={
                    <div style={{
                      height: 200,
                      background: `linear-gradient(135deg, #667eea 0%, #764ba2 100%)`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      color: 'white',
                      fontSize: 24,
                      fontWeight: 600
                    }}>
                      {template.name}
                    </div>
                  }
                  actions={[
                    <Button key="preview" type="text" icon={<Eye size={16} />}>
                      预览
                    </Button>,
                    <Button key="use" type="text" icon={<Download size={16} />}>
                      使用
                    </Button>,
                  ]}
                >
                  <Card.Meta
                    title={template.name}
                    description={
                      <div>
                        <Text type="secondary" style={{ fontSize: 13 }}>
                          {template.description}
                        </Text>
                        <div style={{ marginTop: 12 }}>
                          <Space size="small">
                            <Tag color="blue">{template.category}</Tag>
                            <Tag color="green">{template.style}</Tag>
                            <Tag color="orange">{template.pages}页</Tag>
                          </Space>
                        </div>
                        <div style={{ marginTop: 8, fontSize: 12, color: '#6b7280' }}>
                          下载次数: {template.downloads}
                        </div>
                      </div>
                    }
                  />
                </Card>
              </Col>
            ))}
          </Row>

          {filteredTemplates.length === 0 && (
            <div style={{ 
              textAlign: 'center', 
              padding: '60px 0',
              color: '#6b7280'
            }}>
              <LayoutTemplate size={48} style={{ marginBottom: 16, opacity: 0.5 }} />
              <div>未找到匹配的模板</div>
              <div style={{ fontSize: 13, marginTop: 8 }}>
                尝试调整搜索条件或分类筛选
              </div>
            </div>
          )}
        </Content>
      </Layout>
    </Layout>
  );
}
