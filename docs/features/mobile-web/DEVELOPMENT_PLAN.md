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

> 2026-09-13 变更：输入区底部改为**常驻模式/模型 pill + 点击切换**（用户决定，
> 原型 ②③ 已同步更新）。当前实现把模式/模型放在「＋」面板里，由 T16 调整到位。

| 项 | 内容 |
|---|---|
| 新增 | `src/app/m/workspace/chats/[thread_id]/page.tsx` |
| 新增 | `src/components/workspace/mobile/chat-header.tsx` — 标题 + 「⋯」溢出菜单 |
| 新增 | `src/components/workspace/mobile/composer.tsx` — 输入区主行（含模式/模型 pill） |
| 新增 | `src/components/workspace/mobile/composer-sheet.tsx` — 「＋」面板 |
| 复用 | `MessageList`、`HumanInputCard`、`useChatPage()` |

**要点**：
- 输入区主行：「＋ / 输入 / 发送」+ 底部常驻**模式 pill 与模型 pill**，点击直接切换
  （模式弹出模式子菜单；模型弹出模型选择器；长模型名截断不破版）
- 「＋」面板保留：相册/拍照/文件/思考 + 图片生成模型/视频生成模型/推理强度/计划模式
  （模式与模型行移出面板）
- 移动端 `autoFocus` 默认关闭（否则键盘弹起遮半屏）
- 消息操作条常驻（触屏无 hover，`opacity-0 group-hover:` 等于不可见）

**验收**：390px 下输入区不溢出；模式/模型 pill 显示当前值、切换后请求携带对应
`mode` / `model_name`；「＋」面板各项可选且生效；
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

### T13 · 会话内容页移动端重设计

用户 2026-09-12 指出：「会话内容页面、查看产物文档等是需要根据移动端重新设计」。

现状属实：**移动端对话页只把桌面转录原样缩小**，内容渲染层零改动 ——
`MessageList` → `MessageGroup` → `MessageListItem` 全是桌面组件，移动端只加了
`chat-surface.css` 里的几条覆盖（常驻操作条）。产物页是按类型重新设计过的，
会话内容页不是。

范围取自 `FEATURE_LIST.md` C6 / C10，外加实测发现的溢出问题：

| # | 问题 | 现状 | 目标 | 最小手段 |
|---|---|---|---|---|
| C6 | 工具调用过程是密集内联面板 | `ChainOfThought` 步骤全展开 | 折叠为「执行了 N 步」摘要，点开看细节 | 需要 `MessageGroup` 收口，**纯 CSS 做不到** |
| C10 | 追问建议 chip 不换行不横滑 | `Suggestion(s)` 平铺 | 横向可滑动 | 多半可纯 CSS（`overflow-x-auto` + 不换行） |
| — | 长表格 / 代码块横向溢出 | 无横向滚动容器 | 横向滚动 + 换行开关（照搬产物页代码视图已有的做法） | `message-list-item.tsx` 的 markdown 容器类名 |

**硬约束与风险**：

- C6/C10 落在 `components/workspace/messages/**`，**是与桌面共享的组件**。
  §1.3 规定非 mobile 目录不改动。因此每条先判断能否用「移动端作用域内的 CSS /
  外面包一层」解决：
  - 能 → 走 CSS（沿用 T5 在 `chat-surface.css` 用 `.mobile-chat-surface` 作用域的
    既有做法），桌面零改动；
  - 不能（C6 大概率属于此类）→ 走**加法式可选 prop**（沿用 T6 给
    `ArtifactsProvider` 加可选回调的先例），默认值保证桌面逐字节不变，
    并在报告里显式声明这是 §1.3 的第几个例外。
- 若某条既不能用 CSS、加 prop 又会显著改变桌面结构，**停下来报告**，不要硬做。
- `chat.spec.ts:182`（长 markdown 横向溢出）是既有红灯，属本条邻近问题 ——
  可以修，但**不允许靠改这个测试来变绿**。

**验收**：
- 390px 下长表格/代码块**不产生整页横向滚动**，且内容可横向查看
- 工具步骤折叠为摘要、可展开，步数正确
- chip 可横滑，不撑破布局
- 桌面 E2E 失败集合不扩大（基线见 §3.1），桌面视觉无回归
- 真机测量：`document.documentElement.scrollWidth === window.innerWidth`

---

