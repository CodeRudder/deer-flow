# Windows 部署 DeerFlow — 交接文档

> 面向接手人。包含：已完成的工作、当前状态、剩余任务、关键坑位、验证方法。
> 最后更新：2026-09-12

---

## 一、项目目标

在内网 Windows 主机上部署 DeerFlow，使**内网其他 Windows 机器**通过浏览器访问 `http://<服务器IP>:3000`。

**交付物形态**：镜像仓库 + 一键初始化脚本集。不是「在开发机上敲临时命令」，而是把每个操作固化为可复用的 PowerShell 脚本，使任意新 Windows 机器能独立完成部署。

---

## 二、当前进度总览

**分支**：`feat/windows-server-deploy`（基于 `jlc/develop` @ `a4c48792`）

### ✅ 已完成并验证

| 能力 | 脚本 | 验证状态 |
|---|---|---|
| SSH 服务端安装 | `scripts/windows/install-sshd.ps1` | ✅ 已部署，免密登录可用 |
| 工具链安装（Node/uv/pnpm） | `scripts/windows/install-toolchain.ps1` | ✅ Node 22.23.2 / pnpm 10.26.2 / uv 0.12.13 |
| 代码同步（开发期） | `scripts/windows-remote/sync-to-windows.sh` | ✅ 4227 文件约 5 秒 |
| 远程执行（开发期） | `scripts/windows-remote/win-exec.sh` | ✅ 支持 PATH 前置与中文输出 |
| 公共模块 | `scripts/windows/DeerFlow.Common.psm1` | ✅ 28 个导出函数 |
| 配置生成 | `scripts/windows/init-config.ps1` | ✅ 幂等，密钥随机生成 |
| 依赖安装 + 构建 | `scripts/windows/install.ps1` | ✅ 幂等（0.7s 跳过 / 冷装约 4 分钟） |
| 服务启停 | `scripts/windows/start.ps1` / `stop.ps1` / `status.ps1` | ✅ **内网访问已验证** |
| 管理员初始化 | `scripts/windows/admin-init.ps1` | ⚠️ **已写完但未验证未提交** |

### 🔴 未完成

| 任务 | 说明 | 优先级 |
|---|---|---|
| **`admin-init.ps1` 验证与提交** | 脚本已存在（47KB）且语法通过，但**未实测、未提交** | P0 |
| **`register-autostart.ps1`** | 开机自启尚未注册（防火墙规则已由 `start.ps1` 配好） | P1 |
| **`deploy.ps1` 一键编排** | 目前需手动依次跑三个脚本 | P2 |
| **`docs/windows-deployment/operations.md`** | 面向运维的部署手册（本文档是交接文档，定位不同） | P1 |
| **端到端全量验证** | 管理员登录链路、SQLite 持久化、重启后自启 | P0 |

---

## 三、目标机现状（`192.168.2.10`）

### 环境事实（实测）

| 项 | 值 |
|---|---|
| 主机名 | `GDW-WIN10` |
| 操作系统 | Windows 10 Pro **Build 19042（20H2）** |
| 架构 | AMD64（AMD Ryzen 5 3600） |
| PowerShell | 5.1.19041.610 |
| 登录账户 | `GDW-WIN10\gongdewei`（**管理员**） |
| 远程默认 shell | PowerShell |
| 网卡网络类别 | **Public**（这曾导致入站被完全阻断） |

### 磁盘

| 盘 | 可用 | 用途建议 |
|---|---|---|
| C: | **仅 3.7 GB** | ⚠️ 极紧张，所有缓存必须避开 |
| **D:** | 167 GB | **部署根目录所在** |
| E: | 99.8 GB | 备用 |
| G: | 328 GB | 备用 |

### 目录布局

```
D:\deer-flow\
├── tools\        Node 22.23.2 / uv 0.12.13（自包含，不污染系统）
├── src\          代码仓库
├── data\         SQLite 数据（DEER_FLOW_HOME）
├── cache\        npm / uv / uv-python 缓存（刻意避开 C 盘）
└── logs\         gateway.log / frontend.log / install-*.log
    └── run\      gateway.pid / frontend.pid
```

