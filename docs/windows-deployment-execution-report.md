# Windows 部署执行报告

> 执行日期：2026-09-12
> 分支：`feat/windows-server-deploy`
> 目标机：`192.168.2.10`（`GDW-WIN10`，Windows 10 Pro 20H2，AMD64）
> 执行方式：SubAgent 驱动，每个任务独立派发并实测验收

---

## 一、最终结果

**内网访问目标已达成。**

从开发机（macOS，与目标机同网段）实测：

| 验证项 | 结果 |
|---|---|
| `http://192.168.2.10:8001/health` | **200** `{"status":"healthy","service":"deer-flow-gateway"}` |
| `http://192.168.2.10:3000/` | **307** → `/setup`（首次部署的正常引导） |
| 跟随重定向 | **200** |
| `http://192.168.2.10:3000/api/models` | **401**（需认证，证明前端→后端链路已通） |
| 管理员登录（经前端 `:3000`） | **成功** |
| 计划任务自启 | **已注册**（`DeerFlow Auto Start`，State=Ready） |
| 防火墙规则 | **2 条**（3000 / 8001，限 `LocalSubnet`） |

**重启持久化验证**：停止服务 → 用计划任务拉起（模拟开机自启）→ 网关 10 秒就绪 → 管理员账号仍可登录。

---

## 二、交付物

### 脚本集（`scripts/windows/`）

| 脚本 | 职责 | 验证状态 |
|---|---|---|
| `install-sshd.ps1` | 安装配置 OpenSSH Server | ✅ 已部署（免密登录可用） |
| `install-toolchain.ps1` | 安装 Node/uv/pnpm（镜像源） | ✅ Node 22.23.2 / pnpm 10.26.2 / uv 0.12.13 |
| `DeerFlow.Common.psm1` | 公共模块（28 个导出函数） | ✅ 全部函数实测 |
| `init-config.ps1` | 非交互生成 `config.yaml` + `.env` | ✅ 幂等、密钥随机 |
| `install.ps1` | 依赖安装 + 前端构建 | ✅ 幂等 0.7s 跳过 / 冷装约 4 分钟 |
| `start.ps1` / `stop.ps1` / `status.ps1` | 服务启停与状态 | ✅ 含误杀防护 |
| `admin-init.ps1` | 管理员初始化 | ✅ 登录链路实测通过 |
| `register-autostart.ps1` / `unregister-autostart.ps1` | 开机自启 | ✅ 任务实际触发拉起服务 |
| `deploy.ps1` | 一键编排 | ✅ 全流程跑通、幂等 |

### 开发期工具（`scripts/windows-remote/`）

| 脚本 | 职责 |
|---|---|
| `sync-to-windows.sh` | 代码同步（tar 管道，增量） |
| `win-exec.sh` | 远程执行（base64 编码，规避转义） |
| `README.md` | 设计说明 |

### 文档

| 文档 | 定位 | 规模 |
|---|---|---|
| `docs/windows-deployment.md` | **面向运维**的部署手册 | 1264 行 |
| `docs/windows-deployment-handover.md` | **面向接手开发者**的交接文档 | 478 行 |
| `docs/superpowers/plans/2026-09-12-windows-deployment-execution.md` | 实施计划 | — |
| 本文档 | 执行报告 | — |

---

## 三、关键实测数据

### 耗时

| 步骤 | 耗时 |
|---|---|
| 工具链安装（Node 22 + uv） | 约 1-2 分钟（走镜像直连） |
| 后端依赖 `uv sync`（214 包） | 8.5-10 秒（uv 缓存热）/ 冷缓存未实测 |
| 前端依赖 `pnpm install`（1458 包冷装） | 2 分 27 秒 |
| 前端构建 `pnpm build` | 52-55 秒 |
| 服务启动（Gateway + Frontend） | 约 10 秒 |
| 代码同步（4227 文件） | 约 5 秒（增量通常更快） |

### 磁盘占用