### T16 · 输入区底部常驻模式/模型切换（2026-09-13 新增）

用户 2026-09-13 决定：**chat 页面底部显示当前模式及模型，并可以切换**。
设计依据：原型 ② 输入区主行「⚡ 思考 ▾ / ⚙ GLM-5.3 Flash ▾」两个 pill；
「＋」面板不再承担模式/模型入口（③ 的「模式」行已删除）。

现状（T5 交付）与目标的差距：模型选择（chat/vision 双 tab）与模式列表都在
`composer-sheet.tsx` 的「＋」面板里（`:319-456` 模型行、`:458-518` 模式列表），
输入区主行只有 ＋/输入/发送（`composer.tsx:420-446`）。

| 项 | 内容 |
|---|---|
| 修改 | `src/components/workspace/mobile/composer.tsx` — 主行 footer 增加「模式 pill + 模型 pill」，显示当前值；点击分别弹出模式子菜单 / 模型选择器（复用 composer-sheet 已有的选择 UI 与状态写入 `setSettings("context", …)`，不新造逻辑） |
| 修改 | `src/components/workspace/mobile/composer-sheet.tsx` — 移除面板中的「模式」行与「模型」行，保留四宫格（相册/拍照/文件/思考快捷开关）与图片/视频模型、推理强度、计划模式 |
| i18n | pill 的 aria-label 与长模型名截断提示；zh/en/types 三处同步 |
| 超出设计 | `FEATURE_LIST.md` C3 原写「模式/模型收进＋面板」——本文档与本原型按用户决定取代之，C3 不回改 |

**要点**：
- pill 是只读展示 + 点击入口，**不在主行内联编辑**；切换交互全部走底部 Sheet
  （与「＋」面板、长按菜单同一交互范式），避免主行弹键盘/焦点问题
- pill 触控目标 ≥44px（视觉 34px 高、热区扩大），长模型名 `text-overflow: ellipsis`
- 「思考」四宫格快捷开关与模式 pill 写同一份 mode 状态（既有行为，保持）
- 推理强度/计划模式行继续受既有条件显隐/派生逻辑约束，不因迁移而改变

**验收**：
- 390px 下主行「＋ / 思考pill / 模型pill / 发送」不溢出，360px 亦不破版
- 点模式 pill 切换后，下一轮请求携带对应 `mode`；点模型 pill 同理带 `model_name`
- 「＋」面板不再出现模式/模型行，其余项功能不变
- E2E：F5 中「选模型/切模式」用例改为经 pill 入口断言，载荷断言不变
- 桌面 E2E 失败集合不扩大

---

### §1.3 例外登记表

§1.3 规定「非 mobile 目录不改动」。**每条例外都必须在此登记**，并说明为什么
CSS 或外层包裹做不到、以及桌面如何证明未变。

| # | 文件 | 改法 | 为什么不能只在 mobile 目录解决 | 桌面不变如何证明 |
|---|---|---|---|---|
| 1 | `components/workspace/artifacts/context.tsx` | `ArtifactsProvider` 加可选 `onOpenChange` 回调（T6） | 产物面板由 provider 自己开合，移动端没有面板可开，只有一条路由要跳 | 回调可选，桌面不传 → 行为逐字节不变 |
| 2 | `components/workspace/messages/message-group.tsx`、`message-list.tsx` | 加可选 `collapsedSteps` prop（T13） | 步骤是 `MessageGroup` 自己排的，外层包不住；纯 CSS 改不了「折叠为摘要」这个结构 | 默认 `false`，桌面不传 → 走原分支；`mobile-transcript.spec.ts` 的 desktop 用例断言的正是「桌面仍是密集面板、无折叠摘要」 |
| 3 | `components/workspace/messages/thinking-message.tsx`（新增）、`message-list.tsx`、`message-list-item.tsx` | 从三个调用点给 `ReasoningTrigger` 传 locale 感知的 `getThinkingMessage`（T19） | 文案硬编码在 `ai-elements/reasoning.tsx` 的 `defaultGetThinkingMessage` 里，而 `ai-elements/**` 是 registry 生成、禁止手改（registry 更新会冲掉）；`ReasoningTrigger` 的调用点全在共享组件里，mobile 目录没有任何一层能包住这段文案 | 只新增 1 个 prop + 1 个 hook 调用；`hasContent`、类名、DOM 结构、其余 props 一字未动；registry 自带 6 个默认行为用例仍全绿 |

