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

### 1.1.1 登录流程与路由分组（T5/T6 复查后补，纠正 T1 的结构）

T1 把标签栏放在 `app/m/layout.tsx`、把**鉴权守卫与 provider 放在
`app/m/workspace/layout.tsx`**。这个切分是错的：`/m/agents`、`/m/settings` 是
标签栏的根屏幕，却不在这层守卫之下 —— 一旦做出来就是**未登录可访问**。
而 `app/m/layout.tsx` 又刻意不含守卫，于是「未登录 → 跳登录页」这条规则
只覆盖了 `/m/workspace/**`。

**正确的切分是按「是否已登录」分组，不是按「是否在 workspace 下」分组。**

```
app/m/
├── layout.tsx              100dvh 外壳 + viewport（不含守卫、不含标签栏）
├── (auth)/                 未登录分支 —— 全屏、无标签栏
│   ├── layout.tsx
│   ├── login/  setup/  auth/callback/
└── (app)/                  已登录分支 —— 守卫 + provider + 标签栏
    ├── layout.tsx          ← 守卫（照抄 app/workspace/layout.tsx 的五个分支）
    │                         + QueryClient / AuthProvider / Toaster / MobileTabBar
    ├── workspace/
    │   ├── page.tsx                会话列表
    │   └── chats/[thread_id]/
    │       ├── page.tsx
    │       └── artifacts/[path]/page.tsx
    ├── agents/page.tsx             智能体（P1，先占位）
    └── settings/page.tsx           设置（P1，先占位）
```

路由组 `(auth)` / `(app)` **不进入 URL**，公开地址一个都不变：

| 公开 URL | 移动端渲染 | 桌面端 |
|---|---|---|
| `/login`、`/setup`、`/auth/callback` | `(auth)/*` | 已有 |
| `/workspace` | `(app)/workspace/page.tsx` | 已有 |
| `/workspace/chats/{id}` | `(app)/workspace/chats/[thread_id]/` | 已有 |
| `/workspace/chats/{id}/artifacts/{p}` | `(app)/.../artifacts/[path]/` | 已有 |
| `/agents` | `(app)/agents/page.tsx` | **无此路由**（桌面是 `/workspace/agents`） |
| `/settings` | `(app)/settings/page.tsx` | **无此路由**（桌面设置是弹窗） |

守卫行为完全对齐桌面 `app/workspace/layout.tsx`：

| `getServerSideUser()` | 动作 |
|---|---|
| `authenticated` | 渲染（provider + 标签栏） |
| `unauthenticated` | `redirect("/login")` |
| `needs_setup` / `system_setup_required` | `redirect("/setup")` |
| `gateway_unavailable` | `GatewayOfflineFallback` |
| `config_error` | `throw` |

重定向目标一律写**公开路径**（`/login`、`/setup`、`/workspace`），由 middleware
把下一个请求落回移动端树 —— 这是 §1.2.1「地址栏不出现 `/m/`」的同一个机制。

**标签栏归属随之改变**：从 `app/m/layout.tsx` 移进 `(app)/layout.tsx`。
`shouldHideMobileTabBar()` 及其单测因此失去用途 —— 未登录分支根本不渲染标签栏，
「隐藏」由路由分组在结构上表达，比按 pathname 前缀判断更可靠，故一并删除。

> **已知不对称**：`/agents`、`/settings` 是**只在移动端存在**的公开路径。
> 桌面 UA 直接访问会 404（桌面智能体是 `/workspace/agents`，设置是弹窗）。
> 这是「同一个地址按设备分流」在移动端独有屏幕上的必然结果，接受。
> 若日后要求桌面也能开这两个地址，需另开任务把桌面入口也迁过去。

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

### T7 · 登录流程收敛与路由分组重构

T5/T6 复查时发现 T1 的切分有误（见 §1.1.1），此任务纠正它。

| 项 | 内容 |
|---|---|
| 重构 | `app/m/` 拆成 `(auth)/` 与 `(app)/` 两个路由组；`workspace/` 移入 `(app)/` |
| 新增 | `app/m/(app)/layout.tsx` — 守卫 + provider + `MobileTabBar` |
| 新增 | `app/m/(app)/{agents,settings}/page.tsx` — P1 屏幕，**先给一个空白页**（不再是 404，也不再漏出未鉴权内容）。空白页只渲染标题占位，不假装功能可用的空状态 |
| 修改 | `app/m/layout.tsx` — 去掉 `MobileTabBar`，只留 100dvh 外壳 + viewport |
| 删除 | `shouldHideMobileTabBar()` 及其单测 —— 未登录分支结构上就不渲染标签栏 |

