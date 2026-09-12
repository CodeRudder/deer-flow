# DeerFlow Windows 部署与运维手册

> 面向**运维人员**。目标：在一台内网 Windows 机器上完成 DeerFlow 部署、日常启停、升级与备份。
>
> 另有一份 `docs/windows-deployment-handover.md` 是给**接手开发者**的（讲脚本内部实现、踩过的坑），
> 两者定位不同，不要混用。

---

## 〇、本手册的验证状态

以下内容在目标机 `192.168.2.10`（`GDW-WIN10`，Windows 10 Pro 20H2，PowerShell 5.1）上**逐条实测**：

| 能力 | 命令 | 验证状态 |
|---|---|---|
| 一键部署（全套） | `deploy.ps1` | ✅ 实测跑通，退出码 0 |
| 幂等（连跑两次） | `deploy.ps1` | ✅ 两次均成功；第二次不打断已运行的服务 |
| 只装不启 | `deploy.ps1 -NoStart` | ✅ 实测，服务未被触动 |
| 跳过工具链检测 | `deploy.ps1 -SkipToolchain` | ✅ 实测 |
| 不提示自启 | `deploy.ps1 -SkipAutostart` | ✅ 实测 |
| 注册自启 | `deploy.ps1 -RegisterAutostart` | ✅ 实测（计划任务 + 防火墙规则均回读确认） |
| 工具链缺失检测 | 临时移走 `tools\node` 后运行 | ✅ 实测：给出明确提示并以退出码 1 中止，未自动安装 |
| 启停循环 | `stop.ps1` → `start.ps1` | ✅ 实测，两个方向退出码均为 0 |
| 状态查询 | `status.ps1` / `status.ps1 -Tail 40` | ✅ 实测，退出码 0（两服务运行中） |
| 日志读取 | `logs\gateway.log` / `frontend.log` | ✅ 实测（服务运行时也能读，见「为什么能读运行中的日志」） |
| 备份路径与做法 | `data\` + `config.yaml` + `.env` | ✅ 实测复制成功；并实测证明**只拷主库会丢数据**（见 §4.5） |
| 防火墙规则 | `Get-NetFirewallRule -DisplayName 'DeerFlow*'` | ✅ 实测：两条规则 `Allow` / `RemoteAddress=LocalSubnet` |
| 计划任务 | `Get-ScheduledTask -TaskName 'DeerFlow Auto Start'` | ✅ 实测：`Ready`，触发器开机延迟 30s，S4U 最高权限 |

**未在本机实测**（脚本已就绪，属首次部署才需要的步骤，本机执行会重复安装）：
`install-sshd.ps1`、`install-toolchain.ps1`、`admin-init.ps1`。
这三步的命令与预期输出按脚本自身的输出契约给出，已在表中标注。

---

## 一、前置要求

### 1.1 目标机要求

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10 1809+ / Windows Server 2019+ | 目标机实测 Windows 10 Pro 20H2（Build 19042） |
| 架构 | x64 | |
| PowerShell | 5.1（系统自带） | 脚本刻意避开 PS7 语法 |
| 权限 | **管理员账户** | 装 SSH、注册自启、放通防火墙都需要 |
| 网络 | 与访问方处于同一内网网段 | 目标机为 `192.168.2.10`（网卡「以太网」，DHCP） |
| 磁盘 | 部署根所在盘 **≥ 10 GB 可用** | 实测 D 盘 164.6 GB 可用；依赖 + 构建产物合计 3 GB 以上 |

> ⚠️ **C 盘不要用**。目标机 C 盘只剩 3.7 GB。所有缓存（npm / uv / uv-python）与依赖都放在部署根下的
> `cache\`、`src\`。这是刻意的，脚本已把缓存全部重定向到数据盘。

### 1.2 需要预装的工具

| 工具 | 目标机现状 | 是否需要手工处理 |
|---|---|---|
| OpenSSH Server | 已安装，免密登录可用 | **新机器**需要跑 `install-sshd.ps1`（见步骤 1） |
| Node 22 / uv / pnpm | 由 `install-toolchain.ps1` 装在 `D:\deer-flow\tools` | 新机器需要跑（见步骤 3） |
| Python 3.12 | 已有：`py -3.12` → `D:\Python\Python312\python.exe` | 已有则无需处理 |
| git | 已有 2.39.1 | 同步代码用 |
| curl.exe / tar.exe | 系统内置 | 无需处理 |

> ⚠️ `python3` 在本机是 **Microsoft Store 存根，不可用**。需要 Python 时一律用 `py -3.12`。

### 1.3 目录布局（必须遵守）

```
D:\deer-flow\
├── tools\        工具链：node\ (Node 22.23.2 + pnpm 10.26.2)、uv\ (0.12.13)
├── src\          代码仓库 + 运行产生的 config.yaml / .env / backend\.venv / frontend\.next
├── data\         SQLite 数据（DEER_FLOW_HOME 指向此处）
├── cache\        npm / uv / uv-python 缓存
└── logs\         gateway.log / frontend.log / install-*.log / deploy.log
    └── run\      gateway.pid / frontend.pid
```

> ⚠️ **`tools\` 与 `src\` 必须平级**。代码同步若使用清空式覆盖（`--delete`），`tools\` 放在 `src\` 内会被一起删掉。

部署根不在 `D:\deer-flow` 时，所有脚本都支持 `-RootDir <路径>` 覆盖；也可以设置环境变量
`DEER_FLOW_DEPLOY_ROOT`（进程 / 用户 / 机器级均可）。**推荐统一用 `-RootDir`**，显式、不易错。

---

## 二、从零到可访问：完整步骤序列

> 全部命令在**目标机的 PowerShell（管理员）** 里执行。
> 本机操作可从步骤 2 开始；只有需要远程管理时才必须先做步骤 1。

**顺序总览**（步骤 6 是 4+5+启动+自检的合并版，二选一即可）：

| # | 步骤 | 必需性 | 需要管理员 |
|---|---|---|---|
| 1 | 装 SSH 服务端 `install-sshd.ps1` | 仅新机器 / 需远程管理 | ✅ |
| 2 | 放代码到 `D:\deer-flow\src` | **必需** | |
| 3 | 装工具链 `install-toolchain.ps1` | **必需**（唯一改系统配置的一步） | ✅ |
| 4 | 生成配置 `init-config.ps1` | **必需**（可由步骤 6 代做） | |
| 5 | 装依赖 + 构建 `install.ps1` | **必需**（可由步骤 6 代做） | |
| **6** | **一键部署 `deploy.ps1`**（= 4 + 5 + 启动 + 自检） | 推荐 | 建议 ✅ |
| 7 | 初始化管理员 `admin-init.ps1` | **必需**（没有管理员无法审批新用户） | |
| 8 | 注册开机自启 `register-autostart.ps1` | 可选 | ✅ |
| 9 | 填入模型配置（改 `config.yaml` / `.env`） | **必需**（否则 LLM 功能不可用） | |

跑完步骤 3 之后，步骤 4 起可以只用一条命令完成（步骤 6）。下面仍然把 4/5 单独列出，
是因为分步执行时出错更容易定位。

### 步骤 1：安装 SSH 服务端（仅新机器 / 仅需远程管理时）

```powershell
& 'D:\deer-flow\src\scripts\windows\install-sshd.ps1'
```

**预期输出**（按脚本输出契约）：安装/启用 OpenSSH Server、设置服务自动启动、放通 22 端口入站（限本网段）、
最后打印「SSH 服务端已就绪」以及验证方法。

**验证**：从另一台机器 `ssh <用户名>@<服务器IP>`，能直接进入 PowerShell 即为成功。

> 本机（服务器控制台）操作可以跳过这一步。目标机已完成，无需重跑。

---

### 步骤 2：放代码

把仓库整份放到部署根下的 `src\`：

```
D:\deer-flow\src\        ← 仓库根目录（内含 backend\ / frontend\ / scripts\ / config.example.yaml）
```

两种方式：

```powershell
# 方式 A：git clone
git clone <仓库地址> D:\deer-flow\src