> **例外 #3 的一处桌面可见副效应（知情后接受）**：`t.toolCalls.thinking` 的英文是
> `Thinking…`（U+2026 单字符省略号），registry 默认是 `Thinking...`（三个 ASCII 点）。
> 既然文案改为走 locale 单一来源，en-US 的桌面用户就会看到这个字形变化。
> 其余英文分支逐字节相同（`Thought for 12s` / `Thought for a few seconds`）。
> 这不是 bug，但属于「为移动端做的改动在桌面可见」，故记在此处而非默默带过。

---

### T17 · 收口工程偏差：标签栏改结构性隐藏 + 例外登记

**背景**：T7 定下「标签栏的显隐由路由分组在结构上表达，不按 pathname 判断」，
但 `shouldHideMobileTabBar()`（`tab-bar.tsx:73-83`）没删，产物页改用它按正则隐藏
标签栏 —— 计划文本与代码不一致，且该正则是**需随路由手动同步的隐式契约**
：新增一条全屏路由忘了改正则，标签栏就会压在产品操作条上。

| 项 | 内容 |
|---|---|
| 重构 | `app/m/(app)/` 下再分两组：`(tabbed)/{workspace,agents,settings}` 与 `(fullbleed)/`；标签栏只挂在 `(tabbed)/layout.tsx` |
| 删除 | `shouldHideMobileTabBar()` 及其单测（`tab-bar.test.ts` 对应 describe） |
| 修改 | `tab-bar.tsx` — 只留 `isTabActive()` 与渲染；不再读 pathname 决定「渲不渲染」 |
| 文档 | 在 §1.3 补「例外登记表」（见上），把 T6/T13 两条已发生的例外补登记 |

**验收**：
- 标签栏只在三个根屏幕出现 —— 由 E2E 断言，不靠代码推断
- 全仓 `grep -r shouldHideMobileTabBar src/ tests/` 为空
- 新增一条全屏路由时不需要动任何「隐藏规则」（结构上就不在标签栏组里）
- 桌面 E2E 失败集合不扩大

**落定（2026-09-13）**：`(tabbed)` = 会话列表 / `/agents` / `/settings`；
`(fullbleed)` = **对话页 + 产物页**。Next 16 接受这个切分（dev server 自生成的
`validator.ts` 里两组路由与 layout 都在，无「两路由组解析到同一路径」报错）。

> **对话页的归属是产品决定，不是实现细节。** 我最初把对话页放在 `(tabbed)`，
> 理由是「既有行为 + `mobile-tabs.spec.ts` 有断言」。施工时发现**原型 ② 根本没有
> 标签栏** —— `class="tabbar"` 只出现在 ①⑥⑦ 三帧（曾据此请用户定夺，用户选「照原型」）。
> 于是对话页移入 `(fullbleed)`，连带的两个非显然改动：
> 1. **`composer.tsx` 原本刻意不加底部安全区**，注释写明「由下方标签栏承担」
>    （`app/m/layout.tsx` 让两者做兄弟）。移走后 composer 独占底边，
>    改为 `pb-[calc(0.375rem+env(safe-area-inset-bottom))]`；
> 2. `mobile-tabs.spec.ts` 那条「thread 里标签栏仍可用」的用例反转为
>    「thread 是全屏、无标签栏」，并从「点标签离开」改为「点返回离开」——
>    **`/m/` 泄漏断言因此仍然留在原地**，只是换了离开方式。
>
> 教训：原型里「某个元素**不在**」和「某个元素在」一样是需求，逐帧比对时
> 只记录「有什么」会漏掉「没有什么」。

---

### T18 · 会话列表「生成中」标识

**背景**：原型 ① 与 `FEATURE_LIST` §0 的第二个高频场景是「**看一眼进度**」，
但 `thread-row.tsx` 全文没有任何 loading/generating 判断，正在跑的会话
和三天前的会话长得一模一样。

| 项 | 内容 |
|---|---|
| 修改 | `components/workspace/mobile/thread-row.tsx` — 行摘要位显示运行中状态；行背景用 `--accent` 高亮 |
| 修改 | `app/m/(app)/workspace/page.tsx` — 把「哪些会话在跑」传下去 |
| i18n | 「生成中」文案（zh/en/types 三处；若桌面已有等价 key 则复用，不新造） |