⚠️ **`tools\` 与 `src\` 必须平级**——代码同步用 `--delete` 时会清空 `src\`，工具链若在其中会被删除。

### 端口

| 端口 | 服务 | 说明 |
|---|---|---|
| 3000 | Next.js 前端 | **内网访问入口** |
| 8001 | Python Gateway | FastAPI + agent 运行时 |
| 22 | OpenSSH | 远程管理 |

### 当前服务状态

```
gateway:8001  listening = True
frontend:3000 listening = True
```

**从开发机（macOS）实测**：
```
http://192.168.2.10:8001/health   → 200  {"status":"healthy","service":"deer-flow-gateway"}
http://192.168.2.10:3000/         → 307  → /setup（首次部署的正常引导页）
跟随重定向                          → 200
http://192.168.2.10:3000/api/models → 401（需认证，证明前端→后端链路已通）
```

### 防火墙规则（已配置）

```
OpenSSH Server (sshd) - Port 22      Inbound  enabled
DeerFlow Frontend (Port 3000)        Inbound  enabled   ← 由 start.ps1 创建
DeerFlow Gateway  (Port 8001)        Inbound  enabled   ← 由 start.ps1 创建
```

规则限定 `-RemoteAddress LocalSubnet`（仅本网段，减少暴露面）。

### 计划任务（自启）

```
（无 —— 尚未注册）
```

---

## 四、给接手人的快速上手

### 1. 连接目标机

```bash
ssh gongdewei@192.168.2.10        # 免密，直接进 PowerShell
```

### 2. 同步本地改动（开发期用）

```bash
cd /Users/gongdewei/work/projects/deer-flow
./scripts/windows-remote/sync-to-windows.sh          # 增量，无改动自动跳过
./scripts/windows-remote/sync-to-windows.sh --list   # 查看同步状态
```

⚠️ **脚本改动后必须先同步再测试**——远端跑的是 `D:\deer-flow\src\scripts\windows\` 下的副本。

### 3. 远程执行命令

```bash
./scripts/windows-remote/win-exec.sh 'node --version'
./scripts/windows-remote/win-exec.sh --dir 'D:/deer-flow/src/backend' 'uv --version'
./scripts/windows-remote/win-exec.sh --file scripts/windows/status.ps1
```

### 4. 服务管理

```bash
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\start.ps1'
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\stop.ps1'
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\status.ps1'
```

### 5. 验证内网访问

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.2.10:3000/
curl -s http://192.168.2.10:8001/health
```

---

## 五、新机器部署流程（目标形态）

```
1. 装 SSH 服务端    →  install-sshd.ps1
                         ↑ 这一台已装好；新机器需先跑
2. 装工具链         →  install-toolchain.ps1
3. 放代码           →  git clone 或拷贝仓库
4. 生成配置         →  init-config.ps1
5. 装依赖 + 构建    →  install.ps1
6. 初始化管理员     →  admin-init.ps1        ← 未验证
7. 启动服务         →  start.ps1
8. 注册开机自启     →  register-autostart.ps1 ← 未实现
```

**目前 1-7 可手动依次执行；8 与一键编排（`deploy.ps1`）待补。**

---

## 六、关键坑位（血泪经验，务必阅读）

> 这些是实测踩过的坑，每一条都会导致看似莫名其妙的故障。

### 1. ⚠️ 系统 PATH 有 Node 16，会遮蔽工具链

```
系统 PATH: ... C:\Program Files\nodejs     ← Node 16.14.2
用户 PATH: D:\deer-flow\tools\node         ← Node 22.23.2

Windows 解析顺序 = 系统 PATH + 用户 PATH  → 系统级永远优先
```

**所有脚本必须调用 `Add-ToolchainToPath`**，否则会用到 Node 16（Next.js 16 无法在 Node 16 上构建）。
裸执行 `node` 仍是 v16；`pnpm` 不受影响（它用自身目录的 node.exe）。

### 2. ⚠️ macOS 的 xattr 会污染 Windows 代码（最隐蔽的坑）

