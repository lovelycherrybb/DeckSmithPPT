#!/bin/bash

echo "=== DeckSmithPPT 主题切换测试 ==="
echo ""
echo "已改造的文件："
echo "1. packages/frontend/src/app/globals.css"
echo "2. packages/frontend/src/components/HomePageContent.jsx"
echo "3. packages/frontend/src/components/StudioLayout.tsx"
echo "4. packages/frontend/src/components/tabs/WorkspaceTab.tsx"
echo "5. packages/frontend/src/components/tabs/HistoryTab.tsx"
echo "6. packages/frontend/src/components/workspace/CreateWizard.tsx"
echo ""
echo "主题切换功能："
echo "- 首页：右上角太阳/月亮图标"
echo "- 工作台：右上角太阳/月亮图标"
echo "- 历史项目：右上角太阳/月亮图标"
echo ""
echo "启动开发服务器..."
echo ""

cd "<项目目录>/packages/frontend"
npm run dev