**验收**：
- 未登录访问 `/workspace`、`/agents`、`/settings` 一律 `redirect("/login")`，
  且地址栏是 `/login`（不含 `/m/`）—— 用真浏览器实测，不看代码推断
- 已登录时三个标签都能打开，地址栏分别为 `/workspace`、`/agents`、`/settings`
- 桌面端不受影响：桌面 E2E 失败集合不扩大
- `(auth)/*` 仍然无守卫（否则登录页自己也进不去）

---

### T9 · 移动端智能体（P1，从空白页变成真页面）

> 用户 2026-09-12：**空白页是需要实现的，不是最终交付结果。** `/agents` 目前是
> 空白页，本节把它做出来。设置页账号区同理由此实现（见 §6 进度）。

范围取自 `FEATURE_LIST.md` §1.3，与桌面一一对应，只换形态。

| 项 | 桌面现状 | 移动端 |
|---|---|---|
| 列表 | 1/2/3/4 列卡片网格（`agent-gallery.tsx`） | **单列**，其余复用 `useAgents()` + `AgentCard` |
| 新建 | 二选一弹窗 → 表单 Sheet 或对话式引导 | 选择弹窗改为底部 Sheet；表单改**全屏页** |
| 编辑 | 右侧抽屉（SOUL.md / 描述 / 模型） | **全屏页**，复用 `useUpdateAgent` 与三态只读语义 |
| 与智能体对话 | `/workspace/agents/[name]/chats/[id]` | 同路径，middleware 重写；复用 `useChatPage()` 的 agent 变体 |

**新增**：
`app/m/(app)/agents/page.tsx`（列表）、`agents/new/page.tsx`（表单创建）、
`agents/[agent_name]/edit/page.tsx`（编辑）、
`components/workspace/mobile/agents/*`（卡片、创建选项 Sheet、表单）

**要点**：
- 标签栏 href 仍是公开路径 `/agents` —— **不要**改成 `/workspace/agents`。
  `isTabActive` 按前缀判活跃，「对话」标签的 href 是 `/workspace`，
  两者会同时点亮（`/m/workspace/agents` 同时以 `/m/workspace` 开头）。
  这是 §1.1.1 里 `/agents` 作为移动端独有公开路径的原因之一。
- 智能体**对话**仍在 `/m/workspace/agents/[name]/chats/[id]`（公开路径
  `/workspace/agents/...`，与桌面同址），与列表的 `/agents` 是两棵树。
- 表单里的 SOUL.md 是长文本 → 全屏页 + 等宽字体，不要塞进 Sheet。
- 复用 `isValidAgentName` / `checkAgentName` / `MODEL_DEFAULT_VALUE` 哨兵，
  校验逻辑一律不重写。

**验收**：单列卡片列表渲染；空态与加载态正确；表单创建成功后列表刷新；
编辑页三态（null=继承全部 / []=无 / 列表=白名单）只读语义正确；
点卡片进入该智能体的对话页且地址栏无 `/m/`；桌面 E2E 失败集合不扩大。

---

### T10 · 流式与滚动的 E2E（补 G8）

现有 `/runs/stream` mock 把回复一次性塞进单个 `values` 事件，所以「流式进行中」
的四个状态全是零覆盖。

| 项 | 内容 |
|---|---|
| 新增 | `tests/e2e/mobile-streaming.spec.ts` |
| 新增 | spec 内的回环 SSE 服务（`http.createServer`），`page.route(..., r => r.continue({url}))` 指过去 |

**必须断言**：流式期间停止按钮出现且可点；回复**逐字增长**（分多次 chunk，中途采样
断言长度递增）；离底时「回到底部」浮标出现、点击后滚回底部且浮标消失；点停止后
输入区回到可用态（不是卡在「停止生成」）。

**禁止**：用挂住 `page.route()` 的方式伪造流进行中 —— 已实测会得到假失败
（rejection 传不到 SDK）。见 `TEST_FLOW.md`「流式测试的技术前提」。

---

### T11 · 修复「发送失败丢掉已输入内容」（G7，先红后绿）✅ 已完成

| 项 | 内容 |
|---|---|
| 先写 | `tests/e2e/mobile-compose-failure.spec.ts` — `/runs/stream` 500 → 断言输入框仍保留用户输入（先红） |
| 再修 | `chats/use-chat-page.ts` — 暂存已提交文本 + `thread.error` 出现时写回；两条路径都返回 promise |

