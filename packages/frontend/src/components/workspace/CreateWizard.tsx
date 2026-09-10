/**
 * 子菜单①：创建向导——一步步引导创建草稿。
 * 步骤：选素材来源（文件多选 / 文本）→ 补充信息 → 提交草稿任务。
 * 契约：DeckRequest.sources 是数组，支持多文件；引擎逐个 source_to_md 合并成草稿。
 */
'use client';

import { useEffect, useState } from 'react';
import { Alert, Button, Card, Form, Input, Segmented, Select, Steps, Tag, Upload, message } from 'antd';
import { ArrowRight, FileText, Sparkles, UploadCloud, X } from 'lucide-react';
import { submitDeck, suggestTitle, suggestTitleStream, uploadFile, type DeckRequest, type EngineInfo, type UploadResponse } from '@/lib/api';

const { TextArea } = Input;

interface Props {
  draftEngines: EngineInfo[];
  currentProjectId: string | null;
  onSubmitted: () => void;
}

export default function CreateWizard({ draftEngines, currentProjectId, onSubmitted }: Props) {
  const [messageApi, contextHolder] = message.useMessage();
  const [step, setStep] = useState(0);

  // 步骤1：素材（多文件）
  const [sourceType, setSourceType] = useState<'upload' | 'text'>('upload');
  const [uploadedFiles, setUploadedFiles] = useState<UploadResponse[]>([]);
  const [seedText, setSeedText] = useState('');

  // 步骤2：信息
  const [draftEngine, setDraftEngine] = useState<string>('');
  const [title, setTitle] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [streamHint, setStreamHint] = useState(''); // Agent 流式输出（实时显示在标题框 placeholder）

  const sourceReady = sourceType === 'upload' ? uploadedFiles.length > 0 : seedText.trim().length > 0;

  // 单文件上传后自动把文件名（去扩展名）带入标题，用户可修改
  useEffect(() => {
    if (sourceType === 'upload' && uploadedFiles.length === 1 && !title) {
      const stem = uploadedFiles[0].filename.replace(/\.[^.]+$/, '').trim();
      if (stem) setTitle(stem);
    }
  }, [uploadedFiles, sourceType]); // eslint-disable-line react-hooks/exhaustive-deps


  const [uploadCount, setUploadCount] = useState(0);
  const uploading = uploadCount > 0;

  const handleUpload = async (file: File) => {
    // 前置体积检查：超过上限直接人话报错，不走无谓的重试
    const MAX_MB = 50;
    if (file.size > MAX_MB * 1024 * 1024) {
      messageApi.error(`「${file.name}」超过 ${MAX_MB}MB 上限，请压缩后再上传`);
      return false;
    }
    setUploadCount((n) => n + 1);
    try {
      const result = await uploadFile(file);
      setUploadedFiles((prev) => [...prev, result]);
      messageApi.success(`已上传：${result.filename}`);
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : `上传失败：${file.name}`);
    } finally {
      setUploadCount((n) => Math.max(0, n - 1));
    }
    return false; // 关键：阻止 antd 内部上传（否则会 POST 到 action/当前页 → 404/500）
  };

  const removeFile = (uploadId: string) => {
    setUploadedFiles((prev) => prev.filter((f) => f.upload_id !== uploadId));
  };

  // 拟题：单文件直接用文件名（去扩展名）；多文件调 Agent 概括拟题；文本素材取首行
  const handleSuggestTitle = async () => {
    if (uploadedFiles.length === 0 && !seedText.trim()) {
      messageApi.warning('请先提供素材');
      return;
    }
    // 单文件：文件名即标题，本地完成（与后端规则一致）
    if (sourceType === 'upload' && uploadedFiles.length === 1) {
      const stem = uploadedFiles[0].filename.replace(/\.[^.]+$/, '').trim();
      if (stem) {
        setTitle(stem);
        messageApi.success(`标题：${stem}`);
        return;
      }
    }
    // 文本素材：首行即标题（去 Markdown # 前缀）
    if (sourceType === 'text') {
      const first = seedText.trim().split('\n')[0] || '';
      const heading = first.replace(/^#+\s*/, '').trim();
      if (heading) {
        setTitle(heading.slice(0, 60));
        messageApi.success(`标题：${heading.slice(0, 60)}`);
        return;
      }
    }
    // 多文件：Agent 通读内容拟题（流式：增量实时显示在标题框 placeholder）
    setSuggesting(true);
    setStreamHint('');
    try {
      const finalTitle = await suggestTitleStream(
        uploadedFiles.map((f) => ({ filename: f.filename, path: f.path })),
        (accumulated) => setStreamHint(accumulated)
      );
      setTitle(finalTitle);
      messageApi.success(`AI 拟题：${finalTitle}`);
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : 'AI 拟题失败');
    } finally {
      setSuggesting(false);
      setStreamHint('');
    }
  };

  const handleSubmit = async () => {
    const engine = ((draftEngine || draftEngines[0]?.name) || 'ppt_master_draft') as DeckRequest['engine'];
    setSubmitting(true);
    try {
      await submitDeck({
        engine,
        engine_type: 'draft',
        // 已有项目：草稿进当前项目（标题隐藏，无需确认）；无项目：提交即创建（项目名=标题/素材拟定）
        project_id: currentProjectId || undefined,
        title: title || undefined,
        sources:
          sourceType === 'upload'
            ? uploadedFiles.map((f) => ({ type: 'file_path' as const, value: f.path }))
            : [{ type: 'text' as const, value: seedText }],
      });
      messageApi.success(
        `草稿任务已提交（${sourceType === 'upload' ? `${uploadedFiles.length} 个文件` : '文本素材'}），完成后将出现在「草稿任务」`
      );
      // 重置向导
      setStep(0);
      setUploadedFiles([]);
      setSeedText('');
      setTitle('');
      onSubmitted();
    } catch (e) {
      messageApi.error(e instanceof Error ? e.message : '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: 720, margin: '0 auto' }}>
      {contextHolder}
      <Card>
        <Steps
          current={step}
          onChange={setStep}
          items={[
            { title: '选择素材', description: '文件（可多选）或文本' },
            { title: '补充信息', description: '标题' },
            { title: '提交', description: '生成草稿' },
          ]}
          style={{ marginBottom: 32 }}
        />

        {step === 0 && (
          <Form layout="vertical">
            <Form.Item label="素材来源">
              <Segmented
                options={[
                  { label: '上传文件（可多选）', value: 'upload' },
                  { label: '粘贴文本', value: 'text' },
                ]}
                value={sourceType}
                onChange={(v) => setSourceType(v as 'upload' | 'text')}
                style={{ marginBottom: 16 }}
              />
              {sourceType === 'upload' ? (
                <>
                  <Upload.Dragger
                    multiple
                    showUploadList={false}
                    beforeUpload={handleUpload}
                    accept=".txt,.md,.docx,.pdf"
                    style={{ marginBottom: 4 }}
                    disabled={uploading}
                  >
                    <p className="ant-upload-drag-icon">
                      <UploadCloud size={36} color="#8b5cf6" />
                    </p>
                    <p className="ant-upload-text" style={{ fontSize: 14 }}>
                      {uploading ? '正在上传…' : '点击或拖拽文件到此处'}
                    </p>
                    <p className="ant-upload-hint" style={{ fontSize: 12 }}>
                      支持多选、可多次追加 · TXT / MD / DOCX / PDF
                    </p>
                  </Upload.Dragger>
                  {uploadedFiles.length > 0 && (
                    <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {uploadedFiles.map((f) => (
                        <div
                          key={f.upload_id}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            padding: '6px 12px',
                            background: 'var(--bg-hover)',
                            border: '1px solid var(--border)',
                            borderRadius: 8,
                          }}
                        >
                          <Tag color="green" icon={<FileText size={12} />} style={{ margin: 0 }}>
                            {f.filename}
                          </Tag>
                          <Button
                            type="text"
                            size="small"
                            icon={<X size={14} />}
                            onClick={() => removeFile(f.upload_id)}
                          />
                        </div>
                      ))}
                      <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                        已上传 {uploadedFiles.length} 个文件，可继续追加，提交时一起交给引擎
                      </span>
                    </div>
                  )}
                </>
              ) : (
                <TextArea
                  rows={8}
                  placeholder="粘贴 Markdown 或纯文本内容…"
                  value={seedText}
                  onChange={(e) => setSeedText(e.target.value)}
                />
              )}
            </Form.Item>
            <Button
              type="primary"
              icon={<ArrowRight size={15} />}
              disabled={!sourceReady}
              onClick={() => setStep(1)}
            >
              下一步
            </Button>
          </Form>
        )}

        {step === 1 && (
          <>
            <Form layout="vertical">
              <Form.Item
                label="草稿引擎"
                help={draftEngines.find((e) => e.name === (draftEngine || draftEngines[0]?.name))?.description || '不同引擎理解素材的方式不同'}
              >
                <Select
                  value={draftEngine || draftEngines[0]?.name}
                  onChange={setDraftEngine}
                  options={draftEngines.map((e) => ({
                    value: e.name,
                    label: e.display_name || e.name,
                  }))}
                />
              </Form.Item>
              {currentProjectId ? null : (
              <Form.Item
                label="标题（将作为新项目名称）"
                help="首次生成会自动创建项目；可让 AI 拟题，留空则由引擎从素材自动拟定"
              >
                <Input.Search
                  placeholder={
                    suggesting
                      ? streamHint
                        ? `✍ AI 正在拟题：${streamHint}`
                        : 'AI 正在通读文件，准备拟题…'
                      : '例如：产品发布会介绍'
                  }
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  enterButton={
                    <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Sparkles size={14} />
                      拟题
                    </span>
                  }
                  loading={suggesting}
                  onSearch={handleSuggestTitle}
                />
              </Form.Item>
              )}
              <Button
                type="primary"
                icon={<Sparkles size={15} />}
                loading={submitting}
                onClick={handleSubmit}
              >
                提交草稿任务
              </Button>
            </Form>
            <Alert
              type="info"
              showIcon
              message="提交后引擎会理解素材并生成结构化草稿，通常几秒到几十秒完成。"
            />
          </>
        )}
      </Card>
    </div>
  );
}
