# DeerFlow 移动端 — 开发计划

> 依据：`FEATURE_LIST.md`（功能清单）+ `mobile-prototype.html`（界面原型）
> 范围：核心优先。对话 / 产物 / 智能体 / 设置；管理后台不做。

## 1. 技术方案（要点）

### 1.1 形态：现有前端响应式改造

同一套代码与路由，按断点切换布局。**`core/**` 业务逻辑与 API 零改动** ——
这正是选它而非另起工程的理由，也是本计划最重要的约束：任何"顺手重构"
都不得越过 `core/`。

### 1.2 断点

复用现有 `hooks/use-mobile.ts`（768px），但**加一个服务端安全的入口**：

```ts
// 现状：useIsMobile 首帧返回 false（useState 初值 undefined → !!undefined）
// 后果：移动端首帧按桌面渲染，再闪一下切到移动布局
export function useIsMobile()            // 保留，sidebar 在用
export function useDeviceClass(): "mobile" | "desktop" | "pending"  // 新增
```

`pending` 态用于**避免首帧闪烁**：在 unmount 前渲染中性骨架，而不是先渲染
桌面布局再切。这对对话页尤其重要（分栏闪一下再变全屏）。

服务端场景（SSR）沿用现有 `useIsMobile` 的 `useEffect` 惰性判定，不引入
`window` 访问，保持现有行为不回归。

### 1.3 样式落点

- **安全区**：`env(safe-area-inset-bottom)` 用于底部标签栏、输入区、产物操作条
- **高度**：`h-screen` → `100dvh`（地址栏收缩时不再跳动）
- **触控目标**：≥44px；**输入框字号 ≥16px**（否则 iOS 自动放大）
- 新增 CSS 变量集中在 `src/styles/globals.css`，不散落

### 1.4 关键决策

| # | 决策 | 取值 | 理由 |
|---|---|---|---|
| D1 | 导航范式 | 底部标签栏 | 按原型；手机原生范式，智能体/设置一键直达 |
| D2 | 产物形态 | 全屏页（图片走灯箱） | 拖拽分栏在窄屏不可用；全屏利于看 HTML 报告 |
| D3 | 悬停交互 | 长按菜单 / 常驻操作条 | 触屏无 hover，`opacity-0 group-hover:` 等于不可见 |
| D4 | 设置降级 | 模型/技能只读，MCP 移除 | 配置一次长期不动，手机上徒增误操作风险 |

> D1/D2 是原型里留给你定夺的两项，本计划按原型取值。若改为「侧边抽屉」
> 或「底部抽屉」，只影响 P3/P4 的导航与产物两处，其余阶段不变。

## 2. 阶段划分

每阶段独立可交付、可回归。**先做 P1（对话）**，因为它是唯一的高频路径，
也是改造风险最集中的地方。

---

### P0 · 基础设施（0.5 天）

| 文件 | 改动 |
|---|---|
| `src/hooks/use-mobile.ts` | 新增 `useDeviceClass`，含 `pending` 态 |
| `src/styles/globals.css` | 安全区变量、`100dvh` 工具类 |
| `src/app/layout.tsx` | `viewport` 导出：`viewport-fit=cover`、`maximum-scale=1` |

**验收**：`useDeviceClass` 在 SSR / 首帧 / resize 三种情况下都不闪烁；
桌面端渲染结果与本阶段前逐像素一致。

---

### P1 · 对话主界面（2 天，最高频）

| 文件 | 改动 |
|---|---|
| `src/app/workspace/chats/[thread_id]/page.tsx:200-225` | 头部 6 个图标 → 移动端只留标题 + 「⋯」溢出菜单 |
| `src/components/workspace/chats/chat-box.tsx:111` | `ResizablePanelGroup` 增加移动端分支（见 P2） |
| `src/components/workspace/input-box.tsx:993` | `PromptInputFooter` 移动端改两行：主行（＋/输入/发送）+ 「＋」面板 |
| `src/components/workspace/messages/message-list.tsx:466` | `opacity-0 group-hover:` → 移动端常驻 |
| `src/components/workspace/messages/message-list-item.tsx:173` | 同上 |
| 新增 `src/components/workspace/mobile/composer-sheet.tsx` | 「＋」面板：相册/拍照/文件/思考 + 模式/图片/视频/推理强度/计划模式 |
| 新增 `src/components/workspace/mobile/header-overflow.tsx` | 会话状态/Token/额度/导出/产物的溢出菜单 |

**注意**：`autoFocus` 在移动端默认关闭 —— 否则一进页面键盘弹起遮半屏。

**验收**：
- 390px 下输入区不换行溢出；「＋」面板可打开全部 5 项且选择生效
- 消息复制/重新生成在触屏可点（不依赖悬停）
- 桌面端头部按钮数量与位置不变
- 长文本消息横向不溢出；流式回复时「回到底部」浮标正常

---

### P2 · 产物全屏页（1.5 天）

| 文件 | 改动 |
|---|---|
| `src/components/workspace/chats/chat-box.tsx:111-183` | 移动端不走 `ResizablePanelGroup`，改为全屏路由/覆盖层 |
| `src/components/workspace/artifacts/artifact-file-detail.tsx` | 移动端容器：顶部返回 + 文件 tabs + 底部操作条 |
| 新增 `src/components/workspace/mobile/artifact-actions.tsx` | 分享（Web Share API，兜底下载）/下载/刷新/全屏 |

**按类型分形态**（原型的 ⑤）：
- HTML → 全屏 iframe（复用现有沙箱渲染与滚动回传）
- 图片 → 灯箱 + 双指缩放
- 代码 → 横向滚动 + 自动换行开关
- 其他 → 仅下载

