#!/usr/bin/env bash
#
# host-lib.sh - 远程 Windows 主机的统一解析逻辑（被同目录脚本 source）
#
# 不是可执行脚本，没有 shebang 语义上的入口——只定义函数与常量。
#
# 解决的问题：本目录下的脚本原先各自硬编码默认主机
# `gongdewei@192.168.2.10`，只能服务那一台机器。要换机器就得改脚本，
# 而且改了之后两台机器没法共存。现在改成：
#
#   1. 主机在 hosts.conf 里登记一次（别名 → user@host + 部署根），各脚本共用
#   2. 也可以直接写 user@host，跳过注册表
#   3. 不传 --host 就报错——见下方「为什么不再有默认主机」
#
# ── 为什么不再有默认主机 ────────────────────────────────────────────────────
#
# 旧默认值是 192.168.2.10。留着一个「猜得到的默认主机」比报错更危险：
# 换到第二台机器后忘传 --host，命令会打到第一台上——同步、部署、甚至
# admin-init（会重置管理员密码）都可能静默作用在错误的机器上。
# 这类错误没有报错信息，只有事后才发现。所以宁可要求每次显式指定。
#
# ── 兼容 bash 3.2 ───────────────────────────────────────────────────────────
#
# macOS 自带 bash 3.2，没有关联数组、mapfile/readarray。本文件一律用
# 位置参数与普通变量实现（与 scripts/serve.sh 的约定一致）。

# ── 常量 ────────────────────────────────────────────────────────────────────

HOST_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# 注册表路径。WIN_HOSTS_FILE 可覆盖（便于测试与多套配置切换）
HOST_LIB_REGISTRY="${WIN_HOSTS_FILE:-$HOST_LIB_DIR/hosts.conf}"
HOST_LIB_EXAMPLE="$HOST_LIB_DIR/hosts.conf.example"

HOST_DEFAULT_ROOT="D:/deer-flow"
HOST_DEFAULT_SUBDIR="src"

# ── 输出兜底 ────────────────────────────────────────────────────────────────
#
# 调用方（sync-to-windows.sh / win-exec.sh）通常已经定义了 die/fail/warn。
# 这里用 declare -F 探测，避免重复定义时把调用方的配色输出覆盖掉。

host_has_func() { declare -F "$1" >/dev/null 2>&1; }

host_die() {
    if host_has_func die; then die "$1"; else printf '%s\n' "$1" >&2; exit 1; fi
}

host_warn() {
    if host_has_func warn; then warn "$1"; else printf '  [WARN] %s\n' "$1" >&2; fi
}

host_info() {
    if host_has_func info; then info "$1"; else printf '         %s\n' "$1"; fi
}

# ── 注册表读取 ──────────────────────────────────────────────────────────────

# 列出已登记的别名，每行一个。注册表不存在时静默返回空。
#
# 解析规则与 hosts.conf.example 的说明一致：空白分隔四列，`#` 开头是注释。
# `|| [ -n "$alias" ]` 是必需的老套路——文件末行没有换行时 read 返回非 0，
# 但那行内容已经读进来了，不补这个判断会丢掉最后一行。
host_list_aliases() {
    [ -f "$HOST_LIB_REGISTRY" ] || return 0
    local alias target root subdir rest
    while read -r alias target root subdir rest || [ -n "$alias" ]; do
        case "$alias" in ''|\#*) continue ;; esac
        printf '%s\n' "$alias"
    done < "$HOST_LIB_REGISTRY"
}

# 把别名列表拼成 "a、b、c" 形式，用于报错提示
_host_aliases_human() {
    # ⚠ 变量展开必须加花括号：紧跟其后的「、」是多字节 UTF-8，
    # 不加括号时 bash 会把这几个字节当成变量名的一部分（报 unbound variable）。
    local out="" name
    while read -r name; do
        [ -n "$name" ] || continue
        if [ -z "$out" ]; then out="$name"; else out="${out}、${name}"; fi
    done <<EOF
$(host_list_aliases)
EOF
    printf '%s' "$out"
}

# host_lookup_alias <别名>
#   命中 → 设置 HOST_TARGET / HOST_ROOT / HOST_SUBDIR 并返回 0
#   未命中 → 返回 1（不改动任何全局变量）
host_lookup_alias() {
    local want="$1" found=1
    [ -f "$HOST_LIB_REGISTRY" ] || return 1

    local alias target root subdir rest
    while read -r alias target root subdir rest || [ -n "$alias" ]; do
        case "$alias" in ''|\#*) continue ;; esac
        if [ "$alias" = "$want" ]; then
            if [ -z "$target" ]; then
                host_warn "注册表里 $want 这行缺少目标（第二列），已忽略"
                return 1
            fi
            HOST_TARGET="$target"
            HOST_ROOT="$root"
            HOST_SUBDIR="$subdir"
            found=0
            break
        fi
    done < "$HOST_LIB_REGISTRY"

    return $found
}