# 方式 B：从别处拷贝整份目录（含 .git 与否均可，部署不需要 .git）
Copy-Item -Path '<源路径>' -Destination 'D:\deer-flow\src' -Recurse
```

**验证**：

```powershell
Test-Path D:\deer-flow\src\config.example.yaml    # 必须 True，init-config.ps1 依赖它
Test-Path D:\deer-flow\src\backend\pyproject.toml # 必须 True
Test-Path D:\deer-flow\src\frontend\package.json  # 必须 True
```

> ⚠️ 从 macOS / Linux 拷贝代码时，不要用会携带扩展属性的方式（macOS 的 `tar` 默认会把
> `com.apple.provenance` 之类的 xattr 打包成 `._*` 边车文件，解到 NTFS 上会变成真文件，
> 其中落在 `backend\...\migrations\versions\` 下的会直接让 Gateway 启动崩溃）。
> 用仓库自带的 `scripts/windows-remote/sync-to-windows.sh` 最稳，它已用
> `tar --no-xattrs --no-mac-metadata` 处理过。

---

### 步骤 3：安装工具链（解压 Node、下载 uv、装 pnpm）

```powershell
& 'D:\deer-flow\src\scripts\windows\install-toolchain.ps1'
```

**预期输出**：依次下载 Node 22 / uv / pnpm（走国内镜像），最后打印工具链版本与目录。

**验证**（三条都要有版本号输出）：

```powershell
& 'D:\deer-flow\tools\node\node.exe' --version   # v22.23.2
& 'D:\deer-flow\tools\uv\uv.exe'     --version   # uv 0.12.13
& 'D:\deer-flow\tools\node\pnpm.cmd' --version   # 10.26.2
```

> ⚠️ **这一步是部署里唯一会改动系统级配置的动作**：解压 Node 到磁盘、写用户级 PATH 与环境变量
> （`NPM_CONFIG_REGISTRY` / `NPM_CONFIG_CACHE` / `UV_CACHE_DIR` / `UV_PYTHON_INSTALL_DIR`）。
> 因此 `deploy.ps1` 只检测、**不会**代跑 —— 需要运维明确知情后执行。
>
> ⚠️ **系统 PATH 里若已有旧版 Node**（目标机上 `C:\Program Files\nodejs` 是 Node 16），
> 直接敲 `node` 会用旧版。工具链脚本把所有部署脚本都做了进程级 PATH 前置，
> 所以**走脚本没问题**；手工敲命令验证时请用上面的完整路径。
>
> ℹ️ `DEER_FLOW_HOME` 由 **`start.ps1` 在 Gateway 启动命令行里显式钉死**为
> `<root>\data`（`src\.env` 里也有一份，但 `.env` 的发现依赖 cwd，不能作为唯一依据）。
> 工具链脚本刻意不设置该变量。一旦它指向别处，SQLite 与管理员凭据文件会落到
> 非预期目录，排查起来非常隐蔽 —— 详见「路径与配置」一节。

---

### 步骤 4：生成配置

```powershell
& 'D:\deer-flow\src\scripts\windows\init-config.ps1'
```

**预期输出**：

```
==> 生成 DeerFlow 配置
         部署根目录 : D:\deer-flow
         代码目录   : D:\deer-flow\src
         数据目录   : D:\deer-flow\data
         config.yaml: D:\deer-flow\src\config.yaml
         .env       : D:\deer-flow\src\.env
  [ OK ] config.yaml 已生成（<哈希>）
  [ OK ] .env 已生成（<哈希>）
==> 配置摘要
  config.yaml : D:\deer-flow\src\config.yaml
    database.backend          : sqlite
    database.sqlite_dir       : D:\deer-flow\data
    auth.allowed_email_domains: sz-jlc.com
    require_admin_approval    : true（审批在管理后台手动完成）
    approval_email.enabled    : true（schema 强制；SMTP 指向回环地址，投递失败不影响审批）
  .env        : D:\deer-flow\src\.env
    DEER_FLOW_PROJECT_ROOT         = D:\deer-flow\src
    DEER_FLOW_HOME                 = D:\deer-flow\data
    DEER_FLOW_INTERNAL_AUTH_TOKEN  = 1a2b...（64 字符）
    BETTER_AUTH_SECRET             = 9f8e...（64 字符）
  [ OK ] 配置生成完成
```

（密钥为 32 随机字节的 hex 编码，因此固定 64 字符。上例中的前缀只是示意，实际每次生成都不同。
「已存在则跳过」的分支实测输出形如：

```
  [ OK ] config.yaml 已存在，跳过（5480e0bf442d...）
  [ OK ] .env 已存在，跳过（d92631c0229b...）
  如需重新生成，请加 -Force（旧文件会先备份为 *.bak.<时间戳>）。
```）

> **幂等**：两个文件已存在时直接跳过、**不做任何改动**。要重新生成加 `-Force`，
> 覆盖前会自动备份为 `<文件名>.bak.<yyyyMMdd-HHmmss>`。
>
> 需要改邮箱白名单：`init-config.ps1 -AllowedEmailDomains sz-jlc.com,example.com -Force`

**验证**：装完依赖后（步骤 5）用后端自己加载一次配置，确认 `config.yaml` 与 `.env` 都没有语法/缺项问题：

```powershell
Set-Location 'D:\deer-flow\src\backend'
$env:PYTHONPATH = 'D:\deer-flow\src\backend'
& '.\.venv\Scripts\python.exe' -c "from deerflow.config import get_app_config; cfg = get_app_config(); print(type(cfg).__name__)"
```

**实测输出**：`AppConfig`（退出码 0）。

打印出 `AppConfig` 说明配置**完整加载并通过了后端校验** —— 包括 `config.yaml` 里 50 个 `$VAR` 引用
全部解析成功、`require_admin_approval` / `approval_email` 的组合校验通过。
配置有缺项时这里会直接抛异常并明确指出缺哪个变量。

> ⚠️ 注意上面这条命令**刻意不含内层引号**。经 SSH 类的多层转义传输时内层引号会被剥掉
> （`print("OK")` 变成 `print(OK)` 报 `NameError`）。在服务器本地手敲则不受影响。

---

### 步骤 5：装依赖 + 构建前端

```powershell
& 'D:\deer-flow\src\scripts\windows\install.ps1'
```

**预期输出**（冷装约 4 分钟；已装好时每步输出 `[SKIP]`，约 1 秒结束）：

```
==> DeerFlow 依赖安装与前端构建
         部署根目录 : D:\deer-flow
         代码目录   : D:\deer-flow\src
         日志目录   : D:\deer-flow\logs

==> 前置检查 1/4：部署目录
  [ OK ] 部署根目录：D:\deer-flow
  [ OK ] 后端目录：D:\deer-flow\src\backend
  [ OK ] 前端目录：D:\deer-flow\src\frontend
  [ OK ] 后端 pyproject.toml 存在
  [ OK ] 前端 package.json 存在
  [ OK ] 前端 pnpm-lock.yaml 存在

==> 前置检查 2/4：工具链
  [ OK ] node     v22.23.2
  [ OK ] pnpm     10.26.2
  [ OK ] uv       uv 0.12.13 ...
  [ OK ] py -3.12 Python 3.12.0

==> 前置检查 3/4：磁盘空间
  [ OK ] D: 可用 164.6 GB / 495 GB

==> 前置检查 4/4：构建环境变量
  [ OK ] 构建将只设 SKIP_ENV_VALIDATION=1

==> 后端依赖安装 (uv sync)          ← 已装好时这里是 [SKIP]
==> 前端依赖安装 (pnpm install)      ← 已装好时这里是 [SKIP]
==> 前端构建 (pnpm build)            ← 已有 BUILD_ID 时这里是 [SKIP]

==> 耗时汇总
  [ OK ] 后端依赖  ——  成功  (86.3s)
  [ OK ] 前端依赖  ——  成功  (54.1s)
  [ OK ] 前端构建  ——  成功  (62.7s)

==> 产物校验
  [ OK ] 后端虚拟环境：D:\deer-flow\src\backend\.venv\Scripts\python.exe
  [ OK ] 前端依赖：D:\deer-flow\src\frontend\node_modules\.modules.yaml
  [ OK ] 前端构建产物：D:\deer-flow\src\frontend\.next\BUILD_ID

  依赖安装与前端构建完成
```

**已装好时的幂等输出（实测，约 1 秒结束）**：

```
  [SKIP] .venv 已存在且可用（D:\deer-flow\src\backend\.venv\Scripts\python.exe），跳过后端依赖安装
  [SKIP] node_modules 已存在且完整，跳过前端依赖安装（D:\deer-flow\src\frontend\node_modules）
  [SKIP] .next\BUILD_ID 已存在（TfYlexLUbzjoDyFrnxkP1），跳过前端构建

