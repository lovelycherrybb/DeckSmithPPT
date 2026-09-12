/**
 * 逐页预览按需拉取 hook（轮询瘦身）。
 *
 * 全局列表轮询（usePollingJobs）仍只带 pages_generated 计数，不塞整页列表；
 * 本 hook 仅在挂载/传入、且 watchKey 变化时才 GET /pages：
 * - running：调用方传 `${pages_generated}-${stage}` 作 watchKey，页数或阶段变了才重取；
 * - completed（详情抽屉/文件库预览）：不传 watchKey，打开时拉一次。
 * 失败时保留上一份数据（轮询瞬时错误不闪空）；jobId 置空即卸载。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { getJobPages, type JobPages } from '@/lib/api';

export function useJobPages(jobId: string | null | undefined, watchKey?: string | number) {
  const [data, setData] = useState<JobPages | null>(null);
  const [loading, setLoading] = useState(false);
  // 换 job 时清空旧数据，防串页显示
  const lastJobRef = useRef<string | null>(null);

  const fetchOnce = useCallback(async () => {
    if (!jobId) return;
    setLoading(true);
    try {
      const d = await getJobPages(jobId);
      setData(d);
    } catch {
      // 保留上一次成功数据；任务被删等场景由调用方 status 兜底
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (!jobId) {
      setData(null);
      lastJobRef.current = null;
      return;
    }
    if (lastJobRef.current !== jobId) {
      lastJobRef.current = jobId;
      setData(null); // 不同任务间不残留上一份
    }
    fetchOnce();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, watchKey]);

  return { data, loading, reload: fetchOnce };
}