macOS 上几乎所有文件带 `com.apple.provenance` 扩展属性。用 `tar` 打包同步时：

- bsdtar 会**自动合成** `._<原名>` 归档成员（AppleDouble 边车）
- Windows 解包时 NTFS 无法保存 xattr，libarchive 把它们**实体化成真实文件**

**实测一次同步凭空多出 2319 个 `._*` 文件**，其中 9 个落在 `migrations/versions/`。
Alembic 会 glob 并 exec 该目录下的所有 `.py`，于是 Gateway 启动直接崩溃：

```
SyntaxError: source code string cannot contain null bytes
```

**为什么 `--exclude './._*'` 拦不住**：`._X` 是**打包过程中合成的归档成员**，
不是文件系统里的真实文件，任何遍历都看不到它。

**正确做法**（已修入 `sync-to-windows.sh`）：

```bash
tar --no-xattrs --no-mac-metadata -czf - .
```

两个 flag **缺一不可**——实测数据：

| 参数组合 | 归档成员 | `._` 边车 | xattr PAX 记录 |
|---|---|---|---|
| （无） | 22 | 11 | 11 |
| `--no-xattrs` | 22 | 11 | 0 | ← 修不了边车 |
| `--no-mac-metadata` | 11 | 0 | 11 | ← 修不了 PAX |
| **两个都加** | **11** | **0** | **0** | ✅ |

### 3. ⚠️ 配置文件中的 `$VAR` 必须全部可解析

`config.example.yaml` 含 **50 个 `$VAR` 引用**，且后端对缺失变量**硬失败**：

```python
# backend/packages/harness/deerflow/config/app_config.py:403-405
env_value = os.getenv(config[1:])
if env_value is None:
    raise ValueError(f"Environment variable {config[1:]} not found")
```

`init-config.ps1` 会扫描全部整值 `$VAR` 引用，缺失的写入 `.env` 并置**空字符串**
（`os.getenv` 返回 `''` 而非 `None` → 配置可加载，界面显示「未配置」）。
**例外**：`DEER_FLOW_*` / `BETTER_AUTH_*` 不置空，缺失应当显式报错。

### 4. ⚠️ `require_admin_approval` 强制要求 `approval_email.enabled`

```python
# backend/packages/harness/deerflow/config/auth_config.py:178-179
if self.require_admin_approval and not self.approval_email.enabled:
    raise ValueError("require_admin_approval requires approval_email.enabled=true")
```

**不能「启用审批但禁用邮件」**。正确做法：启用 `approval_email.enabled` 但指向**回环 SMTP 桩**
（`127.0.0.1:465`，凭据 `disabled`）。审批流程本身不依赖邮件——`approve()` 先提交状态变更，
之后才尽力发信，失败被捕获并标记 `approval_email_status=failed`，不阻断审批。
回环地址即时拒绝（实测 ~4ms），无网络超时。

### 5. ⚠️ PowerShell 脚本编码：必须 UTF-8 **with BOM** + CRLF

PowerShell 5.1 会把**无 BOM** 的 `.ps1` 按 ANSI（中文系统是 GBK）解析，中文全变乱码甚至语法错误。

`.gitattributes` 已声明 `*.ps1 text eol=crlf`——git 内部存 LF，检出到 Windows 自动转 CRLF（已验证）。
但 **BOM 必须手动加**，用 python 转换：

```python
with open(path, 'rb') as f: raw = f.read()
if raw.startswith(b'\xef\xbb\xbf'): raw = raw[3:]
text = raw.decode('utf-8').replace('\r\n','\n').replace('\n','\r\n')
with open(path, 'wb') as f: f.write(b'\xef\xbb\xbf' + text.encode('utf-8'))
```

同理，**Python 文件必须无 BOM**（否则 `﻿import` 语法错误）——用 `Write-TextFileNoBom`。

### 6. ⚠️ 退出码陷阱

PowerShell 的 `$ErrorActionPreference='Stop'` 会终止脚本，但**不设非零退出码**——
会让编排脚本误判成功。**每个脚本必须有顶层 `trap` 兜底**。

### 7. ⚠️ 后台进程必须用 WMI 启动，不能用 `Start-Process`