**验收**：三种产物类型在 390px 下均可看可下载；桌面端分栏行为不变；
`getArtifactUrl` 等 `core/` 逻辑零改动。

---

### P3 · 导航重构（1 天）

| 文件 | 改动 |
|---|---|
| 新增 `src/components/workspace/mobile/tab-bar.tsx` | 对话 / 智能体 / 设置 |
| `src/app/workspace/layout.tsx` | 移动端渲染 tab bar；桌面端不变 |
| 新增 `src/app/workspace/mobile-chats/page.tsx` | 会话列表根屏幕（时间分组 + 搜索 + 长按菜单） |

**长按菜单**：复用现有 `recent-chat-list.tsx` 的置顶/重命名/删除动作，
只换触发方式（长按 → 底部 ActionSheet）。删除确认沿用 `AlertDialog`。

**验收**：三个标签可切换且高亮正确；长按出菜单，三项动作生效；
桌面端侧边栏完全不变。

---

### P4 · 智能体 + 设置（1.5 天）

| 文件 | 改动 |
|---|---|
| `src/components/workspace/agents/agent-gallery.tsx` | 网格 → 单列卡片（`grid-cols-1 sm:2 lg:3 xl:4` 已具备，主要是间距/触控） |
| `src/components/workspace/agents/agent-create-sheet.tsx` / `agent-edit-sheet.tsx` | Sheet → 移动端全屏页 |
| `src/components/workspace/settings/settings-dialog.tsx` | Dialog → 移动端全屏页 + 返回 |
| `model-settings-page.tsx` / `skill-settings-page.tsx` | 移动端只读（隐藏增删改，保留列表与状态） |
| `settings-dialog.tsx` 导航项 | 移动端隐藏「工具（MCP）」入口 |

**验收**：设置全屏可滚动、可返回；模型/技能无编辑入口；桌面端仍可增删改。

---

### P5 · 登录与收尾（0.5 天）

| 文件 | 改动 |
|---|---|
| `src/app/(auth)/login/page.tsx` | 邮箱 `type=email`、密码 `autoComplete`、输入框 ≥16px |
| `(auth)/setup`、`auth/callback` | 触控目标与字号对齐 |
| 全局 | 会话过期路径：不出现空白页 |

---

### P6 · 测试与验收（1.5 天）

见第 3 节。

## 3. 测试策略

UI 响应式改造不适合纯 TDD，按「能抽成纯函数的一律单测，交互走 E2E」拆：

**单元测试**（`tests/unit/`）
- `useDeviceClass`：SSR / 首帧 / resize / 卸载
- 「＋」面板与溢出菜单的状态机（抽成纯函数，如 `nextPanelState()`）
- 断点相关的工具函数（安全区、字号判定的常量）

**E2E**（`tests/e2e/`）—— 现有 17 个 spec，新增 `mobile-*.spec.ts`，
使用 Playwright 的 `devices['iPhone 13']` 预设：
- 390px 下完成一次「新建会话 → 发消息 → 收到回复」
- 打开「＋」面板选择模式，确认选择被带进请求
- 打开产物全屏页并下载
- 标签栏三向切换
- 澄清卡片：作答后输入框解锁，且刷新后仍显示已作答

**回归**：现有 17 个 E2E 全部在**桌面视口**下跑通，是本计划每个阶段的硬门槛。
响应式改造最大的风险是桌面回归，E2E 是唯一能兜住的网。

**真机验收**（不写进 CI）：iOS Safari + 安卓 Chrome 各一台，重点看
键盘弹起时输入区是否被遮、安全区是否正确、地址栏收缩时不跳动。

## 4. 风险

| 风险 | 影响 | 应对 |
|---|---|---|
| **桌面端回归** | 高 | 每阶段跑完整 17 个桌面 E2E；改动限定在 `md:` 断点以下 |
| 首帧布局闪烁 | 中 | P0 的 `useDeviceClass` 三态设计；对话页尤其要验 |
| iOS 键盘遮挡输入区 | 中 | 真机验收；必要时用 `visualViewport` 监听 |
| 触屏长按与文本选择冲突 | 中 | 长按阈值 ≥500ms，且仅在非文本区域生效 |
| 改动侵入 `core/` | 中 | 明确禁止；code review 时检查 diff 只落在 `components/` 与 `hooks/` |

## 5. 工作量

| 阶段 | 内容 | 估时 |
|---|---|---|
| P0 | 基础设施 | 0.5 天 |
| P1 | 对话主界面 | 2 天 |
| P2 | 产物全屏页 | 1.5 天 |
| P3 | 导航重构 | 1 天 |
| P4 | 智能体 + 设置 | 1.5 天 |
| P5 | 登录与收尾 | 0.5 天 |
| P6 | 测试与验收 | 1.5 天 |
| | **合计** | **8.5 天** |

按单人全职估算，不含真机验收往返。P1 与 P2 强相关（都动 `chat-box`），
建议同一人连续做。

## 6. 明确不做

- 管理后台四个标签页（用量 / 额度 / 会话追踪 / 用户管理）
- MCP 工具配置、技能安装、模型增删改、渠道绑定
- `/[lang]/docs` 与博客的移动端优化（保持可用即可）
- PWA / 离线 / 推送到桌面的能力（本阶段不涉及）
- 平板专属布局（768–1024px 沿用桌面）

## 7. 下一步

1. ~~功能清单~~ ✅
2. ~~界面原型~~ ✅
3. ~~技术方案 + 开发计划~~ ✅ 本文档
4. 待确认后开工，按 P0 → P6 顺序推进
