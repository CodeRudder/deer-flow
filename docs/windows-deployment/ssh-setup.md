# Windows SSH 服务配置与免密登录

> **适用场景:** 在一台 Windows 机器上启用 OpenSSH 服务端，并从 macOS / Linux 客户端免密登录。
>
> **本文事实来源:** 2026-09-26 对 `gdw@192.168.31.129`、以及更早对 `gongdewei@192.168.2.10` 两台实机的直接采集，
> 命令与输出均为实测，非文档抄录。标注「实测」的内容可直接复现。

---

## 1. 先分清三件事

配置 SSH 时**最容易出错的地方，是把这三件事混在一起**：

| # | 事项 | 出错后果 |
|---|---|---|
| 1 | 服务端装在哪条路线上（系统内置 vs 便携版） | 找不到 `sshd_config`、不知道文件该放哪 |
| 2 | 登录用户是**管理员**还是普通用户 | **公钥写错文件，免密登录永远失败** |
| 3 | 文件 ACL 是否正确 | sshd 静默拒绝该密钥文件 |

其中 **#2 是本文存在的根本原因**——它是 Windows OpenSSH 与 Linux 最大的行为差异，且报错信息毫无提示。

---

## 2. 服务端安装：两条路线

两条路线**最终都是同一个 sshd**，区别只在「谁管理文件」和「升级方式」。

| | 路线 A：系统内置可选功能 | 路线 B：Win32-OpenSSH 便携版/MSI |
|---|---|---|
| 适用 | Windows 10 1809+ / Server 2019+ | 任意版本，或需要版本比系统新的 |
| 安装 | `Add-WindowsCapability`，**无需下载** | 下载 zip/MSI 手工部署 |
| 二进制位置 | `C:\Windows\System32\OpenSSH\` | 自定义，常见 `C:\Program Files\OpenSSH\` |
| 配置位置 | `C:\ProgramData\ssh\` | `C:\ProgramData\ssh\`（同左） |
| 升级 | Windows Update | 手工替换 |
| 本环境两台机器 | ❌ 均未使用 | ✅ **均使用此路线** |

> 实测：`192.168.31.129` 上 `Get-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0`
> 返回 **`NotPresent`**，但 sshd 服务正常运行——因为它装的是便携版（`sshd 9.5.0.0`）。
> **不要用 capability 状态判断 SSH 是否可用**，要用 `Get-Service sshd`。

### 路线 A：系统内置（推荐，最省事）

管理员 PowerShell，一条命令搞定安装 + 服务注册：

```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType Automatic
```

### 路线 B：便携版

从 <https://github.com/PowerShell/Win32-OpenSSH/releases> 下载 `OpenSSH-Win64.zip`（或 `OpenSSH-Win64-*.msi`）：

```powershell
Expand-Archive OpenSSH-Win64.zip -DestinationPath 'C:\Program Files\OpenSSH' -Force
cd 'C:\Program Files\OpenSSH'
powershell -ExecutionPolicy Bypass -File .\install-sshd.ps1
```

`install-sshd.ps1` 会做三件事：注册 `sshd`/`ssh-agent` 服务、生成主机密钥、在防火墙放行。
**执行完后该脚本自身会被删除**（实测：`Test-Path .\install-sshd.ps1` → `False`），属正常现象。

> ⚠️ 该目录**不要放在 C 盘空间紧张的位置**；便携版整套约 20 MB，但主机密钥与日志会持续增长。

---

## 3. 服务与防火墙

### 3.1 确认服务状态

```powershell
Get-Service sshd, ssh-agent | Select-Object Name, Status, StartType
```

期望输出（实测值）：

```
Name       Status StartType
----       ------ ---------
ssh-agent Running Automatic
sshd      Running Automatic
```

`sshd` 未启动就 `Start-Service sshd`；`StartType` 不是 `Automatic` 就 `Set-Service sshd -StartupType Automatic`。
（否则重启后服务不会自启，表现为「昨天还好好的，今天就连不上」。）

### 3.2 确认防火墙

```powershell
Get-NetFirewallRule | Where-Object { $_.DisplayName -match "SSH" } |
  Select-Object DisplayName, Enabled, Direction, Action, Profile