实测：`Start-Process` 启动的子进程在宿主 PowerShell 退出时**立即死亡**，
所以经 SSH 启动的服务总是起不来且日志 0 字节。

正确做法：`Invoke-CimMethod Win32_Process.Create`（WMI 启动的进程可干净脱离）。
副作用：子进程继承的是 **WmiPrvSE 的环境**而非脚本的，所以 `PYTHONPATH`
与工具链 `PATH` 必须**显式写在命令行里**。

### 8. ⚠️ 停止服务要杀整棵进程树

`cmd.exe` 包装进程被优雅终止后立即退出，`/F /T` 分支根本不会执行，
子进程（`uv → uvicorn → python`）被重新挂到其他父进程继续运行——
表现为「端口看起来空闲但 status 仍报运行中」。

`DeerFlow.Common.psm1` 已提供 `Stop-DeerFlowProcessTree`（**杀之前**先快照进程树，
因为重新挂载后无法事后遍历）与 `Stop-DeerFlowPortOwner`（向上找到**部署根内最顶层的祖先**）。

### 9. ⚠️ SSH 传输命令的四层转义

把 PowerShell 拼进 `ssh host "powershell -Command \"...\""` 要穿过
**bash → ssh → cmd → PowerShell** 四层转义。实测 PATH 前置语句被静默吞掉，
命令回落到 Node 16 而不报错。

**正确做法**：用 `-EncodedCommand`（UTF-16LE base64）+ `-OutputFormat Text`：

```bash
encoded=$(printf '%s' "$code" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')
ssh host "powershell -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand $encoded"
```

`-OutputFormat Text` 抑制 CLIXML——远端 PowerShell 在输出被重定向时会改用
CLIXML 序列化，往 stdout 混入 XML 噪声，污染所有字符串比较。

`scripts/windows-remote/win-exec.sh` 已封装这些。

### 10. ⚠️ macOS 环境限制

- 本机 bash 是 **3.2**（无 `mapfile`/`readarray`）——脚本用兼容写法
- 本机无 GNU `timeout`（`win-exec.sh` 在缺失时降级为不设超时）
- 本机是 `openrsync` 而非 GNU rsync——参数行为有差异，故用 tar 管道

### 11. SOCKS 代理写法

Windows 上装工具链时如需代理，**必须写 `socks5://` 或 `socks5h://`**。
写 `socks://` 时 curl 会按 **SOCKS4** 处理，而 SOCKS4 不支持远程 DNS，
解析域名会失败（实测：`socks://` → `Could not resolve host`）。

目标机本机有 Shadowsocks 监听 `127.0.0.1:1080`，但**实测不需要代理**——
PyPI 与国内镜像（清华/阿里/腾讯/中科大）直连均可达，npm 走 npmmirror 也直连。

### 12. ⚠️ `config.yaml` 里的托管区域是机器管理的，**不要手工编辑**

`config.yaml` 的 `models:` 段下有一段被标记围起来的区域，由网页端「设置 → 模型」整体管理：

```yaml
# >>> DeerFlow Web UI 托管区域 — 界面会整体重写，请勿手工编辑 >>>
  - name: my-model
    ...
# <<< DeerFlow Web UI 托管区域结束 <<<
```

**规则**：

- **区域内**：界面每次保存会**整体重写**。手工加进去的条目/注释会被下一次保存抹掉。
  要改模型请走界面（管理员登录 → 设置 → 模型）。
- **区域外**：**完全不受影响**。这正是该功能的设计目标 —— `config.yaml` 有 1400+ 行
  注释，写入时必须逐字保留。写入走的是「外科手术式」文本替换，不是 YAML 反序列化后重
  序列化（后者会把全部注释吃掉）。
- 新增模型时，密钥写进 `.env`，`config.yaml` 里只留 `$变量名` 引用（见 `docs/windows-deployment/operations.md` §4.6）。

**每次成功保存都会先备份**成 `config.yaml.bak.<YYYYMMDD-HHMMSS>`（同目录，保留最近 10 份），
回滚命令见 `docs/windows-deployment/operations.md` §4.7。校验失败的写入**不会**产生备份，也不会
碰原始文件。相关实现：`backend/packages/harness/deerflow/config/models_section.py`、
`backend/app/gateway/routers/models.py`。

