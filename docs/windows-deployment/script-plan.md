# Windows 部署脚本集 实施计划（SubAgent 驱动）

> **执行方式:** superpowers:subagent-driven-development。每个 Task 由独立 SubAgent 执行，主 agent 负责验收与派发，**不需要人工逐步确认**。
>
> **中止条件（仅这几种才需询问人类）:**
> 1. 同一 Task 连续 3 次修复仍失败
> 2. 需要修改 Windows 系统级配置（系统 PATH、组策略、全局防火墙规则）
> 3. 需要安装计划外的软件
> 4. 发现凭据泄露风险
>
> 其余情况（依赖失败重试、换镜像源、调超时、修脚本 bug）**自主决策并继续**。

**目标:** 产出一套可复用的 Windows 部署脚本集，使**任意一台新 Windows 机器**能从零完成 DeerFlow 部署并长期运行——而不是靠开发机逐条 SSH 打临时命令。

**核心原则:** 所有系统操作都必须固化为**仓库内的 PowerShell 脚本**。开发机上的 `win-exec.sh` 仅用于**开发期验证**，正式部署不依赖它。

**架构:** 镜像仓库 + 一键初始化。新机器流程：

```
git clone / 拷贝仓库   →   init.ps1（工具链）   →   install.ps1（依赖+构建）
                                                         ↓
                                              start.ps1（启服务）→ register-autostart.ps1（开机自启）
```

**技术栈:** Windows 10 20H2+（目标机实测 20H2 / AMD64）、PowerShell 5.1、Node 22.23.2、pnpm 10.26.2、uv 0.12.13、Python 3.12、Next.js 16、FastAPI、SQLite。

---

## 已完成的前置工作（无需重做）

| 项 | 状态 | 证据 |
|---|---|---|
| SSH 免密登录 | ✅ | `install-sshd.ps1` 已部署，直接进 PowerShell |
| 工具链安装脚本 | ✅ | `scripts/windows/install-toolchain.ps1`（含镜像源、PATH 遮蔽检测） |
| 后端依赖已装 | ✅ | `D:\deer-flow\src\backend\.venv` 214 包 703MB，`import app.gateway.app` 通过 |
| 同步/执行机制 | ✅ | `scripts/windows-remote/{sync-to-windows.sh,win-exec.sh}` |

## 已核实的关键事实

| 事实 | 依据 |
|---|---|
| **PyPI 镜像已内置** | `backend/pyproject.toml:51-52` `index-url = https://pypi.tuna.tsinghua.edu.cn/simple` |
| PyPI 及国内镜像**直连可达** | 实测 pypi.org / tuna / aliyun / tencent / ustc 均 200 |
| npm 镜像已配置 | 用户级 `NPM_CONFIG_REGISTRY=https://registry.npmmirror.com` |
| Python 3.12 已在本机 | `py -3.12` → `D:\Python\Python312\python.exe`，无需 uv 下载 |
| 系统 PATH 有 Node 16 遮蔽 | `C:\Program Files\nodejs`；脚本须显式前置工具链 |
| C 盘仅剩 3.7 GB | 缓存须重定向到 D 盘 |
| 前端构建约束 | `next start` 需要 `.next`；**不能**设 `NEXT_PUBLIC_STATIC_WEBSITE_ONLY`（会禁用后端）也**不能**设 `NEXT_CONFIG_BUILD_OUTPUT=standalone` |

## 关键设计决策（源自实测经验）

**1. SSH 传输的 `-EncodedCommand` 编码方式**
把 PowerShell 拼进 `ssh host "powershell -Command \"...\""` 会穿四层转义，实测 PATH 前置语句被静默吞掉、命令回落到 Node 16。必须用 base64 + `-OutputFormat Text`（后者抑制 CLIXML 噪声）。

**2. `python -c "..."` 的内层引号会被剥离**
实测：`win-exec.sh 'python -c "print(\"OK\")"'` 到达远端变成 `print(OK)`，报 `NameError`。**验证脚本一律写成 `.py` 文件**，不要用 `-c` 内联。

**3. Python 脚本文件必须无 BOM**
`Set-Content -Encoding UTF8` 会写 BOM，导致 `﻿import` 语法错误。用 `[System.IO.File]::WriteAllText($p, $s, (New-Object Text.UTF8Encoding($false)))`。

**4. `import app.*` 依赖 cwd 或 PYTHONPATH**
仅 `--dir` 切目录不够——若验证脚本本身在别的目录，`sys.path[0]` 会被脚本所在目录占据。须显式设 `$env:PYTHONPATH`。

