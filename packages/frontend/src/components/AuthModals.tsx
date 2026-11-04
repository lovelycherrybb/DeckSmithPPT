'use client';

/** 登录弹窗 + 修改密码弹窗（用户系统 v0.8）。用 message.useMessage()（无全局 <App> 包裹）。 */
import { useState } from 'react';
import { Form, Input, message, Modal } from 'antd';

import { changePassword } from '@/lib/api';
import { useAuth } from '@/components/AuthProvider';

export function LoginModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { doLogin } = useAuth();
  const [msg, holder] = message.useMessage();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const handleOk = async () => {
    try {
      const { username, password } = await form.validateFields();
      setSubmitting(true);
      const claimed = await doLogin(username, password);
      msg.success(claimed > 0 ? `登录成功，未登录期间创建的 ${claimed} 份草稿已归入当前账号` : '登录成功');
      form.resetFields();
      onClose();
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return; // 表单校验错误，不重复提示
      msg.error(e instanceof Error ? e.message : '登录未成功，请检查用户名和密码后重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="登录" open={open} onOk={handleOk} onCancel={onClose} confirmLoading={submitting} okText="登录" destroyOnClose centered>
      {holder}
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
          <Input placeholder="请输入登录用户名" autoComplete="username" />
        </Form.Item>
        <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
          <Input.Password placeholder="请输入密码" autoComplete="current-password" onPressEnter={handleOk} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

export function ChangePasswordModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [msg, holder] = message.useMessage();
  const [form] = Form.useForm();
  const [submitting, setSubmitting] = useState(false);

  const handleOk = async () => {
    try {
      const { old_password, new_password } = await form.validateFields();
      setSubmitting(true);
      await changePassword(old_password, new_password);
      msg.success('密码已修改');
      form.resetFields();
      onClose();
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      msg.error(e instanceof Error ? e.message : '密码修改未成功，请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal title="修改密码" open={open} onOk={handleOk} onCancel={onClose} confirmLoading={submitting} okText="确定修改" destroyOnClose centered>
      {holder}
      <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
        <Form.Item name="old_password" label="当前密码" rules={[{ required: true, message: '请输入当前密码' }]}>
          <Input.Password autoComplete="current-password" />
        </Form.Item>
        <Form.Item name="new_password" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 6, message: '新密码至少 6 位' }]}>
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Form.Item
          name="confirm_password"
          label="确认新密码"
          dependencies={['new_password']}
          rules={[
            { required: true, message: '请再输入一遍新密码' },
            ({ getFieldValue }) => ({
              validator(_, value) {
                if (!value || getFieldValue('new_password') === value) return Promise.resolve();
                return Promise.reject(new Error('两次输入的新密码不一致'));
              },
            }),
          ]}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
      </Form>
    </Modal>
  );
}
