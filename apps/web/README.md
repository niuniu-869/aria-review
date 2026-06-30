# apps/web

Vite + React + TypeScript 前端 (D5: 纯视图层, 后端唯一为 Python agent)。Phase 0 实现切片1: 上传→概览。

## 关键点
- **类型单一真源**: `src/api/schema.d.ts` 由 `packages/contracts/openapi.yaml` 生成 (`pnpm gen:api`, Codex-10)。手不改。
- 服务端态走 **TanStack Query** (`src/api/hooks.ts`); 客户端只 fetch (`src/api/client.ts`)。
- 错误经 `ApiError` 携带契约 code。

## 开发
```bash
pnpm install
pnpm gen:api          # 从契约生成类型
cp .env.example .env  # 可选：覆盖 VITE_API_BASE
pnpm dev
```

开发模式默认直连 `http://localhost:8000`。生产构建默认使用同源 `/api`，Docker
镜像里的 nginx 会把 `/api/*` 反代到 Compose 内的 `agent:8000`。

## 校验/测试
```bash
pnpm gen:api && pnpm typecheck   # tsc 严格类型检查
pnpm build                       # tsc + vite 产物
pnpm test                        # vitest: client 单测 (mock fetch)
```
组件级 E2E 由 Playwright 在切片 E2E 覆盖 (设计 §7), 不在本骨架。

## 待办
- 流式 agent 对话 UI (切片2, SSE EventSource)。
- 其余 7 个分析页。
- 组件库 (shadcn/ui 或 Mantine, 设计 §6)。
