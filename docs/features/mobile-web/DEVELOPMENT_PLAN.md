# DeerFlow 移动端 — 开发计划

> 依据：`FEATURE_LIST.md`（功能清单）+ `mobile-prototype.html`（界面原型）
> 范围：登录 → 查看会话 → 输入 → 查看产物。管理后台不做。

## 1. 技术方案

### 1.1 形态：`/m/*` 独立路由 + middleware 分流

**一个访问地址，按设备自动显示不同页面。** 移动端与 PC 端的**渲染树与路由代码分开**，
互不影响，也不把两套布局塞进同一个文件。

```
浏览器请求 /workspace/chats/abc
        │
   middleware（读 User-Agent）
        ├── 桌面 UA → /workspace/chats/abc        （现有 PC 页面，一行不动）
        └── 移动 UA → /m/workspace/chats/abc      （新增移动端页面）
```

**为什么 middle 可用（已核实）**：真实 Web 部署走 `next start`（Windows 部署脚本
`install.ps1:33-39, 629-640` 刻意清除了 `NEXT_CONFIG_BUILD_OUTPUT` 与
`NEXT_PUBLIC_STATIC_WEBSITE_ONLY`），是 Node 服务，middleware 生效。

**静态演示站 / Electron 不受影响**：它们构建时不设移动端入口，且 Electron 的 UA 是
桌面，本就不会进 `/m/*`。移动端不在其支持范围内（已与用户确认）。

> 曾考虑「同路由内运行时判断」，因首帧会闪一下桌面布局而被否决；
> middleware 在服务端决定，无闪烁。

### 1.2 三层拆分：什么共享、什么分开

这是本计划的核心约束。上一版计划的错误在于把移动端逻辑塞进同一个
`chat-box.tsx` / `page.tsx`，让同一文件承载两套布局。

| 层 | 处理 | 说明 |
|---|---|---|
| **路由 / 布局** | **分开** | `app/workspace/...` vs `app/m/workspace/...` |
| **行为 / 状态** | **抽成 hook 共享** | 新增 `useChatPage()` |
| **展示原子** | **共享** | `MessageList`、`HumanInputCard`、产物预览、`AgentCard` |
| **`core/**`** | **共享，零改动** | API、类型、i18n、threads/messages 逻辑 |

#### 为什么必须抽 `useChatPage()`

现有 `app/workspace/chats/[thread_id]/page.tsx`（350 行）**不只是渲染**，
它挂着 15+ 个 hooks 与副作用：

```
useThreadStream / useHumanInput / useThreadMetadata / useThreadTokenUsage
useSpecificChatMode / useThreadSettings / useLocalSettings / useNotification
useModels / useI18n / useRouter ...
```

若移动端复制一份，就是第 **三、四** 份这段最复杂的逻辑。现有代码已有前车之鉴 ——
两个对话页近乎重复（350 / 385 行），各自写着互相提醒的注释：

```
// NOTE: Keep feature parity with src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx
// NOTE: Keep feature parity with src/app/workspace/chats/[thread_id]/page.tsx
```

抽成 `useChatPage()` 后，两边 page 都只剩「取 hook 结果 → 摆布局」。

### 1.2.1 客户端跳转与 middleware 的真实关系（T2 实测澄清）

初稿这里写错了一次，记录正确结论以免后续任务照错的做。

**实测**（dev server，带 `RSC: 1` 头，即 `router.replace` 实际发出的请求）：

```
移动 UA + RSC 头  →  x-middleware-rewrite: /m/workspace/chats/new
桌面 UA + RSC 头  →  200，无 rewrite
```

结论分两类，**不要混为一谈**：

| 动作 | 是否发请求 | 是否经 middleware | 结果 |
|---|---|---|---|
| `<Link>` / `router.push` / `router.replace` | ✅ 发 RSC 请求 | ✅ **经过** | 自动落到正确的树，**无需** `basePath` |
| `history.replaceState` | ❌ 不发请求 | — | 只改地址栏，**写什么就是什么** |

**因此真正的约束是：**

> `history.replaceState` 写入的必须是**公开 URL**（`/workspace/...`），
> 绝不能写 `/m/...` —— 那会把内部前缀泄漏到地址栏，
> 破坏「同一个访问地址」的设计，也让链接变成设备相关。

即：**地址栏里永远不该出现 `/m/`**。`useChatPage()` 的 `basePath`
在两个树上都取默认值 `/workspace`，无论哪个树都不需要传 `/m/workspace`。

`basePath` 选项保留为逃生舱口（附带单测），但当前 T5 **不应使用它**。

### 1.3 桌面端隔离策略

**PC 端 JSX 一行不动**（已与用户确认）。唯一例外是 T2：把内联的 hooks 调用
替换为 `useChatPage()`。这是**纯搬移、行为不变**，由现有 17 个桌面 E2E 兜底。

