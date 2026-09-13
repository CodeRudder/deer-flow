# Windows 服务器部署 DeerFlow 实施计划

> **执行方式:** 按 Phase 顺序推进，每 Task 完成后提交。Task 0 是**前置验证**——若失败，整个方案需回退到 nginx 方案，必须在写脚本前完成。

**目标:** 在一台 Windows 服务器上以原生进程方式部署 DeerFlow，内网其他 Windows 机器用浏览器访问 `http://<服务器IP>:3000`，并提供完整的部署与运维脚本。

**架构:** 复用上游 `scripts/serve.sh` 的服务拓扑，但用 PowerShell 重写编排（Windows 无 `lsof`/`pgrep`/`ss`/`kill -9`）。**不引入 nginx**，浏览器直连 Next.js 的 3000 端口，由 `next.config.js` 的 `/api/:path*` rewrite 在服务端转发到 Gateway。数据落 SQLite，无需 PostgreSQL。

**技术栈:** Windows 10/11 或 Server 2019+、PowerShell 5.1+、Python 3.12+（uv 管理）、Node.js 22+、pnpm 10.26.2、Next.js 16、FastAPI Gateway、SQLite。

---

## 前置条件

- [ ] **P1** 确认分支：`git branch --show-current` 输出 `feat/windows-server-deploy`，基于 `jlc/develop`（`a4c48792`）
- [ ] **P2** 确认目标 Windows 服务器可访问外网（首次需下载依赖），或已准备离线依赖包
- [ ] **P3** 确认服务器已装 Git for Windows（`uv sync` 与部分脚本依赖）、Python 3.12+、Node.js 22+、pnpm

> **注意:** `jlc/develop` 的 fetch 此前失败（`git-w.jlcops.com:22` 连接超时）。若需最新代码，先解决 SSH 访问后 `git fetch jlc` 再执行本计划。

---

## 已核实的关键事实（脚本设计依据）

| 事实 | 依据 |
|---|---|
| 服务拓扑：frontend `3000`、gateway `8001`、nginx `2026` | `scripts/serve.sh:122,408-410` |
| Gateway 默认端口 `8001` | `backend/app/gateway/config.py:10` |
| 前端 `/api/*` rewrite 到 gateway（构建期固化） | `frontend/next.config.js:28-75` |
| **SQLite 是默认存储**，无需 PostgreSQL | `config.example.yaml:1435-1437` 注释明确；`deps.py:215` `backend == "sqlite"` |
| 注册审批开关 `auth.local_registration.require_admin_approval` | `config.example.yaml:1761`、`routers/auth.py:357` |
| 审批邮件是**尽力而为**，失败仅标记可重试，审批可在管理后台手动完成 | `backend/AGENTS.md:258` |
| 首个管理员用 CLI 初始化，凭据写入 `.deer-flow/admin_initial_credentials.txt`（0600） | `backend/app/gateway/auth/reset_admin.py:1-10` |
| 注册受邮箱域名白名单限制 | `config.example.yaml:1752` 已有 `sz-jlc.com` |
| Windows 已有部分支持：Git Bash 包装器 + uvicorn `--loop none` | `scripts/run-with-git-bash.cmd`、`serve.sh:325-330` |
| **Windows 上无任何 PowerShell 部署脚本、无服务注册、无防火墙配置** | `git ls-files` 全仓库仅 1 个 `.cmd` 文件 |

### 必须替换的 POSIX 依赖

`serve.sh` 重度依赖以下命令，**Windows 原生环境全部不存在**，PowerShell 脚本需用等价实现替换：

| POSIX | 用途 | PowerShell 等价 |
|---|---|---|
| `lsof -nP -iTCP:$port -sTCP:LISTEN` | 端口占用检测 | `Get-NetTCPConnection -LocalPort $port -State Listen` |
| `pgrep -f <pattern>` | 按命令行查找进程 | `Get-CimInstance Win32_Process` + `CommandLine -match` |
| `kill -9` / `kill` | 终止进程 | `Stop-Process -Force` / `taskkill /F /T /PID` |
| `ss` / `netstat -ltn` | 端口监听检测 | `Get-NetTCPConnection` |
| `nohup ... &` | 后台常驻 | `Start-Process` + PID 文件 |
| `ps -p $pid -o args=` | 进程命令行 | `Win32_Process.CommandLine` |
| `uname -s` | 平台判断 | `$PSVersionTable` / `$IsWindows` |

