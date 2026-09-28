#!/usr/bin/env bash
#
# win-exec.sh - 在 Windows 测试机上远程执行命令
#
# 用法:
#   ./scripts/windows-remote/win-exec.sh --host <别名|user@host> [选项] <命令>
#   ./scripts/windows-remote/win-exec.sh --host <主机> --ps <PowerShell 片段>
#   ./scripts/windows-remote/win-exec.sh --host <主机> --file <本地脚本.ps1> [-- arg...]
#
# 目标主机（必需，二者选一）:
#   --host win2                 hosts.conf 里登记的别名
#   --host gdw@192.168.31.129   直接写 user@host
#
# 选项:
#   --root PATH    远端部署根。默认：注册表 > WIN_ROOT > D:/deer-flow
#   --dir PATH     先切换到远端目录再执行
#   --timeout SECS SSH 超时，默认 300；长任务（装依赖、构建前端）务必调大
#   --quiet        仅输出命令的 stdout
#   --env K=V      设置远端环境变量，可重复
#
# 主机登记: 见同目录 hosts.conf.example —— 复制为 hosts.conf 后登记别名，
#           登记一次，本目录下所有脚本共用。
#
# 环境变量（都可被命令行参数覆盖）:
#   WIN_HOST   目标主机；命令行 --host 优先
#   WIN_ROOT   远端根目录，默认 D:/deer-flow
#
# 示例:
#   # 用单引号包裹裸命令；PATH 已自动前置工具链（见下方说明）
#   ./win-exec.sh --host win2 'node --version; pnpm --version; uv --version'
#
#   # 直接写 PowerShell 片段
#   ./win-exec.sh --host win2 'Get-ChildItem D:\deer-flow | Select-Object Name'
#
#   # 切换目录后执行
#   ./win-exec.sh --host win2 --dir 'D:/deer-flow/src' 'git status --short'
#
#   # 执行本地 .ps1 脚本（自动上传）
#   ./win-exec.sh --host win2 --file scripts/windows/check-env.ps1
#
# ⚠ 传入的命令不要再套一层引号。命令经 base64 传输，按原样交给 PowerShell：
#     正确: win-exec.sh --host win2 'node --version'
#     错误: win-exec.sh --host win2 '"node --version"'   # 被当成字符串字面量打印
#
# 重要说明 — 关于 PATH:
#   目标机系统 PATH 里通常已有一个 `C:\Program Files\nodejs`（旧测试机是
#   Node 16，新机是 Node 25），而 Windows 的解析顺序是「系统 PATH + 用户 PATH」，
#   系统级 Node 会遮蔽部署根下 tools\node 里的 Node 22。
#   本脚本默认把工具链目录前置到进程 PATH，保证 node/pnpm/uv 用对版本。
#   该目录随 --root 走，因此换部署根时无需改脚本。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

# 主机与远端路径由 host-lib.sh 统一解析（--host / WIN_HOST，别名查 hosts.conf）。
# 解析必须等参数解析完（--host 是个参数），所以这些值在下面才填。
source "$SCRIPT_DIR/host-lib.sh"

HOST_TARGET=""
HOST_ROOT=""

# 工具链目录（前置到 PATH，规避系统 Node 的遮蔽）。随 --root 走，见下方解析处。
TOOLS_PATH=""

WORK_DIR=""
TIMEOUT=300
QUIET=false
MODE="command"   # command | ps | file
PAYLOAD=""
HOST_ARG=""
ROOT_ARG=""
declare -a FILE_ARGS=()
declare -a ENV_VARS=()

# 取一个带值的选项：$1 是选项名（仅用于报错），$2 是值。
# 直接写 "$2" 在 set -u 下、选项位于末尾时会报 unbound variable。
need_value() {
    [ $# -ge 2 ] && [ -n "${2:-}" ] || {
        echo "$1 后面缺少值（用 --help 查看用法）" >&2
        exit 1
    }
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host)    need_value "$1" "${2:-}"; HOST_ARG="$2"; shift 2 ;;
        --root)    need_value "$1" "${2:-}"; ROOT_ARG="$2"; shift 2 ;;
        --dir)     WORK_DIR="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        --quiet)   QUIET=true; shift ;;
        --ps)      MODE="ps"; PAYLOAD="$2"; shift 2 ;;
        --file)    MODE="file"; PAYLOAD="$2"; shift 2 ;;
        --env)     ENV_VARS+=("$2"); shift 2 ;;
        --)        shift; FILE_ARGS=("$@"); break ;;
        -h|--help)
            # 打印文件头注释块：从第 2 行到 `set -euo pipefail` 之前。
            # 用 awk 而不是写死行号——头注释长度会变，写死会截断。
            awk 'NR>1 && /^set -euo pipefail/ { exit } NR>1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)         PAYLOAD="$1"; shift ;;
    esac
done

if [ -z "$PAYLOAD" ]; then
    echo "用法: $0 [--dir 目录] [--ps|--file] <命令或脚本>" >&2
    exit 1
fi

SSH_OPTS=(-o BatchMode=yes -o "ConnectTimeout=20" -o StrictHostKeyChecking=accept-new)

# ── 解析目标主机 ────────────────────────────────────────────────────────────

host_resolve "$HOST_ARG" "$ROOT_ARG" ""
TOOLS_PATH="$HOST_ROOT/tools/node;$HOST_ROOT/tools/uv"

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
    local ssh_cmd=("ssh" "${SSH_OPTS[@]}" "$HOST_TARGET"
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

        remote_script="$HOST_ROOT/cache/temp/$(basename "$PAYLOAD")"
        remote_dir=$(dirname "$remote_script")

        run_remote_ps "New-Item -ItemType Directory -Force -Path '$remote_dir' | Out-Null" >/dev/null

        scp "${SSH_OPTS[@]}" "$PAYLOAD" "$HOST_TARGET:$remote_script" >/dev/null

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
