'use client';

/**
 * 用户管理（用户系统 v0.8，仅管理员）。接 /api/users 一组端点。
 *
 * 列表 + 新增 + 改信息（用户名不可改）+ 重置密码为 234567 + 启用/禁用。
 * 内置 manager 账号在界面上禁用所有操作（后端也会 403 兜底）。
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Button, Card, Form, Input, message, Modal, Popconfirm, Space, Switch, Table, Tag, Tooltip,
} from 'antd';
import { KeyRound, Plus, Save } from 'lucide-react';

import {
  createUser, listUsers, resetUserPassword, setUserStatus, updateUser, type User,
} from '@/lib/api';
import { timeOrDash } from '@/lib/time';

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
      msg.error(e instanceof Error ? e.message : '用户列表加载失败，请刷新页面重试');
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
        msg.success('用户已创建，请通知本人使用初始密码登录后自行修改');
      } else if (editing) {
        await updateUser(editing.user_id, { nickname: v.nickname, phone: v.phone, is_vip: v.is_vip });
        msg.success('已更新该用户');
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
      msg.success(`已重置 ${u.username} 的登录密码，请通知本人登录后自行修改`);
    } catch (e) {
      msg.error(e instanceof Error ? e.message : '重置失败');
    }
  };

  const handleToggleStatus = async (u: User) => {
    const next = u.status === 'enabled' ? 'disabled' : 'enabled';
    try {
      await setUserStatus(u.user_id, next as 'enabled' | 'disabled');
      msg.success(next === 'disabled' ? '已停用，该账号暂不能登录，资料仍保留' : '已恢复登录');
      load();
    } catch (e) {
      msg.error(e instanceof Error ? e.message : '修改未成功，请重试');
    }
  };

  const columns = [
    { title: '用户名', dataIndex: 'username', key: 'username' },
    { title: '昵称', dataIndex: 'nickname', key: 'nickname' },
    { title: '手机号', dataIndex: 'phone', key: 'phone', render: (v: string | null) => v || '—' },
    {
      title: '权限', dataIndex: 'role', key: 'role',
      render: (r: string) => <Tag color={ROLE_COLOR[r]}>{ROLE_LABEL[r] || r}</Tag>,
    },
    {
      title: '账号状态', dataIndex: 'status', key: 'status',
      render: (s: string) => <Tag color={s === 'enabled' ? 'green' : 'red'}>{s === 'enabled' ? '启用' : '禁用'}</Tag>,
    },
    {
      // 口径：这是"最后访问"——该用户会话内每次请求都刷新（后端 60 秒落库节流，显示精度分钟级）。
      // 上一版用"最后登录"是错的：登录只在进系统时发生一次，handong 当天跑着任务、
      // 列却停在昨天，就是这么来的。文案与契约 User.last_access_at 的说明一致。
      title: '上次使用时间', dataIndex: 'last_access_at', key: 'last_access_at', width: 110,
      render: (v: string | null) => timeOrDash(v),
    },
    {
      // 操作列一律钉在列尾（全站口径，与 HistoryTab 一致）：窄屏横向滚动时按钮不许被推出可视区。
      // width 按内容实测算足（三个按钮 + 间距 + 单元格左右内边距），不靠试出来。
      title: '操作', key: 'actions', width: 300, fixed: 'right' as const,
      render: (_: unknown, u: User) => {
        const isManager = u.role === 'manager';
        return (
          <Space size="small">
            <Tooltip title={isManager ? '内置管理员账号不可修改' : undefined}>
              <span>
                <Button size="small" icon={<Save size={13} />} disabled={isManager} onClick={() => openEdit(u)}>编辑</Button>
              </span>
            </Tooltip>
            <Tooltip title={isManager ? '内置管理员账号不可重置密码' : undefined}>
              <span>
                <Popconfirm
                  title={`重置 ${u.username} 的登录密码？`}
                  description="重置后该用户需使用初始密码登录，登录后请通知其自行修改"
                  disabled={isManager}
                  onConfirm={() => handleReset(u)}
                  okText="重置"
                  cancelText="取消"
                >
                <Button size="small" icon={<KeyRound size={13} />} disabled={isManager}>重置密码</Button>
                </Popconfirm>
              </span>
            </Tooltip>
            <Tooltip title={isManager ? '内置管理员账号不可停用' : undefined}>
              <span>
                <Button size="small" disabled={isManager} onClick={() => handleToggleStatus(u)}>
                  {u.status === 'enabled' ? '禁用' : '启用'}
                </Button>
              </span>
            </Tooltip>
          </Space>
        );
      },
    },
  ];

  return (
    <Card
      title="用户管理"
      extra={<Button type="primary" icon={<Plus size={14} />} onClick={openCreate}>新建用户</Button>}
    >
      {holder}
      <Table rowKey="user_id" loading={loading} columns={columns} dataSource={users} pagination={false} size="middle"
        scroll={{ x: 900 }} />

      <Modal
        title={creating ? '新建用户' : '编辑用户'}
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
            extra={creating ? undefined : '用户名用于登录，创建后不可修改'}
          >
            <Input disabled={!creating} placeholder="登录用户名" />
          </Form.Item>
          <Form.Item name="nickname" label="昵称"><Input placeholder="显示给他人看的名称，未填写时使用用户名" /></Form.Item>
          <Form.Item name="phone" label="手机号"><Input placeholder="选填" /></Form.Item>
          <Form.Item name="is_vip" label="高级用户" valuePropName="checked" extra="开启后，该用户可使用更细致的生成设置；关闭则仅使用基础功能">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