---

## 文件结构总览

所有新增脚本放在 `scripts/windows/`（与现有 `scripts/*.sh` 并列，不污染根目录）。

| 文件 | 职责 |
|---|---|
| `scripts/windows/DeerFlow.Common.psm1` | 公共模块：路径解析、端口检测、进程查找、日志、PID 文件、颜色输出 |
| `scripts/windows/check-env.ps1` | 环境预检：Python/uv/Node/pnpm/Git 版本与可用性，输出修复指引 |
| `scripts/windows/init-config.ps1` | 生成 `config.yaml`（sqlite + auth）与 `.env`（随机密钥），幂等 |
| `scripts/windows/install.ps1` | 安装依赖（`uv sync --all-packages`、`pnpm install`）并构建前端 |
| `scripts/windows/start.ps1` | 启动 Gateway + Frontend，写入 PID 文件，等待端口就绪 |
| `scripts/windows/stop.ps1` | 按 PID 文件停止服务，清理遗留进程 |
| `scripts/windows/status.ps1` | 服务状态、端口、PID、运行时长、日志尾部 |
| `scripts/windows/admin-init.ps1` | 初始化首个管理员账号并输出凭据 |
| `scripts/windows/register-service.ps1` | 开放防火墙端口 + 注册开机自启（计划任务） |
| `scripts/windows/unregister-service.ps1` | 移除自启注册与防火墙规则 |
| `scripts/windows/README.md` | 脚本速查（详细文档在 `docs/`） |
| `docs/windows-deployment/operations.md` | 完整部署手册（面向运维） |
| `.gitignore` | 补充 Windows 产物（`logs/`、`.deer-flow/`、`*.pid`） |

**不改动:** `scripts/serve.sh`、`docker/nginx/*`、`frontend/next.config.js`、`backend/**`。本次是**纯新增**部署层，不修改上游逻辑，便于后续 rebase。

---

# Phase 0：前置验证（阻塞性）

### Task 0: 验证无 nginx 直连 3000 的可行性

**为什么必须先做:** 你选择了不引入 nginx。但 nginx 配置里承载了 DeerFlow 的**流式输出硬需求**——`proxy_buffering off`（`docker/nginx/nginx.local.conf:56,84`）、`X-Accel-Buffering no`（:58,86）、`proxy_read_timeout 600s`（:63,91）、`client_max_body_size 100M`（:94）。若 Next.js 自带的 rewrite 代理会缓冲 SSE 或限制上传体积，则本方案不成立，必须回退到 nginx for Windows。

**涉及文件:** 无（仅验证）

**验证步骤**

1. 在本机（macOS 开发环境）用现有拓扑拉起服务：`make dev`（或 `./scripts/serve.sh --dev`），确认 `localhost:2026` 可用。
2. 改走前端直连路径：直接访问 `http://localhost:3000`，确认页面加载、`/api/*` 请求经 rewrite 到达 gateway。
3. **流式验证（关键）**：发起一次对话，用浏览器 DevTools 的 Network 面板观察 `/api/langgraph/*` 响应。确认响应头为 `Transfer-Encoding: chunked` 且**消息逐 token 到达**，而非一次性整包返回。
4. **上传验证（关键）**：上传一个 >50MB 的文件（或接近 100M），确认不被 3000 端口拒绝。Next.js 默认无 body 大小限制，但需实测确认。
5. **长连接验证**：发起一个耗时 >2 分钟的任务，确认连接不被中断。

**验收标准**

- 步骤 3 的响应逐块到达，首字节到末字节时间差与任务时长匹配（非瞬间完成）。
- 步骤 4 的 >50MB 文件上传成功。
- 步骤 5 的 >2 分钟任务正常完成，无连接重置。