| 项 | 大小 |
|---|---|
| 后端 `.venv` | 703.5 MB（214 包） |
| 前端 `node_modules` | 506,744 个文件 |
| SQLite 数据 | 主库 4096 B + WAL 671,592 B |
| D 盘剩余 | 167 GB（C 盘仅 3.7 GB，已全程避开） |

---

## 四、过程中发现并修复的缺陷

> 这些都是实测暴露的真实问题，不是理论推演。

### 我的脚本自身的问题

**1. 代码同步产生 2319 个垃圾文件，导致后端起不来（最严重）**

macOS 文件带 `com.apple.provenance` 扩展属性，用 `tar` 打包时会**自动合成** `._<原名>` 边车成员；
Windows 解包时 NTFS 无法保存 xattr，libarchive 把它们**实体化成真实文件**。

后果：9 个 `._*` 文件落在 `migrations/versions/`，Alembic 会 glob 并 exec 该目录所有 `.py`，Gateway 启动崩溃：

```
SyntaxError: source code string cannot contain null bytes
```

**为什么 `--exclude './._*'` 拦不住**：`._X` 是打包过程中**合成的归档成员**，
不是文件系统里的真实文件，任何遍历都看不到它。

修复：`tar --no-xattrs --no-mac-metadata`，**两个 flag 缺一不可**。实测数据：

| 参数组合 | 归档成员 | `._` 边车 | xattr PAX 记录 |
|---|---|---|---|
| （无） | 22 | 11 | 11 |
| `--no-xattrs` | 22 | 11 | 0 |
| `--no-mac-metadata` | 11 | 0 | 11 |
| **两个都加** | **11** | **0** | **0** |

**2. SSH 传输命令被四层转义吞掉 PATH 前置**

`ssh host "powershell -Command \"...\""` 要穿过 bash → ssh → cmd → PowerShell 四层转义。
实测 PATH 前置语句被**静默吞掉**，命令回落到系统 Node 16 而不报错。

修复：改用 `-EncodedCommand`（UTF-16LE base64）+ `-OutputFormat Text`。

**3. `DEER_FLOW_HOME` 冲突**

`install-toolchain.ps1` 早期把用户级 `DEER_FLOW_HOME` 设为 `D:\deer-flow`，
而 `init-config.ps1` 期望 `D:\deer-flow\data`。用户级环境变量优先于项目 `.env`，
导致运行时状态（SQLite、管理员凭据）落到部署根而非 `data\`。

修复：`install-toolchain.ps1` 不再设置该变量，`.env` 成为唯一权威来源。

**4. 编排脚本的子进程输出污染返回值**

`& powershell.exe @args` 不加 `| Out-Host` 时，子进程 stdout 会进入函数返回值，
调用方拿字符串数组与 `0` 比较，导致 `deploy.ps1` 在阶段 1 就误判失败（尽管子脚本退出码为 0）。

修复：加 `| Out-Host`。已用故意失败的子脚本验证：`RETURN_TYPE=Int32 / RETURN_VALUE=3`。

**5. 后台进程用 `Start-Process` 会立即死亡**

实测：`Start-Process` 启动的子进程在宿主 PowerShell 退出时立即死亡，
经 SSH 启动的服务总是起不来且日志 0 字节。

修复：改用 `Invoke-CimMethod Win32_Process.Create`（WMI 启动可干净脱离）。
副作用：子进程继承 WmiPrvSE 环境而非脚本的，故 `PYTHONPATH` 与工具链 PATH 必须显式写在命令行。

**6. 停止服务留下孤儿进程**

`cmd.exe` 包装进程被优雅终止后立即退出，`/F /T` 分支根本不执行，
子进程（`uv → uvicorn → python`）被重新挂载继续运行——表现为「端口空闲但 status 报运行中」。

修复：新增 `Stop-DeerFlowProcessTree`（**杀之前**先快照进程树，因为重新挂载后无法事后遍历）。

**7. 双重 BOM**

`admin-init.ps1` 被写入了两个 UTF-8 BOM（`EF BB BF EF BB BF`）——
转换脚本未先剥离已有 BOM。PowerShell 5.1 容忍，但与其他 9 个脚本不一致。
已修复为单 BOM。

### 上游的硬约束（实施中发现，修正了原计划的错误假设）

**8. `require_admin_approval` 强制要求 `approval_email.enabled`**

原计划写「启用审批但禁用邮件」——**架构上不可能**：

```python
# backend/packages/harness/deerflow/config/auth_config.py:178-179
if self.require_admin_approval and not self.approval_email.enabled:
    raise ValueError("require_admin_approval requires approval_email.enabled=true")
