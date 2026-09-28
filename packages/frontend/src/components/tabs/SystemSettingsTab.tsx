/**
 * 系统设置（仅管理员，ADR-020）。
 *
 * 这个页面**不认识任何一个设置项的名字**：它把后端下发的 `fields[]` 按 `type`
 * 通用渲染、按 `group` 分区。后端注册表加一项（包括将来的 agent CLI 配置），
 * 本页自动长出来，不需要改这里——这正是当初把元数据放进契约的理由。
 *
 * 三条界面纪律：
 * 1. 生效方式逐项如实：`restart_required` 为假说"保存后对下一个任务即生效"，
 *    为真说"需重启容器后生效"。含糊成"稍后生效"等于骗人。
 * 2. secret 项不回显明文，只回掩码；**没改动就不提交这个键**（提交了空串会被后端拒，
 *    提交了 null 则是清空密钥，两者都不是用户想要的"我没动它"）。
 * 3. 后端 400 的 detail 原样展示：那里逐列了哪一项为什么不合法，不另编一套话术。
 */
'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Divider,
  Input,
  InputNumber,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd';
import { RotateCcw, Save } from 'lucide-react';
import {
  ApiError,
  getSystemSettings,
  saveSystemSettings,
  type SystemSettingsField,
  type SystemSettingsView,
} from '@/lib/api';

const { Text } = Typography;

type DraftValue = string | number | boolean;

/**
 * 把加载失败翻译成人话。
 *
 * 直接把框架原文糊到脸上是不合格的：实测后端进程是改动前起的、不认识新路由时，
 * 这里显示的是 `Not Found` —— 管理员看到只会以为"权限不对"或"东西被删了"，
 * 而真实原因是"后端服务需要重启"，是一个他自己能动手解决的动作。
 */
function describeLoadError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404) {
      return '后端未提供系统设置接口。若这些功能是刚发布的，重启后端服务即可（旧进程不认识新路由）。';
    }
    if (e.status === 403) {
      return '当前账号不是管理员，无法查看或修改系统设置。';
    }
    if (e.status === 401) {
      return '登录状态已失效，请重新登录后再试。';
    }
    return e.message || `读取失败（HTTP ${e.status}）`;
  }
  return e instanceof Error ? e.message : '读取系统设置失败';
}

