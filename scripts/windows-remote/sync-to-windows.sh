#!/usr/bin/env bash
#
# sync-to-windows.sh - 把本地仓库同步到 Windows 测试机
#
# 用法:
#   ./scripts/windows-remote/sync-to-windows.sh              # 增量同步
#   ./scripts/windows-remote/sync-to-windows.sh --dry-run    # 只列出将同步的文件
#   ./scripts/windows-remote/sync-to-windows.sh --full       # 全量同步（忽略增量判断）
#   ./scripts/windows-remote/sync-to-windows.sh --delete     # 同步前清空远端目录
#   ./scripts/windows-remote/sync-to-windows.sh --list       # 显示上次同步状态
#
# 环境变量:
#   WIN_HOST   目标主机，默认 gongdewei@192.168.2.10
#   WIN_ROOT   远端根目录，默认 D:/deer-flow
#   WIN_SUBDIR 远端代码子目录，默认 src
#
# 实现说明:
#   目标机没有 rsync（实测 rsync/7z 均缺失），因此用
#   `tar | ssh | tar` 管道传输。两端都用 tar 是刻意的选择：
#
#   - 本机是 macOS，自带 openrsync 而非 GNU rsync，--delete/--exclude-from
#     等参数行为与 GNU 版有差异，不可靠。
#   - Windows 10 1803+ 自带 bsdtar（C:\WINDOWS\system32\tar.exe），
#     与 macOS 的 bsdtar 同源，兼容性最好。
#   - git 虽然两端都有，但仓库里有未提交的改动（scripts/windows/ 就是
#     未跟踪文件），git 路线会漏掉它们。
#
#   传输内容为纯源码：node_modules / .next / .venv 等平台相关的构建产物
#   必须排除——它们在 Windows 上无法使用，且体积巨大（本仓库 4.4G 中
#   约 4.35G 是这些目录）。
#
#   实测结论：中文文件名可正确传输。Windows 终端显示为乱码是 GBK 控制台
#   的显示问题，文件系统层面字节无误（已验证 UTF-8 字节一致）。

set -euo pipefail

# ── 配置 ────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd -P)"

WIN_HOST="${WIN_HOST:-gongdewei@192.168.2.10}"
WIN_ROOT="${WIN_ROOT:-D:/deer-flow}"
WIN_SUBDIR="${WIN_SUBDIR:-src}"

# 远端代码目录（POSIX 风格路径，供 ssh 命令内拼接）
REMOTE_DIR="$WIN_ROOT/$WIN_SUBDIR"
# 远端标记文件：记录最后一次同步的时间戳
REMOTE_STAMP="$REMOTE_DIR/.last-sync"

# 本地端的同步快照，与远端标记文件比对
LOCAL_STAMP_DIR="$REPO_ROOT/.sync-state"
LOCAL_STAMP="$LOCAL_STAMP_DIR/last-sync"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new)

# ── 输出 ────────────────────────────────────────────────────────────────────

if [ -t 1 ]; then
    C_RED=$'\033[0;31m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
    C_CYAN=$'\033[0;36m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_CYAN=''; C_DIM=''; C_OFF=''
fi

step() { printf '\n%s==> %s%s\n' "$C_CYAN" "$1" "$C_OFF"; }
ok()   { printf '  %s[ OK ]%s %s\n' "$C_GREEN" "$C_OFF" "$1"; }
warn() { printf '  %s[WARN]%s %s\n' "$C_YELLOW" "$C_OFF" "$1"; }
fail() { printf '  %s[FAIL]%s %s\n' "$C_RED" "$C_OFF" "$1" >&2; }
info() { printf '         %s%s%s\n' "$C_DIM" "$1" "$C_OFF"; }

die() { fail "$1"; exit 1; }

# ── 参数解析 ────────────────────────────────────────────────────────────────

