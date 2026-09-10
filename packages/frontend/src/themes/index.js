// 主题配置 - 每个主题都有独特的布局、动画、交互方式
export const THEMES = {
  magnetic: {
    id: 'magnetic',
    name: '磁性流体',
    description: '深色背景 + 流体动画 + 磁性光标跟随',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'masonry',
      processLayout: 'timeline',
    },
    animation: {
      entrance: 'magnetic',
      hover: 'magnetic',
      scroll: 'parallax',
      cursor: 'magnetic',
    },
    colors: {
      bg: '#0a0a0a',
      bgGlow: '#111827',
      text: '#f3f4f6',
      textSub: '#9ca3af',
      accent: '#8b5cf6',
      accentGlow: 'rgba(139,92,246,0.3)',
      border: '#1f2937',
      cardBg: 'rgba(17,24,39,0.8)',
    },
    fonts: {
      heading: 'Inter, sans-serif',
      body: 'Inter, sans-serif',
      mono: 'JetBrains Mono, monospace',
    },
    effects: {
      blur: 20,
      glow: true,
      particles: true,
      gradient: 'radial-gradient(circle at 50% 50%, rgba(139,92,246,0.15) 0%, transparent 50%)',
    },
  },

  geometric: {
    id: 'geometric',
    name: '几何解构',
    description: '白色背景 + 几何图形解构重组',
    layout: {
      heroAlign: 'left',
      heroDirection: 'row',
      featuresLayout: 'grid',
      processLayout: 'steps',
    },
    animation: {
      entrance: 'geometric',
      hover: 'deconstruct',
      scroll: 'sequential',
      cursor: 'geometric',
    },
    colors: {
      bg: '#ffffff',
      bgGlow: '#f9fafb',
      text: '#111827',
      textSub: '#6b7280',
      accent: '#ef4444',
      accentGlow: 'rgba(239,68,68,0.1)',
      border: '#e5e7eb',
      cardBg: '#ffffff',
    },
    fonts: {
      heading: 'Space Grotesk, sans-serif',
      body: 'Inter, sans-serif',
      mono: 'Space Mono, monospace',
    },
    effects: {
      blur: 0,
      glow: false,
      particles: false,
      gradient: 'none',
    },
  },

  typewriter: {
    id: 'typewriter',
    name: '复古打字机',
    description: '米色背景 + 打字机效果 + 纸张质感',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'cards',
      processLayout: 'scroll',
    },
    animation: {
      entrance: 'typewriter',
      hover: 'stamp',
      scroll: 'reveal',
      cursor: 'typewriter',
    },
    colors: {
      bg: '#fef3c7',
      bgGlow: '#fde68a',
      text: '#1c1917',
      textSub: '#78716c',
      accent: '#92400e',
      accentGlow: 'rgba(146,64,14,0.1)',
      border: '#d6d3d1',
      cardBg: '#fffbeb',
    },
    fonts: {
      heading: 'Courier Prime, monospace',
      body: 'Lora, serif',
      mono: 'Courier Prime, monospace',
    },
    effects: {
      blur: 0,
      glow: false,
      particles: false,
      gradient: 'none',
    },
  },

  cyberpunk: {
    id: 'cyberpunk',
    name: '赛博朋克',
    description: '黑色背景 + 霓虹灯效果 + 故障艺术',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'glitch',
      processLayout: 'neon',
    },
    animation: {
      entrance: 'glitch',
      hover: 'neon',
      scroll: 'scanline',
      cursor: 'neon',
    },
    colors: {
      bg: '#000000',
      bgGlow: '#0a0a0a',
      text: '#00ff00',
      textSub: '#00cc00',
      accent: '#ff00ff',
      accentGlow: 'rgba(255,0,255,0.3)',
      border: '#00ff00',
      cardBg: 'rgba(0,0,0,0.8)',
    },
    fonts: {
      heading: 'Share Tech Mono, monospace',
      body: 'Share Tech Mono, monospace',
      mono: 'Share Tech Mono, monospace',
    },
    effects: {
      blur: 0,
      glow: true,
      particles: true,
      gradient: 'linear-gradient(180deg, #000000 0%, #0a0a0a 100%)',
    },
  },

  minimalist: {
    id: 'minimalist',
    name: '极简主义',
    description: '极简设计 + 大量留白 + 黑白配色',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'grid',
      processLayout: 'steps',
    },
    animation: {
      entrance: 'fade',
      hover: 'subtle',
      scroll: 'smooth',
      cursor: 'default',
    },
    colors: {
      bg: '#ffffff',
      bgGlow: '#fafafa',
      text: '#111111',
      textSub: '#666666',
      accent: '#000000',
      accentGlow: 'rgba(0,0,0,0.1)',
      border: '#e0e0e0',
      cardBg: '#ffffff',
    },
    fonts: {
      heading: 'Helvetica Neue, sans-serif',
      body: 'Helvetica Neue, sans-serif',
      mono: 'SF Mono, monospace',
    },
    effects: {
      blur: 0,
      glow: false,
      particles: false,
      gradient: 'none',
    },
  },

  organic: {
    id: 'organic',
    name: '自然有机',
    description: '有机形状 + 自然色调 + 柔和曲线',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'organic',
      processLayout: 'flow',
    },
    animation: {
      entrance: 'organic',
      hover: 'breathe',
      scroll: 'flow',
      cursor: 'organic',
    },
    colors: {
      bg: '#f5f0e8',
      bgGlow: '#ede5d8',
      text: '#3d3425',
      textSub: '#8a7e6e',
      accent: '#7c9a5e',
      accentGlow: 'rgba(124,154,94,0.2)',
      border: '#d4c9b8',
      cardBg: '#faf7f2',
    },
    fonts: {
      heading: 'Playfair Display, serif',
      body: 'Source Sans 3, sans-serif',
      mono: 'IBM Plex Mono, monospace',
    },
    effects: {
      blur: 0,
      glow: false,
      particles: false,
      gradient: 'radial-gradient(circle at 30% 50%, rgba(124,154,94,0.1) 0%, transparent 50%)',
    },
  },

  futuristic: {
    id: 'futuristic',
    name: '未来科技',
    description: '科技感 + 渐变 + 玻璃态',
    layout: {
      heroAlign: 'center',
      heroDirection: 'column',
      featuresLayout: 'glass',
      processLayout: 'neon',
    },
    animation: {
      entrance: 'slide',
      hover: 'glow',
      scroll: 'parallax',
      cursor: 'glow',
    },
    colors: {
      bg: '#0c0c1d',
      bgGlow: '#141432',
      text: '#e0e0ff',
      textSub: '#a0a0cc',
      accent: '#00d4ff',
      accentGlow: 'rgba(0,212,255,0.3)',
      border: 'rgba(0,212,255,0.2)',
      cardBg: 'rgba(20,20,50,0.8)',
    },
    fonts: {
      heading: 'Orbitron, sans-serif',
      body: 'Exo 2, sans-serif',
      mono: 'JetBrains Mono, monospace',
    },
    effects: {
      blur: 20,
      glow: true,
      particles: true,
      gradient: 'linear-gradient(135deg, #0c0c1d 0%, #141432 50%, #1a1a4a 100%)',
    },
  },
};

export const getTheme = (themeId) => THEMES[themeId] || THEMES.magnetic;
export const getThemeIds = () => Object.keys(THEMES);
export const getThemeList = () => Object.values(THEMES);