改动 diff 的硬性检查：除 T2 外，`app/workspace/**` 与
`components/workspace/**`（非 mobile 目录）**不应出现改动**。

### 1.4 断点与设备基线

| 项 | 取值 |
|---|---|
| 基准宽度 | **390px**（iPhone 14/15） |
| 最小支持 | 360px，不出现横向滚动 |
| 触控目标 | ≥44×44px |
| 输入框字号 | **≥16px**（iOS 上 <16px 会触发自动放大） |
| 平板 | 768–1024px 沿用桌面，不单独设计 |

### 1.5 样式与兼容要点

- 安全区：`env(safe-area-inset-bottom)` 用于标签栏、输入区、产物操作条
- 高度：`h-screen` → `100dvh`（地址栏收缩时不跳动）
- `viewport` 导出：`viewport-fit=cover`
- **不引入 `useIsMobile` 做分流**：分流由 middleware 负责；`useIsMobile`
  仅在移动端壳内做次要适配时使用

## 2. 任务分解

每个任务独立可交付。**串行执行**（子代理逐任务派发），因为多数任务最终都要
碰 `middleware.ts` 或 `/m/` 布局。

---

### T1 · 基础设施：middleware 分流 + `/m/*` 骨架

| 项 | 内容 |
|---|---|
| 新增 | `src/middleware.ts` — UA 判定 + rewrite |
| 新增 | `src/app/m/layout.tsx` — 移动端根布局 |
| 新增 | `src/components/workspace/mobile/tab-bar.tsx` — 底部标签栏 |
| 新增 | `src/lib/device.ts` — UA 判定纯函数（可单测） |

**middleware 必须排除**：`/_next`、`/api`、`/mock`、含扩展名的静态资源、
已带 `/m/` 前缀的路径。**i18n 交互需实测**（`next.config.js` 配了
`i18n: { locales: ["en","zh"] }`，matcher 写法受影响）。

**验收**：
- `curl -A "<iPhone UA>" /workspace` → 落在 `/m/workspace`（URL 不变）
- `curl -A "<Chrome UA>" /workspace` → 原桌面页面，字节级无变化
- 已带 `/m/` 前缀不重复重写；`/_next/static/*` 与 `/api/*` 不被拦截
- `src/lib/device.ts` 的 UA 判定有单测

---

### T2 · 行为层抽取：`useChatPage()`

| 项 | 内容 |
|---|---|
| 新增 | `src/components/workspace/chats/use-chat-page.ts` |
| 修改 | `app/workspace/chats/[thread_id]/page.tsx` — 内联 hooks → 调用 hook |

**这是唯一触碰桌面端的任务，且必须是纯搬移**：不改行为、不改 JSX 结构。

**验收**：
- 桌面 E2E 失败集合不扩大（基线见 §3.1）——硬门槛
- `pnpm typecheck` / `pnpm lint` 干净
- diff 中不出现 JSX 结构变化

---

### T3 · 移动端登录

| 项 | 内容 |
|---|---|
| 新增 | `src/app/m/(auth)/login/page.tsx` |
| 新增 | `src/app/m/(auth)/setup/page.tsx` |
| 新增 | `src/app/m/(auth)/auth/callback/page.tsx` |

复用 `core/auth/**` 与现有表单校验逻辑；只重写布局与触控细节。

**验收**：邮箱 `type=email`；输入框 ≥16px；SSO 按钮可达；待审批态可渲染；
登录成功后**地址栏为 `/workspace`（不含 `/m/`）**，且服务端 rewrite 命中
`/m/workspace`。（原写「跳 `/m/workspace`」是 §1.2.1 澄清前的笔误，已纠正。）

---

### T4 · 移动端会话列表

| 项 | 内容 |
|---|---|
| 新增 | `src/app/m/workspace/page.tsx` — 会话列表根屏幕 |
| 新增 | `src/components/workspace/mobile/thread-row.tsx` — 行 + 长按菜单 |

复用 `core/threads/hooks.ts` 的分页/搜索；复用 `recent-chat-list.tsx` 的
置顶/重命名/删除**动作**，只换触发方式（长按 → ActionSheet）。

**验收**：时间分组正确；搜索可用；长按出菜单且三项动作生效；
新建会话入口可达。

---

### T5 · 移动端对话页

| 项 | 内容 |
|---|---|
| 新增 | `src/app/m/workspace/chats/[thread_id]/page.tsx` |
| 新增 | `src/components/workspace/mobile/chat-header.tsx` — 标题 + 「⋯」溢出菜单 |
| 新增 | `src/components/workspace/mobile/composer.tsx` — 输入区主行 |
| 新增 | `src/components/workspace/mobile/composer-sheet.tsx` — 「＋」面板 |
| 复用 | `MessageList`、`HumanInputCard`、`useChatPage()` |