**5. 后台启动的可靠性**
`Start-Process -PassThru` 在不等待时，进程可能未持久化（实测后台 UV 进程 60 秒后消失且日志为 0 字节）。长任务用 `-Wait` 同步执行，异步需额外确认进程存活。

**6. 脚本编码**
含中文的 `.ps1` 必须存为 **UTF-8 with BOM + CRLF**，否则 PowerShell 5.1 按 GBK 解析，中文变乱码甚至语法错误。
（已验证：`.gitattributes` 的 `*.ps1/.psm1 text eol=crlf` 生效——git blob 存 LF，检出到 Windows 自动转 CRLF，实测 1030 CRLF + BOM 齐全。无需调整 gitattributes。）

**7. `require_admin_approval` 强制要求 `approval_email.enabled`（实施中发现，修正原计划）**
原计划写「启用审批但禁用邮件」——**这在架构上不可能**：
```
backend/packages/harness/deerflow/config/auth_config.py:178-179
    if self.require_admin_approval and not self.approval_email.enabled:
        raise ValueError("require_admin_approval requires approval_email.enabled=true")
```
正确做法：**启用** `approval_email.enabled` 但指向**回环 SMTP 桩**（`127.0.0.1:465`，凭据 `disabled`）。
审批流程实际不依赖邮件——`user_management_service.approve()` 先提交状态变更，**之后**才尽力发信；
失败会被捕获并标记 `approval_email_status=failed`，不阻断审批。回环地址即时拒绝（实测 ~4ms），无网络超时。

**8. `config.yaml` 中的 `$VAR` 必须全部可解析（实施中发现）**
`config.example.yaml` 含 **50 个 `$VAR` 引用**，且 `app_config.py:403-405` 对缺失变量**硬失败**：
```
env_value = os.getenv(config[1:])
if env_value is None:
    raise ValueError(f"Environment variable {config[1:]} not found")
```
其中 `image_editing` 段的 `$OPENAI_IMAGE_AUTHORIZATION` 是字段级硬失败（后端只对 `api_key` 字段做白名单）。
生成配置时须扫描全部整值 `$VAR` 引用，缺失的写入 `.env` 并置**空字符串**（`os.getenv` 返回 `''` 而非 `None`，配置可加载，
界面显示为「未配置」，运维后续填真实密钥即可）。
**例外**：`DEER_FLOW_*` / `BETTER_AUTH_*` 不应置空，这些缺失应当**显式报错**。

---

# 文件结构

所有部署脚本放 `scripts/windows/`（与已有 `install-sshd.ps1`、`install-toolchain.ps1` 同目录）。

| 文件 | 职责 | 状态 |
|---|---|---|
| `install-sshd.ps1` | 安装配置 OpenSSH Server | ✅ 已有 |
| `install-toolchain.ps1` | 安装 Node/uv/pnpm（镜像源） | ✅ 已有 |
| `DeerFlow.Common.psm1` | 公共模块：路径解析、配置读写、进程管理、日志 | **新建** |
| `init-config.ps1` | 生成 `config.yaml` 与 `.env`（幂等，随机密钥） | **新建** |
| `install.ps1` | 装依赖 + 构建前端（uv sync / pnpm install / pnpm build） | **新建** |
| `start.ps1` | 启动 gateway + frontend，写 PID 文件，等待就绪 | **新建** |
| `stop.ps1` | 按 PID 停止服务，清理残留 | **新建** |
| `status.ps1` | 服务状态、端口、运行时长、日志尾部 | **新建** |
| `admin-init.ps1` | 初始化管理员账号并输出凭据 | **新建** |
| `register-autostart.ps1` | 注册开机自启（计划任务）+ 防火墙入站规则 | **新建** |
| `unregister-autostart.ps1` | 移除自启与防火墙规则 | **新建** |
| `deploy.ps1` | 一键编排：init-config → install → start | **新建** |
| `README.md` | 脚本速查 + 新机器部署步骤 | **新建** |

**目录约定（目标机）:**
```
D:\deer-flow\
├── tools\      Node / uv（由 install-toolchain.ps1 创建）
├── src\        代码仓库（git clone 或拷贝）
├── data\       SQLite 数据（DEER_FLOW_HOME）
├── cache\      各类缓存
└── logs\       gateway.log / frontend.log
```