**若任一项失败:** 停止本计划，改为"原生进程 + nginx for Windows"方案（nginx Windows 版需处理 `pid`/`logs` 相对路径与 `nginx -s quit` 的信号差异，作为独立 Task 补充）。

**提交:** 无（此 Task 只产出结论，若通过则继续）

---

# Phase 1：PowerShell 部署脚本

### Task 1: 公共模块 DeerFlow.Common.psm1

**背景:** Windows 缺少 `serve.sh` 依赖的全部进程/端口工具（见上表），且多个脚本需要共享同一套路径解析与进程管理逻辑。先把这些能力收敛到一个模块，避免每个脚本重复实现（DRY）。

**涉及文件:** 新建 `scripts/windows/DeerFlow.Common.psm1`

**变更要点**

1. **路径解析**：`Get-DeerFlowRoot`（从脚本位置向上两级定位仓库根）、`Get-DeerFlowLogsDir`、`Get-DeerFlowPidFile`。
2. **端口能力**：`Test-PortListening`（`Get-NetTCPConnection -State Listen`）、`Get-PortOwnerPid`、`Wait-PortReady`（带超时与轮询间隔）。
3. **进程能力**：`Get-DeerFlowProcesses`（按 `CommandLine` 匹配 `uvicorn app.gateway.app:app` / `next-server` / `next start`，并校验工作目录在仓库根内以防误杀他人在 3000 端口上的项目）、`Stop-DeerFlowProcess`（先 `Stop-Process` 后 `taskkill /F /T` 兜底）。
4. **PID 文件**：`Write-PidFile`、`Read-PidFile`、`Remove-PidFile`（容忍文件不存在与进程已退出）。
5. **日志**：`Write-Step`/`Write-Ok`/`Write-Warn`/`Write-Err`（带颜色，且 `-Quiet` 模式下不输出）、`Show-LogTail`。
6. 兼容 PowerShell 5.1（不使用 PS7 专属语法如 `??`），并保证 `$PSScriptRoot` 在模块中可用。

**验收标准**

- `Import-Module ./scripts/windows/DeerFlow.Common.psm1` 成功，无语法错误。
- `Test-PortListening 3000` 在端口空闲时返回 `$false`，在 `python -m http.server 3000` 占用时返回 `$true`。
- `Wait-PortReady -Port 3000 -TimeoutSeconds 3` 在无服务时超时返回 `$false` 且不抛异常。
- 在 PowerShell 5.1 与 PowerShell 7 下均可导入（至少在一个版本下验证，另一版本记录差异）。

**提交:** `feat(windows): add shared PowerShell module for deployment scripts`

---

### Task 2: 环境预检脚本 check-env.ps1

**背景:** 上游 `scripts/check.py` 面向 POSIX，且 Windows 上 `python3` 常解析到 Microsoft Store 存根（`serve.sh:362-364` 已记录此坑）。部署前需要明确告诉运维缺什么、怎么装。

**涉及文件:** 新建 `scripts/windows/check-env.ps1`

**变更要点**

1. 检测项：Python ≥3.12、uv、Node.js ≥22、pnpm（期望 10.26.2）、Git for Windows。
2. Python 探测需**避开 Store 存根**：依次尝试 `py -3.12`、`py -3`、`python`，并实际执行 `--version` 校验输出版本号而非仅看命令是否存在。
3. 每项输出：状态（✓/✗）、当前版本、要求版本、缺失时的**具体安装命令**（winget / 官方下载链接）。
4. 退出码：全部满足返回 0，任一关键项缺失返回 1（供 `install.ps1` 前置调用）。
5. 额外检查：目标端口 3000/8001 是否已被占用（复用 Task 1 的 `Test-PortListening`）。

**验收标准**

- 在依赖齐全的机器上退出码为 0，全部显示 ✓。
- 临时把 pnpm 加入不存在的路径后运行，输出 ✗ 与安装指引，退出码为 1。
- 端口被占用时给出明确警告（不阻断，因为可能是重启场景）。