export default function SystemSettingsTab() {
  const [messageApi, contextHolder] = message.useMessage();
  const [data, setData] = useState<SystemSettingsView | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 改动：key → 新值；cleared：被点了"恢复默认"的 key（提交时传 null）
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [cleared, setCleared] = useState<Set<string>>(new Set());

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const view = await getSystemSettings();
      setData(view);
      setDraft({});
      setCleared(new Set());
      setError(null);
    } catch (e) {
      setError(describeLoadError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const dirty = useMemo(
    () => Object.keys(draft).length + cleared.size,
    [draft, cleared],
  );

  const setValue = (key: string, value: DraftValue | undefined) => {
    setCleared((prev) => {
      if (!prev.has(key)) return prev;
      const next = new Set(prev);
      next.delete(key);
      return next;
    });
    if (value === undefined) {
      setDraft((prev) => {
        const next = { ...prev };
        delete next[key];
        return next;
      });
      return;
    }
    setDraft((prev) => ({ ...prev, [key]: value }));
  };

  const restoreDefault = (key: string) => {
    setDraft((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setCleared((prev) => new Set(prev).add(key));
  };

  const handleSave = async () => {
    const values: Record<string, string | number | boolean | null> = { ...draft };
    cleared.forEach((k) => { values[k] = null; });
    setSaving(true);
    try {
      await saveSystemSettings(values);
      messageApi.success(`已保存 ${Object.keys(values).length} 项设置`);
      await reload();
    } catch (e) {
      // 后端逐项列出为什么不合法，原样给管理员看
      messageApi.error(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const renderControl = (f: SystemSettingsField) => {
    const clearedHere = cleared.has(f.key);
    if (f.type === 'bool') {
      const current = clearedHere ? !!f.default : (draft[f.key] as boolean | undefined) ?? !!f.value;
      return (
        <Switch
          checked={current}
          disabled={clearedHere}
          onChange={(v) => setValue(f.key, v)}
        />
      );
    }
    if (f.type === 'int') {
      const current = clearedHere ? undefined : (draft[f.key] as number | undefined) ?? (f.value as number | undefined);
      return (
        <Space size={6}>
          <InputNumber
            value={current}
            min={f.min ?? undefined}
            max={f.max ?? undefined}
            disabled={clearedHere}
            style={{ width: 140 }}
            onChange={(v) => (v == null ? undefined : setValue(f.key, Number(v)))}
          />
          {/* 单位只是显示，不参与取值；后端 min/max 才是真边界
              （antd 的 InputNumber addonAfter 已弃用，改用 Space 并排） */}
          {f.unit ? (
            <Text type="secondary" style={{ fontSize: 12 }}>{f.unit}</Text>
          ) : null}
        </Space>
      );
    }
    if (f.type === 'secret') {
      // 明文不回显：留空表示不改；placeholder 用掩码让人确认"是不是这一个"
      return (
        <Input.Password
          placeholder={f.configured ? `已配置（${f.masked}），留空不修改` : '未设置'}
          disabled={clearedHere}
          style={{ width: 320 }}
          onChange={(e) => {
            const v = e.target.value;
            if (v) setValue(f.key, v);
            else setValue(f.key, undefined);
          }}
        />
      );
    }
    const current = clearedHere ? '' : (draft[f.key] as string | undefined) ?? String(f.value ?? '');
    return (
      <Input
        value={current}
        disabled={clearedHere}
        style={{ width: 320 }}
        onChange={(e) => setValue(f.key, e.target.value)}
      />
    );
  };

  const statusTag = (f: SystemSettingsField) => {
    if (cleared.has(f.key)) return <Tag>已恢复默认</Tag>;
    if (draft[f.key] !== undefined) return <Tag color="blue">未保存</Tag>;
    return f.overridden
      ? <Tag color="purple">已覆盖</Tag>
      : <Tag>走默认</Tag>;
  };

  if (loading && !data) {
    return <Card loading variant="borderless" />;
  }

  return (
    // 限宽 + 居中：与 globals.css 里子标签胶囊、当前项目行同一套路。
    // 只给 maxWidth 不给 margin:auto 会让整块靠左，宽屏下右侧空一大片。
    // 表单说明文字较长，铺满整屏反而不好读，所以保留宽度上限。
    <div style={{ maxWidth: 880, margin: '0 auto' }}>
      {contextHolder}
      {error && (
        <Alert type="error" showIcon message="系统设置不可用" description={error} style={{ marginBottom: 16 }} />
      )}
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {(data?.groups || []).map((g) => {
          const fields = (data?.fields || []).filter((f) => f.group === g.id);
          if (fields.length === 0) return null;
          return (
            <Card
              key={g.id}
              title={g.label}
              extra={<Text type="secondary" style={{ fontSize: 12 }}>{g.description}</Text>}
            >
              <Divider style={{ margin: '0 0 16px' }} />
              {fields.map((f) => (
                <div
                  key={f.key}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'flex-start',
                    gap: 24,
                    marginBottom: 18,
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <Space size={8} align="center">
                      <Text strong style={{ fontSize: 13 }}>{f.label}</Text>
                      {statusTag(f)}
                    </Space>
                    {f.description && (
                      <div><Text type="secondary" style={{ fontSize: 12 }}>{f.description}</Text></div>
                    )}
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                      默认 {String(f.default ?? '—')}
                      {f.unit ? ` ${f.unit}` : ''}
                      {' · '}
                      {f.restart_required ? '需重启容器后生效' : '保存后对下一个任务即生效'}
                      {f.overridden && f.type !== 'secret' && !cleared.has(f.key) ? (
                        <Button
                          size="small"
                          type="link"
                          icon={<RotateCcw size={12} />}
                          style={{ padding: '0 4px' }}
                          onClick={() => restoreDefault(f.key)}
                        >
                          恢复默认
                        </Button>
                      ) : null}
                    </div>
                  </div>
                  <div style={{ minWidth: 200, textAlign: 'right' }}>{renderControl(f)}</div>
                </div>
              ))}
            </Card>
          );
        })}

        {/* 读不到设置项时不摆这条：底下没有任何可保存的东西，"保存"只会再吃一次 404 */}
        {data && (
        <Card styles={{ body: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {dirty > 0 ? `${dirty} 项改动未保存` : '没有未保存的改动'}
            {data?.updated_at ? ` · 上次修改 ${data.updated_at.slice(0, 19).replace('T', ' ')}` : ''}
          </Text>
          <Space>
            <Button onClick={reload} disabled={saving}>放弃改动并重载</Button>
            <Button
              type="primary"
              icon={<Save size={14} />}
              loading={saving}
              disabled={dirty === 0}
              onClick={handleSave}
            >
              保存
            </Button>
          </Space>
        </Card>
        )}
      </Space>
    </div>
  );
}