```

正确做法：启用 `approval_email.enabled` 但指向**回环 SMTP 桩**（`127.0.0.1:465`）。
审批**不依赖邮件**——`approve()` 先提交状态变更，之后才尽力发信，失败被捕获并标记
`approval_email_status=failed`，不阻断审批。回环地址即时拒绝（实测 ~4ms），无网络超时。

**9. 配置中的 `$VAR` 必须全部可解析**

`config.example.yaml` 含 **50 个 `$VAR` 引用**，后端对缺失变量**硬失败**：

```python
# backend/packages/harness/deerflow/config/app_config.py:403-405
env_value = os.getenv(config[1:])
if env_value is None:
    raise ValueError(f"Environment variable {config[1:]} not found")
```

`init-config.ps1` 会扫描全部整值 `$VAR`，缺失的写入 `.env` 并置**空字符串**
（`os.getenv` 返回 `''` 而非 `None` → 配置可加载，界面显示「未配置」）。

**10. `reset_admin` CLI 无法创建首个管理员**

它的语义是「在已存在用户上重置密码」，空库时只报 `no admin user found` 并退出 1，
**永远建不出第一个账号**。`admin-init.ps1` 因此分两条分支：有管理员走 CLI，无管理员走自带引导程序。

**11. 登录接口用 `OAuth2PasswordRequestForm`**

要求 **`application/x-www-form-urlencoded`** 而非 JSON，字段名是 `username` 而非 `email`。
用 JSON 会得到误导性的 `422 字段缺失`。

**12. `base_dir` 解析依赖 cwd，会静默错位（收尾复核时发现，已加固）**

DeerFlow 运行时状态目录（凭据文件、`memory.json`、`users\`、`agents\`）由
`get_paths().base_dir` 决定，链路是：

```
get_paths().base_dir → runtime_home() → os.getenv("DEER_FLOW_HOME")
                                      ↘ 缺失时 project_root() / ".deer-flow"
```

问题出在「缺失时」那一支：`DEER_FLOW_HOME` 本身可能来自 `app_config.py` 顶层那次
`load_dotenv()`，而 dotenv 是**从 cwd 逐级向上**找 `.env` 的。从部署根启动时够不到
`src\.env`（向上只上溯到 `D:\`），变量就「缺失」，`base_dir` 静默落到 `<root>\.deer-flow`。

实测（目标机，逐 cwd 取 `get_paths().base_dir`）：

| cwd | 结果 | |
|---|---|---|
| `src\backend` | `D:\deer-flow\data` | ✓ |
| `src` | `D:\deer-flow\data` | ✓ |
| `D:\deer-flow`（部署根） | `D:\deer-flow\.deer-flow` | ✗ **静默错位** |

错位的后果不是报错而是**脑裂**：`config.yaml` 仍把 SQLite 指向 `data\`，
但凭据/记忆/用户数据写到 `.deer-flow\`，两处状态各走各的。

原有脚本之所以没暴露这个问题，是因为它们**碰巧**都 `cd` 到了 `src\backend`
（`start.ps1` 的启动命令行、`admin-init.ps1` 的 `Invoke-UvCommand`）——
是巧合而非保证：换启动器、换 cwd、换调用方就失效。

**修复**：`start.ps1` 在 Gateway 启动命令行里显式
`set DEER_FLOW_HOME=<root>\data` 与 `set DEER_FLOW_PROJECT_ROOT=<src>`，
使 `base_dir` 与 cwd、dotenv 发现彻底无关。实测重启后从
`D:\deer-flow` 与 `src\backend` 启动结果都稳定为 `data\`。
（用 `Get-CimInstance Win32_Process` 确认 `cmd.exe` 包装进程命令行确实携带该变量，
uvicorn 子进程继承之。）

同时修正了两处**互相矛盾**的注释：`install-toolchain.ps1` 原写「`.env` 是唯一权威来源」，
`admin-init.ps1` 原写「`.env` 根本没被加载」——两句都不准确（真相是「取决于 cwd」），
已按上面的机制改写，避免后来者照着错误前提「顺手改回去」。

---

## 五、目标机最终状态

### 目录布局

```
D:\deer-flow\
├── tools\      Node 22.23.2 / uv 0.12.13（自包含，不污染系统）
├── src\        代码仓库（4227 源文件）
├── data\       deerflow.db (+ -wal 671KB) + admin_initial_credentials.txt
├── cache\      npm / uv / uv-python 缓存（刻意避开 C 盘）
└── logs\       gateway.log / frontend.log / install-*.log / deploy.log
    └── run\    gateway.pid / frontend.pid