```

`install-sshd.ps1` 会放行两条规则（实测存在于 `192.168.31.129`）：

```
DisplayName                          Enabled Direction Action  Profile
-----------                          ------- --------- ------  -------
OpenSSH SSH Server Preview (sshd)      True   Inbound  Allow  Private
OpenSSH Server (sshd) - Port 22        True   Inbound  Allow      Any
```

若缺失，手工添加：

```powershell
New-NetFirewallRule -Name sshd -DisplayName 'OpenSSH Server (sshd) - Port 22' `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

> 排查顺序永远是：**先 `sshd` 服务 → 再防火墙 → 最后才怀疑密钥**。
> 从客户端 `nc -z -G 5 <host> 22` 可以快速区分「网络/防火墙不通」与「SSH 认证失败」。

---

## 4. 免密登录配置（核心）

### 4.1 客户端：准备密钥

```bash
ls -la ~/.ssh/                                    # 看有没有现成的
ssh-keygen -y -P "" -f ~/.ssh/id_ed25519          # 能输出公钥 = 未设 passphrase
```

若没有密钥或想换新：

```bash
ssh-keygen -t ed25519 -C "$(whoami)@$(hostname)"  # 一路回车即可
```

> **优先用 ed25519**。RSA 需依赖 `rsa-sha2-256/512` 才被现代 sshd 接受，多一层不确定性。

### 4.2 ⚠️ 关键分叉：公钥该写到哪个文件

**Windows OpenSSH 的默认 `sshd_config` 里有一段 Linux 没有的规则：**

```
AuthorizedKeysFile	.ssh/authorized_keys      # 普通用户走这里

Match Group administrators
       AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys    # 管理员走这里
```

因此：

| 登录用户身份 | 公钥目标文件 |
|---|---|
| **属于 Administrators 组** | `C:\ProgramData\ssh\administrators_authorized_keys` |
| 普通用户 | `C:\Users\<用户>\.ssh\authorized_keys` |

> 🔴 **`Match Group administrators` 只判断账号是否属于管理员组，与是否「以管理员身份运行」无关。**
> 一个非提权的管理员会话，依然读 `administrators_authorized_keys`。
>
> 这就是「公钥明明写进了 `~/.ssh/authorized_keys`，却仍然要输密码」的根因。
> 两处都写不会更安全，只会更难排查——**先判定身份，只写对的那一个**。

判定身份（管理员 PowerShell 或远程会话中）：

```powershell
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# True  → administrators_authorized_keys
# False → ~/.ssh/authorized_keys
```

用 `cmd` 风格也可以：`whoami /groups | findstr /i S-1-5-32-544`（有输出即管理员）。

### 4.3 写入公钥

在客户端拿到公钥内容：

```bash
cat ~/.ssh/id_ed25519.pub
# ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI... user@host
```

**管理员用户**（Windows 侧管理员 PowerShell）：

```powershell
$key = 'ssh-ed25519 AAAA...你的公钥... gongdewei@Mac'
$f   = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -Path $f -Value $key -Encoding ascii
```

> ⚠️ `Set-Content` **整文件覆盖**。该文件可能已有其他管理员的公钥（多台机器共用一台服务器时会），
> 覆盖会**静默踢掉别人**。若文件已存在，改用追加：
> ```powershell
> Add-Content -Path $f -Value $key -Encoding ascii
> ```
> 追加前先 `Get-Content $f` 确认是否已存在同一条公钥，避免重复行。

**普通用户**：

```powershell
$dir = "$env:USERPROFILE\.ssh"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Add-Content -Path "$dir\authorized_keys" -Value $key -Encoding ascii
```

> **必须用 ASCII / 无 BOM 编码**。`Set-Content` 的 `-Encoding ascii` 即可。
> PowerShell 5.1 默认写 `UTF-16LE`，sshd 会直接把整个文件当作损坏而忽略。

### 4.4 修复 ACL（漏了这步必定失败）

sshd 对密钥文件有严格的属主/权限要求，**不满足就静默拒绝**（客户端只看到继续要密码）。

便携版自带的 `OpenSSHUtils` 模块提供了官方修复命令，**优先用它**：

```powershell
Import-Module 'C:\Program Files\OpenSSH\OpenSSHUtils.psm1'
Repair-AdministratorsAuthorizedKeysPermission -FilePath 'C:\ProgramData\ssh\administrators_authorized_keys' -Confirm:$false
```

> 该 cmdlet 声明了 `ConfirmImpact="High"`，**脚本中不加 `-Confirm:$false` 会卡在交互确认上**。
> 普通用户文件用 `Repair-AuthorizedKeyPermission -FilePath <路径> -Confirm:$false`。

也可用 `icacls` 等价实现（系统内置 OpenSSH 无 `OpenSSHUtils` 时用这条）：

```powershell
icacls $f /inheritance:r /grant "SYSTEM:F" /grant "BUILTIN\Administrators:F"
```

正确结果（实测）：

```
C:\ProgramData\ssh\administrators_authorized_keys BUILTIN\Administrators:(F)
                                                  NT AUTHORITY\SYSTEM:(F)
