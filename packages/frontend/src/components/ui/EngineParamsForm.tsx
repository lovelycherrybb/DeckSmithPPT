/**
 * 引擎参数动态表单：按 GET /api/engines 返回的 params_schema（JSON Schema）渲染控件。
 * 契约原则：前端不硬编码任何引擎参数——schema 变了表单自动跟着变。
 */
'use client';

import { useEffect } from 'react';
import { Form, InputNumber, Select, Switch } from 'antd';
import type { EngineInfo } from '@/lib/api';

interface Props {
  schema: EngineInfo['params_schema'];
  /** 排除的参数名（如 source 类字段不进表单） */
  exclude?: string[];
}

export default function EngineParamsForm({ schema, exclude = ['source', 'files', 'prompt'] }: Props) {
  const props = (schema?.properties || {}) as Record<string, {
    type?: string;
    enum?: unknown[];
    default?: unknown;
    description?: string;
    'x-hidden'?: boolean;
  }>;

  // schema 变化时把默认值写入表单（隐藏的纯工程旋钮不写入，交由引擎默认值兜底）
  useEffect(() => {
    const form = Form.useFormInstance();
    const defaults: Record<string, unknown> = {};
    Object.entries(props).forEach(([key, value]) => {
      if (exclude.includes(key) || value['x-hidden']) return;
      if (value.default !== undefined) defaults[key] = value.default;
      else if (value.enum?.length) defaults[key] = value.enum[0];
      else if (value.type === 'boolean') defaults[key] = false;
    });
    form.setFieldsValue(defaults);
  }, [schema]); // eslint-disable-line react-hooks/exhaustive-deps

  const entries = Object.entries(props).filter(([key, value]) => !exclude.includes(key) && !value['x-hidden']);
  if (entries.length === 0) return null;

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
      {entries.map(([key, value]) => (
        <Form.Item key={key} name={key} label={value.description?.split('。')[0] || key}>
          {value.enum?.length ? (
            <Select
              options={value.enum.map((v) => ({ value: v, label: String(v) }))}
              placeholder={`选择 ${key}`}
            />
          ) : value.type === 'boolean' ? (
            <Select
              options={[
                { value: true, label: '开启' },
                { value: false, label: '关闭' },
              ]}
            />
          ) : value.type === 'integer' ? (
            <InputNumber min={1} style={{ width: '100%' }} placeholder={key} />
          ) : (
            <Select placeholder={key} options={[]} disabled />
          )}
        </Form.Item>
      ))}
    </div>
  );
}

/** 从表单值中挑出属于 schema 的参数（剔除表单里的其他字段） */
export function pickEngineParams(
  schema: EngineInfo['params_schema'],
  values: Record<string, unknown>,
  exclude: string[] = ['source', 'files', 'prompt']
): Record<string, unknown> {
  const props = (schema?.properties || {}) as Record<string, { 'x-hidden'?: boolean }>;
  const result: Record<string, unknown> = {};
  Object.keys(props).forEach((key) => {
    if (exclude.includes(key) || props[key]?.['x-hidden']) return;
    if (values[key] !== undefined) result[key] = values[key];
  });
  return result;
}