DRY_RUN=false
FULL=false
DELETE=false
LIST_ONLY=false

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=true ;;
        --full)    FULL=true ;;
        --delete)  DELETE=true ;;
        --list)    LIST_ONLY=true ;;
        -h|--help)
            sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) die "未知参数: $1（用 --help 查看用法）" ;;
    esac
    shift
done

# ── 排除规则 ────────────────────────────────────────────────────────────────
#
# 这些目录/文件是平台相关或本地专属的，绝不能同步到 Windows：
# 在 macOS 上生成的原生二进制（sharp、esbuild 等）在 Windows 上无法运行。

EXCLUDES=(
    # 构建产物与依赖（平台相关，体积巨大）
    './node_modules'
    './frontend/node_modules'
    './backend/.venv'
    './frontend/.next'
    './build'
    './release'
    './dist'
    # 运行时数据与日志
    './logs'
    './temp'
    './.deer-flow'
    './backend/.deer-flow'
    './test-results'
    './playwright-report'
    # 缓存
    './.pytest_cache'
    './.ruff_cache'
    './backend/.pytest_cache'
    './__pycache__'
    # 版本控制与本地状态
    './.git'
    './.sync-state'
    # 本地专属配置（含密钥，应在目标机各自生成）
    './.env'
    './config.yaml'
    './extensions_config.json'
    './mcp_config.json'
    # macOS 产物
    './.DS_Store'
    './._*'
)

# 构造 tar 的排除参数（bsdtar 语法）。
#
# 结果存入全局数组 TAR_EXCLUDES，调用方用 "${TAR_EXCLUDES[@]}" 展开。
# 不用 mapfile/readarray —— macOS 自带 bash 3.2 没有这两个内建命令，
# 而本仓库的脚本约定是兼容 3.2（见 scripts/serve.sh）。
TAR_EXCLUDES=()
build_tar_excludes() {
    TAR_EXCLUDES=()
    local pattern
    for pattern in "${EXCLUDES[@]}"; do
        TAR_EXCLUDES+=(--exclude "$pattern")
    done
    # 任意深度的缓存与系统产物
    TAR_EXCLUDES+=(--exclude '*/__pycache__')
    TAR_EXCLUDES+=(--exclude '*.pyc')
    TAR_EXCLUDES+=(--exclude '*/.DS_Store')
    TAR_EXCLUDES+=(--exclude '*/._*')
}

# ── 远端执行封装 ────────────────────────────────────────────────────────────
#
# 远端默认 shell 是 PowerShell（由 install-sshd.ps1 设置）。
#
# 命令经 base64(-EncodedCommand) 传输，而不是拼进 `-Command "..."`：
# 后者要穿过 bash → ssh → cmd → PowerShell 四层转义，实测 PATH 前置语句会被
# 吞掉，导致命令静默用到系统 Node 16。base64 不含 shell 元字符，彻底消除该类问题。

remote_ps() {
    # $1 = PowerShell 代码（纯文本）
    local code="[Console]::OutputEncoding = [Text.Encoding]::UTF8
# 进度流会被 PowerShell 序列化成 CLIXML 混入 stdout（首次调用 cmdlet 时
# 尤其明显），污染所有下游的字符串比较。改为彻底关闭进度输出。
\$ProgressPreference = 'SilentlyContinue'
\$ErrorActionPreference = 'Stop'
$1"

    local encoded
    encoded=$(printf '%s' "$code" | iconv -f UTF-8 -t UTF-16LE | base64 | tr -d '\n')

    # -OutputFormat Text 是抑制 CLIXML 的关键：默认情况下远端 PowerShell 检测到
    # 输出未重定向到控制台时，会用 CLIXML 序列化，导致输出里夹带 XML 噪声。
    ssh "${SSH_OPTS[@]}" "$WIN_HOST" \
        "powershell -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand $encoded"
}

# ── 前置检查 ────────────────────────────────────────────────────────────────

step "检查连通性"

[ -n "$WIN_HOST" ] || die "WIN_HOST 为空"

if ! ssh "${SSH_OPTS[@]}" "$WIN_HOST" 'exit 0' 2>/dev/null; then
    die "无法连接 $WIN_HOST —— 检查网络、SSH 服务与密钥"
