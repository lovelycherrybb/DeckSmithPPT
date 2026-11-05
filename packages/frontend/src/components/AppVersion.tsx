'use client';

/**
 * 当前安装包版本徽标：渲染在 "DeckSmithPower" 后面的淡灰版本号，后面跟一个问号提示。
 *
 * 版本来自后端 /api/version（发版时 compose 用 IMAGE_TAG 注入 DECKSMITH_VERSION）。
 * 开发态后端返回 version:null → 本组件不渲染任何东西，符合"未确认版本不显示"的诉求。
 * 问号只在 releaseNotes 里命中当前版本时出现；对不上整个不出现，不拿别的版本的说法糊。
 * 模块级缓存：页头与首页共用一次请求。
 */
import { useEffect, useState } from 'react';
import { Tooltip } from 'antd';
import { CircleHelp } from 'lucide-react';
import { getAppVersion } from '@/lib/api';
import { releaseNoteFor } from '@/lib/releaseNotes';

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
  const note = releaseNoteFor(version);
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
      {note && (
        <Tooltip
          placement="bottomLeft"
          trigger={['hover', 'click']}
          styles={{ root: { maxWidth: 420 } }}
          title={
            <div style={{ maxWidth: 400 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>{note.heading}</div>
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {note.items.map((item) => (
                  <li key={item} style={{ marginBottom: 4 }}>
                    {item}
                  </li>
                ))}
              </ul>
            </div>
          }
        >
          {/* 顶栏里本组件整块在「标志与产品名」这个回首页的链接内部：这里必须用 span，
              用按钮会变成「链接里套按钮」的非法嵌套；点击挡住，否则点问号会跳回首页。 */}
          <span
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            style={{
              marginLeft: 5,
              display: 'inline-flex',
              alignItems: 'center',
              verticalAlign: 'middle',
              cursor: 'help',
            }}
          >
            <CircleHelp size={12} strokeWidth={2} />
          </span>
        </Tooltip>
      )}
    </span>
  );
}