```

### 服务与端口

| 端口 | 服务 | 状态 |
|---|---|---|
| 3000 | Next.js 前端（**内网入口**） | 监听中 |
| 8001 | Python Gateway | 监听中 |
| 22 | OpenSSH | 监听中 |

### 自启配置

**计划任务**：`DeerFlow Auto Start`
```
触发器    MSFT_TaskBootTrigger  delay=PT30S
动作      powershell.exe -NoProfile -ExecutionPolicy Bypass -File "...\start.ps1" -RootDir "D:\deer-flow"
运行账户   gongdewei  logon=S4U  runlevel=Highest
设置      执行时限=PT0S(不限)  重试=3
```

**为什么用 S4U 而非 SYSTEM**：服务需要访问用户级缓存（`UV_CACHE_DIR=D:\deer-flow\cache\uv`），
`SYSTEM` 读不到用户级环境变量，会静默回落到 C 盘缓存。S4U 无需存储密码、
开机时无需登录会话。代价：S4U 不携带网络凭据（UNC/SMB 会失败），且要求该账户至少登录过一次。

**防火墙**：`DeerFlow Frontend (Port 3000)` / `DeerFlow Gateway (Port 8001)`，限 `LocalSubnet`。
（目标机网卡类别是 **Public**，Windows 对 Public 网络入站默认全阻断——防火墙规则是**必需**的。）

### 当前管理员

```
邮箱: admin@sz-jlc.com
凭据: D:\deer-flow\data\admin_initial_credentials.txt (243 字节)  ← 生效的那份
状态: needs_setup=True（首次登录后前端会引导到 /setup 设置新密码）
```

> 部署根下另有一份同名的历史孤儿文件（密码已失效），见第六节「一个孤立的凭据文件」。

---

## 六、未完成 / 已知限制

### 未完成的验证

| 项 | 说明 |
|---|---|
| **从另一台内网机器访问** | 开发机与目标机同网段且已验证；但**未用第三台机器浏览器实测**（需人工操作） |
| **重启服务器验证自启** | 用 `Start-ScheduledTask` 模拟触发已验证；**未真正重启 Windows** |
| **冷缓存下的后端安装耗时** | uv 缓存已热（0.67 GB），未测冷启动；脚本设了 30 分钟超时 |
| **`install-sshd.ps1` / `install-toolchain.ps1` 的从零重跑** | 这两台已装好，未重新执行（会重复安装） |

### 已知限制

1. **`models` 段默认为空** —— `config.yaml` 里所有模型配置都被注释掉了。
   配置能加载，但**LLM 功能不可用**，需填入实际的 provider/API Key。
2. **无 HTTPS** —— 内网明文传输。如需加密建议前置反代（Caddy / nginx）。
3. **单机 SQLite** —— 不支持多节点。多节点需 PostgreSQL（见 `config.example.yaml` database 段）。
4. **升级需停服** —— SQLite 单文件，升级期间须停止服务。
5. **Windows 10 20H2 已 EOL** —— 2022 年 5 月停止安全更新，长期运行有风险。
6. **`Get-AllLanCandidates` 在三处重复** —— `start.ps1` / `deploy.ps1` / 模块私有函数。
   未重构（避免动已验证的脚本），可作为后续改进。

### ⚠ 不要删除目标机的 `DEER_FLOW_HOME` 用户级环境变量

目标机用户级环境变量存在 `DEER_FLOW_HOME=D:\deer-flow\data`。

**初版报告建议删除它，这是错的 —— 该变量是承重的，删掉会破坏部署。**
原因见第四节「缺陷 12」：`base_dir` 的解析有一支依赖 cwd，而这个变量正是让
`base_dir` 变成 cwd 无关的那个锚点。删掉后，任何从部署根启动的进程都会把运行时状态
写到 `D:\deer-flow\.deer-flow\` 而非 `data\`。

它由脚本创建、与 `.env` 取值一致，属**有意设置**，保留即可。

（加固后 `start.ps1` 已自行钉死该变量，因此即便它哪天丢了，Gateway 也不会错位；
但其它调用方仍会受 cwd 影响，故不建议移除。）

### 一个孤立的凭据文件

`D:\deer-flow\admin_initial_credentials.txt` 是 `DEER_FLOW_HOME=D:\deer-flow`
时期（缺陷 3，03:45）留下的孤儿，与当前生效的
`D:\deer-flow\data\admin_initial_credentials.txt` 不是同一份，**密码也不同**。

已实测确认孤儿文件里的密码**已失效**（登录返回 `401 invalid_credentials`），
当前有效密码只存在于 `data\` 那份（登录返回 `200`）。因此它不构成凭据泄露，
但仍是部署根下一个含历史明文密码的文件，建议删除：

```powershell
Remove-Item D:\deer-flow\admin_initial_credentials.txt
```

> 顺带记一个排查体感：确认「哪份凭据有效」时踩了两次空 ——
> 登录路由是 `/api/v1/auth/login/local`（`auth.router` 的
> `prefix="/api/v1/auth"`），先当作 `/api/auth/login` 和 `/api/auth/login/local`
> 试都返回 `401 not_authenticated`（中间件拦的，不是密码错）。
> 两者报错码不同：路由不存在 → `not_authenticated`；密码错 → `invalid_credentials`。
> 另外该端点有 **5 次/5 分钟** 的 IP 锁定，试密码前先确认路由是对的。

---

## 七、日常运维速查

```bash
# 从开发机操作目标机
ssh gongdewei@192.168.2.10