**要点**：先查 `useThreads()` / thread 列表响应里是否已有运行中字段
（桌面 `recent-chat-list.tsx` 若有同款判断，**照抄数据来源，不要新拉接口**）。
拿不到就**停下来报告**，不要靠轮询或猜测造一个假状态。

**验收**：一条正在跑的会话在列表里可被肉眼识别（E2E：mock 一条 running 线程，
断言标识出现；一条非 running 线程断言标识不出现）。

---

### T19 · Reasoning 触发文案接 locale

**背景**：原型 ② 是「思考了 12 秒」，实现是硬编码英文 —— 中文用户看到的是
`Thought for 12s`。文案在 `components/ai-elements/reasoning.tsx:185,187`，
而 `ai-elements/` 是 **registry 生成、不得手工编辑**。

| 项 | 内容 |
|---|---|
| 新增 | mobile 目录下的包装组件，把 locale 文案喂给 `Reasoning` 的触发区 |
| 修改 | `components/workspace/messages/*` 的调用点改用包装组件（若可行） |
| i18n | `thoughtFor(duration)` / `thinking` 两处，zh/en/types 三处同步 |

**要点**：不得改 `ai-elements/**`。若该组件的触发文案**没有**可从外部注入的
prop / children 口子，那就**停下来报告**，用「改注册表文件」换文案是不划算的
（下次 registry 更新会被冲掉）。

**验收**：zh-CN 下渲染「思考了 N 秒」，en-US 下渲染 `Thought for Ns`；
桌面视觉不变。

---

### T20 · C11 待办列表（plan mode）

**背景**：`FEATURE_LIST` §1.1 C11 要求「输入框上方，可折叠」。桌面
`app/workspace/chats/[thread_id]/page.tsx:18,145` 渲染了 `TodoList`，
移动端 page 与 mobile 组件里**都没有** —— 计划模式开着却看不到待办。

| 项 | 内容 |
|---|---|
| 修改 | `app/m/(app)/workspace/chats/[thread_id]/page.tsx` — 复用桌面 `TodoList`，位置照 `FEATURE_LIST`（输入框上方） |
| 修改 | 必要时加移动端作用域 CSS（折叠态、44px 触控目标） |

**要点**：`TodoList` 是共享组件 → 优先只用 CSS / 外层包裹；确需改它时按 §1.3
走加法式可选 prop 并**登记到例外表**。

**设计已补图（2026-09-13）**：原型新增 **⑨ 待办列表 · 展开** 与
**⑩ 待办列表 · 折叠（默认）**，形态照搬 `todo-list.tsx` 的真实渲染
（只有上沿两角圆角、无下边框、下压 4px 塞进输入区底下；列表区固定 112px 内部滚动；
**默认折叠**）。补充这条是因为原型原先只画到 ③，待办面板没有出图 ——
而它是 C11 的需求，没有图就没有验收基准。

> **连带发现一处 i18n 缺口**：`todo-list.tsx` 的标题是硬编码英文 `To-dos`，
> 不读 locale，与 T19 修的 `Thought for 12s` 属同一类。原型按中文「待办」出图，
> 实现要补齐 —— 这会是 §1.3 的又一条例外（改共享组件的文案来源），
> 待 T20 回报后统一编号登记。

**验收**：mock 一个带 todos 的线程，待办列表出现在输入框上方、可折叠、
项数正确；无 todos 时不渲染任何空壳。

---

### T21 · A5 分享 + 产物操作条补齐

| 项 | 内容 |
|---|---|
| 修改 | `components/workspace/mobile/artifact-actions.tsx` — 首键「分享」 |
| 实现 | `navigator.share` 优先（`canShare({files})` 判定）；不支持则**兜底为下载**，不允许出现点了没反应的按钮 |
| 修改 | 产物页 — 补「刷新」 |
| i18n | `share` 文案（`common.share` 已有，先复用）；刷新文案三处同步 |

**明确不做**：「全屏」（原型 ⑤ 的第四键）—— 手机浏览器地址栏会退不掉，
收益为负；在计划里写清是**有意不做**，不是漏做。

**验收**：E2E 断言分享按钮存在且触发 `navigator.share`（注入 stub）或
回落到 `?download=true`；刷新按钮重新拉取产物内容。

---

### T22 · 设置页其余分区（S2–S9，G5）