**提交:** `feat(windows): add environment preflight check script`

---

### Task 3: 配置初始化脚本 init-config.ps1

**背景:** 上游 `scripts/setup_wizard.py` 是**交互式**的（`_is_interactive()` 检查），不适合服务器无人值守部署。需提供幂等的非交互配置生成。

**涉及文件:** 新建 `scripts/windows/init-config.ps1`

**变更要点**

1. **`config.yaml`**（仓库根，若已存在则跳过不动）：从 `config.example.yaml` 派生，显式写入：
   - `database.backend: sqlite`、`database.sqlite_dir: <仓库根>/.deer-flow/data`（避免相对路径歧义）
   - `auth.local_registration.require_admin_approval: true`
   - `auth.allowed_email_domains` 保留默认 `sz-jlc.com`，并支持 `-AllowedEmailDomains` 参数覆盖
   - **不启用** `approval_email.enabled`（审批改为管理后台手动完成，避免依赖 SMTP）
   - Gateway 绑定 `0.0.0.0`（供内网访问）
2. **`.env`**（仓库根，若已存在则跳过）：生成随机密钥
   - `DEER_FLOW_INTERNAL_AUTH_TOKEN`、`BETTER_AUTH_SECRET`——用 `System.Security.Cryptography.RandomNumberGenerator` 生成高强度随机值
   - `DEER_FLOW_PROJECT_ROOT`、`DEER_FLOW_HOME` 指向绝对路径
   - **不设置** `DEER_FLOW_AUTH_DISABLED`（你选择了启用登录）
   - **不设置** `DEER_FLOW_ENV=production`——注意此变量会解除 `is_auth_disabled` 的生产保护，此处无需设置
3. 幂等：已存在的文件默认不覆盖，`-Force` 才重写；重写前备份为 `*.bak.<时间戳>`。
4. 完成后打印配置摘要（**脱敏**：密钥只显示前 4 位）。

**验收标准**

- 首次运行生成 `config.yaml` 与 `.env`，`Get-Content .env` 可见非空的随机密钥。
- 再次运行不修改文件（对比文件哈希不变）。
- `-Force` 运行会生成 `.bak.*` 备份且文件内容更新。
- 生成的 `config.yaml` 能被后端加载（`cd backend && uv run python -c "from deerflow.config import get_app_config; get_app_config()"` 无异常）。

**提交:** `feat(windows): add non-interactive config bootstrap script`

---

### Task 4: 依赖安装与构建脚本 install.ps1

**背景:** `serve.sh:383-396` 的安装逻辑用 `set -e` + 子 shell `cd`，Windows 需等价实现。且生产模式下前端必须**先构建**（`next start` 要求 `.next` 产物存在）。

**涉及文件:** 新建 `scripts/windows/install.ps1`

**变更要点**

1. 前置调用 `check-env.ps1`，不通过则中止并提示。
2. 后端：`uv sync --all-packages`（**不加** `--extra postgres`，因为用 SQLite）。工作目录 `backend/`。
3. 前端：`pnpm install`（工作目录 `frontend/`）。
4. 前端构建：`pnpm build`，环境变量 `SKIP_ENV_VALIDATION=1`（与上游 `frontend/Makefile` 一致）。
5. **不设置** `NEXT_PUBLIC_STATIC_WEBSITE_ONLY`——该标志会禁用后端功能（`frontend/src/core/static-mode.ts`），只用于静态演示站。
6. **不设置** `NEXT_CONFIG_BUILD_OUTPUT`——本方案用 `next start` 而非 standalone，保持与 `serve.sh --prod` 一致。
7. 每步失败时非零退出，并输出该步日志尾部。
8. 支持 `-SkipFrontendBuild`（仅重装后端时加速）。

**验收标准**

- 全新克隆的仓库上运行成功，`backend/.venv` 与 `frontend/.next` 均生成。
- `frontend/.next/BUILD_ID` 存在。
- 重复运行不报错（幂等）。
- 故意让 `pnpm build` 失败（如临时改坏 `next.config.js`）时脚本非零退出而非静默通过。

