# Windows 远程测试脚本

把本机（macOS）的代码同步到内网 Windows 机器，并在其上执行命令。

支持**同时管多台**机器：主机在 `hosts.conf` 里登记一次，本目录下所有脚本共用。

## 快速开始

```bash
# 1. 登记目标主机（只需一次）
cp scripts/windows-remote/hosts.conf.example scripts/windows-remote/hosts.conf
$EDITOR scripts/windows-remote/hosts.conf

# 2. 同步代码（首次全量，之后增量）
./scripts/windows-remote/sync-to-windows.sh --host win2

# 3. 在远端执行命令
./scripts/windows-remote/win-exec.sh --host win2 'node --version'

# 4. 安装依赖（切到代码目录）
./scripts/windows-remote/win-exec.sh --host win2 --dir 'D:/deer-flow/src/backend' 'uv sync --all-packages'
```

## 指定目标主机

`--host` 是**必需**参数，两种写法都行：

| 写法 | 说明 |
|---|---|
| `--host win2` | `hosts.conf` 里登记的别名 |
| `--host gdw@192.168.31.129` | 直接写 `user@host`，跳过注册表（值里含 `@` 即按地址处理） |

### `hosts.conf`

本地文件、**不入版本库**（已加入 `.gitignore`）——它记录的是各人的内网地址。
模板见 `hosts.conf.example`。格式：

```
# 别名        目标                        部署根            代码子目录（可省）
win2         gdw@192.168.31.129          D:/deer-flow
win-old      gongdewei@192.168.2.10      D:/deer-flow
```

第 3、4 列可省略，省略时用默认值 `D:/deer-flow` 与 `src`。

> ⚠️ **没有默认主机，不传 `--host` 直接报错。** 这是刻意的：留一个「猜得到的默认值」
> 比报错更危险——换到第二台机器后忘传 `--host`，同步、部署、甚至 `admin-init`
> （会**重置管理员密码**）都可能静默作用在错误的机器上，且没有任何报错信息。

### 参数优先级

| 值 | 优先级（左高右低） |
|---|---|
| 主机 | `--host` > `WIN_HOST` 环境变量 |
| 部署根 | `--root` > 注册表第 3 列 > `WIN_ROOT` > `D:/deer-flow` |
| 代码子目录 | `--subdir` > 注册表第 4 列 > `WIN_SUBDIR` > `src` |

## 脚本

### `sync-to-windows.sh`

| 参数 | 说明 |
|---|---|
| `--host NAME\|user@host` | **必需**，目标主机 |
| `--root PATH` | 远端部署根，默认见上表 |
| `--subdir PATH` | 远端代码子目录，默认 `src` |
| `--dry-run` | 只统计将传输的文件数与体积，不实际传输 |
| `--full` | 忽略增量判断，强制全量同步 |
| `--delete` | 同步前清空远端代码目录（该目录内一切内容都会没，谨慎） |
| `--list` | 只显示同步状态（时间戳、远端文件数） |

增量判断依据**远端**的 `.last-sync` 时间戳；本地快照按主机分开存于
`.sync-state/last-sync-<主机>`，因此多台机器互不干扰。

### `win-exec.sh`

| 参数 | 说明 |
|---|---|
| `--host NAME\|user@host` | **必需**，目标主机 |
| `--root PATH` | 远端部署根，默认见上表 |
| `--dir PATH` | 先切换到远端目录再执行 |
| `--timeout SECS` | SSH 超时，**默认 300 秒** |
| `--quiet` | 仅输出 stdout 与退出码 |
| `--env K=V` | 设置远端环境变量，可重复 |
| `--ps <片段>` | 语义化地声明「这是 PowerShell 片段」 |
| `--file <本地.ps1>` | 上传脚本后在远端执行；`--` 之后是脚本参数 |

> ⚠️ **长任务务必调大 `--timeout`**。装依赖、构建前端动辄十几分钟，默认 300 秒
> 会把它们杀掉。部署这类任务建议 `--timeout 3600`。

## 设计要点

### 传输用 tar 管道，不用 rsync

目标机没有 rsync（`rsync`/`7z` 均缺失），本机是 macOS 自带 `openrsync` 而非 GNU rsync，
参数行为有差异。两端都用 tar：

```bash
tar -czf - . | ssh host "cd <dir>; tar -xzf -"
```

Windows 10 1803+ 自带 `C:\WINDOWS\system32\tar.exe`（bsdtar），与 macOS 的 bsdtar 同源。

**不要用 git 路线**：`git` 传的是某个提交，而这里要的是「工作区当前状态」——
仓库里常有未提交的改动与本地文件（`hosts.conf` 就是），git 会漏掉它们。

### 命令经 base64 传输

把 PowerShell 代码拼进 `ssh host "powershell -Command \"...\""` 要穿过
bash → ssh → cmd → PowerShell 四层转义。实测 PATH 前置语句会被静默吞掉，
命令回落到系统 Node 而不报错。

改用 `-EncodedCommand`（UTF-16LE base64）后不含任何 shell 元字符，同时解决中文编码。
配合 `-OutputFormat Text` 抑制 CLIXML 噪声——远端 PowerShell 在输出被重定向时
会改用 CLIXML 序列化，往 stdout 混入 XML。

### PATH 前置

目标机系统 PATH 通常已含 `C:\Program Files\nodejs`（旧机是 Node 16，新机是 Node 25），
而 Windows 的解析顺序是「系统 PATH + 用户 PATH」，用户级的工具链目录**无法**覆盖系统级。

两个脚本都会把 `<部署根>\tools\{node,uv}` 前置到进程 PATH，保证 `node`/`pnpm`/`uv`
用对版本（22.23.2 / 10.26.2 / 0.12.13）。该路径随 `--root` 走。

### 同步排除规则

`node_modules` / `.next` / `.venv` 等**必须在目标机本地生成**——它们是平台相关的，
macOS 上构建的原生二进制在 Windows 上无法运行。仓库 4.4G 里有约 4.35G 属于这类目录。

`config.yaml` / `.env` 也排除：含密钥，应在目标机各自生成。

### macOS 兼容

- 本机 bash 是 **3.2**（无关联数组、无 `mapfile`/`readarray`），脚本用兼容写法
- 本机无 GNU `timeout`（`--timeout` 在缺失时降级为不设超时）
- zsh 下**不要**把 `-o` 选项攒进变量再展开：zsh 默认不做单词拆分，
  `ssh $OPTS host` 会把整串当成一个参数，报 `keyword batchmode extra arguments`

### 中文

中文文件名可正确传输——已验证远端 UTF-8 字节与本机一致。
Windows 终端里显示为乱码是 GBK 控制台的显示问题，不影响文件系统。
`win-exec.sh` 通过 `[Console]::OutputEncoding = UTF8` 保证命令输出的中文不乱码。

> ⚠️ 本目录脚本自身是 bash，写注释时注意：`$var` 后面紧跟中文字符必须写成
> `${var}`，否则 bash 会把多字节字符当成变量名的一部分，报 `unbound variable`。