```

要点：**关闭继承（`/inheritance:r`）**，且只保留 `SYSTEM` 与 `Administrators` 的完全控制。
若文件里出现了普通用户或 `Users` 组，sshd 会拒绝。

### 4.5 验证

```bash
ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 gdw@192.168.31.129 'echo OK'
ssh -o BatchMode=yes gdw@192.168.31.129 'echo OK'      # 不指定 key
```

`BatchMode=yes` 会禁用所有交互式提问——**成功即代表真免密**，不用靠「没弹密码框」来肉眼判断。

> 修改 `authorized_keys` **无需重启 sshd**（每次认证都重新读取）。
> 但修改 `sshd_config` 必须 `Restart-Service sshd`。

---

## 5. 客户端 `~/.ssh/config`（可选但推荐）

免密已生效后，加一条 Host 别名，省去每次敲 IP：

```
Host win-gdw
    HostName 192.168.31.129
    User gdw
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

`IdentitiesOnly yes` 让客户端**只**出示指定密钥，避免逐个试错其他密钥触发
`MaxAuthTries` 上限（默认 6）。

用法：`ssh win-gdw`。远程 shell 默认是 PowerShell。

---

## 6. 故障排查

| 症状 | 最可能的原因 | 处置 |
|---|---|---|
| `Connection refused` / `nc` 不通 | sshd 未运行，或防火墙未放行 | 查 `Get-Service sshd`；查入站规则 |
| `Connection timed out` | 网络不通（不同网段、VLAN 隔离） | `ping` / `nc -z` 分层确认 |
| 一直要密码，公钥无效 | **公钥写错文件**（管理员 vs 普通用户） | 见 §4.2，先判定身份 |
| 一直要密码，文件写对了 | **ACL 不合格** | 见 §4.4，用 `Repair-*` 或 `icacls` |
| 一直要密码，内容也对 | **编码是 UTF-16/带 BOM** | 重写为 `-Encoding ascii` |
| `Permission denied (publickey)` | 客户端根本没出示正确的密钥 | `ssh -v` 看 `Offering` 行 |
| `Too many authentication failures` | 客户端连续试了过多密钥 | `config` 里加 `IdentitiesOnly yes` |
| 连上了但中文乱码 | 远程 PowerShell 输出是 GBK | 命令前加 `[Console]::OutputEncoding=[Text.Encoding]::UTF8` |

**通用排查手法：** 客户端 `ssh -v`（多级 `-vvv`）看认证协商过程；
服务端把 `sshd_config` 的 `LogLevel` 调到 `DEBUG1` 后 `Restart-Service sshd`，日志在
`C:\ProgramData\ssh\logs\sshd.log`。

---

## 7. 本环境两台机器实测记录（2026-09-26）

| | `192.168.31.129` | `192.168.2.10` |
|---|---|---|
| 登录账号 | `gdw`（**管理员**） | `gongdewei`（**管理员**） |
| OS | Windows 11 专业工作站版，Build 26200 | Windows 10 Pro，Build 19042（20H2） |
| 主机名 | DESKTOP-RTCAKA8 | GDW-WIN10 |
| 安装路线 | 便携版 `C:\Program Files\OpenSSH` | 便携版 `C:\Program Files\OpenSSH` |
| sshd 版本 | 9.5.0.0 | — |
| 免密生效的密钥文件 | `C:\ProgramData\ssh\administrators_authorized_keys` | 同左 |
| 网段 | 192.168.31.x | 192.168.2.x（**与 31.x 不通**，当前未接入） |

> 两台机器都是**管理员账号**，所以**都不是** `~/.ssh/authorized_keys`。这是本环境反复踩的同一个坑。

---

## 8. 速查

```powershell
# —— 服务端：状态 / 启动 / 自启 ——
Get-Service sshd, ssh-agent | Select-Object Name, Status, StartType
Start-Service sshd
Set-Service sshd -StartupType Automatic
Restart-Service sshd            # 仅在改过 sshd_config 后需要

# —— 判断该用哪个 authorized_keys 文件 ——
([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# —— 管理员用户的免密三连 ——
$f = 'C:\ProgramData\ssh\administrators_authorized_keys'
Add-Content -Path $f -Value 'ssh-ed25519 AAAA...你的公钥...' -Encoding ascii   # 新建用 Set-Content，已有文件用 Add-Content
Import-Module 'C:\Program Files\OpenSSH\OpenSSHUtils.psm1'
Repair-AdministratorsAuthorizedKeysPermission -FilePath $f -Confirm:$false

# —— 查看实际生效的配置 ——
Get-Content C:\ProgramData\ssh\sshd_config
Select-String -Path C:\ProgramData\ssh\sshd_config -Pattern 'AuthorizedKeysFile','Match'
```

```bash
# —— 客户端验证（BatchMode 成功 = 真免密）——
ssh -o BatchMode=yes gdw@192.168.31.129 'echo OK'
ssh -v gdw@192.168.31.129            # 排查认证过程
nc -z -G 5 192.168.31.129 22         # 只测网络可达性
```

---

## 相关文档

- [server-deployment.md](server-deployment.md) — 在 Windows 上部署 DeerFlow 服务端
- [operations.md](operations.md) — 部署后的日常运维