**提交:** `feat(windows): add dependency install and frontend build script`

---

### Task 5: 服务启停控制脚本 start/stop/status.ps1

**背景:** 这是 `serve.sh` 的核心替换。`serve.sh:430-476` 的 `run_service` 负责端口预检 → 启动 → 等待就绪 → 失败时打印日志尾部。Windows 需用 `Start-Process` + PID 文件实现，且必须避免误杀非本项目的进程（`serve.sh:96-116` 的 `_is_deerflow_pid` 逻辑）。

**涉及文件:** 新建 `scripts/windows/start.ps1`、`scripts/windows/stop.ps1`、`scripts/windows/status.ps1`

**变更要点**

**`start.ps1`**
1. 前置：`config.yaml` 存在（否则提示先跑 `init-config.ps1`）、依赖已装（否则提示先跑 `install.ps1`）。
2. 先调用 `stop.ps1` 清理遗留（与 `serve.sh` 一致的行为）。
3. 端口预检：3000/8001 任一被**非本项目**进程占用则中止并提示；被本项目占用则先清理。
4. 启动 Gateway：`Start-Process` 执行 `uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001`，工作目录 `backend/`，stdout/stderr 重定向到 `logs/gateway.log`，PID 写入 `logs/gateway.pid`。
5. 启动 Frontend：`pnpm run start`（生产）或 `pnpm run dev`（`-Dev` 开关），工作目录 `frontend/`，日志到 `logs/frontend.log`。
6. 等待就绪：Gateway 超时 60s、Frontend 超时 120s（对齐 `serve.sh:466,471` 的 30/120，Gateway 因冷启动慢适当放宽）。
7. 打印访问地址：`http://<本机内网IP>:3000`——**明确用内网 IP 而非 localhost**，因为目标是内网访问。
8. 失败时打印对应日志尾部并清理已启动的进程。

**`stop.ps1`**
1. 读 PID 文件停止；PID 失效则回退到按命令行匹配（复用 Task 1 的 `Get-DeerFlowProcesses`）。
2. 先优雅停止再 `-Force`，中间等待若干秒（对齐 `serve.sh:258` 的 `sleep 1`）。
3. 清理残留的 PID 文件。
4. 报告实际停止了哪些进程。

**`status.ps1`**
1. 对每个服务显示：运行状态、PID、端口、监听地址、运行时长、日志文件路径。
2. 显示最近若干行日志（`-Tail` 参数控制行数）。
3. 全部停止时退出码为 1，用于运维脚本判断。

**验收标准**

- `start.ps1` 后 `http://localhost:3000` 可访问，`logs/gateway.pid` 与 `logs/frontend.pid` 存在且对应真实进程。
- `status.ps1` 正确显示两个服务为运行中，退出码 0。
- `stop.ps1` 后 3000/8001 均无监听，`status.ps1` 退出码 1。
- 重复执行 `start.ps1` 不会产生孤儿进程（首次启动的进程已被清理）。
- 在一个非本项目进程占用 3000 时（如 `python -m http.server 3000`），`start.ps1` **拒绝启动并给出清晰提示**，不会误杀该进程。

**提交:** `feat(windows): add service start/stop/status scripts`

---

### Task 6: 管理员初始化脚本 admin-init.ps1

**背景:** 启用登录后需要一个初始管理员才能审批后续注册。上游已提供 CLI（`python -m app.gateway.auth.reset_admin`），它把凭据写入 `.deer-flow/admin_initial_credentials.txt`（权限 0600）而非打印到 stdout，避免密钥进日志。

**涉及文件:** 新建 `scripts/windows/admin-init.ps1`

**变更要点**

1. 支持 `-Email` 参数指定管理员邮箱；不指定时用该 CLI 的默认行为。
2. 调用 `uv run python -m app.gateway.auth.reset_admin`，工作目录 `backend/`。
3. 调用前检查服务是否已停止（数据库文件被占用时可能失败），或在服务运行时也能工作——以实测为准并在脚本中处理。
4. 完成后从 `.deer-flow/admin_initial_credentials.txt` 读取凭据并**醒目展示一次**，同时提示该文件权限与后续删除建议。
5. 提示管理员登录后应立即修改密码。
6. 邮箱必须匹配 `auth.allowed_email_domains` 中配置的域名，否则提前给出警告。

