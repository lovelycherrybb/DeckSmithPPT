'use client';

/**
 * 当前安装包版本徽标：渲染在 "DeckSmithPower" 后面的淡灰版本号。
 *
 * 版本来自后端 /api/version（发版时 compose 用 IMAGE_TAG 注入 DECKSMITH_VERSION）。
 * 开发态后端返回 version:null → 本组件不渲染任何东西，符合"未确认版本不显示"的诉求。
 * 模块级缓存：页头与首页共用一次请求。
 */
import { useEffect, useState } from 'react';
import { getAppVersion } from '@/lib/api';

let cached: string | null | undefined; // undefined=未取过, null=无版本(dev), string=版本号

export default function AppVersion({ style }: { style?: React.CSSProperties }) {
  const [version, setVersion] = useState<string | null>(cached ?? null);

  useEffect(() => {
    if (cached !== undefined) {
      setVersion(cached);
      return;
    }
    let alive = true;
    getAppVersion()
      .then((r) => {
        cached = r.version ?? null;
        if (alive) setVersion(cached);
      })
      .catch(() => {
        cached = null; // 取不到就当无版本，不显示
      });
    return () => {
      alive = false;
    };
  }, []);

  if (!version) return null;
  const label = version.startsWith('v') ? version : `v${version}`;
  return (
    <span
      style={{
        marginLeft: 6,
        fontSize: '0.72em',
        fontWeight: 500,
        color: 'var(--text-muted)',
        letterSpacing: '0.02em',
        ...style,
      }}
    >
      {label}
    </span>
  );
}
