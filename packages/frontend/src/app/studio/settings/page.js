'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  Avatar,
  Button,
  Card,
  Col,
  Divider,
  Form,
  Input,
  Layout,
  Menu,
  Row,
  Space,
  Switch,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd';
import {
  ArrowLeft,
  Bell,
  FolderOpen,
  LayoutTemplate,
  Lock,
  Save,
  Settings,
  Sparkles,
  User,
} from 'lucide-react';
import { ClockCircleOutlined } from '@ant-design/icons';

const { Header, Content, Sider } = Layout;
const { Title, Text } = Typography;

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

export default function SettingsPage() {
  const [collapsed, setCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState('profile');
  const [form] = Form.useForm();

  const handleSave = () => {
    form.validateFields().then(values => {
      console.log('保存设置:', values);
      message.success('设置已保存');
    });
  };

  const tabItems = [
    {
      key: 'profile',
      label: (
        <span>
          <User size={16} style={{ marginRight: 8 }} />
          个人信息
        </span>
      ),
      children: (
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            name: '张三',
            email: 'zhangsan@example.com',
            company: '示例公司',
            jobTitle: '产品经理',
          }}
        >
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item
                label="姓名"
                name="name"
                rules={[{ required: true, message: '请输入姓名' }]}
              >
                <Input placeholder="请输入姓名" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                label="邮箱"
                name="email"
                rules={[
                  { required: true, message: '请输入邮箱' },
                  { type: 'email', message: '请输入有效的邮箱地址' },
                ]}
              >
                <Input placeholder="请输入邮箱" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={24}>
            <Col span={12}>
              <Form.Item label="公司" name="company">
                <Input placeholder="请输入公司名称" />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="职位" name="jobTitle">
                <Input placeholder="请输入职位" />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item label="个人简介" name="bio">
            <Input.TextArea rows={4} placeholder="介绍一下自己..." />
          </Form.Item>
        </Form>
      ),
    },
    {
      key: 'notifications',
      label: (
        <span>
          <Bell size={16} style={{ marginRight: 8 }} />
          通知设置
        </span>
      ),
      children: (
        <div>
          <div style={{ marginBottom: 24 }}>
            <Title level={5}>邮件通知</Title>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
              <div>
                <div style={{ fontWeight: 500 }}>生成完成通知</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>PPT生成完成后发送邮件通知</div>
              </div>
              <Switch defaultChecked />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
              <div>
                <div style={{ fontWeight: 500 }}>每周摘要</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>每周发送使用情况摘要</div>
              </div>
              <Switch defaultChecked />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0' }}>
              <div>
                <div style={{ fontWeight: 500 }}>营销邮件</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>接收产品更新和优惠信息</div>
              </div>
              <Switch />
            </div>
          </div>
          <div>
            <Title level={5}>浏览器通知</Title>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
              <div>
                <div style={{ fontWeight: 500 }}>桌面通知</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>允许浏览器发送桌面通知</div>
              </div>
              <Switch defaultChecked />
            </div>
          </div>
        </div>
      ),
    },
    {
      key: 'security',
      label: (
        <span>
          <Lock size={16} style={{ marginRight: 8 }} />
          安全设置
        </span>
      ),
      children: (
        <div>
          <div style={{ marginBottom: 24 }}>
            <Title level={5}>修改密码</Title>
            <Form layout="vertical">
              <Form.Item label="当前密码" name="currentPassword">
                <Input.Password placeholder="请输入当前密码" />
              </Form.Item>
              <Form.Item label="新密码" name="newPassword">
                <Input.Password placeholder="请输入新密码" />
              </Form.Item>
              <Form.Item label="确认新密码" name="confirmPassword">
                <Input.Password placeholder="请再次输入新密码" />
              </Form.Item>
              <Form.Item>
                <Button type="primary">更新密码</Button>
              </Form.Item>
            </Form>
          </div>
          <Divider />
          <div>
            <Title level={5}>账户操作</Title>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0', borderBottom: '1px solid #f0f0f0' }}>
              <div>
                <div style={{ fontWeight: 500 }}>导出数据</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>下载您的所有数据</div>
              </div>
              <Button>导出</Button>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 0' }}>
              <div>
                <div style={{ fontWeight: 500, color: '#ef4444' }}>删除账户</div>
                <div style={{ color: '#6b7280', fontSize: 13 }}>永久删除您的账户和所有数据</div>
              </div>
              <Button danger>删除账户</Button>
            </div>
          </div>
        </div>
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
          selectedKeys={['settings']}
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
              <Title level={4} style={{ margin: 0 }}>账户设置</Title>
              <Text type="secondary">管理您的账户信息和偏好设置</Text>
            </div>
          </div>
          <Button type="primary" icon={<Save size={16} />} onClick={handleSave}>
            保存设置
          </Button>
        </Header>
        <Content style={{ padding: 24 }}>
          <Row gutter={24}>
            <Col span={6}>
              <Card>
                <div style={{ textAlign: 'center', marginBottom: 24 }}>
                  <Avatar size={80} icon={<User size={40} />} />
                  <div style={{ marginTop: 12, fontWeight: 500 }}>张三</div>
                  <div style={{ color: '#6b7280', fontSize: 13 }}>zhangsan@example.com</div>
                </div>
                <div style={{ 
                  padding: '12px 16px', 
                  background: '#f8fafc', 
                  borderRadius: 8,
                  marginBottom: 16
                }}>
                  <div style={{ fontSize: 13, color: '#6b7280' }}>账户状态</div>
                  <div style={{ fontWeight: 500, color: '#10b981' }}>正常</div>
                </div>
                <div style={{ 
                  padding: '12px 16px', 
                  background: '#f8fafc', 
                  borderRadius: 8,
                  marginBottom: 16
                }}>
                  <div style={{ fontSize: 13, color: '#6b7280' }}>注册时间</div>
                  <div style={{ fontWeight: 500 }}>2024-01-01</div>
                </div>
                <div style={{ 
                  padding: '12px 16px', 
                  background: '#f8fafc', 
                  borderRadius: 8
                }}>
                  <div style={{ fontSize: 13, color: '#6b7280' }}>已生成PPT</div>
                  <div style={{ fontWeight: 500 }}>23 个</div>
                </div>
              </Card>
            </Col>
            <Col span={18}>
              <Card>
                <Tabs
                  activeKey={activeTab}
                  onChange={setActiveTab}
                  items={tabItems}
                />
              </Card>
            </Col>
          </Row>
        </Content>
      </Layout>
    </Layout>
  );
}