**验收标准**

- 运行后在 `.deer-flow/admin_initial_credentials.txt` 生成凭据。
- 用该凭据可在浏览器 `http://<IP>:3000/login` 成功登录。
- 登录后能访问管理后台并看到「用户管理」面板。
- 用不允许的域名调用时脚本给出明确警告而非静默失败。

**提交:** `feat(windows): add first admin bootstrap script`

---

### Task 7: 防火墙与开机自启脚本 register-service.ps1

**背景:** 内网访问需放通 3000 端口（Windows 防火墙默认拦截入站）。服务器场景还需开机自启。因服务不是真正的 Windows 服务（是 `uvicorn`/`node` 进程），用**计划任务**在开机时触发启动脚本是更可靠的做法（`New-Service` 要求进程实现服务控制协议，不适用）。

**涉及文件:** 新建 `scripts/windows/register-service.ps1`、`scripts/windows/unregister-service.ps1`

**变更要点**

**`register-service.ps1`**
1. **防火墙**：`New-NetFirewallRule` 放通 TCP 3000 入站，限定**本地子网**（`-RemoteAddress LocalSubnet`）而非任意来源，减少暴露面。规则名带项目前缀便于识别。
2. **开机自启**：注册计划任务
   - 触发器：`AtStartup`
   - 运行账户：需管理员权限（或配置为 `SYSTEM`）；因涉及用户目录下的 `uv`/`pnpm` 缓存，**建议用当前管理员账户**并在脚本中说明权衡
   - 动作：调用 `start.ps1`
   - 设置：`-RunLevel Highest`、失败重试、`-ExecutionTimeLimit 0`（不限时长）
   - 建议加启动延迟（如 30 秒），避免与网络就绪竞争
3. **幂等**：同名规则/任务已存在时先移除再创建，或跳过并提示。
4. 完成后打印：内网访问地址、防火墙规则名、计划任务名、验证方法。

**`unregister-service.ps1`**
1. 移除防火墙规则与计划任务（按名称精确匹配，不误删他人规则）。
2. 不删除数据与日志，仅提示其位置。

**验收标准**

- 在**另一台内网 Windows 机器**上浏览器访问 `http://<服务器IP>:3000` 成功（这条是本次部署的核心目标）。
- `Get-NetFirewallRule -DisplayName "*DeerFlow*"` 可见新规则，且 `RemoteAddress` 为 LocalSubnet。
- `Get-ScheduledTask -TaskName "*DeerFlow*"` 可见新任务。
- 重启服务器后服务自动拉起（或明确记录该步骤需人工验证）。
- `unregister-service.ps1` 后规则与任务均被移除。
- 重复运行 `register-service.ps1` 不产生重复规则/任务。

**提交:** `feat(windows): add firewall and auto-start registration scripts`

---

# Phase 2：文档与端到端验证

### Task 8: 部署文档

**涉及文件:** 新建 `docs/windows-deployment/operations.md`、`scripts/windows/README.md`、更新 `.gitignore`

**变更要点**

1. `docs/windows-deployment/operations.md` 面向运维，包含：
   - 前置要求清单与各依赖的安装命令（winget）
   - **从零到可访问的完整步骤序列**（check-env → init-config → install → admin-init → start → register-service）
   - **架构说明**：为何无 nginx、`/api/*` 如何到达 Gateway、SQLite 数据位置、端口清单
   - **运维手册**：启停、看日志、升级、备份（重点说明 `.deer-flow/` 与 `config.yaml`、`.env` 是需备份的）、故障排查
   - **安全说明**：为何启用登录、管理员审批流程（含无 SMTP 时如何在后台手动审批）、密钥文件位置、建议的内网加固项
   - **已知限制**：单机 SQLite 不支持多节点；无 HTTPS（如需建议反代）；升级需停服