==> 耗时汇总
  [SKIP] 后端依赖  ——  跳过  (0.0s)
  [SKIP] 前端依赖  ——  跳过  (0.0s)
  [SKIP] 前端构建  ——  跳过  (0.0s)
```

三个步骤的日志分别落在（**每次真正执行时会重写**，避免混入上几次的输出）：

```
D:\deer-flow\logs\install-backend.log
D:\deer-flow\logs\install-frontend.log
D:\deer-flow\logs\install-build.log
```

> 只想重跑构建（改了前端代码后最常用）：
> `& 'D:\deer-flow\src\scripts\windows\install.ps1' -SkipBackend -SkipFrontend`
>
> ⚠️ 构建时**不能**设 `NEXT_PUBLIC_STATIC_WEBSITE_ONLY`（会静默禁用所有后端功能）
> 也**不能**设 `NEXT_CONFIG_BUILD_OUTPUT=standalone`。脚本已刻意避开这两个变量，请勿手工加回。

---

### 步骤 6：一键部署（等价于 步骤 4 + 5 + 启动 + 自检）

从零开始也可以直接跑这一条，它会按顺序完成配置、依赖、启动与自检：

```powershell
& 'D:\deer-flow\src\scripts\windows\deploy.ps1'
```

**预期输出（实测节选）**：

```
==> 阶段 0/5 · 环境检查
  [ OK ] 当前以管理员身份运行
  [ OK ] 代码目录: D:\deer-flow\src
  [ OK ] 配置模板: config.example.yaml
  [ OK ] 部署根所在盘（D:）可用空间 164.6 GB
  [ OK ] Node 可用: v22.23.2
  [ OK ] uv 可用: uv 0.12.13 ...
  [ OK ] pnpm 可用: 10.26.2
阶段 0/5 环境检查通过

==> 阶段 1/5 · 生成配置（config.yaml + .env）
  [ OK ] 配置已存在且校验通过（本次未改动）
阶段 1/5 配置就绪

==> 阶段 2/5 · 安装依赖与构建前端
  [SKIP] .venv 已存在且可用，跳过后端依赖安装
  [SKIP] node_modules 已存在且完整，跳过前端依赖安装
  [SKIP] .next\BUILD_ID 已存在（TfYlexLUbzjoDyFrnxkP1），跳过前端构建
  [ OK ] 依赖安装与构建完成（1s）
阶段 2/5 依赖与构建完成 (1s)

==> 阶段 3/5 · 启动服务（已在运行，跳过）
  [ OK ] Gateway 运行中（PID 20976）
  [ OK ] Frontend 运行中（PID 19272）
阶段 3/5 服务已在运行，跳过启动