fi
ok "SSH 连接正常: $WIN_HOST"

# 确认远端 tar 可用（Windows 10 1803+ 自带 bsdtar）
if ! remote_ps "if (Test-Path 'C:\WINDOWS\system32\tar.exe') { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
    die "远端缺少 C:\\WINDOWS\\system32\\tar.exe —— 本系统可能低于 Windows 10 1803"
fi
ok "远端 tar 可用"

# ── --list：显示同步状态 ────────────────────────────────────────────────────

if $LIST_ONLY; then
    step "同步状态"

    if [ -f "$LOCAL_STAMP" ]; then
        info "本地记录: $(cat "$LOCAL_STAMP")"
    else
        info "本地记录: (无)"
    fi

    remote_stamp=$(remote_ps "if (Test-Path '$REMOTE_STAMP') { Get-Content '$REMOTE_STAMP' } else { 'NONE' }" 2>/dev/null | tr -d '\r')
    info "远端记录: $remote_stamp"

    # 注意：判断的是远端路径，必须经 SSH 查询，不能用本地的 test -d
    exists=$(remote_ps "if (Test-Path '$REMOTE_DIR') { 'YES' } else { 'NO' }" 2>/dev/null | tr -d '\r')
    if [ "$exists" = "YES" ]; then
        count=$(remote_ps "(Get-ChildItem -Recurse -File '$REMOTE_DIR' -ErrorAction SilentlyContinue | Measure-Object).Count" 2>/dev/null | tr -d '\r')
        ok "远端文件数: $count"
        info "远端路径: $REMOTE_DIR"
    else
        warn "远端目录不存在: $REMOTE_DIR"
    fi
    exit 0
fi

# ── 判断是否需要同步 ────────────────────────────────────────────────────────

if ! $FULL && ! $DELETE; then
    # 与远端记录的时间戳比对：若本地无变更（按最新修改时间）则跳过
    remote_stamp=$(remote_ps "if (Test-Path '$REMOTE_STAMP') { Get-Content '$REMOTE_STAMP' } else { 'NONE' }" 2>/dev/null | tr -d '\r')

    if [ "$remote_stamp" != "NONE" ] && [ -n "$remote_stamp" ]; then
        # 找出比上次同步时间更新的文件（排除构建产物）
        find_args=("$REPO_ROOT" -newermt "$remote_stamp" -type f)
        changed=$(find "${find_args[@]}" 2>/dev/null \
            | grep -vE '/(node_modules|\.next|\.venv|\.git|logs|temp|test-results|\.deer-flow|__pycache__|\.sync-state|release|build|dist)/' \
            | grep -vE '\.pyc$|\.DS_Store$|/\._' \
            | head -5 || true)

        if [ -z "$changed" ]; then
            ok "自 $remote_stamp 以来无变更，跳过同步"
            info "强制同步请加 --full"
            exit 0
        fi
        info "检测到变更，例如:"
        printf '%s\n' "$changed" | while read -r f; do info "  ${f#"$REPO_ROOT"/}"; done
    else
        info "远端无同步记录，执行首次全量同步"
    fi
fi

# ── dry-run ─────────────────────────────────────────────────────────────────

if $DRY_RUN; then
    step "将同步的文件（dry-run，仅统计）"

    build_tar_excludes
    count=$(cd "$REPO_ROOT" && tar "${TAR_EXCLUDES[@]}" -cf - . 2>/dev/null | tar -tf - 2>/dev/null | wc -l | tr -d ' ')
    total=$(cd "$REPO_ROOT" && tar "${TAR_EXCLUDES[@]}" -cf - . 2>/dev/null | wc -c | tr -d ' ')

    ok "文件数: $count"
    ok "压缩后约: $(( total / 1024 )) KB"
    info "目标: $WIN_HOST:$REMOTE_DIR"
    info "实际同步请去掉 --dry-run"
    exit 0
