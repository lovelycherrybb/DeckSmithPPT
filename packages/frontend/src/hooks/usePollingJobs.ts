/**
 * 任务轮询 hook：存在**未结束**任务（排队中 + 生成中）时自动定时刷新。
 * 返回 (jobs, refresh)。停止条件由回调结果驱动（全部进入终态即停）。
 *
 * 判据必须是 isPending：只看 running 的话，队列里全是排队任务时会掉到 15 秒慢档，
 * 用户眼里的现象就是"排队位置根本不动"。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { listJobs, type JobWithProgress } from '@/lib/api';
import { isPending } from '@/lib/jobStatus';

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
        const hasActive = data.some((j) => isPending(j.status));
        timer = setTimeout(tick, hasActive ? intervalMs : 15000);
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