**根因（初稿写错，已勘误）**：不是「返回值 promise/undefined 不一致」。真正的
原因是 `langgraph-sdk` 的 `StreamManager.start` 是 fire-and-forget
（`manager.js:291` 只把任务入队），所以 `sendMessage` 在 `/runs/stream` 请求发出
**之前**就 resolve了 —— 有附件时也一样。失败的 run 无法 reject 该 promise，
`prompt-input.tsx` 的错误分支对 run 失败**不可达**。详见 `TEST_FLOW.md` G7 详解。

**已达成**：失败后文字会被交还给输入框；上传失败路径也一致了；
全量 E2E 111 passed（基线 110，+1 为本任务新增）。

**未达成（诚实记录）**：错误提示**仍要 23 秒**才出现（SDK 自己重试 5 次，页面层
够不着），且输入框是「先清空、23 秒后再填回」而非从头到尾不丢。要彻底解决需改
`ai-elements/prompt-input.tsx`（禁止）或引入 per-run started 信号（当前没有，
`onStart` 每 thread 只触发一次），代价是第 2 条消息起输入框不再清空 —— 已判定
该回归更糟，因此保留现状。**若后续要根治，需先解决 started 信号。**

---

### T12 · 附件上传 E2E

| 项 | 内容 |
|---|---|
| 新增 | `tests/e2e/mobile-attachments.spec.ts` |

测「选中文件之后的链路」：选择后进入待发列表、可移除、发送时随请求带出、
上传失败有提示。**不测系统选择器本身**（`capture` 属性另行断言即可）。

---

### T8 · 测试与验收

| 项 | 内容 |
|---|---|
| 新增 | `tests/e2e/mobile-*.spec.ts` — 用 `devices['iPhone 13']` 预设 |
| 新增 | `tests/unit/lib/device.test.ts` 等纯函数单测 |
| 回归 | 桌面 E2E 失败集合不扩大（失败集合恒为 §3.1 的 9 个，总数随移动端 spec 增长） |

单测命令是 `pnpm test`（Rstest），**不是** `pnpm exec vitest run` —— 见 §3.1 的说明。

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

移动端 spec 落地后总数增长（T3 后 76/9，T5 后 87/9），**失败集合始终是下面这 9 个**。

> **测试命令**：单测用 **`pnpm test`**（Rstest）。`pnpm exec vitest run` 在本仓库
> 是错的 —— 用例按 Rstest API 编写，vitest 下 86 个文件里 80 个直接报
> `Rstest API 'describe' is not registered yet`；`package.json` 里 vitest 已降级为
> 陈旧的 `test:legacy`。
>
> **E2E 不要跑在 `pnpm dev` 上**：Turbopack 按需编译会让首个 spec 大面积超时
> （实测 5/11 超时、整轮 12.3 分钟），同一份代码在生产构建下 32 秒通过。
> 本地验证用 `pnpm build && pnpm start`，或接受首次运行的预热代价。

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
| T7 | 登录流程收敛 + 路由分组重构 | 0.5 天 |
| T8 | 测试与验收 | 1.5 天 |
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
- [x] T1 middleware 分流 + `/m/*` 骨架
- [x] T2 `useChatPage()` 抽取
- [x] T3 移动端登录
- [x] T4 会话列表
- [x] T5 对话页
- [x] T6 产物全屏页（含入口接线 —— 原计划只写了全屏页，实测发现移动端两个产物入口都点了没反应）
- [x] T7 登录流程收敛 + 路由分组重构（`(auth)` / `(app)`；补 `/agents`、`/settings` 空白页）
- [x] 设置页账号区 S1（改密 + 退出登录）—— 原为空白页，用户要求实现
- [x] T8 标签栏/根屏幕 E2E（部分 —— F8 已完成，E2/E4 未交付）
- [x] T11 修复发送失败丢输入（根因与初稿不同，已在文档勘误；**未彻底**，见 G7）
- [x] T10 流式与滚动 E2E（回环 SSE 服务）
- [x] T14 修复「回到底部」在冷启动会话中不可用（T10 顺带发现）
- [x] T12 附件上传 E2E（顺带发现 G10：相册/拍照丢文件）
- [x] T15 修复 G10：相册/拍照静默丢弃选中的文件
- [ ] T9 移动端智能体（`/agents` 从空白页变成真页面）
- [ ] T13 会话内容页移动端重设计（C6 折叠步骤 / C10 横向 chip / 表格与代码块）
- [ ] 测试与验收
