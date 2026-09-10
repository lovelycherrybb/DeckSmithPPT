'use client';

import { useEffect, useState } from 'react';

/**
 * 避免 hydration 错误的钩子
 * 只在客户端渲染时返回 true
 */
export function useClientOnly() {
  const [isClient, setIsClient] = useState(false);

  useEffect(() => {
    setIsClient(true);
  }, []);

  return isClient;
}