**要点**：
- 输入区主行只留「＋ / 输入 / 发送」，其余 5 项控件进「＋」面板
- 移动端 `autoFocus` 默认关闭（否则键盘弹起遮半屏）
- 消息操作条常驻（触屏无 hover，`opacity-0 group-hover:` 等于不可见）

**验收**：390px 下输入区不溢出；「＋」面板 5 项均可选且生效；
澄清卡片可作答且作答后输入区解锁；流式回复与「回到底部」正常。

---

### T6 · 移动端产物全屏页

| 项 | 内容 |
|---|---|
| 新增 | `src/app/m/workspace/chats/[thread_id]/artifacts/[path]/page.tsx` |
| 新增 | `src/components/workspace/mobile/artifact-actions.tsx` — 底部操作条 |
| 复用 | `artifact-file-detail.tsx` 的沙箱 iframe / 图片 / 代码渲染 |

**按类型分形态**（原型的 ⑤）：HTML → 全屏 iframe；图片 → 灯箱 + 双指缩放；
代码 → 横向滚动 + 自动换行开关；其他 → 仅下载。

**验收**：三种类型在 390px 下均可看可下载；桌面端分栏行为不变。

---

### T7 · 测试与验收

| 项 | 内容 |
|---|---|
| 新增 | `tests/e2e/mobile-*.spec.ts` — 用 `devices['iPhone 13']` 预设 |
| 新增 | `tests/unit/lib/device.test.ts` 等纯函数单测 |
| 回归 | 桌面 E2E 失败集合不扩大（基线 58 passed / 9 failed，见 §3.1） |

移动端 E2E 沿用现有做法（`page.route()` 拦截后端），**不依赖真实服务**：

- 登录 → 进会话列表
- 新建会话 → 发消息 → 收到回复
- 「＋」面板选择模式，确认带进请求
- 打开产物并下载
- 澄清卡片作答后输入区解锁
- 桌面 UA 与移动 UA 落在不同页面

## 3. 风险

### 3.1 E2E 基线（2026-09-12 实测，开工前）

当前分支 `feat/mobile-web` 起点的 E2E 实测为 **58 passed / 9 failed**。
**这 9 个是既有失败，不是移动端改动造成的**，已用「收起全部改动再跑」独立验证。

| 类别 | 数量 | 说明 |
|---|---|---|
| 真实回归 | **3** | `artifact-stream-state:57`、`chat.spec.ts:182`、`subtask-card:43` —— 需要有人修，但与本计划无关 |
| 陈旧测试 | **6** | `landing.spec.ts` ×2 期待的营销落地页已不存在（`app/page.tsx` 现在是重定向门）；`model-settings.spec.ts` ×4 需要真实部署，本地没有 |

**因此验收标准修正为**：不得引入**新的**失败，即保持 `≥58 passed` 且失败集合不扩大。
「17 个 spec 全过」这一说法不成立（实际 17 个 spec 文件、共 67 个用例）。

## 3.2 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| **middleware 误伤桌面端** | 高 | T1 验收含「桌面 UA 字节级无变化」；排除清单逐项测 |
| **i18n 与 middleware matcher 冲突** | 中 | T1 实测；必要时在 matcher 中显式放行语言前缀 |
| **`useChatPage()` 抽取引入回归** | 高 | T2 后立即跑桌面 E2E，失败集合不得超出 §3.1 基线 |
| 移动端复制行为逻辑 | 中 | 硬约束：移动端 page 不得直接调 `core/threads` 的流式 hook，只走 `useChatPage()` |
| 改动侵入 `core/**` | 中 | code review 检查 diff 只落 `app/m/**`、`components/workspace/mobile/**`、`lib/` |
| iOS 键盘遮挡输入区 | 中 | 真机验收；必要时监听 `visualViewport` |

## 4. 工作量

| 任务 | 内容 | 估时 |
|---|---|---|
| T1 | middleware + `/m/*` 骨架 | 1 天 |
| T2 | `useChatPage()` 抽取 | 1 天 |
| T3 | 移动端登录 | 0.5 天 |
| T4 | 会话列表 | 1 天 |
| T5 | 对话页 | 1.5 天 |
| T6 | 产物全屏页 | 1 天 |
| T7 | 测试与验收 | 1.5 天 |
| | **合计** | **7.5 天** |

## 5. 明确不做

- 管理后台四个标签页（用量 / 额度 / 会话追踪 / 用户管理）
- MCP 工具配置、技能安装、模型增删改、渠道绑定
- `/[lang]/docs` 与博客的移动端优化
- PWA / 离线 / 推送
- 平板专属布局
- **静态演示站与 Electron 的移动端支持**（UA 为桌面，天然不进 `/m/*`）

## 6. 进度

- [x] 功能清单 `FEATURE_LIST.md`
- [x] 界面原型 `mobile-prototype.html`
- [x] 技术方案 + 开发计划 ✅ 本文档
- [ ] T1 → T2 → T3 → T4 → T5 → T6 → T7
