#!/usr/bin/env bash
#
# win-exec.sh - 在 Windows 测试机上远程执行命令
#
# 用法:
#   ./scripts/windows-remote/win-exec.sh <命令>
#   ./scripts/windows-remote/win-exec.sh --ps <PowerShell 片段>
#   ./scripts/windows-remote/win-exec.sh --file <本地脚本.ps1> [-- arg...]
#   ./scripts/windows-remote/win-exec.sh --dir <远端目录> <命令>
#
# 选项:
#   --dir PATH     先切换到远端目录再执行
#   --timeout SECS SSH 超时，默认 300
#   --quiet        仅输出命令的 stdout
#   --env K=V      设置远端环境变量，可重复
#
# 环境变量:
#   WIN_HOST   目标主机，默认 gongdewei@192.168.2.10
#   WIN_ROOT   远端根目录，默认 D:/deer-flow
#
# 示例:
#   # 用单引号包裹裸命令；PATH 已自动前置工具链（见下方说明）
#   ./win-exec.sh 'node --version; pnpm --version; uv --version'
#
#   # 直接写 PowerShell 片段
#   ./win-exec.sh 'Get-ChildItem D:\deer-flow | Select-Object Name'
#
#   # 切换目录后执行
#   ./win-exec.sh --dir 'D:/deer-flow/src' 'git status --short'
#
#   # 执行本地 .ps1 脚本（自动上传）
#   ./win-exec.sh --file scripts/windows/check-env.ps1
#
# ⚠ 传入的命令不要再套一层引号。命令经 base64 传输，按原样交给 PowerShell：
#     正确: win-exec.sh 'node --version'
#     错误: win-exec.sh '"node --version"'   # 会被当成字符串字面量打印
#
# 重要说明 — 关于 PATH:
#   该机系统 PATH 含 C:\Program Files\nodejs（Node 16），Windows 解析顺序是
#   「系统 PATH + 用户 PATH」，因此系统级 Node 会遮蔽 D:\deer-flow\tools\node。
#   本脚本默认把工具链目录前置到进程 PATH，保证 node/pnpm/uv 用对版本。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

WIN_HOST="${WIN_HOST:-gongdewei@192.168.2.10}"
WIN_ROOT="${WIN_ROOT:-D:/deer-flow}"

# 工具链目录（前置到 PATH，规避系统 Node 16 的遮蔽）
TOOLS_PATH="$WIN_ROOT/tools/node;$WIN_ROOT/tools/uv"

WORK_DIR=""
TIMEOUT=300
QUIET=false
MODE="command"   # command | ps | file
PAYLOAD=""
declare -a FILE_ARGS=()
declare -a ENV_VARS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)     WORK_DIR="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --quiet)   QUIET=true; shift ;;
        --ps)      MODE="ps"; PAYLOAD="$2"; shift 2 ;;
        --file)    MODE="file"; PAYLOAD="$2"; shift 2 ;;
        --env)     ENV_VARS+=("$2"); shift 2 ;;
        --)        shift; FILE_ARGS=("$@"); break ;;
        -h|--help) sed -n '2,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)         PAYLOAD="$1"; shift ;;
    esac
done

if [ -z "$PAYLOAD" ]; then
    echo "用法: $0 [--dir 目录] [--ps|--file] <命令或脚本>" >&2
    exit 1
fi

SSH_OPTS=(-o BatchMode=yes -o "ConnectTimeout=20" -o StrictHostKeyChecking=accept-new)

# ── 构造环境变量前置语句 ────────────────────────────────────────────────────
#
# 注意：这里用单引号书写 PowerShell 字符串，避免 bash 侧展开。
# 整个脚本最终会被 base64 编码后传输（见下），所以不必再关心 shell 转义。

env_prefix="\$env:Path = '${TOOLS_PATH};' + \$env:Path"
for kv in "${ENV_VARS[@]:-}"; do
    [ -n "$kv" ] || continue
    key="${kv%%=*}"; val="${kv#*=}"
    env_prefix="$env_prefix; \$env:$key = '${val}'"
done

