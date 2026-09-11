# Windows 远程测试脚本

把本机（macOS）的代码同步到内网 Windows 测试机，并在其上执行命令。

## 快速开始

```bash
# 1. 同步代码（首次全量，之后增量）
./scripts/windows-remote/sync-to-windows.sh

# 2. 在远端执行命令
./scripts/windows-remote/win-exec.sh 'node --version'

# 3. 安装依赖（切到代码目录）
./scripts/windows-remote/win-exec.sh --dir 'D:/deer-flow/src/backend' 'uv sync --all-packages'
```

## 脚本

### `sync-to-windows.sh`

| 参数 | 说明 |
|---|---|
| （无） | 增量同步；本地无改动时直接跳过 |
| `--dry-run` | 只统计将传输的文件数与体积 |
| `--full` | 忽略增量判断，强制全量同步 |
| `--delete` | 同步前清空远端目录 |
| `--list` | 显示同步状态（时间戳、远端文件数） |

环境变量：`WIN_HOST`（默认 `gongdewei@192.168.2.10`）、`WIN_ROOT`（默认 `D:/deer-flow`）、`WIN_SUBDIR`（默认 `src`）。

### `win-exec.sh`

| 参数 | 说明 |
|---|---|
| `<命令>` | 直接执行 PowerShell 片段 |
| `--ps <片段>` | 同上，语义更明确 |
| `--file <本地.ps1>` | 上传脚本后在远端执行 |
| `--dir <远端目录>` | 先切换目录 |
| `--timeout <秒>` | 超时，默认 300 |
| `--quiet` | 只输出 stdout 与退出码 |
| `--env K=V` | 设置远端环境变量，可重复 |

## 设计要点

### 传输用 tar 管道，不用 rsync

目标机没有 rsync（`rsync`/`7z` 均缺失），本机是 macOS 自带 `openrsync` 而非 GNU rsync，
参数行为有差异。两端都用 tar：

```bash
tar -czf - . | ssh host "cd <dir>; tar -xzf -"
```

Windows 10 1803+ 自带 `C:\WINDOWS\system32\tar.exe`（bsdtar），与 macOS 的 bsdtar 同源。

**不要用 git 路线**：仓库里存在未提交的改动（本目录就是未跟踪文件），git 会漏掉。

### 命令经 base64 传输

把 PowerShell 代码拼进 `ssh host "powershell -Command \"...\""` 要穿过
bash → ssh → cmd → PowerShell 四层转义。实测 PATH 前置语句会被静默吞掉，
命令回落到系统 Node 16 而不报错。

改用 `-EncodedCommand`（UTF-16LE base64）后不含任何 shell 元字符，同时解决中文编码。
配合 `-OutputFormat Text` 抑制 CLIXML 噪声——远端 PowerShell 在输出被重定向时
会改用 CLIXML 序列化，往 stdout 混入 XML。

### PATH 前置

该机系统 PATH 含 `C:\Program Files\nodejs`（Node 16），而 Windows 的解析顺序是
「系统 PATH + 用户 PATH」，用户级的工具链目录**无法**覆盖系统级。

两个脚本都会把 `D:\deer-flow\tools\{node,uv}` 前置到进程 PATH，保证 `node`/`pnpm`/`uv`
用对版本（22.23.2 / 10.26.2 / 0.12.13）。

### 同步排除规则

`node_modules` / `.next` / `.venv` 等**必须在目标机本地生成**——它们是平台相关的，
macOS 上构建的原生二进制在 Windows 上无法运行。仓库 4.4G 里有约 4.35G 属于这类目录。

`config.yaml` / `.env` 也排除：含密钥，应在目标机各自生成。

### macOS 兼容

- 本机 bash 是 **3.2**（无 `mapfile`/`readarray`），脚本用兼容写法
- 本机无 GNU `timeout`（`--timeout` 在缺失时降级为不设超时）

### 中文

中文文件名可正确传输——已验证远端 UTF-8 字节与本机一致。
Windows 终端里显示为乱码是 GBK 控制台的显示问题，不影响文件系统。
`win-exec.sh` 通过 `[Console]::OutputEncoding = UTF8` 保证命令输出的中文不乱码。
