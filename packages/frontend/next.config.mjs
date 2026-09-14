/** @type {import('next').NextConfig} */
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const nextConfig = {
  // 开发环境配置
  devIndicators: false,

  // 关闭服务端 gzip：compression 中间件会把 SSE 响应憋到流结束才一次性吐出
  // （浏览器 EventSource 必带 Accept-Encoding: gzip），导致 AI 流式输出假死
  compress: false,

  // 实验性功能
  experimental: {
    // 启用服务器组件
    serverActions: {
      bodySizeLimit: '50mb',
    },
    // rewrite 代理的请求体上限（默认 10MB，大 docx/pdf 上传会被掐断 ECONNRESET）
    middlewareClientMaxBodySize: 50 * 1024 * 1024,
  },

  // 重写规则 (API 代理到后端；主机与端口分别读 BACKEND_HOST / BACKEND_PORT)
  //
  // ⚠️ rewrites() 在 `next build` 时求值并烘焙进 .next/standalone 的路由清单，
  //    **运行期改环境变量无效**。容器构建必须用 build arg 传入；且容器里 127.0.0.1
  //    是 web 自己，打不到后端，故 Docker 下 BACKEND_HOST 必须等于后端 service 名（app）。
  //    默认 127.0.0.1 保持开发环境行为不变。
  async rewrites() {
    const backendHost = process.env.BACKEND_HOST || '127.0.0.1';
    const backendPort = process.env.BACKEND_PORT || '8080';
    return [
      {
        source: '/api/:path*',
        destination: `http://${backendHost}:${backendPort}/api/:path*`,
      },
    ];
  },
  
  // 头部配置 (开发环境)
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          {
            key: 'X-Frame-Options',
            value: 'SAMEORIGIN',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
        ],
      },
    ];
  },
  
  // 图片优化
  images: {
    domains: ['localhost', '127.0.0.1'],
    unoptimized: true, // 开发环境禁用优化
  },
  
  // 构建配置
  output: 'standalone',
  
  // Webpack配置
  webpack: (config, { dev, isServer }) => {
    // 去重 @ant-design/cssinjs：registry 被解析到顶层 v2.1.2、antd 用嵌套 v1.24.0，
    // 两份缓存对不上导致服务端抽不到样式（刷新裸样式）。统一指向 antd 实际用的 v1.24.0。
    config.resolve.alias = {
      ...config.resolve.alias,
      '@ant-design/cssinjs': path.resolve(__dirname, 'node_modules/antd/node_modules/@ant-design/cssinjs'),
    };

    // 开发环境优化
    if (dev && !isServer) {
      config.watchOptions = {
        poll: 1000,
        aggregateTimeout: 300,
      };
    }
    
    return config;
  },
};

export default nextConfig;