⚠️ `tools\` 与 `src\` 必须平级——若代码同步用 `--delete`，工具链在 `src\` 内会被删除。

---

# Phase 1：公共模块

### Task 1: 建立 DeerFlow.Common.psm1

**Files:** Create `scripts/windows/DeerFlow.Common.psm1`

**背景:** 后续 6 个脚本都需要共享路径解析、配置读写、进程管理能力。先收敛到一个模块，避免重复实现（DRY）。

**变更要点**

1. **路径解析**：`Get-DeerFlowRoot`（默认 `D:\deer-flow`，可用环境变量 `DEER_FLOW_DEPLOY_ROOT` 覆盖）、`Get-DeerFlowSrcDir`、`Get-DeerFlowDataDir`、`Get-DeerFlowLogsDir`、`Get-DeerFlowRunDir`（放 PID 文件）
2. **配置读写**：`Read-DotEnv`（解析 `.env` 为 hashtable，跳过注释与空行）、`Write-DotEnv`、`Test-DeerFlowConfigured`
3. **进程管理**：
   - `Test-PortListening`（`Get-NetTCPConnection -State Listen`）
   - `Get-DeerFlowProcess`（按 PID 文件，回退到按命令行匹配 `uvicorn app.gateway.app:app` / `next` / `node`，**并校验进程工作目录在部署根内**以防误杀他人进程）
   - `Stop-DeerFlowProcess`（先 Stop-Process 再 taskkill /T 兜底）
   - `Get-PortOwnerPid`
4. **PID 文件**：`Write-PidFile` / `Read-PidFile` / `Remove-PidFile`（容忍文件缺失与进程已退出）
5. **日志与输出**：`Write-Step/Ok/Warn/Fail/Info`（带颜色）、`Write-Log`（同时输出到控制台与文件）
6. **编码工具**：`Write-TextFileNoBom`（用 `UTF8Encoding($false)`，规避 BOM 导致 Python 语法错误）
7. **PATH 前置**：`Add-ToolchainToPath`（把 `tools\node`、`tools\uv` 前置，规避系统 Node 16 遮蔽）

**兼容性要求**: PowerShell 5.1（不使用 `??`、三元运算符等 PS7 语法）。

**验收标准**

- `Import-Module scripts\windows\DeerFlow.Common.psm1` 成功
- `Get-DeerFlowRoot` 返回预期路径，`DEER_FLOW_DEPLOY_ROOT` 环境变量可覆盖
- `Write-TextFileNoBom` 写出的文件无 BOM（读回首字节不是 `EF BB BF`）
- `Test-PortListening 3000` 在空闲时返回 `$false`，有服务时返回 `$true`
- 模块在 PowerShell 5.1 下无语法错误（用 `[Parser]::ParseFile` 验证）

**提交:** `feat(windows): add shared deployment module`

---

# Phase 2：配置与安装

### Task 2: 配置生成脚本 init-config.ps1

**Files:** Create `scripts/windows/init-config.ps1`

**背景:** 上游 `scripts/setup_wizard.py` 是交互式的（有 `_is_interactive()` 检查），不适合无人值守部署。需非交互生成。

**变更要点**

1. **`config.yaml`**（位于 `src\` 根；已存在则跳过，`-Force` 才覆盖且先备份为 `*.bak.<时间戳>`）
   从 `config.example.yaml` 派生，显式写入：
   - `database.backend: sqlite`、`database.sqlite_dir: <数据目录绝对路径>`
   - `auth.local_registration.require_admin_approval: true`
   - `auth.allowed_email_domains` 保留 `config.example.yaml` 的默认值，支持 `-AllowedEmailDomains` 覆盖
   - **不启用** `approval_email.enabled`（审批走管理后台手动完成，避免 SMTP 依赖）
2. **`.env`**（位于 `src\` 根；已存在则跳过）
   - 用 `System.Security.Cryptography.RandomNumberGenerator` 生成 `DEER_FLOW_INTERNAL_AUTH_TOKEN`、`BETTER_AUTH_SECRET`
   - `DEER_FLOW_PROJECT_ROOT`、`DEER_FLOW_HOME` 指向绝对路径
   - **不设置** `DEER_FLOW_AUTH_DISABLED`（启用了登录认证）
   - **不设置** `DEER_FLOW_ENV=production`（该变量会解除 auth-disabled 的生产保护，本场景无需）
3. 完成后打印配置摘要，密钥**脱敏**（只显示前 4 位）

**验收标准**

- 首次运行生成两个文件；`.env` 中密钥非空且长度合理（>32 字符）
- 再次运行不修改文件（对比文件哈希不变）
- `-Force` 运行会先生成 `.bak.*` 备份
- 生成的 `config.yaml` 能被后端加载：
  ```
  cd backend; $env:PYTHONPATH=(Get-Location).Path; .venv\Scripts\python.exe -c "from deerflow.config import get_app_config; get_app_config(); print('CONFIG_OK')"
  ```
  （注意用 `-c` 时只传不含内层引号的简单表达式，或写成 `.py` 文件）

**提交:** `feat(windows): add non-interactive config bootstrap`

---

### Task 3: 依赖安装脚本 install.ps1

**Files:** Create `scripts/windows/install.ps1`

**背景:** 后端依赖已在验证阶段装过（`.venv` 214 包），但脚本必须能从零跑通，供新机器使用。

**变更要点**

1. 前置检查：调用 `DeerFlow.Common` 的 `Add-ToolchainToPath`；确认 `node`/`pnpm`/`uv`/`py -3.12` 可用；确认部署根、代码目录存在；检查磁盘空间（<10GB 警告）
2. **后端**：在 `backend` 下 `uv sync --all-packages --frozen`
   - 失败回退：去掉 `--frozen` 重试一次
   - **不加** `--extra postgres`（用 SQLite）
   - 镜像已由 `backend/pyproject.toml` 内置（清华），无需额外配置
3. **前端**：在 `frontend` 下 `pnpm install --frozen-lockfile`
   - 失败回退：去掉 `--frozen-lockfile` 重试
4. **构建**：在 `frontend` 下 `pnpm build`，环境变量 `SKIP_ENV_VALIDATION=1`
   - **不设** `NEXT_PUBLIC_STATIC_WEBSITE_ONLY`（会禁用后端功能）
   - **不设** `NEXT_CONFIG_BUILD_OUTPUT=standalone`（本方案用 `next start`）
5. 每步输出日志到 `logs\install-*.log`；失败时打印日志尾部并**非零退出**
6. 支持 `-SkipBackend` / `-SkipFrontend` / `-SkipBuild` 便于分步重跑
7. 长任务用 `-Wait` 同步执行（实测后台 `Start-Process` 不可靠）

**验收标准**

- 全新环境跑通：`.venv` 生成、`node_modules` 生成、`.next\BUILD_ID` 存在
- 重复运行不报错（幂等）
- 故意让某步失败（如临时改坏 `package.json`）时脚本**非零退出**而非静默通过
- 每步耗时被记录到日志

**提交:** `feat(windows): add dependency install and build script`

---

# Phase 3：服务管理

### Task 4: 服务启停脚本 start/stop/status.ps1

**Files:** Create `scripts/windows/start.ps1`、`scripts/windows/stop.ps1`、`scripts/windows/status.ps1`

**背景:** 这是最核心的部分。参考上游 `scripts/serve.sh` 的 `run_service`（端口预检 → 启动 → 等待就绪 → 失败时打印日志尾部），但要用 PowerShell 实现并适配 Windows 的进程模型。

**变更要点**

**`start.ps1`**
1. 前置：`config.yaml` 与 `.env` 存在（否则提示先跑 `init-config.ps1`）；`.venv` 与 `.next` 存在（否则提示先跑 `install.ps1`）
2. 先调用 `stop.ps1` 清理遗留（避免端口占用）
3. 端口预检：8001/3000 若被**非本项目**进程占用则中止并提示（不误杀）
4. 启动 Gateway：`uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001`
   - 工作目录 `backend\`，设 `$env:PYTHONPATH`
   - stdout/stderr 重定向到 `logs\gateway.log`
   - PID 写入 `logs\run\gateway.pid`
5. 启动 Frontend：`pnpm run start`（`-Dev` 时用 `pnpm run dev`）
   - 工作目录 `frontend\`，日志到 `logs\frontend.log`
6. 等待就绪：Gateway 60s、Frontend 120s（对齐 `serve.sh` 的 30/120，Gateway 冷启动慢故放宽）
7. 打印访问地址：**用本机内网 IP**（`Get-NetIPAddress`），而非 localhost——目标是内网访问
8. 失败时打印对应日志尾部，并清理已启动的进程

**`stop.ps1`**
1. 读 PID 文件停止；PID 失效则回退到按命令行匹配 + 工作目录校验
2. 先优雅停止，等待数秒，再 `-Force`
3. 清理 PID 文件；报告实际停止了哪些进程

**`status.ps1`**
1. 每个服务：运行状态、PID、端口、监听地址、运行时长、日志路径
2. `-Tail N` 显示日志末尾
3. 全部停止时退出码为 1（供运维脚本判断）

**验收标准**

- `start.ps1` 后 `http://localhost:3000` 与 `http://localhost:8001/health` 均返回 200
- 两个 PID 文件存在且对应真实进程
- `status.ps1` 正确显示运行中，退出码 0；`stop.ps1` 后 3000/8001 无监听，`status.ps1` 退出码 1
- 重复执行 `start.ps1` 不产生孤儿进程
- **误杀防护**：用非本项目进程占用 3000（如 `python -m http.server 3000`），`start.ps1` 应**拒绝启动并明确提示**，不杀该进程