2. `scripts/windows/README.md`：脚本速查表（脚本名、用途、常用参数、示例）。
3. `.gitignore` 补充：`logs/`、`*.pid`、`.deer-flow/`、`admin_initial_credentials.txt`。**注意 `config.yaml` 与 `.env` 应已在忽略列表**（确认后无需重复添加）。

**验收标准**

- 文档中的命令序列可在干净的 Windows 机器上**逐条复制执行**并走通。
- 包含防火墙、管理员审批、备份恢复、故障排查四节。
- `.gitignore` 更新后 `git status` 不再显示 `logs/`、`.deer-flow/`、`*.pid`、凭据文件。

**提交:** `docs(windows): add deployment guide and script reference`

---

### Task 9: 端到端验收

**涉及文件:** 无源码改动；发现问题回到对应 Task 修复

**验收步骤**

1. **环境预检**：干净服务器上 `check-env.ps1`，确认能准确报出缺失项。
2. **全新部署**：按 `docs/windows-deployment/operations.md` 从零走一遍，记录实际耗时。
3. **启动与访问**：
   - `start.ps1` 成功，`status.ps1` 显示两个服务运行中
   - **服务器本机** `http://localhost:3000` 可访问
   - **另一台内网机器** `http://<服务器IP>:3000` 可访问
4. **登录与审批链路**（本次核心功能）：
   - 用 `admin-init.ps1` 生成的凭据登录成功
   - 用另一邮箱注册 → 提示待审批（HTTP 202，无会话）
   - 管理员在后台「用户管理」中审批通过
   - 该用户可登录并使用
5. **核心功能**：发起一次对话，确认**流式输出逐 token 显示**（这是 Task 0 的复验）；上传一个大文件确认成功。
6. **持久化**：重启服务后历史会话仍在（验证 SQLite 落盘）。
7. **运维操作**：`stop.ps1` → `start.ps1` 循环一次；`status.ps1` 输出准确。
8. **自启**：`register-service.ps1` 后重启服务器，确认服务自动拉起且内网可访问。
9. **记录结果**：在 `docs/windows-deployment/operations.md` 追加「验收记录」小节，写明实际耗时、遇到的问题与解决方式、未通过项及其规避措施。

**验收标准**

- 上述 1–8 全部通过，或有明确的失败记录与规避方案。
- 步骤 3 的**另一台内网机器访问**成功——这是本计划的最终目标。
- 步骤 5 的流式输出正常。

**提交:** `docs(windows): record end-to-end deployment verification`

---

## 目标机沙箱配置（`config.yaml` → `sandbox`）

部署后最容易忽略、且报错信息完全指不出方向的一块。

```yaml
sandbox:
  use: deerflow.sandbox.local:LocalSandboxProvider
  allow_host_bash: true          # 默认 false
  mounts:
    - host_path: D:\deer-flow\data\tmp   # 宿主机真实目录，不存在会自动建
      container_path: /tmp               # agent 命令里写的虚拟路径
      read_only: false                   # 默认 false
```

**`allow_host_bash`** —— 控制 `bash` 工具与 `bash` 子代理是否可用。判据在
`deerflow/sandbox/security.py::is_host_bash_allowed()`：**只有** `sandbox.use`
是本地 provider 时才看这个开关，用 `AioSandboxProvider` 时恒为允许。拦截点有三处：
`tools/tools.py:71`（工具不注册）、`subagents/registry.py:158`（子代理不可见）、
`tools/builtins/task_tool.py:503`（委派时返回错误）。

**`mounts`** —— 模型在 Windows 上仍按 Unix 惯例写 `/tmp/...`，而
`tools.py::validate_local_bash_command_paths()` 会拒绝任何不在已知虚拟前缀
（`/mnt/user-data`、`/mnt/skills`、`/mnt/acp-workspace`、以及这里的 `container_path`）
下的绝对路径，报 `Unsafe absolute paths in command`。**报错信息不会提示可以配映射**，
这是它难排查的原因。加一条 mount 即可：`tools.py:820` 放行自定义 mount 路径，
`local/local_sandbox_provider.py:115-123` 负责把虚拟路径真正映射到宿主目录 ——
两边都成立才是「既放行又转换」。

