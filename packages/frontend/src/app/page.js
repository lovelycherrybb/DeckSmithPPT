'use client';

import Link from 'next/link';
import { Button, Layout, Space } from 'antd';
import { ArrowRight, BookOpen, Layers, Zap } from 'lucide-react';

const { Header, Content, Footer } = Layout;

export default function HomePage() {
  return (
    <Layout className="home-layout">
      {/* Header */}
      <Header className="home-header">
        <div className="home-header-inner">
          <Link href="/" className="home-logo">
            DeckSmith
          </Link>
          <nav className="home-nav">
            <a href="#features">功能</a>
            <a href="#process">流程</a>
            <a href="#" onClick={(e) => { e.preventDefault(); alert('文档建设中'); }}>文档</a>
          </nav>
          <Space>
            <Link href="/studio">
              <Button>登录</Button>
            </Link>
            <Link href="/studio">
              <Button type="primary">开始使用</Button>
            </Link>
          </Space>
        </div>
      </Header>

      <Content>
        {/* Hero */}
        <section className="home-hero">
          <div className="home-hero-badge">
            <Zap size={14} />
            AI 驱动的 PPT 生成工具
          </div>
          <h1>
            把材料变成<span>可编辑的 PPT 草稿</span>
          </h1>
          <p>
            上传材料，选择风格，30秒拿到第一份草稿。先编辑，再确认，告别一次生成就定稿的压力。
          </p>
          <Space size="middle">
            <Link href="/studio">
              <Button type="primary" size="large" icon={<ArrowRight />}>
                开始创作
              </Button>
            </Link>
            <a href="#features">
              <Button size="large">了解更多</Button>
            </a>
          </Space>
        </section>

        {/* Features */}
        <section className="home-features" id="features">
          <div className="home-section-header">
            <h2>为什么选择 DeckSmithPPT</h2>
            <p>不是另一个模板工具，而是你的 AI 设计助手</p>
          </div>
          <div className="home-features-grid">
            <div className="home-feature-card">
              <div className="home-feature-icon">
                <Layers size={24} />
              </div>
              <div className="home-feature-number">01</div>
              <h3>风格统一</h3>
              <p>视觉风格和内容风格分开选择，整体更一致。</p>
            </div>
            <div className="home-feature-card">
              <div className="home-feature-icon">
                <BookOpen size={24} />
              </div>
              <div className="home-feature-number">02</div>
              <h3>表达更准</h3>
              <p>材料先整理成适合宣传和展示的结构，让每句话都更有力量。</p>
            </div>
            <div className="home-feature-card">
              <div className="home-feature-icon">
                <Zap size={24} />
              </div>
              <div className="home-feature-number">03</div>
              <h3>草稿可改</h3>
              <p>先编辑，再确认，减少一次生成就定稿的压力。</p>
            </div>
          </div>
        </section>

        {/* Process */}
        <section className="home-process" id="process">
          <div className="home-section-header">
            <h2>简单三步，专业PPT</h2>
            <p>无需设计经验，无需学习复杂工具</p>
          </div>
          <div className="home-process-steps">
            <div className="home-process-step">
              <div className="home-step-number">1</div>
              <h3>上传材料</h3>
              <p>支持文件上传或直接粘贴文本</p>
            </div>
            <div className="home-process-step">
              <div className="home-step-number">2</div>
              <h3>选择风格</h3>
              <p>挑选视觉风格和内容风格</p>
            </div>
            <div className="home-process-step">
              <div className="home-step-number">3</div>
              <h3>编辑确认</h3>
              <p>查看草稿，满意后生成最终PPT</p>
            </div>
          </div>
        </section>

        {/* CTA */}
        <section className="home-cta">
          <h2>准备好开始了吗？</h2>
          <p>现在开始，30秒后拿到你的第一份草稿</p>
          <Link href="/studio">
            <Button type="primary" size="large">
              免费开始创作
            </Button>
          </Link>
        </section>
      </Content>

      <Footer className="home-footer">
        <div className="home-footer-inner">
          <div className="home-footer-logo">DeckSmith</div>
          <div className="home-footer-links">
            <a href="#" onClick={(e) => { e.preventDefault(); alert('功能开发中'); }}>关于我们</a>
            <a href="#" onClick={(e) => { e.preventDefault(); alert('功能开发中'); }}>使用条款</a>
            <a href="#" onClick={(e) => { e.preventDefault(); alert('功能开发中'); }}>隐私政策</a>
            <a href="#" onClick={(e) => { e.preventDefault(); alert('功能开发中'); }}>帮助中心</a>
          </div>
          <div className="home-footer-copyright">
            © 2026 DeckSmithPPT. All rights reserved.
          </div>
        </div>
      </Footer>
    </Layout>
  );
}