现状只有账号区 S1。原型 ⑦ 的分区列表：个人（账号/外观/通知/记忆）、
连接（渠道）、模型与能力（模型只读/技能只读）、其他（关于）。

| 项 | 内容 |
|---|---|
| 修改 | `app/m/(app)/settings/page.tsx` — 由「只有一块」改为分组列表（分组标题 + chevron 行） |
| 新增 | `components/workspace/mobile/settings/*` — 各分区全屏子页 |
| 复用 | 外观→既有主题/语言状态；通知→既有通知权限逻辑；记忆→`core/memory/**`；渠道→`core/channels/**` 只读态；模型/技能→只读列表 + 只读徽章；关于→版本号 |
| 明确不做 | S7 MCP 工具入口（按 §1.4 移除） |

**要点**：S5/S8 是**只读**（看已配置列表与启用状态，增删留桌面）；
只读徽章要在行上可见，不做「点进去发现不能改」。

**验收**：七行都可点开且不空转；只读区明确标注只读；
`/settings` 地址栏不含 `/m/`；深色模式下分区页正常（E8 的一部分）。

---

### T23 · T9 移动端智能体页

原样保留原 T9 描述（见上），此处仅记状态：`/agents` 目前是 `MobileBlankScreen`，
**整屏缺失**，是本轮最大的单块未实现功能。

---

### T24 · 测试收口与执行环境

**每次会话都要重新踩的坑，此节一次说清，后续任务照此执行。**

#### 执行环境（本机）

`:3000` 长期被用户自己的开发栈占用（实测为 `next start`，`BUILD_ID` 早于进程
启动），而 `playwright.config.ts` 的 `baseURL` 是 3000 且
`reuseExistingServer: !process.env.CI` —— 于是 `pnpm test:e2e` 会**静默接管**
那台服务器。它没带 `DEER_FLOW_AUTH_DISABLED=1`，所有 spec 开局就被弹到
`/login`，表现为「大面积莫名失败」。

因此**不要在主工作区跑 E2E**：`pnpm build` 会重写 `.next/**`，正在运行的
生产服务是按需从磁盘读 chunk 的，会被换掉。

隔离副本做法（已验证可用，首次约 1–2 分钟，之后增量构建）：

```bash
# 一次性
rsync -a --exclude node_modules --exclude .next --exclude test-results \
  frontend/ /tmp/df-e2e/
cp -al frontend/node_modules /tmp/df-e2e/node_modules
# 改完代码后同步 + 跑
rsync -a --exclude node_modules --exclude .next --exclude test-results \
  frontend/ /tmp/df-e2e/
cd /tmp/df-e2e                       # playwright.config.ts 已改为 3100 + 不复用
pnpm build && pnpm exec playwright test tests/e2e/mobile-*.spec.ts
```

#### 本轮必须补的用例

| # | 项 | 手段 |
|---|---|---|
| E2 | 全部触控目标 ≥44×44px | 目前只断言了产物页；扩到列表/对话/设置/智能体 |
| E1 | 360px 窄屏不出现横向滚动 | `setViewportSize({360, 780})` + `scrollWidth === innerWidth` |
| E4 | 无限滚动长列表 | 不重复请求、不跳位（桌面已有，移动端缺） |
| E5 | 会话过期（N5） | mock 401 → 断言提示或重登，**不卡空白页** |
| E7 | 请求中途返回 | 已发出消息不丢 |
| E8 | 深色模式 | 三根屏幕 + 对话页 + 产物页 |

#### 回归门槛

桌面 E2E 失败集合恒为 §3.1 的 9 个；移动端 spec 全绿。**失败集合扩大即回滚**。

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
| | *—— 以上为第一轮（已完成）——* | |
| T9/T23 | 移动端智能体页（整屏） | 1.5 天 |
| T13 | 会话内容页重设计 | 1 天 |
| T16 | 输入区常驻模式/模型 pill | 0.5 天 |
| T17 | 收口工程偏差 + 例外登记 | 0.5 天 |
| T18 | 会话列表「生成中」标识 | 0.5 天 |
| T19 | Reasoning 文案接 locale | 0.5 天 |
| T20 | C11 待办列表 | 0.5 天 |
| T21 | A5 分享 + 操作条刷新 | 0.5 天 |
| T22 | 设置页 S2–S9 | 1 天 |
| T24 | 测试收口（隔离环境 + 6 项缺口） | 1.5 天 |
| | **第二轮合计** | **8.5 天** |

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
- [x] **T13 会话内容页移动端重设计**（`bda47946`）——`collapsedSteps` 可选 prop 折叠
      步骤、`useFollowups` 横滑 chip、长 markdown 不横溢；已登记为 §1.3 例外 #2