**提交:** `feat(windows): add service start/stop/status scripts`

---

### Task 5: 管理员初始化脚本 admin-init.ps1

**Files:** Create `scripts/windows/admin-init.ps1`

**背景:** 启用登录后需要初始管理员才能审批注册。上游已提供 CLI `python -m app.gateway.auth.reset_admin`，它把凭据写入 `.deer-flow/admin_initial_credentials.txt`（0600）而非打印到 stdout，避免密钥进日志。

**变更要点**

1. 支持 `-Email` 指定管理员邮箱；缺省用 CLI 默认行为
2. 调用 `uv run python -m app.gateway.auth.reset_admin`，工作目录 `backend\`，设 `$env:PYTHONPATH`
3. 邮箱需匹配 `auth.allowed_email_domains`，否则提前警告
4. 完成后从凭据文件读取并**醒目展示一次**，提示该文件位置与后续删除建议
5. 提示登录后应立即改密

**验收标准**

- 运行后凭据文件生成
- 用该凭据能通过登录接口（POSIX 下用 curl 验证 `POST /api/v1/auth/login` 返回 token）
- 用不在白名单的域名调用时给出明确警告

**提交:** `feat(windows): add admin bootstrap script`

---

# Phase 4：自启动与编排

### Task 6: 开机自启脚本 register-autostart.ps1

**Files:** Create `scripts/windows/register-autostart.ps1`、`scripts/windows/unregister-autostart.ps1`

**背景:** 服务是 `uvicorn`/`node` 进程而非真正的 Windows 服务（`New-Service` 要求实现服务控制协议，不适用）。用**计划任务**在开机时触发启动脚本更可靠。

**变更要点**

**`register-autostart.ps1`**
1. **防火墙**：`New-NetFirewallRule` 放通 TCP 3000 入站，**限定 `-RemoteAddress LocalSubnet`**（仅本网段，减少暴露面）
2. **计划任务**：
   - 触发器 `AtStartup`，建议加 30 秒延迟避免与网络就绪竞争
   - 动作：调用 `start.ps1`
   - `-RunLevel Highest`、失败重试、`-ExecutionTimeLimit 0`（不限时长）
   - 运行账户：**用当前管理员账户**（因需访问用户级 `uv`/`pnpm` 缓存），并在输出中说明该权衡
3. 幂等：同名规则/任务已存在则先移除再创建
4. 完成后打印：内网访问地址、规则名、任务名、验证方法

**`unregister-autostart.ps1`**
1. 按名称精确匹配移除规则与任务（不误删他人）
2. 不删除数据与日志，仅提示其位置

**验收标准**

- `Get-NetFirewallRule -DisplayName "*DeerFlow*"` 可见新规则且 `RemoteAddress` 为 LocalSubnet
- `Get-ScheduledTask -TaskName "*DeerFlow*"` 可见新任务
- 重复运行不产生重复规则/任务
- `unregister-autostart.ps1` 后规则与任务均被移除

**提交:** `feat(windows): add firewall and autostart registration`

---

### Task 7: 一键部署编排 deploy.ps1

**Files:** Create `scripts/windows/deploy.ps1`、`scripts/windows/README.md`、`docs/windows-deployment/operations.md`

**背景:** 新机器部署应能一条命令完成，降低人为遗漏。

**变更要点**

1. `deploy.ps1` 按顺序编排：环境检查 → `init-config.ps1` → `install.ps1` → `start.ps1` → 自检
2. 每阶段失败即停止并给出手动补救提示（不静默跳过）
3. 支持 `-SkipToolchain`（工具链已装）、`-SkipBuild`、`-NoStart`（只装不启）
4. **检测工具链是否就绪**，未就绪则提示先跑 `install-toolchain.ps1`（不自动执行，因其涉及系统级安装）
5. 完成后打印：访问地址、管理员初始化命令、自启注册命令

**文档要求（`docs/windows-deployment/operations.md`）**

面向运维，包含：
- **前置要求**与依赖安装命令
- **从零到可访问的完整步骤序列**（含每步的预期输出）
- **架构说明**：为何无 nginx、`/api/*` 如何到达 Gateway、SQLite 数据位置、端口清单
- **运维手册**：启停、看日志、升级（同步代码 + 重新构建）、备份（重点：`data\`、`config.yaml`、`.env`）
- **安全说明**：为何启用登录、管理员审批流程（无 SMTP 时如何在后台手动审批）、密钥文件位置
- **已知限制**：单机 SQLite 不支持多节点；无 HTTPS（如需建议反代）；升级需停服

**验收标准**

- 文档中的命令序列可在干净 Windows 上逐条复制执行
- README 提供脚本速查表（脚本名、用途、常用参数、示例）
- `deploy.ps1` 在当前机器上能完整跑通（依赖已装的情况下应快速跳过并成功启动）

**提交:** `docs(windows): add deployment guide and orchestration script`

---

# Phase 5：验证与记录

### Task 8: 端到端验证与执行报告

**Files:** Create `docs/windows-deployment/execution-report.md`

**执行要点**

1. **脚本级验证**（在已部署机器上）：
   - `status.ps1` 输出正确
   - `stop.ps1` → `start.ps1` 循环一次成功
   - `deploy.ps1` 在已装环境下幂等跑通
2. **功能验证**：
   - 本机 `http://localhost:3000` 返回 200
   - `/api/models` 经 rewrite 到达 gateway 返回 JSON（验证前端→后端链路）
   - `admin-init.ps1` 后能登录
   - SQLite 文件已生成
3. **内网验证（最终目标）**：从 **macOS** curl `http://192.168.2.10:3000` 返回 200
   - 若不通：检查防火墙规则；允许添加本项目所需的入站规则（限本网段）
4. **自启验证**：`register-autostart.ps1` 后确认规则与任务已创建（重启验证可选，若执行则记录）
5. **报告内容**：实际步骤、耗时、错误与解决、最终状态、**未解决问题及影响**、后续运维要点

**验收标准**

- 步骤 1-4 全部通过，或有明确的失败记录与规避方案
- 步骤 3 的**内网访问成功**——这是最终目标
- 报告能让其他人照着在 30 分钟内完成同样部署

**提交:** `docs(windows): record end-to-end deployment verification`

---

## 执行约定（给 SubAgent）

**自主决策范围（不要询问）:**
- 依赖安装失败 → 重试、换镜像源、调整超时
- 脚本有 bug → 修复、重新同步、重跑
- 服务启动失败 → 读日志、修配置、重启
- 端口占用 → 找到占用进程并处理（**仅限本项目进程**）
- 脚本编码问题 → 转 UTF-8 BOM + CRLF 后重试

**必须中止并询问:**
- 需要改 Windows 系统级配置（系统 PATH / 组策略 / 全局防火墙规则）
- 需要安装计划外软件（VS Build Tools、PostgreSQL 等）
- 同一问题修复 3 次仍失败
- 发现凭据以明文出现在日志或输出中

**工作方式:**
- 每步执行后**立即验证**，不要连续执行多步再统一验证
- 长任务（>60s）用 `-Wait` 同步执行（实测后台 `Start-Process` 不可靠）；若必须异步，确认进程存活
- 验证 Python 时**写成 `.py` 文件**，不要用 `-c` 内联（内层引号会被剥离）
- Python 脚本文件必须**无 BOM**
- `import app.*` 需显式设 `$env:PYTHONPATH`
- 代码改动需同步：改本机 → `sync-to-windows.sh` → 远端执行
- 日志统一放 `D:\deer-flow\logs\`
- **所有能力都必须固化为脚本**——开发机的 `win-exec.sh` 只用于验证，不是交付物

**脚本质量要求:**
- 含中文的 `.ps1` 存为 **UTF-8 with BOM + CRLF**
- 必须幂等（重复运行结果一致）
- 必须有清晰的中文错误提示与补救建议
- 必须能用 `[Parser]::ParseFile` 通过语法检查
