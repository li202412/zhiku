# 知库 前端

React 19 + TypeScript + Vite + Tailwind 构建的「知库」前端，包含登录/注册、知识问答（SSE 流式）、知识库管理、历史会话、个人信息与设置。

## 常用命令

```bash
npm install        # 安装依赖
npm run dev        # 开发模式，http://localhost:3000（接口代理到 127.0.0.1:8000）
npm run build      # tsc 类型检查 + 生产构建（产物在 dist/）
npm run lint       # ESLint
npm run preview    # 预览构建产物
```

后端地址可用环境变量覆盖：`VITE_BACKEND_TARGET=http://127.0.0.1:8011 npm run dev`。

## 目录

```
src/
├── pages/          # AIChat 知识问答 / KnowledgeBase 知识库 / Sessions 历史会话
│                   # Login / Register / Profile / Settings / AboutUs
├── components/     # layout(Sidebar) / knowledge(DocumentDetailDrawer) / common
├── hooks/useSSE.ts # SSE 解析：fetch + ReadableStream，事件 thinking/response/done/error
├── api/            # client(axios+拦截器) / endpoints / auth / knowledge / sessions
├── stores/         # zustand：用户、会话、主题、语言
├── i18n/           # zh-CN / en-US
└── router/         # react-router 6 路由表
```

## 说明

- 登录后 token 存本地存储，请求由 axios 拦截器注入 `Authorization`。
- 问答为 SSE 流式：`POST /chat/agent/query/stream`，逐帧渲染「思考步骤 + 回答正文 + 参考依据」。
- 生产镜像由 `Dockerfile` + `nginx.conf` 构建，`/chat`、`/knowledge`、`/user` 等路径反代到 backend 容器。