- [x] **T16 输入区常驻模式/模型 pill**（`bda47946`）——实机走查通过（REVIEW §5.1）；
      390/360px 复测无横溢、切模式与切模型都进请求载荷；「＋」面板已无这两行
- [x] **文件入口改到标题栏 + 产物页文件名切换器**（`d6177297`）——原型 ⑤ 同步为
      居中对话框；真机复测 12 项全通过
- [x] **T17 收口工程偏差** —— `(tabbed)` / `(fullbleed)` 路由组切分，
      `shouldHideMobileTabBar()` 与其单测已删；对话页经产品决定移入 `(fullbleed)`
      （原型 ② 无标签栏），`composer.tsx` 补回自己的底部安全区。真浏览器实测：
      五个根/子路由 `navs=1 tabLinks=3`，产物页 `navs=0 actionbars=1`
- [x] **T19 Reasoning 文案接 locale** —— 复用 `ReasoningTrigger` 既有的
      `getThinkingMessage` 注入口，**未碰 registry 文件**；新增
      `thinking-message.tsx`（纯格式化 + 带秒级跳动），三个调用点接同一个 hook。
      用反证证明流式跳动未退化（摘掉 interval 后测试立刻变红）。已登记为 §1.3 例外 #3
- [ ] **T18 会话列表「生成中」标识** —— 进行中
- [ ] **T20 C11 待办列表** —— 未做
- [ ] **T22 设置页 S2–S9** —— 未做
- [ ] **T23 移动端智能体页（T9）** —— 进行中，本轮最大单块
- [ ] **T24 测试收口**（隔离 E2E 环境 + E1/E2/E4/E5/E7/E8）—— 进行中（基线）
- [ ] **T25 评审缺陷修复 · messages 折叠簇** —— 进行中（含 Critical #1）
- [ ] **T26 评审缺陷修复 · 产物页簇** —— 进行中（已并入原 T21 的分享/刷新）

> **未执行的测试**：`mobile-chat.spec.ts`、`mobile-transcript.spec.ts`、
> `mobile-artifacts.spec.ts` 三份 spec 的改动**都还没有在本机跑过**（原因见 T24）。
> 它们当前是「已写好但未验证」状态，不是绿灯。

### 执行顺序（2026-09-13 编排）

**实际编排（2026-09-13，5 并发）**：

| 波次 | 并行任务 | 为什么可以并行 |
|---|---|---|
| 1 | T17 ∥ T19 ∥ T24a ∥ 评审 | T19 碰 `messages/**`、与 `app/m/**` 不相交；T24a 只在 `/tmp/df-e2e` 用 `git archive HEAD` 的快照干活（**刻意不 rsync 工作区**，否则会抓到 T17 移动到一半的路由树）；评审只读 |
| 2 | T23 ∥ T18 ∥ T26 | T17 落地后三者的文件集互不相交：`agents/**`、`thread-row + 列表页`、`artifact-actions + 产物页` |

**关键使能动作：i18n 先集中铺好。** 三份 locale 文件（`types.ts` / `zh-CN.ts` /
`en-US.ts`）几乎每个任务都要追加 key，是**唯一**的跨任务写冲突点。
按原计划「同波次只允许一个任务改 locale」会把并行度压到 1。改为**开工前一次性把
key 全铺好**（提交 `1dee7c70`），后续任务只消费不写入，冲突面直接消失。

配套硬约束：

1. 子代理**不得**改 `frontend/src/core/i18n/locales/**`。缺 key 就在报告里说明缺哪个，
   由主代理统一补 —— 否则并行写同一文件必然互相覆盖。
2. 并发任务的文件集必须不相交。派发前先列出每个任务要写的文件并与在跑任务比对。
3. **结构性改动先落单跑**：T17 重构了 `app/m/(app)/**`，而这正是 T18/T20/T22/T23
   要新建文件的地方，所以它必须先单独跑完。
4. 子代理一律**不得跑 `pnpm build`**（会弄坏用户 `:3000` 上正在跑的生产服务）；
   需要构建的验证统一放到 `/tmp/df-e2e` 隔离副本里。