==> 阶段 4/5 · 自检
  [ OK ] Gateway 端口 8001 监听中
  [ OK ] Frontend 端口 3000 监听中
  [ OK ] Gateway /health -> 200
         {"status":"healthy","service":"deer-flow-gateway"}
  [ OK ] Frontend / -> 200
  [ OK ] Frontend /api/* -> Gateway 链路已通（401，需登录属预期）
  [ OK ] SQLite 数据文件已生成（3 个：主库 + WAL/SHM）
阶段 4/5 自检通过

==> 阶段 5/5 · 开机自启（默认不注册）
         需要时显式执行（需管理员权限）: & 'D:\deer-flow\src\scripts\windows\register-autostart.ps1'
阶段 5/5 自启状态: 未注册（默认）

==========================================
  DeerFlow 部署完成
==========================================

  内网访问地址（其它机器用这个）:
    http://192.168.2.10:3000

  后端健康检查:
    http://192.168.2.10:8001/health
```

**参数**：

| 参数 | 作用 | 何时用 |
|---|---|---|
| `-RootDir <路径>` | 部署根，默认 `D:\deer-flow` | 部署根不在默认位置时 |
| `-SkipToolchain` | 跳过工具链就绪检测 | 工具链由镜像预置、已确认可用 |
| `-SkipBuild` | 跳过前端构建 | ⚠️ 仅当 `.next\BUILD_ID` 已存在；否则启动会失败 |
| `-NoStart` | 只装不启 | 先装好、稍后统一启动 |
| `-RegisterAutostart` | 部署后注册开机自启（需管理员） | 希望开机自动拉起服务 |
| `-SkipAutostart` | 连「请手动注册自启」的提示都省掉 | 自动化流水线 |

**行为约定**：

- **每阶段失败即停止**，并打印该阶段的手动补救命令（可复制），退出码 1。已完成的阶段不回滚，
  修好问题后重跑即可，已就绪的步骤会自动跳过。
- **幂等**：重复执行不会打断正在提供的服务。两个服务都在运行时会跳过启动（而不是先停再起）。
- **工具链缺失时只提示、不代装**：会明确告诉你先跑 `install-toolchain.ps1`，然后以退出码 1 中止。
- **默认不注册开机自启**：注册计划任务影响面较大，需要显式加 `-RegisterAutostart`。

部署日志（只记里程碑，不含密码）：`D:\deer-flow\logs\deploy.log`

---

### 步骤 7：初始化管理员账号（**必做**）

本部署启用了登录认证 + **管理员审批**：新用户注册后是 `pending` 状态，必须由管理员在后台审批才能登录。
而系统里还没有管理员时无从审批 —— 所以这是**部署完成后必须做的一步**，不是可选优化。

```powershell
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -Email admin@sz-jlc.com
```

**预期输出**（密码只在控制台明文显示**一次**，且**不写入日志**）：

```
==> 检查前置条件
  [ OK ] config.yaml: D:\deer-flow\src\config.yaml
  [ OK ] 后端虚拟环境已就绪
  [ OK ] uv: D:\deer-flow\tools\uv\uv.exe

==> 解析凭据文件位置
  [ OK ] base_dir: D:\deer-flow\data
         凭据文件: D:\deer-flow\data\admin_initial_credentials.txt

==========================================================
  管理员凭据（仅本次显示，请立即记录）
==========================================================

  登录地址 : http://192.168.2.10:3000/login
  账号     : admin@sz-jlc.com
  密码     : <初始密码>

  凭据文件 : D:\deer-flow\data\admin_initial_credentials.txt
  建议     : 记录完密码后删除该文件；
             登录后立即在「设置 - 账号」修改密码。

  注册审批 : 新用户注册后是 pending 状态，需在管理后台审批后才能登录。
```

> ⚠️ **邮箱必须落在 `auth.allowed_email_domains` 白名单内**（默认 `sz-jlc.com`），
> 否则脚本会提前给出警告。
>
> ⚠️ **重复执行会重置密码**：旧密码立即失效，已签发的登录会话（token）全部作废。
> 这正是「忘记管理员密码」的恢复手段。

**验证**：浏览器打开 `http://<服务器IP>:3000/login`，用上面的邮箱与密码登录成功即可。

其他用法：

```powershell
# 只看已有凭据，不做任何写操作（不重置、不创建、不改库）
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -ShowOnly

# 不指定邮箱：重置库里第一个管理员（库为空时会报错，要求补 -Email）
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1'
```

> ℹ️ `-ShowOnly` 适合「密码已经拿到了，只想再看一眼」。它会打印凭据文件里的明文密码，
> **不要在有人旁观或屏幕被投屏时执行**；执行完输出会留在终端里，记得清屏。
>
> ℹ️ 它**不需要服务停止**：`-ShowOnly` 只读凭据文件（实测在服务运行中执行，退出码 0）。
> 创建 / 重置分支会直接写 SQLite，建议在低峰期执行，避免与大量并发写入撞在一起。

> ℹ️ 凭据文件的正确位置是 **`D:\deer-flow\data\admin_initial_credentials.txt`**
> （脚本会打印它解析到的 `base_dir`，实测为 `D:\deer-flow\data`）。
> 如果发现 `D:\deer-flow\admin_initial_credentials.txt`（部署根下、不在 `data\` 里），
> 那是早期版本遗留的旧文件：早期 `install-toolchain.ps1` 会把 `DEER_FLOW_HOME` 设成部署根，
> 导致运行状态落到部署根。**该问题已修复**（工具链脚本不再设置 `DEER_FLOW_HOME`，
> 改由 `start.ps1` 显式钉死为 `data\`）。
>
> ⚠️ 删除旧文件前，先确认它的密码**已失效**（拿它登录应返回
> `401 invalid_credentials`），且当前有效密码在 `data\` 那份里（登录返回 `200`）。
> 两份文件的密码通常不同，不能只看文件名就删。

---

### 步骤 8：开机自启（可选，需管理员权限）

```powershell
& 'D:\deer-flow\src\scripts\windows\register-autostart.ps1'
```

**预期输出（实测节选）**：

```
==> 配置防火墙
  [ OK ] 防火墙规则已存在，跳过创建: DeerFlow Frontend (Port 3000)
  [ OK ] 防火墙规则已存在，跳过创建: DeerFlow Gateway (Port 8001)

==> 注册开机自启计划任务
  [ OK ] 计划任务已注册: DeerFlow Auto Start
         触发器: 开机时（延迟 30 秒）
         账户  : GDW-WIN10\gongdewei（S4U，最高权限，不需登录）

==> 回读校验
  任务名     : DeerFlow Auto Start
  状态       : Ready
  触发延迟   : PT30S
  运行账户   : gongdewei / LogonType=S4U / RunLevel=Highest
  动作       : powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\start.ps1" -RootDir "D:\deer-flow"

  DeerFlow 开机自启已注册
```

**验证**：

```powershell
Get-ScheduledTask -TaskName 'DeerFlow Auto Start' | Format-List TaskName,State,Triggers,Actions
(Get-ScheduledTask -TaskName 'DeerFlow Auto Start').Triggers[0].Delay   # PT30S

# 手动触发一次（会先停掉现有服务再重新拉起，约 1-2 分钟）
Start-ScheduledTask -TaskName 'DeerFlow Auto Start'
& 'D:\deer-flow\src\scripts\windows\status.ps1'    # 退出码 0 且两服务「运行中」即成功
```

> ℹ️ 为什么用**计划任务**而不是 Windows 服务：`uvicorn` / `node` 是普通用户态进程，
> 不实现 Windows 服务控制协议（SCM），用 `New-Service` 注册出来的「服务」永远无法启动成功。
>
> ℹ️ 运行账户用**当前管理员账户**（S4U，不用存密码）：服务运行时要读用户级环境变量
> （`UV_CACHE_DIR` 等）并访问 `D:\deer-flow\cache`。改成 SYSTEM 会让 uv/pnpm 退回默认缓存位置、
> 重复下载依赖。前提是该账户在本机**至少登录过一次**。
>
> ⚠️ 自启只保证「开机拉起」，**不负责进程崩溃后的守护**。计划任务里的自动重试
> （失败后 1 分钟重试、最多 3 次）只覆盖「`start.ps1` 自身退出码非 0」这一种情况。

取消自启：

```powershell
& 'D:\deer-flow\src\scripts\windows\unregister-autostart.ps1'
# 彻底清理（连防火墙规则一起删，准备卸载部署时用）
& 'D:\deer-flow\src\scripts\windows\unregister-autostart.ps1' -RemoveFirewall
```

> 默认**不删**防火墙规则：规则名与 `start.ps1` 共用，删了会立刻切断正在服务的内网访问，
> 而 `start.ps1` 下次启动又会重建它。

---

### 步骤 9：填入模型配置（否则 LLM 功能不可用）

生成的 `config.yaml` 里 `models` 段**默认为空**（全部是注释示例），页面会显示「未配置」。

```
D:\deer-flow\src\config.yaml     ← models 段（当前为空，需填入实际模型）
D:\deer-flow\src\.env            ← 填 *_API_KEY
```

改完后重启服务生效：

```powershell
& 'D:\deer-flow\src\scripts\windows\stop.ps1'
& 'D:\deer-flow\src\scripts\windows\start.ps1'
```

> ℹ️ `config.yaml` 里的 `$VAR` 引用（如 `$DEEPSEEK_API_KEY`）会在启动时从环境变量解析。
> `init-config.ps1` 已把**所有**被引用的变量名补进 `.env` 并置空 —— 补上真实 key 即可，
> **不必改 `config.yaml`**。缺失的变量会让后端启动直接失败（硬校验），所以不要手工删 `.env` 里的键。

---

### 最终验收清单

| 检查项 | 命令 / 方式 | 期望 |
|---|---|---|
| 前端本机可达 | 在服务器上 `curl.exe -o NUL -w "%{http_code}" http://127.0.0.1:3000/` | `200` 或 `307` |
| 后端健康 | `curl.exe http://127.0.0.1:8001/health` | `{"status":"healthy",...}` |
| **内网可达（核心目标）** | 在**另一台内网机器**浏览器打开 `http://192.168.2.10:3000` | 出现登录 / 注册页 |
| 前端→后端链路 | 浏览器访问任意需要登录的接口，或 `status.ps1` 的自检项 | 返回 401（需登录），**不是** 404/502 |
| 管理员登录 | 步骤 7 的邮箱 + 初始密码 | 登录成功 |
| 数据持久化 | `Test-Path D:\deer-flow\data\deerflow.db` | `True` |
| 重启后自启 | 重启服务器，等 2 分钟后在别的机器打开页面 | 页面正常 |

---

## 三、架构说明

### 3.1 服务拓扑

```
浏览器（内网其它机器）
        │  http://<服务器IP>:3000
        ▼
┌─────────────────────────┐
│ Next.js 前端  :3000     │   ← 内网唯一入口
│  · 页面渲染             │
│  · /api/* 反向代理      │   next.config.js 的 rewrites
└───────────┬─────────────┘
            │  http://127.0.0.1:8001/api/*
            ▼
┌─────────────────────────┐
│ FastAPI Gateway :8001   │   ← uvicorn + agent 运行时（LLM 调用、工具、会话）
└───────────┬─────────────┘
            │
            ▼
      SQLite  D:\deer-flow\data\deerflow.db
```

### 3.2 为什么没有 nginx

**Next.js 自带的 `rewrites` 已经承担了反向代理的角色**，再放一层 nginx 只会多一个需要维护、
需要排障的组件：

- 前端与后端在同一台机器上，`/api/*` 只需转发到 `127.0.0.1:8001`，没有负载均衡、灰度、
  多后端的诉求；
- 只有两个服务、三个端口，不存在「路由规则复杂到必须集中管理」的情况；
- 内网明文 HTTP，不需要 nginx 做 TLS 终止。

`frontend\next.config.js` 里的规则（按顺序匹配）：

| 前端路径 | 转发目标 |
|---|---|
| `/api/langgraph`、`/api/langgraph/:path*` | `http://127.0.0.1:8001/api`、`/api/:path*` |
| `/api/agents`、`/api/agents/:path*` | `http://127.0.0.1:8001/api/agents`、`.../:path*` |
| `/api/skills`、`/api/skills/:path*` | `http://127.0.0.1:8001/api/skills`、`.../:path*` |
| `/api/:path*`（兜底：models、threads、memory、mcp、artifacts、uploads…） | `http://127.0.0.1:8001/api/:path*` |

所以**浏览器只需要访问 3000**，8001 不必对外开放（脚本仍为它建了限本网段的入站规则，
便于排障时直接 curl `/health`）。

### 3.3 端口清单

| 端口 | 服务 | 开放范围 | 说明 |
|---|---|---|---|
| **3000** | Next.js 前端 | 内网（防火墙限 `LocalSubnet`） | **内网访问入口** |
| **8001** | FastAPI Gateway | 内网（防火墙限 `LocalSubnet`） | 一般不直接访问，排障时可用 `/health` |
| 22 | OpenSSH Server | 防火墙规则 | 远程管理 |

防火墙规则（`start.ps1` 与 `register-autostart.ps1` 都会幂等创建）：

```
DeerFlow Frontend (Port 3000)   Inbound  TCP  Allow  RemoteAddress=LocalSubnet  Profile=Any
DeerFlow Gateway  (Port 8001)   Inbound  TCP  Allow  RemoteAddress=LocalSubnet  Profile=Any
OpenSSH Server (sshd) Port 22   Inbound  TCP  Allow
```

> ⚠️ 目标机网卡在 Windows 防火墙里被判为**公用网络（Public）**，Public 配置下入站默认全拦。
> **这两条规则是内网能访问的前提，不是可选项** —— 没有它们，服务器本机 curl 一切正常，
> 内网其它机器全部超时。

### 3.4 数据位置（**重要**）

| 内容 | 路径 |
|---|---|
| SQLite 数据库 | `D:\deer-flow\data\deerflow.db` |
| **WAL 日志（数据实际在这里）** | `D:\deer-flow\data\deerflow.db-wal` |
| 共享内存索引 | `D:\deer-flow\data\deerflow.db-shm` |
| 管理员初始凭据 | `D:\deer-flow\data\admin_initial_credentials.txt` |
| 配置 | `D:\deer-flow\src\config.yaml` |
| 密钥 / 环境变量 | `D:\deer-flow\src\.env` |
| 服务日志 | `D:\deer-flow\logs\{gateway,frontend}.log` |

**数据库用的是 WAL（Write-Ahead Logging）模式，实测确认**：

```
deerflow.db       = 4 096 B      ← 主库，很小，这是正常的
deerflow.db-wal   = 671 592 B    ← 数据实际在这里
deerflow.db-shm   = 32 768 B
journal_mode      = wal
```

`deerflow.db` 只有 4 KB **不代表数据丢了** —— 表结构与数据都在 `-wal` 里，SQLite 读取时会自动合并。
**但这直接决定了备份方式**：见 §4.5。

### 3.5 关键环境变量

| 变量 | 值 | 来源 | 说明 |
|---|---|---|---|
| `DEER_FLOW_HOME` | `D:\deer-flow\data` | `src\.env` | **唯一权威来源**。决定 SQLite 与凭据文件的位置 |
| `DEER_FLOW_PROJECT_ROOT` | `D:\deer-flow\src` | `src\.env` | 代码根 |
| `DEER_FLOW_INTERNAL_AUTH_TOKEN` | 随机 32 字节 hex | `src\.env` | 内部鉴权令牌 |
| `BETTER_AUTH_SECRET` | 随机 32 字节 hex | `src\.env` | 会话签名密钥 |
| `NPM_CONFIG_REGISTRY` | `https://registry.npmmirror.com` | 用户级环境变量 | 工具链脚本设置 |
| `NPM_CONFIG_CACHE` | `D:\deer-flow\cache\npm` | 用户级环境变量 | 避开 C 盘 |
| `UV_CACHE_DIR` | `D:\deer-flow\cache\uv` | 用户级环境变量 | 避开 C 盘 |
| `UV_PYTHON_INSTALL_DIR` | `D:\deer-flow\cache\uv-python` | 用户级环境变量 | 避开 C 盘 |

**`DEER_FLOW_HOME` 的权威来源是服务启动处，不是 `.env`。**

> ⚠️ **本节曾写成「唯一权威来源是 `src\.env`」，那是错的，已更正。**
> 早期版本还建议运维**删除**用户级的 `DEER_FLOW_HOME` —— **千万不要照做**，
> 那个变量是承重的，删掉才会真的把数据写错地方。原因见下。

`base_dir`（凭据文件、`memory.json`、`users\`、`agents\` 的落脚点）解析链路：

```
get_paths().base_dir → runtime_home() → os.getenv("DEER_FLOW_HOME")
                                      ↘ 缺失时 project_root() / ".deer-flow"
```

关键在于「缺失时」那一支**依赖 cwd**：`DEER_FLOW_HOME` 可能来自 `app_config.py`
顶层的 `load_dotenv()`，而 dotenv 是**从 cwd 逐级向上**找 `.env` 的。
从部署根启动时够不到 `src\.env`（向上只上溯到 `D:\`），变量就「缺失」，
`base_dir` 静默落到 `<root>\.deer-flow`。实测：

| cwd | `base_dir` | |
|---|---|---|
| `src\backend` | `D:\deer-flow\data` | ✓ |
| `src` | `D:\deer-flow\data` | ✓ |
| `D:\deer-flow`（部署根） | `D:\deer-flow\.deer-flow` | ✗ **静默错位** |

错位不报错，而是**脑裂**：`config.yaml` 仍把 SQLite 指向 `data\`，
凭据/记忆/用户数据却写到 `.deer-flow\`。

因此：

- `install-toolchain.ps1` **刻意不设置**这个变量。它早期版本会把 `DEER_FLOW_HOME`
  设成部署根，导致状态落到部署根而非 `data\`。已修复，不要再「顺手加回去」。
- **`start.ps1` 会在 Gateway 启动命令行里显式 `set DEER_FLOW_HOME=<root>\data`**
  （同时设 `DEER_FLOW_PROJECT_ROOT`）。这是让 `base_dir` 与 cwd 无关的保证，
  **不要删掉这两行**。Gateway 因此不受 cwd 与用户级变量影响。
- **用户级 `DEER_FLOW_HOME` 保留即可，不要删除。** 目标机当前 User 级值为
  `D:\deer-flow\data`，与 `data\` 一致，是**有意设置**的锚点 —— 它正是让
  `base_dir` 变成 cwd 无关的那个变量。删掉后，任何从部署根启动的进程都会写错目录。
  （加固后 Gateway 已自行钉死该值，故它不再是唯一依赖；但其它调用方仍受 cwd 影响。）
- 若该变量**指向别处**（例如被手工改成部署根），那才是问题 —— 会表现为
  「数据库突然空了」「审批记录不见了」。正确做法是**改成 `D:\deer-flow\data`**，
  而不是删掉：

```powershell
# 检查当前值
[Environment]::GetEnvironmentVariable('DEER_FLOW_HOME','User')

# 仅当它不等于 <root>\data 时才需要改（正常部署下无需执行）
[Environment]::SetEnvironmentVariable('DEER_FLOW_HOME','D:\deer-flow\data','User')
```

- ⚠️ 改完**必须新开一个 PowerShell 会话**，或重启服务，环境变量才会重新加载。

- `DEER_FLOW_*` / `BETTER_AUTH_*` 与其它被 `config.yaml` 以 `$VAR` 引用的变量不同：
  **前者缺失时后端会显式报错**（不会被补成空值），因为缺了它们系统根本无法安全工作。

---

## 四、运维手册

### 4.1 启停与状态

```powershell
# 启动（幂等：先清理遗留进程，再启动两个服务，等待就绪）
& 'D:\deer-flow\src\scripts\windows\start.ps1'

# 停止（幂等：没在跑时正常返回，退出码 0）
& 'D:\deer-flow\src\scripts\windows\stop.ps1'

# 查看状态（退出码：0=至少一个在运行，1=两个都没运行，可当条件用）
& 'D:\deer-flow\src\scripts\windows\status.ps1'

# 顺便看两个服务日志的末尾 40 行
& 'D:\deer-flow\src\scripts\windows\status.ps1' -Tail 40
```

`status.ps1` 输出示例（实测）：

```
==> DeerFlow 服务状态

  ── Gateway · REST API + agent runtime
     状态      : 运行中
     PID       : 17572
     来源      : PidFile
     端口      : 8001 监听中 (0.0.0.0)
     运行时长  : 00:06:47
     日志      : D:\deer-flow\logs\gateway.log
                 最后写入 2026-09-12 06:45:29，5.1 KB

  ── Frontend · Next.js
     状态      : 运行中
     PID       : 18596
     端口      : 3000 监听中 (::)

  内网访问: http://192.168.2.10:3000
  健康检查: http://192.168.2.10:8001/health

  两个服务均在运行（2/2）
```

**怎么看这些字段**：

- **状态**判据是「有没有本项目进程」，不是「端口是否被监听」。端口被别人的程序占用时会明确标注
  「端口被他人占用」，不会误报成「我们的服务在运行」。
- **运行时长 + 最后写入时间**一起看：进程活着但日志半小时没更新 = 服务卡住了。
- **来源**：`PidFile` 表示来自 `logs\run\*.pid`；`CommandLine` 表示 PID 文件失效、按命令行扫描到。

### 4.2 日志

| 日志 | 路径 | 内容 |
|---|---|---|
| Gateway | `D:\deer-flow\logs\gateway.log` | uvicorn + agent 运行时（含 LLM 调用、工具执行、异常栈） |
| Frontend | `D:\deer-flow\logs\frontend.log` | Next.js 输出（含 `/api/*` 代理错误） |
| 部署里程碑 | `D:\deer-flow\logs\deploy.log` | 每次 `deploy.ps1` 走到哪个阶段、何时失败 |
| 依赖安装 | `D:\deer-flow\logs\install-{backend,frontend,build}.log` | 三步安装/构建的完整输出 |
| 管理员操作 | `D:\deer-flow\logs\admin-init.log` | 管理员初始化/重置（**不含密码**） |

**实时查看**（服务运行时也能看，不会被文件锁挡住）：

```powershell
Get-Content D:\deer-flow\logs\gateway.log -Wait -Tail 50     # Ctrl+C 退出
Get-Content D:\deer-flow\logs\frontend.log -Tail 100
```

> ℹ️ **为什么能读正在运行的服务日志**：日志是被服务以追加方式持有的。
> 脚本用共享模式 `ReadWrite|Delete` 打开，因此不阻塞读取。但如果你用某些工具读会报
> 「文件被另一个进程占用」，换成 `Get-Content` 或 `status.ps1 -Tail N` 即可。
>
> ℹ️ 日志文件**不会自动轮转**。`gateway.log` 里每次启动会重写运行头，但内容持续追加。
> 长期运行发现日志过大时，停服后直接删除或改名即可（下次启动会重建）。

### 4.3 升级流程

```powershell
# 1) 停服（必须先停，避免 .next / .venv 在构建中被占用）
& 'D:\deer-flow\src\scripts\windows\stop.ps1'

# 2) 备份（重要，见 §4.5）
#    把 D:\deer-flow\data\ 与 src\config.yaml、src\.env 拷到别处

# 3) 放新代码
#    覆盖 D:\deer-flow\src\ 下的代码（保留 config.yaml / .env 不被覆盖）

# 4) 重新装依赖 + 构建（已就绪的步骤会自动跳过）
& 'D:\deer-flow\src\scripts\windows\install.ps1'

# 5) 启动
& 'D:\deer-flow\src\scripts\windows\start.ps1'

# 6) 验证
& 'D:\deer-flow\src\scripts\windows\status.ps1' -Tail 40
```

> ⚠️ **`config.yaml` 与 `.env` 不要被新代码覆盖**。大多数同步/覆盖方式会连它们一起刷掉；
> 覆盖前先备份，覆盖后确认这两个文件还在、内容未变。
>
> 只改了前端且依赖没变时，可只重跑构建：
> `& '...\install.ps1' -SkipBackend -SkipFrontend`
>
> 全新代码库首次部署（含依赖变更）：直接 `& '...\deploy.ps1'`，它会按顺序做完
> 配置 → 依赖 → 启动 → 自检，中途失败会停在那一步并给出补救命令。

### 4.4 常用运维命令速查

```powershell
# 服务
& 'D:\deer-flow\src\scripts\windows\start.ps1'                    # 启动
& 'D:\deer-flow\src\scripts\windows\stop.ps1'                     # 停止
& 'D:\deer-flow\src\scripts\windows\status.ps1' -Tail 40          # 状态 + 日志尾部

# 部署
& 'D:\deer-flow\src\scripts\windows\deploy.ps1'                   # 一键（幂等）
& 'D:\deer-flow\src\scripts\windows\deploy.ps1' -NoStart          # 只装不启
& 'D:\deer-flow\src\scripts\windows\install.ps1'                  # 只装依赖 + 构建

# 管理员
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -Email admin@sz-jlc.com   # 建/重置
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -ShowOnly                 # 只看凭据

# 自启
& 'D:\deer-flow\src\scripts\windows\register-autostart.ps1'       # 注册
& 'D:\deer-flow\src\scripts\windows\unregister-autostart.ps1'     # 注销

# 排查
Get-NetFirewallRule -DisplayName 'DeerFlow*' | Select-Object DisplayName,Enabled,Direction,Action
Get-ScheduledTask -TaskName 'DeerFlow Auto Start' | Format-List TaskName,State
Get-NetTCPConnection -LocalPort 3000,8001 -State Listen | Select-Object LocalPort,OwningProcess
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object IPAddress,InterfaceAlias
```

### 4.5 备份与还原

**要备份的东西**：

| 优先级 | 内容 | 路径 |
|---|---|---|
| **P0** | **数据（SQLite）** | `D:\deer-flow\data\` （**整个目录**，含 `-wal` / `-shm`） |
| **P0** | 密钥与环境变量 | `D:\deer-flow\src\.env` |
| **P0** | 业务配置 | `D:\deer-flow\src\config.yaml` |
| P1 | 自定义模型 / 技能 / agent 定义 | 如 `src\` 下有自行修改的文件 |
| P2 | 日志 | `D:\deer-flow\logs\`（排障用，可不备） |

#### 推荐做法：停服后整体拷贝

```powershell
# 1) 停服 —— 绕开 WAL 一致性问题的唯一简单可靠办法
& 'D:\deer-flow\src\scripts\windows\stop.ps1'

# 2) 拷贝到备份位置（示例：D:\deer-flow-backup\20260912）
$stamp  = Get-Date -Format 'yyyyMMdd-HHmmss'
$dest   = "D:\deer-flow-backup\$stamp"
New-Item -ItemType Directory -Force -Path $dest | Out-Null

Copy-Item 'D:\deer-flow\data'             -Destination $dest -Recurse
Copy-Item 'D:\deer-flow\src\config.yaml'  -Destination $dest
Copy-Item 'D:\deer-flow\src\.env'         -Destination $dest

# 3) 校验
Get-ChildItem $dest -Recurse -File | Select-Object FullName, Length

# 4) 启回来
& 'D:\deer-flow\src\scripts\windows\start.ps1'
```

#### ⚠️ 为什么不能只拷 `deerflow.db`

实测证据（在目标机上做的对照实验）：

```powershell
# 服务运行中，data\ 目录的实际状态：
deerflow.db      =   4 096 B      ← 主库，只有 4 KB
deerflow.db-wal  = 671 592 B      ← 数据实际在这里
```

把**只有主库**的文件拷成一份独立的数据库再打开：

```
db path      : ...\only-main.db
  (main)     exists=True  size=4096
  -wal       exists=False           ← 没有 WAL
journal_mode : wal
tables       : 0                    ← 表结构全部丢失
```

**这张库是空的**。因为 WAL 模式下，建表与写入先落 `-wal`，之后才在检查点时合并回主库。
漏掉 `-wal` 就漏掉了几乎全部数据。

**因此**：

- 备份必须**整个 `data\` 目录一起拷**（`deerflow.db` + `deerflow.db-wal` + `deerflow.db-shm`），
  或先停服再拷；
- **服务运行中直接拷 `deerflow.db` 得到的是空库或残缺库**，而且不会报错 —— 这是最危险的一种
  「看起来成功了」的备份；
- 只执行 `PRAGMA wal_checkpoint(TRUNCATE)` **不足以**保证一致性：检查点之后服务还在继续写入，
  备份期间的新写入仍会落在 `-wal` 里。**要一致就停服**。

#### 还原

```powershell
& 'D:\deer-flow\src\scripts\windows\stop.ps1'

# 用备份覆盖
Copy-Item '<备份目录>\data'            -Destination 'D:\deer-flow' -Recurse -Force
Copy-Item '<备份目录>\config.yaml'     -Destination 'D:\deer-flow\src' -Force
Copy-Item '<备份目录>\.env'            -Destination 'D:\deer-flow\src' -Force

& 'D:\deer-flow\src\scripts\windows\start.ps1'
& 'D:\deer-flow\src\scripts\windows\status.ps1' -Tail 30
```

> ⚠️ `.env` 里的 `DEER_FLOW_INTERNAL_AUTH_TOKEN` / `BETTER_AUTH_SECRET` 与数据库里的会话数据相关。
> 只还原数据库、不还原 `.env`（或反之）会导致**已登录用户全部掉线**（需重新登录，不影响数据）。
> 要完整还原就两者一起。

---

## 五、安全说明

### 5.1 为什么启用登录认证

系统会调用 LLM（有成本）并能执行工具，**默认开放给整个内网意味着任何人都能消耗你的额度、
查看所有人的会话**。因此部署默认启用认证：

- `src\.env` 里**不设** `DEER_FLOW_AUTH_DISABLED`；
- 服务端口只放通 `LocalSubnet`（内网网段），不暴露到公网。

### 5.2 管理员审批流程（**读这一节能省掉一次「为什么注册了登不进去」的排查**）

`config.yaml` 的配置：

```yaml
auth:
  allowed_email_domains:
    - sz-jlc.com                  # 注册与改邮箱时的域名白名单
  local_registration:
    require_admin_approval: true  # 新注册用户需管理员审批
    approval_email:
      enabled: true
```

**流程**：

1. 新用户在登录页**注册**（邮箱域名必须在白名单内，默认 `sz-jlc.com`）；
2. 账号进入 **`pending`（待审批）** 状态 —— 此时**可以输密码但无法进入系统**；
3. 管理员用步骤 7 创建的账号登录，进入**后台「用户管理」**，找到该用户点**「通过」**；
4. 用户此时才能正常登录使用。

> ⚠️ **邮件发不出去是预期行为，审批不依赖邮件。**
>
> `config.yaml` 里的 `approval_email` 指向一个**回环 SMTP 桩**（`127.0.0.1:465`，凭据 `disabled`），
> 本机没有 SMTP 服务，所以投递一定失败（几毫秒内被拒绝），前端可能显示
> `approval_email_status=failed`。**这不影响审批** —— 批准动作先提交状态变更，之后才尽力发信，
> 失败被捕获并只做标记。
>
> 为什么不干脆关掉邮件：**这是上游的硬约束，做不到**。
> `backend/packages/harness/deerflow/config/auth_config.py` 里有校验：
>
> ```python
> if self.require_admin_approval and not self.approval_email.enabled:
>     raise ValueError("require_admin_approval requires approval_email.enabled=true")
> ```
>
> 即「启用审批」必然要求「启用邮件通知」。用回环地址是为了让投递立刻失败，
> 而不是让每个请求都卡在 SMTP 超时上（实测回环约 4 ms 返回，无网络超时）。

### 5.3 密钥文件位置与处理

| 文件 | 内容 | 处理建议 |
|---|---|---|
| `D:\deer-flow\src\.env` | `DEER_FLOW_INTERNAL_AUTH_TOKEN`、`BETTER_AUTH_SECRET`、各 provider 的 `*_API_KEY` | 视同密码。不要进版本库、不要贴聊天记录。备份要覆盖它 |
| `D:\deer-flow\data\admin_initial_credentials.txt` | 管理员初始邮箱与密码 | **登录成功后立即删除** |
| `D:\deer-flow\config.yaml` | 可能含模型 API Key（若直接写死而非用 `$VAR`） | 建议一律用 `$VAR` 引用 + `.env` 提供真值 |

处理建议：

```powershell
# 管理员首次登录并改密后，删除凭据文件
Remove-Item 'D:\deer-flow\data\admin_initial_credentials.txt' -Force

# 确认它真的没了
Test-Path 'D:\deer-flow\data\admin_initial_credentials.txt'   # False
```

`admin-init.ps1` 的日志里**不记录密码**（写日志时用 `****` 代替），密码只在控制台明文显示一次。

### 5.4 登录后建议立即做的事

1. **修改管理员密码**（首次登录时系统通常会引导修改）；
2. **删除凭据文件** `data\admin_initial_credentials.txt`；
3. 确认 `config.yaml` 的 `models` 段用的是 `$VAR` 引用而非明文 Key；
4. 检查防火墙规则仍是 `LocalSubnet`：

```powershell
Get-NetFirewallRule -DisplayName 'DeerFlow*' | ForEach-Object {
  $af = $_ | Get-NetFirewallAddressFilter
  "$($_.DisplayName) | Action=$($_.Action) | RemoteAddress=$($af.RemoteAddress) | Profile=$($_.Profile)"
}
# 实测输出：
# DeerFlow Frontend (Port 3000) | Action=Allow | RemoteAddress=LocalSubnet | Profile=Any
# DeerFlow Gateway (Port 8001)  | Action=Allow | RemoteAddress=LocalSubnet | Profile=Any
```

---

## 六、已知限制

| 限制 | 说明 | 影响 / 规避 |
|---|---|---|
| **单机 SQLite，不支持多节点** | 数据存在本地 `deerflow.db`，没有共享存储 | 只能单机部署。要多节点 / 高可用需切 PostgreSQL：`config.example.yaml` 的 `database` 段（`backend: postgres` + `connection_string`），并按 `backend\pyproject.toml` 的 `[project.optional-dependencies] postgres` 安装额外依赖 |
| **没有 HTTPS** | 内网明文 HTTP，传输内容可被同网段嗅探 | 仅限可信内网。需要加密时在前端加一层反向代理做 TLS 终止 |
| **升级需要停服** | 依赖安装 / 前端构建会占用 `.venv`、`.next`，且新代码与运行中进程不一致 | 停服 → 备份 → 放代码 → `install.ps1` → `start.ps1`（见 §4.3） |
| **`models` 段默认为空** | `init-config.ps1` 只生成骨架，不含具体模型配置 | **必须手工填入模型与 API Key**，否则 LLM 功能不可用、页面显示「未配置」。见步骤 9 |
| **自启不守护进程** | 计划任务只在开机时拉起；服务起来后崩溃不会被自动重启 | 计划任务的 3 次重试只覆盖 `start.ps1` 退出码非 0 的情况。需要守护请另配监控 |
| **日志不轮转** | `gateway.log` / `frontend.log` 持续追加 | 长期运行需人工清理（停服后删除即可） |
| **审批邮件发不出去** | SMTP 指向回环桩（上游 schema 强制要求 `enabled: true`） | 审批在后台手动完成，不依赖邮件。见 §5.2 |
| **凭据文件是明文** | `data\admin_initial_credentials.txt` 含明文初始密码 | 首次登录改密后立即删除。见 §5.3 |

---

## 七、常见问题（FAQ）

### Q1：内网机器打不开 `http://192.168.2.10:3000`，但服务器本机能打开

按顺序查：

```powershell
# 1) 服务是否在跑
& 'D:\deer-flow\src\scripts\windows\status.ps1'

# 2) 端口是否在监听、监听地址是 0.0.0.0 还是 127.0.0.1
Get-NetTCPConnection -LocalPort 3000 -State Listen | Select-Object LocalAddress,LocalPort,OwningProcess
#    LocalAddress 必须是 0.0.0.0 或 ::（不能是 127.0.0.1），否则只有本机能访问
#    实测正常值：3000 -> ::    8001 -> 0.0.0.0

# 3) 防火墙规则是否在（最常见的原因）
Get-NetFirewallRule -DisplayName 'DeerFlow*' | ForEach-Object {
  $af = $_ | Get-NetFirewallAddressFilter
  "$($_.DisplayName) | Enabled=$($_.Enabled) | RemoteAddress=$($af.RemoteAddress)"
}

# 4) 本机 IP 是否就是访问方所在网段（多网卡机器容易打错地址）
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Select-Object IPAddress,InterfaceAlias
```

**目标机实测的地址候选**（默认路由网卡是「以太网」= `192.168.2.10`，也是脚本打印的那个）：

```
192.168.2.10    以太网                     ← 正确的内网地址
192.168.2.58    WLAN 7
192.168.137.1   VMware Network Adapter VMnet8   ← 虚拟网卡，内网连不上
192.168.6.1     VMware Network Adapter VMnet8
192.168.15.1    VMware Network Adapter VMnet1
192.168.56.1    VirtualBox Host-Only Network
```

> 脚本用「默认路由所在网卡」来挑地址，就是为了避开这些虚拟网卡。
> 若打印出来的地址不对，用上面的命令自己挑一个试。

**规则缺失时手工补**（管理员）：

```powershell
New-NetFirewallRule -DisplayName 'DeerFlow Frontend (Port 3000)' -Direction Inbound `
  -Protocol TCP -LocalPort 3000 -Action Allow -RemoteAddress LocalSubnet -Profile Any
```

> ⚠️ 首次用 `start.ps1` 启动时若**不是管理员身份**，防火墙规则会创建失败（脚本只警告不中止），
> 表现就是「本机正常、内网不通」。用管理员身份重跑一次 `start.ps1` 即可。

### Q2：端口被占用怎么办

```powershell
# 看谁占着
Get-NetTCPConnection -LocalPort 3000,8001 -State Listen |
  Select-Object LocalPort,LocalAddress,OwningProcess
Get-Process -Id <PID> | Select-Object Id,ProcessName,Path

# 看完整命令行（判断是不是本项目的进程）
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object -ExpandProperty CommandLine
```

- **是本项目的残留进程**（命令行含 `D:\deer-flow`）：先 `stop.ps1` 清理；仍不行就
  `taskkill /F /T /PID <PID>`，再 `start.ps1`。
- **是别人的程序**：`start.ps1` 会**拒绝启动并明确提示是谁占着**，绝不误杀。
  请自行决定停掉它，或改用 `-Timeout` / 临时改端口（改动需同步前端 rewrite 目标，不推荐）。

### Q3：服务起不来怎么排查

```powershell
# 1) 状态 + 日志尾部（先看这个）
& 'D:\deer-flow\src\scripts\windows\status.ps1' -Tail 40

# 2) 直接看完整日志
Get-Content 'D:\deer-flow\logs\gateway.log' -Tail 100
Get-Content 'D:\deer-flow\logs\frontend.log' -Tail 100
```

常见原因与对应检查：

| 日志里的线索 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'app'` | 后端依赖没装好 | `install.ps1` 重装后端 |
| `Environment variable XXX not found` | `config.yaml` 引用了 `.env` 里没有的 `$VAR` | 检查 `.env` 是否被误删 / 手工删过键；`init-config.ps1` 会补齐 |
| `require_admin_approval requires approval_email.enabled=true` | `config.yaml` 的 auth 段被改坏 | 恢复 `approval_email.enabled: true`；或 `init-config.ps1 -Force` 重新生成 |
| `SyntaxError: source code string cannot contain null bytes` | 代码里混入了 macOS 的 `._*` 边车文件（`migrations\versions\` 下最致命） | 删掉所有 `._*` 文件，改用 `sync-to-windows.sh` 同步 |
| `EADDRINUSE` / 端口占用 | 残留进程占着端口 | 见 Q2 |
| 前端日志 `Next.js build ID not found` / 启动即退 | `.next` 构建产物缺失或不完整 | `install.ps1 -SkipBackend -SkipFrontend` 重新构建 |
| Gateway 一直启动超时 | 首次启动较慢 / 磁盘慢 | `start.ps1 -Timeout 300` 放宽等待 |

### Q4：忘记管理员密码怎么办

重跑 `admin-init.ps1` 会**重置**密码（旧密码立即失效，已登录会话全部作废）：

```powershell
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -Email admin@sz-jlc.com
```

不记得管理员邮箱时，先看凭据文件里记的邮箱：

```powershell
& 'D:\deer-flow\src\scripts\windows\admin-init.ps1' -ShowOnly
```

### Q5：如何新增用户

用户自己注册 + 管理员审批，两步：

1. 用户在 `http://<服务器IP>:3000` 的登录页点**注册**，用白名单域名内的邮箱（默认 `sz-jlc.com`）；
2. 管理员登录后台 →「**用户管理**」→ 找到该用户 → 点「**通过**」。

> 邮件通知不会发出（回环 SMTP 桩），管理员**必须主动去后台审批**，
> 不能等邮件提醒。见 §5.2。

### Q6：`data\deerflow.db` 只有 4 KB，数据丢了吗

**没有。** 数据库是 WAL 模式，数据在 `deerflow.db-wal` 里，主库文件小是正常的：

```powershell
Get-ChildItem D:\deer-flow\data -Filter 'deerflow.db*' | Select-Object Name, Length
# deerflow.db      4096
# deerflow.db-shm  32768
# deerflow.db-wal  671592     ← 数据在这里
```

> ⚠️ 但这意味着**备份不能只拷主库**，否则得到的是空库。见 §4.5。

### Q7：`deploy.ps1` 说「工具链尚未就绪」，为什么不自动装

`install-toolchain.ps1` 会做**系统级改动**：把 Node 解压到磁盘、下载 uv、写**用户级 PATH 与环境变量**。
如果自动执行，一旦中途失败（镜像不可达、磁盘不足、代理没配），系统会被留在「半装」状态，
而运维并不知道编排动过系统配置、也不知道该从哪一步续跑。

所以 `deploy.ps1` 只检测 + 打印该执行的命令。按提示手动执行即可：

```powershell
& 'D:\deer-flow\src\scripts\windows\install-toolchain.ps1'
```

工具链已由镜像 / 其它方式预置时，用 `-SkipToolchain` 跳过检测。

### Q8：`deploy.ps1` 为什么不自动注册开机自启

注册计划任务会让服务在每次开机后自动拉起，影响面超出「部署一套服务」本身。
这种决定应由运维显式做出，因此默认只打印命令。需要时：

```powershell
& 'D:\deer-flow\src\scripts\windows\deploy.ps1' -RegisterAutostart
```

### Q9：重复跑 `deploy.ps1` 会不会重启服务、中断用户

**不会。** 两个服务都在运行时，`deploy.ps1` 会跳过启动阶段（实测连跑两次：

```
==> 阶段 3/5 · 启动服务（已在运行，跳过）
  [ OK ] Gateway 运行中（PID 20976）
  [ OK ] Frontend 运行中（PID 19272）
```

只有「都没起」或「只起了一半」时才会调用 `start.ps1`（它先停再起，是重启语义）。

### Q10：怎么确认前端到后端的链路是通的

```powershell
# 需要登录的接口返回 401 = 链路通（rewrite 已把请求转到 Gateway）
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:3000/api/models
# 401   ← 正常
# 404/502 ← 不通，检查 frontend\next.config.js 的 rewrites 与 Gateway 是否健康

curl.exe -s http://127.0.0.1:8001/health
# {"status":"healthy","service":"deer-flow-gateway"}
```

`deploy.ps1` 的自检阶段会自动做这两项检查。

---

## 附录 A：脚本速查表

全部位于 `D:\deer-flow\src\scripts\windows\`。

| 脚本 | 用途 | 常用参数 |
|---|---|---|
| `deploy.ps1` | **一键编排**：环境检查 → 配置 → 依赖 → 启动 → 自检 | `-RootDir` `-NoStart` `-SkipToolchain` `-SkipBuild` `-SkipAutostart` `-RegisterAutostart` |
| `install-sshd.ps1` | 安装 OpenSSH Server（仅新机器） | |
| `install-toolchain.ps1` | 安装 Node / uv / pnpm（**唯一系统级改动**） | `-RootDir` `-NodeVersion` `-UvVersion` `-PnpmVersion` `-Force` |
| `init-config.ps1` | 生成 `config.yaml` + `.env`（幂等） | `-Force` `-AllowedEmailDomains` `-RootDir` |
| `install.ps1` | 装依赖 + 构建前端 | `-SkipBackend` `-SkipFrontend` `-SkipBuild` `-Force` |
| `start.ps1` | 启动 Gateway + Frontend | `-Dev` `-Timeout` `-SkipFirewall` |
| `stop.ps1` | 停止两个服务 | `-WaitSeconds` |
| `status.ps1` | 状态 + 端口 + 时长 + 日志尾部（**退出码 0/1**） | `-Tail N` |
| `admin-init.ps1` | 创建 / 重置管理员，输出凭据 | `-Email` `-ShowOnly` |
| `register-autostart.ps1` | 注册开机自启 + 防火墙 | `-TaskName` `-DelaySeconds` `-SkipFirewall` |
| `unregister-autostart.ps1` | 注销自启 | `-TaskName` `-RemoveFirewall` |
| `DeerFlow.Common.psm1` | 公共模块（其它脚本自动加载，不直接调用） | |

**约定**（所有脚本一致）：

- **幂等**：重复执行结果一致，不重复创建资源。
- **退出码**：`0` 成功，`1` 失败（`status.ps1` 的 `0/1` 表示「至少一个服务在运行 / 都不在运行」）。
- **失败不静默**：每步失败都会打印原因 + 补救建议 + 日志位置。
- **不误杀**：只操作命令行落在部署根目录内的进程；端口被他人占用时拒绝启动并说明是谁。

## 附录 B：目录与文件位置速查

```
D:\deer-flow\
├── tools\node\node.exe                        Node 22.23.2（+ pnpm.cmd 10.26.2）
├── tools\uv\uv.exe                            uv 0.12.13
├── src\                                       代码仓库
│   ├── config.yaml                            业务配置（models 段需手工填）
│   ├── .env                                   密钥与路径（DEER_FLOW_HOME 也在此，但权威在 start.ps1）
│   ├── backend\.venv\                         后端虚拟环境
│   ├── frontend\.next\                        前端构建产物（BUILD_ID）
│   └── scripts\windows\                       全部部署脚本
├── data\
│   ├── deerflow.db                            主库（小是正常的）
│   ├── deerflow.db-wal                        **数据实际在这里**
│   ├── deerflow.db-shm
│   └── admin_initial_credentials.txt          登录改密后请删除
├── cache\{npm,uv,uv-python}                   各类缓存
└── logs\
    ├── gateway.log  frontend.log              服务日志
    ├── deploy.log                             部署里程碑
    ├── install-{backend,frontend,build}.log   安装/构建日志
    ├── admin-init.log                         管理员操作日志（不含密码）
    └── run\{gateway,frontend}.pid             PID 文件
```
