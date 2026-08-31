import { AntdRegistry } from '@ant-design/nextjs-registry';
import { ConfigProvider } from 'antd';
import './globals.css';

export const metadata = {
  title: 'DeckSmithPPT',
  description: '上传材料、生成草稿、编辑确认、查看历史任务',
};

export default function RootLayout({ children }) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <ConfigProvider
            theme={{
              token: {
                colorPrimary: '#1f6feb',
                borderRadius: 6,
                colorBgLayout: '#f4f7fb',
                colorTextBase: '#172033',
                fontFamily:
                  'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
              },
            }}
          >
            {children}
          </ConfigProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