# ── 主解析入口 ──────────────────────────────────────────────────────────────

# host_resolve <--host 的值> <--root 的值> <--subdir 的值>
#
# 三个参数都传空串表示「未指定」。解析完成后读全局变量：
#   HOST_TARGET   user@host
#   HOST_ROOT     部署根（POSIX 风格，如 D:/deer-flow）
#   HOST_SUBDIR   代码子目录（如 src）
#
# 优先级（左优先）：
#   主机   --host  >  WIN_HOST 环境变量  >  报错
#   部署根 --root  >  注册表第三列      >  WIN_ROOT 环境变量  >  D:/deer-flow
#   子目录 --subdir>  注册表第四列      >  WIN_SUBDIR 环境变量 >  src
host_resolve() {
    local host_arg="$1" root_arg="$2" subdir_arg="$3"
    local requested="${host_arg:-${WIN_HOST:-}}"
    local entry_root="" entry_subdir="" target=""

    if [ -z "$requested" ]; then
        _host_die_no_host
    fi

    case "$requested" in
        *@*)
            # 直写 user@host：跳过注册表
            target="$requested"
            ;;
        *)
            if host_lookup_alias "$requested"; then
                target="$HOST_TARGET"
                entry_root="$HOST_ROOT"
                entry_subdir="$HOST_SUBDIR"
            else
                _host_die_unknown_alias "$requested"
            fi
            ;;
    esac

    HOST_TARGET="$target"
    HOST_ROOT="${root_arg:-${entry_root:-${WIN_ROOT:-$HOST_DEFAULT_ROOT}}}"
    HOST_SUBDIR="${subdir_arg:-${entry_subdir:-${WIN_SUBDIR:-$HOST_DEFAULT_SUBDIR}}}"
}

_host_die_no_host() {
    local msg="未指定目标主机。请用 --host 指定，例如："
    msg="$msg
    --host win2                      # 用 hosts.conf 里登记的别名
    --host gdw@192.168.31.129        # 或直接写 user@host"

    if [ -f "$HOST_LIB_REGISTRY" ]; then
        local aliases
        aliases="$(_host_aliases_human)"
        if [ -n "$aliases" ]; then
            msg="$msg

已登记的别名: $aliases"
        else
            msg="$msg

（$HOST_LIB_REGISTRY 里还没有登记任何主机）"
        fi
    else
        msg="$msg

注册表不存在: $HOST_LIB_REGISTRY
先复制模板并登记主机:
    cp $HOST_LIB_EXAMPLE $HOST_LIB_REGISTRY"
    fi

    host_die "$msg"
}

_host_die_unknown_alias() {
    local requested="$1"
    local aliases
    aliases="$(_host_aliases_human)"

    local msg="未知的主机别名: $requested"

    if [ -f "$HOST_LIB_REGISTRY" ]; then
        if [ -n "$aliases" ]; then
            msg="$msg

已登记的别名: $aliases"
        else
            msg="$msg

$HOST_LIB_REGISTRY 里还没有登记任何主机"
        fi
        msg="$msg

注册表: $HOST_LIB_REGISTRY"
    else
        msg="$msg

注册表不存在: $HOST_LIB_REGISTRY
先复制模板并登记主机:
    cp $HOST_LIB_EXAMPLE $HOST_LIB_REGISTRY"
    fi

    if [ -n "$aliases" ]; then
        msg="$msg

如果你本意是直接指定地址，请写成 user@host 形式（含 @ 才会被当作地址）:
    --host gdw@192.168.31.129"
    fi

    host_die "$msg"
}

# ── 派生值 ──────────────────────────────────────────────────────────────────

# 远端代码目录（POSIX 风格路径，供 ssh 命令内拼接）
host_remote_dir() { printf '%s/%s' "$HOST_ROOT" "$HOST_SUBDIR"; }

# 把 user@host 变成可安全用作文件名的字符串。
#
# 用途：本地同步戳需要按主机分开存。原先只有一个 .sync-state/last-sync，
# 两台机器共用会导致 `--list` 显示的是另一台机器的时间戳。
# 这里只保留 [A-Za-z0-9._-]，其余（@ : / 空格）一律换成下划线。
host_state_name() {
    printf '%s' "$1" | tr -c 'A-Za-z0-9._-' '_'
}