# 同步代码（开发期）
cd /Users/gongdewei/work/projects/deer-flow
./scripts/windows-remote/sync-to-windows.sh

# 服务管理
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\start.ps1'
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\stop.ps1'
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\status.ps1'

# 一键部署（新机器）
./scripts/windows-remote/win-exec.sh --quiet '& D:\deer-flow\src\scripts\windows\deploy.ps1'

# 验证内网访问
curl -s -o /dev/null -w "%{http_code}\n" http://192.168.2.10:3000/
curl -s http://192.168.2.10:8001/health
```

**在 Windows 上直接操作**：
```powershell
D:\deer-flow\src\scripts\windows\status.ps1
D:\deer-flow\src\scripts\windows\start.ps1
D:\deer-flow\src\scripts\windows\stop.ps1
```

详细运维说明见 `docs/windows-deployment.md`。

---

## 八、下一步建议

按优先级：

1. **填 `models` 配置**（P0）—— 否则 LLM 功能不可用，这是当前最影响可用性的项
2. **在另一台内网机器上真实浏览器走一遍**（P0）—— 验证登录、审批、对话全流程
3. **真实重启服务器**（P1）—— 确认自启在真实开机场景下工作
4. **升级 Windows 10 20H2**（P2）—— 已 EOL，安全风险
5. 补充 HTTPS 反代（P2）—— 如需在内网之外访问