**必须重启才生效。** `sandbox` 在
`config/reload_boundary.py::STARTUP_ONLY_FIELDS` 中。注意其中给出的理由是
**provider 单例缓存**（针对 `sandbox.use`）；`allow_host_bash` 的实际读取路径
（`tools/tools.py:69` 的 `get_app_config()`）是热加载的，但 `mounts` 的映射建在
provider 里，所以改了 mounts 仍然要重启。

**安全边界（务必如实理解）**：`LocalSandboxProvider` **不是隔离边界**，代码里的
提示原文是 "not a secure sandbox boundary"。开启 `allow_host_bash` 后，任何能使用
该实例的人都能以服务账号身份在宿主机执行任意命令 —— 而实例通常内网可达。因此：
只在完全可信的单用户环境开启；`host_path` 指向专用空目录，**绝不要**映射到 `C:\`
或 `D:\` 这类根目录；不需要时改回 `false`。

**修改方式**：直接编辑 `D:\deer-flow\src\config.yaml`，改前备份。该文件是
**CRLF + UTF-8 无 BOM**，用 PowerShell 改时两个坑：
`Set-Content -Encoding UTF8` 会写入 BOM；
用正则做替换时 `$` 前面若用 `\s*` 会把行尾的 `\r` 一起吃掉，产生全文件唯一的 LF 行。
匹配行内容后**插入**新行（不要连行尾一起替换），并在写入后核对
`\r\n` / 孤立 `\n` / 孤立 `\r` 的计数。

---

## 风险与待确认项

| 项 | 风险 | 应对 |
|---|---|---|
| **无 nginx 的 SSE 流式** | **已复现并已解决，无需回退 nginx**。Next 默认的响应压缩会给 `text/event-stream` 加 `Content-Encoding: gzip`，而 gzip 流是**缓冲**而非逐事件 flush 的：实测经 Next 取流**9.41 秒内零事件**，随后 1465 帧一次性到达（12 个 socket 块）；浏览器表现为刷新后一直停在「思考中」、推理与步骤行直到 run 结束才出现，正文成段跳变。nginx 不压缩该流，所以两个入口表现不同 —— 这也解释了为什么只在直连 3000 时出现 | `next.config.js` 设 `compress: false`（已落地，附注释与 `tests/unit/next-config.test.ts` 守护）。修复后同一 run：首帧 15ms、逐事件 flush（1482 块 / 1475 帧） |
| **大文件上传** | 无 nginx 的 `client_max_body_size 100M` 保护，但可能被其他层限制 | Task 0 步骤 4 实测 |
| **无 HTTPS** | 内网明文传输，凭据与内容可被同网段嗅探 | 文档中说明；如需 HTTPS 另立计划（建议 Caddy 或 nginx 反代） |
| **监听 `0.0.0.0`** | Gateway 与前端暴露到所有网络接口 | 配合防火墙限定 LocalSubnet；若服务器有多网卡需进一步收紧 |
| **计划任务运行账户** | 用 `SYSTEM` 可能找不到用户级 `uv`/`pnpm`；用用户账户需该用户已登录或配置存储凭据 | Task 7 中实测选定，文档记录 |
| **`jlc/develop` 无法 fetch** | 基于的是本地缓存（2026-09-11），可能已落后 | 先解决 `git-w.jlcops.com` SSH 访问再开工 |
| **升级流程** | SQLite 单文件，升级期间需停服 | 文档写明停服升级步骤与 `config.yaml` 备份 |

## 明确不在本次范围

- HTTPS / 域名 / 证书
- 多节点或高可用部署（SQLite 单机；多节点需 PostgreSQL，见 `config.example.yaml:1433`）
- nginx 或其他反向代理的引入
- Docker / WSL2 部署路径
- 自动化 CI/CD 与灰度发布
- IM 渠道（飞书/企微等）接入配置
