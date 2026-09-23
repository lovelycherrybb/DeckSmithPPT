'use client';

/**
 * 用户管理（用户系统 v0.8，仅管理员）。接 /api/users 一组端点。
 *
 * 列表 + 新增 + 改信息（用户名不可改）+ 重置密码为 234567 + 启用/禁用。
 * 内置 manager 账号在界面上禁用所有操作（后端也会 403 兜底）。
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Button, Card, Form, Input, message, Modal, Popconfirm, Space, Switch, Table, Tag,
} from 'antd';
import { KeyRound, Plus, Save } from 'lucide-react';

import {
  createUser, listUsers, resetUserPassword, setUserStatus, updateUser, type User,
} from '@/lib/api';

const ROLE_LABEL: Record<string, string> = { manager: '管理员', vip: '高级用户', normal: '普通用户' };
const ROLE_COLOR: Record<string, string> = { manager: 'gold', vip: 'purple', normal: 'default' };

export default function UsersTab() {
  const [msg, holder] = message.useMessage();
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);

  const [editing, setEditing] = useState<User | null>(null); // 正在编辑的用户（null=关闭）
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setUsers(await listUsers());
    } catch (e) {
      msg.error(e instanceof Error ? e.message : '加载用户失败');
    } finally {
      setLoading(false);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(); }, [load]);

  const openCreate = () => { setEditing(null); setCreating(true); form.resetFields(); };
  const openEdit = (u: User) => {
    setEditing(u); setCreating(false);
    form.setFieldsValue({ username: u.username, nickname: u.nickname, phone: u.phone, is_vip: u.role === 'vip' });
  };
  const closeModal = () => { setCreating(false); setEditing(null); };

  const handleSubmit = async () => {
    try {
      const v = await form.validateFields();
      setSaving(true);
      if (creating) {
        await createUser({ username: v.username, nickname: v.nickname, phone: v.phone, is_vip: v.is_vip });
        msg.success('用户已创建，初始密码 234567');
      } else if (editing) {
        await updateUser(editing.user_id, { nickname: v.nickname, phone: v.phone, is_vip: v.is_vip });
        msg.success('已保存');
      }
      closeModal();
      load();
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      msg.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleReset = async (u: User) => {
    try {
      await resetUserPassword(u.user_id);
      msg.success(`已将 ${u.username} 的密码重置为 234567`);
    } catch (e) {
      msg.error(e instanceof Error ? e.message : '重置失败');
    }
  };

  const handleToggleStatus = async (u: User) => {
    const next = u.status === 'enabled' ? 'disabled' : 'enabled';
    try {
      await setUserStatus(u.user_id, next as 'enabled' | 'disabled');
      msg.success(next === 'disabled' ? '已禁用（数据保留，禁止登录）' : '已启用');
      load();
    } catch (e) {
      msg.error(e instanceof Error ? e.message : '操作失败');
    }
  };

  const columns = [
    { title: '用户名', dataIndex: 'username', key: 'username' },
    { title: '昵称', dataIndex: 'nickname', key: 'nickname' },
    { title: '手机号', dataIndex: 'phone', key: 'phone', render: (v: string | null) => v || '—' },
    {
      title: '角色', dataIndex: 'role', key: 'role',
      render: (r: string) => <Tag color={ROLE_COLOR[r]}>{ROLE_LABEL[r] || r}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={s === 'enabled' ? 'green' : 'red'}>{s === 'enabled' ? '启用' : '禁用'}</Tag>,
    },
    {
      title: '操作', key: 'actions',
      render: (_: unknown, u: User) => {
        const isManager = u.role === 'manager';
        return (
          <Space size="small">
            <Button size="small" icon={<Save size={13} />} disabled={isManager} onClick={() => openEdit(u)}>编辑</Button>
            <Popconfirm
              title="重置该用户密码为 234567？"
              disabled={isManager}
              onConfirm={() => handleReset(u)}
              okText="重置"
              cancelText="取消"
            >
              <Button size="small" icon={<KeyRound size={13} />} disabled={isManager}>重置密码</Button>
            </Popconfirm>
            <Button size="small" disabled={isManager} onClick={() => handleToggleStatus(u)}>
              {u.status === 'enabled' ? '禁用' : '启用'}
            </Button>
          </Space>
        );
      },
    },
  ];

  return (
    <Card
      title="用户管理"
      extra={<Button type="primary" icon={<Plus size={14} />} onClick={openCreate}>新增用户</Button>}
    >
      {holder}
      <Table rowKey="user_id" loading={loading} columns={columns} dataSource={users} pagination={false} size="middle" />

      <Modal
        title={creating ? '新增用户' : `编辑用户（${editing?.username ?? ''}）`}
        open={creating || !!editing}
        onOk={handleSubmit}
        onCancel={closeModal}
        confirmLoading={saving}
        okText="保存"
        destroyOnClose
        centered
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="username" label="用户名"
            rules={creating ? [{ required: true, message: '请输入用户名' }] : []}
            extra={creating ? undefined : '用户名是登录凭据，不可修改'}
          >
            <Input disabled={!creating} placeholder="登录用户名" />
          </Form.Item>
          <Form.Item name="nickname" label="昵称"><Input placeholder="展示名，缺省同用户名" /></Form.Item>
          <Form.Item name="phone" label="手机号"><Input placeholder="选填" /></Form.Item>
          <Form.Item name="is_vip" label="高级用户（Vip）" valuePropName="checked" extra="开启=高级用户（可调整引擎参数），关闭=普通用户">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
