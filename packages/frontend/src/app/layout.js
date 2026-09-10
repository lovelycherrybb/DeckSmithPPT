import '@ant-design/v5-patch-for-react-19'; // antd v5 兼容 React 19（官方补丁，入口引一次即可）
import { AntdRegistry } from '@ant-design/nextjs-registry';
import ThemeProvider from '@/components/ThemeProvider';
import './globals.css';

export const metadata = {
  title: 'DeckSmithPower',
  description: '上传材料、生成草稿、编辑确认、查看历史任务',
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* 首屏前同步应用主题，消除刷新时先黑一下/闪烁 */}
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var t=localStorage.getItem('decksmith-theme');if(t!=='light'&&t!=='dark'){t='dark';}document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`,
          }}
        />
      </head>
      <body suppressHydrationWarning>
        <AntdRegistry>
          <ThemeProvider>{children}</ThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
