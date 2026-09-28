/**
 * 只读容量提示（ADR-020 决策 7）。
 *
 * 出现在工作台顶部：让等待的人知道"为什么慢"——系统同时只生成 N 个、
 * 现在有多少在排队。刻意只是三个数，不构成一个只读设置面（数据来自
 * /api/settings/capacity，后端也只回这三个）。
 *
 * 自己 15 秒轮询一次，不挂到任务轮询上：这个数字变化很慢，跟 2 秒的任务刷新
 * 绑在一起只会白白翻倍请求量。取不到就整条不显示——它是解释性提示，
 * 不该在故障时留一个空壳或报错占位。
 */
'use client';

import { useEffect, useState } from 'react';
import { Typography } from 'antd';
import { Hourglass } from 'lucide-react';
import { getCapacity, type CapacityInfo } from '@/lib/api';

const { Text } = Typography;

export default function CapacityNotice() {
  const [cap, setCap] = useState<CapacityInfo | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await getCapacity();
        if (!cancelled) setCap(data);
      } catch {
        // 拿不到就不显示，不做假数据也不报错打扰
      }
    };
    tick();
    const timer = setInterval(tick, 15000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!cap) return null;

  const waiting = cap.queued;
  return (
    <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
      <Hourglass size={13} style={{ color: 'var(--text-muted)' }} />
      <Text type="secondary" style={{ fontSize: 12 }}>
        {`系统同时生成 ${cap.max_concurrent_jobs} 个任务`}
        {waiting > 0
          ? ` · 当前 ${cap.running} 个在生成、${waiting} 个排队中`
          : ` · 现在没有排队，提交后马上开始`}
      </Text>
    </div>
  );
}