---

## 七、环境配置要点

### 镜像源（已配置，新机器需重配）

| 用途 | 配置位置 | 值 |
|---|---|---|
| npm / pnpm | 用户级 `NPM_CONFIG_REGISTRY` | `https://registry.npmmirror.com` |
| PyPI | `backend/pyproject.toml:51-52`（**仓库内置**） | `https://pypi.tuna.tsinghua.edu.cn/simple` |

### 环境变量（用户级）

```
DEER_FLOW_HOME          = D:\deer-flow\data
NPM_CONFIG_REGISTRY     = https://registry.npmmirror.com
NPM_CONFIG_CACHE        = D:\deer-flow\cache\npm
UV_CACHE_DIR            = D:\deer-flow\cache\uv
UV_PYTHON_INSTALL_DIR   = D:\deer-flow\cache\uv-python
```

> ⚠️ 这些变量**不是** `install-toolchain.ps1` 设的（该脚本刻意不碰 `DEER_FLOW_HOME`）。
> `DEER_FLOW_HOME` 由 `init-config.ps1` 写入 `.env` 决定取值，并由 `start.ps1`
> 在 Gateway 启动命令行显式钉死。
>
> **不要删除用户级的 `DEER_FLOW_HOME`**：它是让 `base_dir` 与 cwd 无关的锚点，
> 删掉会让从部署根启动的进程把状态写到 `.deer-flow\` 而非 `data\`。
> 早期文档曾建议删除，那是个错误，已更正。详见 `windows-deployment/operations.md`
> 「路径与配置」一节。

### 其他工具（目标机已有）

- Python 3.12.0（`py -3.12` → `D:\Python\Python312\python.exe`）
- ⚠️ `python3` 是 **Microsoft Store 存根**，不可用，必须用 `py -3.12`
- git 2.39.1
- curl.exe 7.55.1（系统内置，版本较老但支持 SOCKS5）
- tar.exe（bsdtar 3.3.2，系统内置）

---

## 八、剩余任务详情

### P0：验证并提交 `admin-init.ps1`

**现状**：脚本已存在于 `scripts/windows/admin-init.ps1`（47KB），语法检查通过（`PARSE_OK`），
**但未实测、未提交**（`git status` 显示 `??`）。

**它做了什么**（读脚本头部注释可得）：
- 按「库里有没有管理员」走两条分支
- **有管理员** → 调用上游 CLI `uv run python -m app.gateway.auth.reset_admin` 重置密码
- **无管理员** → 用自带 Python 引导程序创建首个管理员
  - 为什么不只用 CLI：`reset_admin` 的语义是「在已存在用户上重置密码」，
    空库时只报 `no admin user found` 并以 1 退出，**永远建不出第一个账号**
  - 引导程序复刻 `/api/v1/auth/initialize` 端点的语义，复用上游同一套 `write_initial_credentials()`

**待验证**：
1. 运行后凭据文件生成且可读
2. **用该凭据实际登录成功**（最关键）——先确认登录路由路径，再 curl 测试
3. 邮箱白名单校验（不在 `auth.allowed_email_domains` 内时给出警告）
4. `-ShowOnly` 能读已有凭据而不重新生成
5. 提交（信息 `feat(windows): add admin bootstrap script`）

**注意**：服务当前在运行。凭据操作可能需要服务停止或不需要——以实测为准。
任务完成后确保服务处于运行状态。

### P1：实现 `register-autostart.ps1`

**需求**：
- **防火墙**：`New-NetFirewallRule` 放通 TCP 3000 入站，限定 `-RemoteAddress LocalSubnet`
  - ⚠️ 注：`start.ps1` 已幂等地创建了 `DeerFlow Frontend (Port 3000)` 与 `DeerFlow Gateway (Port 8001)` 规则，
    新脚本应复用/检查而非重复创建
- **开机自启**：注册**计划任务**（不是 Windows 服务——`uvicorn`/`node` 是普通进程，
  `New-Service` 要求实现服务控制协议，不适用）
  - 触发器 `AtStartup`，建议加 30 秒延迟避免与网络就绪竞争
  - 动作：调用 `start.ps1`
  - `-RunLevel Highest`、失败重试、`-ExecutionTimeLimit 0`
  - 运行账户：**用当前管理员账户**（需访问用户级 `uv`/`pnpm` 缓存），权衡需在输出中说明
- **幂等**：同名任务已存在则先移除再创建
- 配套 `unregister-autostart.ps1`（按名称精确匹配移除，不误删他人规则）

**为什么网络类别很重要**：目标机网卡类别是 **Public**，Windows 对 Public 网络的入站默认全阻断。
这就是为什么防火墙规则是**必需**的，不是可选项。

### P1：编写 `docs/windows-deployment/operations.md`

面向运维的部署手册（本文档是交接文档，定位不同）。需包含：
- 前置要求与依赖安装命令
- **从零到可访问的完整步骤序列**（含每步预期输出）
- 架构说明：为何无 nginx、`/api/*` 如何到达 Gateway、SQLite 位置、端口清单
- 运维手册：启停、看日志、升级、备份（重点 `data\`、`config.yaml`、`.env`）
- 安全说明：为何启用登录、管理员审批流程（无 SMTP 时如何在后台手动审批）
- 已知限制：单机 SQLite 不支持多节点；无 HTTPS（如需建议反代）

### P2：编写 `deploy.ps1` 一键编排

按序编排：环境检查 → `init-config.ps1` → `install.ps1` → `start.ps1` → 自检。
每阶段失败即停止并给出手动补救提示。支持 `-SkipToolchain` / `-SkipBuild` / `-NoStart`。
**检测工具链是否就绪**，未就绪则提示先跑 `install-toolchain.ps1`（不自动执行，因涉及系统级安装）。

### P0：端到端全量验证

1. `status.ps1` / `stop.ps1` → `start.ps1` 循环
2. 管理员登录链路（依赖 P0 的 `admin-init.ps1`）
3. SQLite 文件已生成且重启后数据仍在
4. **从另一台内网机器**（不只是开发机）浏览器访问 `http://192.168.2.10:3000`
5. 注册 → 审批 → 登录 完整流程
6. 重启服务器后服务自动拉起（依赖 P1 的自启）
7. 产出 `docs/windows-deployment/execution-report.md`

---

## 九、验证清单（接手后按序执行）

```bash
cd /Users/gongdewei/work/projects/deer-flow

# 1. 连通性
ssh gongdewei@192.168.2.10 'echo OK'

# 2. 同步最新代码
./scripts/windows-remote/sync-to-windows.sh

# 3. 服务状态
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\status.ps1'

# 4. 内网访问（核心目标）
curl -s -o /dev/null -w "frontend: %{http_code}\n" http://192.168.2.10:3000/
curl -s http://192.168.2.10:8001/health

# 5. 管理员初始化（待验证）
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\admin-init.ps1'
```

---

## 十、参考

- **完整实施计划**：`docs/windows-deployment/script-plan.md`
- **开发期工具说明**：`scripts/windows-remote/README.md`
- **上游编排参考**：`scripts/serve.sh`（POSIX 版，Electron 桌面端曾参考它）
- **配置模板**：`config.example.yaml`（关键段落：database 约 1425-1480 行、auth 约 1745-1800 行）

### 提交历史（本项目）

```
e6a419fa  feat(windows): add service start/stop/status scripts
cf288aa3  feat(windows): add dependency install and build script
aabbbe5c  feat(windows): add non-interactive config bootstrap
40ff8fdc  feat(windows): add shared deployment module
2ec7f166  feat(windows): add remote sync and execution scripts
4bde387c  feat(windows): install Node/uv/pnpm toolchain via domestic mirrors
613c1a93  feat(windows): download portable OpenSSH through a SOCKS5 proxy
d91c3bed  fix(windows): support Windows 10 20H2 via portable OpenSSH install
3b337328  feat(windows): add OpenSSH Server installation script
```
