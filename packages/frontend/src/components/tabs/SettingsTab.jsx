'use client';

import { Card, Form, Select, Switch, Button, Divider, message } from 'antd';
import { Save } from 'lucide-react';

export default function SettingsTab() {
  const [form] = Form.useForm();

  const handleSave = () => {
    message.success('设置已保存');
  };

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      <Card title="设置">
        <Form form={form} layout="vertical" initialValues={{ defaultEngine: 'ppt_master', autoSave: true }}>
          <Form.Item name="defaultEngine" label="默认输出引擎">
            <Select
              options={[
                { value: 'ppt_master', label: 'PPT Master' },
                { value: 'html_output', label: 'HTML 输出' },
              ]}
            />
          </Form.Item>

          <Form.Item name="defaultPages" label="默认页数">
            <Select
              options={[
                { value: 5, label: '5 页' },
                { value: 8, label: '8 页' },
                { value: 10, label: '10 页' },
                { value: 15, label: '15 页' },
              ]}
            />
          </Form.Item>

          <Divider />

          <Form.Item name="autoSave" label="自动保存草稿" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item>
            <Button type="primary" icon={<Save size={16} />} onClick={handleSave}>
              保存设置
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
