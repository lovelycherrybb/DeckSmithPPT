'use client';

/**
 * 设置（用户系统 v0.8）：本人偏好读写，接 /api/settings/preferences。
 * 引擎选项来自 GET /api/engines?type=output（不再写死假引擎）。偏好仅作初始值，不改权限。
 */
import { useEffect, useState } from 'react';
import { Alert, Card, Divider, Form, InputNumber, message, Select, Switch, Button } from 'antd';
import { Save } from 'lucide-react';

import { getMyPreferences, listEngines, saveMyPreferences } from '@/lib/api';

export default function SettingsTab() {
  const [form] = Form.useForm();
  const [msg, holder] = message.useMessage();
  const [engines, setEngines] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [prefs, engs] = await Promise.all([getMyPreferences(), listEngines('output')]);
        setEngines(engs.map((e) => ({ value: e.name, label: e.display_name || e.name })));
        form.setFieldsValue({
          default_engine: prefs.default_engine ?? undefined,
          default_pages: prefs.default_pages ?? undefined,
          auto_save: prefs.auto_save ?? true,
        });
      } catch (e) {
        msg.error(e instanceof Error ? e.message : '加载偏好失败');
      } finally {
        setLoading(false);
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    try {
      const v = await form.validateFields();
      setSaving(true);
      await saveMyPreferences({
        default_engine: v.default_engine ?? null,
        default_pages: v.default_pages ?? null,
        auto_save: v.auto_save,
      });
      msg.success('设置已保存');
    } catch (e) {
      if (e && typeof e === 'object' && 'errorFields' in e) return;
      msg.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ maxWidth: 600, margin: '0 auto' }}>
      {holder}
      <Card title="设置">
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="这些是你的个人默认值，仅作为新建任务时的初始值；不改变权限（引擎参数能否调整由角色决定）。"
        />
        <Form form={form} layout="vertical" disabled={loading}>
          <Form.Item name="default_engine" label="默认输出引擎">
            <Select allowClear options={engines} placeholder="不设置则由引擎决定" />
          </Form.Item>
          <Form.Item name="default_pages" label="默认页数">
            <InputNumber min={1} precision={0} style={{ width: '100%' }} placeholder="留空则由引擎决定" />
          </Form.Item>

          <Divider />

          <Form.Item name="auto_save" label="自动保存草稿" valuePropName="checked">
            <Switch />
          </Form.Item>

          <Form.Item>
            <Button type="primary" icon={<Save size={16} />} onClick={handleSave} loading={saving}>
              保存设置
            </Button>
          </Form.Item>
        </Form>
      </Card>
    </div>
  );
}