# ── 通过 base64 传递命令 ────────────────────────────────────────────────────
#
# 直接把 PowerShell 代码塞进 `ssh host "powershell -Command \"...\""` 会经历
# bash → ssh → cmd → PowerShell 四层转义，$env: 这类变量几乎必然被破坏
# （实测：PATH 前置语句被吞掉，命令仍用系统 Node 16）。
#
# 改为用 -EncodedCommand（PowerShell 原生支持 UTF-16LE base64）：
# 代码以 base64 传输，不含任何 shell 元字符，彻底消除转义问题。
# 顺便解决中文编码——base64 保证内容按 UTF-16LE 精确还原。

# 超时命令：macOS 默认没有 GNU timeout，装了 coreutils 才有 gtimeout。
# 都没有时降级为不设超时（SSH 自身的 ConnectTimeout 仍生效）。
TIMEOUT_CMD=""
for candidate in timeout gtimeout; do
    if command -v "$candidate" >/dev/null 2>&1; then
        TIMEOUT_CMD="$candidate"
        break
    fi
done

run_remote_ps() {
    # $1 = PowerShell 代码（纯文本）
    # 附加输出编码设置，避免中文在 GBK 控制台下变成乱码
    local code="[Console]::OutputEncoding = [Text.Encoding]::UTF8
# 关闭进度流：它会被序列化成 CLIXML 混入 stdout
\$ProgressPreference = 'SilentlyContinue'
\$ErrorActionPreference = 'Continue'
$1"

    # 转成 UTF-16LE 再 base64（PowerShell -EncodedCommand 的要求）
    local encoded
    encoded=$(printf '%s' "$code" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')

    # -OutputFormat Text 抑制 CLIXML：远端 PowerShell 在输出被重定向时会改用
    # CLIXML 序列化，导致 stdout 里混入 XML 噪声。
    local ssh_cmd=("ssh" "${SSH_OPTS[@]}" "$WIN_HOST"
        "powershell -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand $encoded")

    if [ -n "$TIMEOUT_CMD" ]; then
        "$TIMEOUT_CMD" "$TIMEOUT" "${ssh_cmd[@]}" || return $?
    else
        "${ssh_cmd[@]}" || return $?
    fi
}

# ── 执行 ────────────────────────────────────────────────────────────────────

case "$MODE" in
    file)
        # 上传脚本到远端临时位置再执行。
        # 直接经 ssh 传脚本内容会受多层转义与编码影响（PowerShell 5.1 对
        # 无 BOM 的脚本按 GBK 解析），因此走文件传输更可靠。
        [ -f "$PAYLOAD" ] || { echo "脚本不存在: $PAYLOAD" >&2; exit 1; }

        remote_script="$WIN_ROOT/cache/temp/$(basename "$PAYLOAD")"
        remote_dir=$(dirname "$remote_script")

        run_remote_ps "New-Item -ItemType Directory -Force -Path '$remote_dir' | Out-Null" >/dev/null

        scp "${SSH_OPTS[@]}" "$PAYLOAD" "$WIN_HOST:$remote_script" >/dev/null

        arg_str=""
        for a in "${FILE_ARGS[@]:-}"; do
            [ -n "$a" ] && arg_str="$arg_str '$a'"
        done

        full="$env_prefix"
        [ -n "$WORK_DIR" ] && full="$full; Set-Location '$WORK_DIR'"
        full="$full; & '$remote_script'$arg_str; exit \$LASTEXITCODE"

        if $QUIET; then
            run_remote_ps "$full" 2>/dev/null || exit $?
        else
            run_remote_ps "$full" || {
                rc=$?
                [ $rc -eq 124 ] && echo "[win-exec] 执行超时（${TIMEOUT}s）" >&2
                exit $rc
            }
        fi
        ;;
    ps|command)
        full="$env_prefix"
        [ -n "$WORK_DIR" ] && full="$full; Set-Location '$WORK_DIR'"
        full="$full; $PAYLOAD; exit \$LASTEXITCODE"

        if $QUIET; then
            run_remote_ps "$full" 2>/dev/null || exit $?
        else
            run_remote_ps "$full" || {
                rc=$?
                [ $rc -eq 124 ] && echo "[win-exec] 执行超时（${TIMEOUT}s）" >&2
                exit $rc
            }
        fi
        ;;
esac
