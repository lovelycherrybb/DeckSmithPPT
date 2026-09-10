/**
 * 任务轮询 hook：存在 running 任务时自动定时刷新。
 * 返回 (jobs, refresh)。停止条件由回调结果驱动（无 running 任务即停）。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { listJobs, type JobWithProgress } from '@/lib/api';

export function usePollingJobs(intervalMs = 2000) {
  const [jobs, setJobs] = useState<JobWithProgress[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await listJobs();
      setJobs(data);
      return data;
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  // 自适应轮询：有 running 任务 2s（看进度），空闲 15s（省渲染/请求）
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const data = await listJobs();
        if (cancelled) return;
        setJobs(data);
        const hasRunning = data.some((j) => j.status === 'running');
        timer = setTimeout(tick, hasRunning ? intervalMs : 15000);
      } catch {
        if (cancelled) return;
        timer = setTimeout(tick, 5000); // 失败稍后重试
      }
    };
    tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [intervalMs]);

  return { jobs, loading, refresh };
}