fi

# ── 准备远端目录 ────────────────────────────────────────────────────────────

step "准备远端目录"

if $DELETE; then
    warn "清空远端目录（--delete）: $REMOTE_DIR"
    remote_ps "Remove-Item -Recurse -Force '$REMOTE_DIR' -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null; Write-Output DONE" >/dev/null
    ok "远端目录已重建"
else
    remote_ps "New-Item -ItemType Directory -Force -Path '$REMOTE_DIR' | Out-Null; Write-Output DONE" >/dev/null
    ok "远端目录就绪: $REMOTE_DIR"
fi

# ── 同步 ────────────────────────────────────────────────────────────────────

step "同步源码"

build_tar_excludes

info "打包并传输（本地 tar → ssh → 远端 tar）..."

sync_start=$(date +%s)

# 用管道直传，不落盘中转。
# --no-same-owner / --no-same-permissions：macOS 的 uid/gid 在 Windows 上无意义，
# 且 Windows 的权限模型与 POSIX 不同，保留会报错。
if ! ( cd "$REPO_ROOT" && tar "${TAR_EXCLUDES[@]}" -czf - . ) 2>/dev/null \
    | ssh "${SSH_OPTS[@]}" "$WIN_HOST" \
        "powershell -NoProfile -ExecutionPolicy Bypass -Command \"cd '$REMOTE_DIR'; & C:\WINDOWS\system32\tar.exe -xzf - --no-same-owner --no-same-permissions; exit \$LASTEXITCODE\""
then
    die "同步失败 —— 检查上方错误输出，以及远端磁盘空间"
fi

sync_end=$(date +%s)
ok "同步完成，耗时 $(( sync_end - sync_start )) 秒"

# ── 校验 ────────────────────────────────────────────────────────────────────

step "校验"

remote_count=$(remote_ps "(Get-ChildItem -Recurse -File '$REMOTE_DIR' -ErrorAction SilentlyContinue | Measure-Object).Count" 2>/dev/null | tr -d '\r')
if [ -n "$remote_count" ] && [ "$remote_count" -gt 0 ] 2>/dev/null; then
    ok "远端文件数: $remote_count"
else
    fail "远端目录为空，同步可能未生效"
    exit 1
fi

# 抽查关键文件是否存在
for f in package.json frontend/package.json backend/pyproject.toml; do
    if remote_ps "if (Test-Path '$REMOTE_DIR/$f') { Write-Output YES } else { Write-Output NO }" 2>/dev/null | grep -q YES; then
        ok "$f"
    else
        fail "$f 缺失"
    fi
done

# ── 记录同步时间戳 ──────────────────────────────────────────────────────────

step "记录状态"

stamp=$(date '+%Y-%m-%d %H:%M:%S')

# 标记文件必须写到远端，经 SSH 写入 —— 本地路径与远端路径不通用。
# 用 Set-Content 并指定 UTF8，避免 PowerShell 5.1 默认写成 UTF-16LE。
remote_ps "Set-Content -Path '$REMOTE_STAMP' -Value '$stamp' -Encoding UTF8" >/dev/null 2>&1 \
    || warn "无法写入远端标记文件（不影响同步结果）"

mkdir -p "$LOCAL_STAMP_DIR"
echo "$stamp" > "$LOCAL_STAMP"
ok "同步时间: $stamp"

printf '\n%s========================================%s\n' "$C_GREEN" "$C_OFF"
printf '%s  同步完成%s\n' "$C_GREEN" "$C_OFF"
printf '%s========================================%s\n' "$C_GREEN" "$C_OFF"
printf '\n  源目录: %s\n' "$REPO_ROOT"
printf '  目标  : %s:%s\n' "$WIN_HOST" "$REMOTE_DIR"
printf '\n  下一步（在 Windows 上安装依赖）:\n'
printf '    ./scripts/windows-remote/win-exec.sh --dir %s "uv sync --all-packages"\n' "$REMOTE_DIR"
printf '\n'
