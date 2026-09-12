<#
.SYNOPSIS
    初始化 DeerFlow 的管理员账号：首次创建，或忘记密码时重置。

.DESCRIPTION
    本部署启用了登录认证 + 管理员审批（config.yaml 的
    auth.local_registration.require_admin_approval = true）：新用户注册后是
    pending 状态，必须由管理员在后台审批才能登录。所以系统上线前**必须先有
    一个管理员账号**。交互式做法是浏览器打开前端 /setup 页面手动建，无人值守
    部署用不了；本脚本就是这一步的自动化。

    按「库里有没有管理员」自动走两条分支：

      1) 已有管理员 —— 调用上游 CLI 重置密码
             uv run python -m app.gateway.auth.reset_admin [--email <邮箱>]
         CLI 的设计是「新密码写进 0600 凭据文件、不打印到 stdout」，避免明文
         密钥进入 CI / 日志聚合系统；它只往 stdout 打文件路径。
         本脚本再把该文件读回来，在控制台醒目显示一次给运维记录。

      2) 库里没有管理员 —— 用本脚本自带的 Python 引导程序创建首个管理员
         为什么不能只靠 CLI：reset_admin 的语义是「在已存在的用户上重置密码」，
         库为空时它只会报 "no admin user found" 并以 1 退出，永远建不出第一个
         账号来。引导程序复刻后端 /api/v1/auth/initialize 端点的语义
         （create_user(system_role='admin', needs_setup=False)），并复用上游
         同一个 write_initial_credentials() 落盘凭据，因此产出的凭据文件在
         格式、权限、路径上与 CLI 完全一致。

    ⚠ 幂等性：重复运行会**重置**已有管理员的密码（这是 CLI 的既有行为）。
      旧密码立即失效；token_version 递增还会让已签发的登录会话全部作废。
      脚本在动手之前会明确提示这一点，不做二次确认（部署场景非交互）。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 凭据文件路径必须问 Python 要，不能按「数据目录」拼
      凭据文件落在 base_dir（= os.getenv('DEER_FLOW_HOME')，缺失时
      project_root()/.deer-flow），而 base_dir 与 config.yaml 里
      database.sqlite_dir 指向的数据目录**不是同一回事**：实测目标机上一度因
      用户级环境变量 DEER_FLOW_HOME=D:\deer-flow 而 sqlite_dir=D:\deer-flow\data，
      于是凭据文件落在 D:\deer-flow\ 下、不在 data\ 里。

      这个值在 PowerShell 里是算不准的，因为它有一条 cwd 相关的分支：
      DEER_FLOW_HOME 可能来自 app_config.py 顶层的 load_dotenv()，而那次查找
      是从 cwd 逐级向上搜 .env。实测同一条命令换 cwd 结果就变
      （cwd=src\backend→data\，cwd=D:\deer-flow→.deer-flow\）。本脚本恰好
      在 src\backend 下调用，所以碰巧正确 —— 但「碰巧」不能作为依据。

      唯一可靠的做法是让 Python 自己说出 get_paths().base_dir ——
      本脚本用同一个引导程序（base-dir 子命令）取这个值，与 CLI 的解析结果
      完全一致。另外服务侧的 start.ps1 已显式 set DEER_FLOW_HOME，使
      Gateway 进程的 base_dir 与 cwd 无关；本脚本因此与之对齐。

    · 必须显式设 PYTHONPATH=<src>\backend
      CLI 的模块路径是 app.gateway.auth.reset_admin，而 app 包在 src\backend。
      实测不设时直接 ModuleNotFoundError: No module named 'app'。虽然 cd 到该
      目录后 Python 会把当前目录加进 sys.path，但 uv run 会先切一层自己的运行
      上下文，不能指望它（start.ps1 的 Gateway 启动也是同样的踩坑与结论）。

    · 原生进程的输出一律走 cmd 重定向落日志文件，不用 `&` 直连控制台
      实测：PowerShell 5.1 在 $ErrorActionPreference='Stop' 下，原生命令往
      stderr 写内容会抛 NativeCommandError，而本脚本恰恰会命中「重置不存在的
      用户」这种往 stderr 写错误的分支——用管道读取会被异常打断。

    · 日志里绝不写密码
      密码只走控制台，写日志时用 **** 代替（日志会被打包、上传、贴进工单）。

.PARAMETER Email
    管理员邮箱。
      · 存在该邮箱的管理员  -> 重置它的密码（旧密码失效）；
      · 不存在（含空库）    -> 用这个邮箱创建管理员；
      · 不传                -> 走 CLI 默认行为（取库里第一个管理员）。
        库为空时无从确定要创建谁，脚本会明确报错并要求补 -Email。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准。

.PARAMETER ShowOnly
    只读取并展示已有凭据文件，不做任何写操作（不重置、不创建、不改库）。
    用于「密码已经拿到了，只想再看一眼」或核对当前账号。

.EXAMPLE
    # 首次部署：创建首个管理员
    .\admin-init.ps1 -Email admin@sz-jlc.com

.EXAMPLE
    # 忘记密码：重置（旧密码立即失效，已登录会话全部作废）
    .\admin-init.ps1 -Email admin@sz-jlc.com

.EXAMPLE
    # 不指定邮箱：重置库里第一个管理员（库为空时会报错提示补 -Email）
    .\admin-init.ps1

.EXAMPLE
    # 只看已有凭据文件，不重新生成
    .\admin-init.ps1 -ShowOnly

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$Email,

    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [switch]$ShowOnly
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让未捕获异常终止脚本，但「终止」不等于「非零
# 退出」：PowerShell 会把它当命令错误继续跑调用方脚本，退出码仍是 0，部署编排
# 会误判成「初始化成功」。这里显式接管，保证「异常 => 退出码 1 + 出错位置」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionString)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非管理员初始化失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

# ── 引入公共模块 ────────────────────────────────────────────────────────────

if ($RootDir) {
    $env:DEER_FLOW_DEPLOY_ROOT = $RootDir
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$modulePath = Join-Path $scriptDir 'DeerFlow.Common.psm1'
if (-not (Test-Path $modulePath)) {
    Write-Host "  [FAIL] 未找到公共模块: $modulePath" -ForegroundColor Red
    exit 1
}
Import-Module $modulePath -Force

# ⚠ 必须调用：uv 要从工具链目录取，且它保证脚本内做工具探测时解析到的路径与
#   后续实际执行的一致（详见 Common 模块的注释）。
Add-ToolchainToPath

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$rootDir    = Get-DeerFlowRoot
$srcDir     = Get-DeerFlowSrcDir
$dataDir    = Get-DeerFlowDataDir
$logsDir    = Get-DeerFlowLogsDir
$cacheDir   = Get-DeerFlowCacheDir
$toolsDir   = Get-DeerFlowToolsDir
$backendDir = Join-Path $srcDir 'backend'
$configPath = Get-DeerFlowConfigPath

$logFile = Join-Path $logsDir 'admin-init.log'

# Python 引导程序的落地位置。放 cache\temp 而不是仓库目录：它是运行期产物，
# 不该被代码同步覆盖，也不该混进 git 状态。
$bootstrapDir  = Join-Path $cacheDir 'temp'
$bootstrapPath = Join-Path $bootstrapDir 'deerflow-admin-bootstrap.py'

# base_dir 解析失败时的兜底（正常永远用不到，见 Get-DeerFlowBaseDir 的注释）。
$fallbackBaseDir = $dataDir

$uvExe = Join-Path (Join-Path $toolsDir 'uv') 'uv.exe'
if (-not (Test-Path $uvExe)) { $uvExe = 'uv' }   # 退回 PATH 查找

# 单步超时：首次建表 / alembic upgrade 实测 3-6 秒，正常路径远低于 60s；
# 给 180s 是为了区分「慢」与「卡死」。
$script:StepTimeoutSeconds = 180

# 展示给运维的访问地址（默认本机，能识别默认路由网卡时换成内网 IP）。
$script:DisplayHost = 'localhost'

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Get-ConfigAllowedEmailDomains {
    <#
    .SYNOPSIS
        从 config.yaml 的 auth 段读出 allowed_email_domains（小写）。

    .DESCRIPTION
        为什么不用 YAML 库：目标机是内网离线环境，装不了 powershell-yaml；
        而这里只需要一个键，按结构扫一遍足够。同 init-config.ps1 的做法：
        只在 auth 段内查找，避免误命中注释或别处的同名键（比如 OIDC 的
        providers.*.allowed_email_domains —— 那是 provider 私有的白名单，
        与本地账号策略无关）。

        支持块序列（- a.com）与内联序列（[a.com, b.com]）两种写法。
        解析失败返回空数组，由调用方决定是「跳过校验」还是报错——本脚本
        只做提前警告，不因解析失败中断。

    .PARAMETER Path
        config.yaml 路径。

    .OUTPUTS
        System.String[]
    #>
    param([Parameter(Mandatory = $true)][string]$Path)

    $result = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path $Path)) { return $result.ToArray() }

    $lines = [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)

    $inAuth = $false
    $inList = $false

    foreach ($line in $lines) {
        if (-not $inAuth) {
            if ($line -match '^auth\s*:') { $inAuth = $true }
            continue
        }

        # 下一个顶层键 ⇒ auth 段结束
        if ($line -match '^[A-Za-z_]') { break }

        if ($inList) {
            if ($line -match '^\s+-\s*(.+?)\s*$') {
                $value = $Matches[1].Trim().Trim('"', "'")
                if ($value) { $result.Add($value.ToLowerInvariant()) }
                continue
            }
            # 列表项之后出现的下一个字段 ⇒ 列表结束（注释与空行继续留在列表内）
            if ($line -match '^\s*[A-Za-z_]') { $inList = $false }
            continue
        }

        if ($line -match '^\s*allowed_email_domains\s*:\s*(.*)$') {
            $inline = $Matches[1].Trim()
            if ($inline) {
                foreach ($item in ($inline.Trim('[', ']') -split ',')) {
                    $value = $item.Trim().Trim('"', "'")
                    if ($value) { $result.Add($value.ToLowerInvariant()) }
                }
            } else {
                $inList = $true
            }
        }
    }

    return $result.ToArray()
}

function Get-DeerFlowLanIp {
    <#
    .SYNOPSIS
        返回本机用于内网访问的 IPv4 地址；取不到时返回 $null。

    .DESCRIPTION
        判据是「默认路由所在网卡的地址」，而不是「第一个非环回地址」。
        与 start.ps1 里的同名逻辑保持一致：目标机装了 VMware / VirtualBox，
        会多出 192.168.137.1 / 192.168.56.1 这类虚拟网卡地址，按「第一个」取
        几乎必然取到虚拟网卡，打印出来的 URL 从别的机器连不上。

        （start.ps1 的同名函数是脚本内私有的，没有导出到 Common 模块，
        因此这里复制一份。改动时请两处同步。）
    #>
    param()

    $routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
    if ($routes.Count -eq 0) { return $null }

    $best = $routes | Sort-Object RouteMetric, ifMetric | Select-Object -First 1
    if (-not $best) { return $null }

    $addr = @(Get-NetIPAddress -InterfaceIndex $best.ifIndex -AddressFamily IPv4 `
                                -ErrorAction SilentlyContinue |
              Where-Object { $_.IPAddress -ne '127.0.0.1' } |
              Select-Object -First 1)
    if ($addr.Count -eq 0) { return $null }

    return $addr[0].IPAddress
}

function Write-BootstrapScript {
    <#
    .SYNOPSIS
        把引导程序写到 cache\temp\deerflow-admin-bootstrap.py。

    .DESCRIPTION
        两个子命令（用同一个文件而不是内联 python -c，见下）：

            base-dir              打印 get_paths().base_dir（凭据文件所在目录）
            create <email>        创建首个管理员并把密码写进凭据文件

        为什么不做 `python -c "<代码>"`：这段代码要经 PowerShell 拼命令行、
        cmd 解析、再到 Python，引号要穿三层（文件可执行路径、参数引号、代码内
        引号）。实测这种嵌套很容易在某一层被吃掉。写文件则只需要拼一个路径参数。

        内容用单引号 here-string 承载（$ 与 {} 原样保留，不被 PowerShell 插值），
        且刻意保持纯 ASCII —— 文件按 UTF-8 无 BOM 写盘，纯 ASCII 可以彻底排除
        编码因素带来的语法错误。

        脚本里用的是单引号 docstring（不是 """）：docstring 里的 \w 之类的
        反斜杠序列在普通字符串里会触发 SyntaxWarning: invalid escape sequence，
        会让 stdout 里混进一行警告。单引号同样是文档字符串，且不解释转义。

    .PARAMETER Path
        目标文件绝对路径（父目录自动创建）。
    #>
    param([Parameter(Mandatory = $true)][string]$Path)

    $source = @'
"""Create the first DeerFlow administrator / resolve DeerFlow's base dir.

Invoked by scripts\windows\admin-init.ps1. Kept in pure ASCII on purpose:
the file is written as UTF-8 without BOM, so ASCII removes any chance of a
decoding-related syntax error.

    base-dir          print get_paths().base_dir (where the credential file lives)
    create <email>    create the first admin and write its password to the
                      same 0600 credential file used by app.gateway.auth.reset_admin
                      (the password is never printed to stdout)
"""

import asyncio
import secrets
import sys


def print_base_dir() -> int:
    # Import only the paths module: no app config, no DB, no side effects.
    from deerflow.config.paths import get_paths

    print(str(get_paths().base_dir))
    return 0


async def create_admin(email: str) -> int:
    from app.gateway.auth.credential_file import write_initial_credentials
    from app.gateway.deps import get_local_provider
    from deerflow.config import get_app_config
    from deerflow.persistence.engine import close_engine, init_engine_from_config

    config = get_app_config()
    await init_engine_from_config(config.database)
    try:
        provider = get_local_provider()

        # Guard against a concurrent writer (a second deploy racing, or the
        # /initialize endpoint being called at the same time): never create a
        # second admin behind the operator's back.
        if await provider.count_admin_users() > 0:
            print("Error: an admin already exists; refusing to create another one.", file=sys.stderr)
            return 1

        password = secrets.token_urlsafe(16)
        user = await provider.create_user(
            email=email,
            password=password,
            system_role="admin",
            needs_setup=False,
        )

        cred_path = write_initial_credentials(str(user.email), password, label="initial")
        print("Created admin: %s" % user.email)
        print("Credentials written to: %s (mode 0600)" % cred_path)
        print("Change the password after the first login, then delete the credential file.")
        return 0
    finally:
        await close_engine()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("usage: deerflow-admin-bootstrap.py base-dir|create <email>", file=sys.stderr)
        return 2
    if args[0] == "base-dir":
        return print_base_dir()
    if args[0] == "create":
        if len(args) < 2 or not args[1].strip():
            print("Error: create requires an email argument.", file=sys.stderr)
            return 2
        return asyncio.run(create_admin(args[1].strip()))
    print("Error: unknown command %r" % args[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
'@

    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }

    # Write-TextFileNoBom：无 BOM，Python 读到首行就是 import，不会撞
    # "invalid character" 语法错误（Set-Content -Encoding UTF8 在 5.1 下会加 BOM）。
    Write-TextFileNoBom -Path $Path -Content $source
}

function Read-AdminCredentialFile {
    <#
    .SYNOPSIS
        解析凭据文件，返回含 Email / Password / Path 的对象；不可用时返回 $null。

    .DESCRIPTION
        文件格式由上游 credential_file.py 生成（固定两行键值 + 若干 # 注释）：

            email: admin@sz-jlc.com
            password: <token_urlsafe(16)>

        按「行首键名」取值而不是按行号：注释行数变化、或将来上游加字段，
        按行号取都会静默错位。读不到 email 或 password 任一，即视为不可用。

        必须显式按 UTF-8 读（.NET ReadAllText）：Get-Content 在 5.1 下对无 BOM
        文件会按 ANSI(GBK) 解码，值里若出现非 ASCII 会乱码。

    .PARAMETER Path
        凭据文件绝对路径。
    #>
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path $Path)) { return $null }

    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)

    $email = $null
    $password = $null

    foreach ($line in ($text -split "`r?`n")) {
        $t = $line.Trim()
        if ($t -match '^email:\s*(.+)$') {
            $email = $Matches[1].Trim()
        } elseif ($t -match '^password:\s*(.+)$') {
            $password = $Matches[1].Trim()
        }
    }

    if (-not $email -or -not $password) { return $null }

    return [pscustomobject]@{
        Email    = $email
        Password = $password
        Path     = $Path
    }
}

function Write-AdminInitLogHead {
    <#
    .SYNOPSIS
        清空本次运行的日志文件并写入运行头。

    .DESCRIPTION
        必须清空：日志用 cmd 的 >> 追加，不清空的话上一次运行的报错会一直堆在
        文件里，失败时回显的「日志尾部」可能是几天前的旧内容。

        用 Write-TextFileNoBom（Common 模块）而不是 Set-Content -Encoding UTF8：
        后者在 5.1 下会写 BOM，而 cmd 的重定向按字节追加，BOM 会在文件中段插入
        EF BB BF 污染后续读取。
    #>
    param([Parameter(Mandatory = $true)][string]$LogFile)

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $head = "================================================================`r`n" +
            "DeerFlow 管理员初始化 (admin-init.ps1)`r`n" +
            "运行时间: $stamp`r`n" +
            "部署根目录: $rootDir`r`n" +
            "代码目录: $srcDir`r`n" +
            "数据目录: $dataDir`r`n" +
            "================================================================`r`n"

    Write-TextFileNoBom -Path $LogFile -Content $head
}

function Write-LogTail {
    <#
    .SYNOPSIS
        打印日志文件最后若干行（失败诊断用）。

    .DESCRIPTION
        用 Common 的 Read-LogFileLines 而不是 [System.IO.File]::ReadAllLines：
        后者以 FileShare.Read 打开，若外部进程仍持有日志写句柄会撞共享冲突，
        导致诊断信息一条都打不出来。读不到时它返回空数组，不会抛异常。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $false)][int]$Lines = 30
    )

    $all = @(Read-LogFileLines -Path $LogFile)

    if ($all.Count -eq 0) {
        if (Test-Path $LogFile) {
            Write-Fail "日志文件为空或正被独占: $LogFile"
        } else {
            Write-Fail "没有生成日志文件: $LogFile"
        }
        return
    }

    $start = [math]::Max(0, $all.Count - $Lines)
    $shown = $all.Count - $start

    Write-Host ""
    Write-Host "  ──── 日志尾部（最后 $shown 行 / 共 $($all.Count) 行）────" -ForegroundColor Red
    for ($i = $start; $i -lt $all.Count; $i++) {
        Write-Host "  | $($all[$i])" -ForegroundColor DarkGray
    }
    Write-Host "  ──── 完整日志: $LogFile ────" -ForegroundColor Red
}

function Invoke-UvCommand {
    <#
    .SYNOPSIS
        在 src\backend 下执行 `uv <参数...>` 并同步等待，返回退出码与耗时。

    .DESCRIPTION
        经 cmd.exe 而不是直接 `& $uvExe`，理由：

          · 退出码取 Start-Process 的 ExitCode，不受 PowerShell 对原生命令 stderr
            的处理影响。实测 5.1 在 $ErrorActionPreference='Stop' 下，原生命令写
            stderr 会抛 NativeCommandError —— 本脚本恰会走「重置不存在的用户」
            这种往 stderr 写错误的分支，直接用管道读会被异常打断。
          · 用 cmd 重定向收集输出，比 Start-Process -RedirectStandardOutput 省事
            （5.1 上后者要求 stdout/stderr 分别落两个文件，还得自己交错合并）。
          · 用追加 >> 而不是覆盖 >：> 会在子进程启动前就截断文件，把调用方刚写好
            的日志头（运行时间 / 路径）整段抹掉。截断职责已由
            Write-AdminInitLogHead 承担。

        -CaptureStdout 用于「需要拿到 stdout 的值」的场景（如解析 base_dir）：
        此时 stdout 写到单独的文件（cmd 的 > ），stderr 仍追加进日志，
        保证诊断信息不丢。不指定时两者都进日志（等价于 2>&1 >> log）。

        子进程环境：PYTHONPATH / PYTHONUTF8 由调用方在本进程设置，cmd 子进程继承。

    .PARAMETER Arguments
        uv 的参数数组（含子命令），如 @('run','python','-m',...)。

    .PARAMETER LogFile
        日志文件绝对路径。

    .PARAMETER TimeoutSeconds
        超时秒数，超时会 taskkill /F /T 连子进程树一起清掉。

    .PARAMETER CaptureStdout
        为真时把 stdout 落盘到临时文件并随结果返回（Stdout 字段）。

    .OUTPUTS
        PSCustomObject（TimedOut / ExitCode / CommandLine / Stdout）
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $false)][int]$TimeoutSeconds = 180,
        [Parameter(Mandatory = $false)][switch]$CaptureStdout
    )

    # 参数含空白或引号时补双引号。本脚本的参数只有开关、模块名与脚本路径，
    # 不含 cmd 元字符（& | < > ^ ( )），因此不需要更复杂的转义。
    $quoted = @()
    foreach ($a in $Arguments) {
        if ($a -match '[\s"]') {
            $quoted += '"' + ($a -replace '"', '\"') + '"'
        } else {
            $quoted += $a
        }
    }

    $inner = 'cd /d "' + $backendDir + '" && "' + $uvExe + '" ' + ($quoted -join ' ')

    $stdoutFile = $null
    if ($CaptureStdout) {
        $stdoutFile = Join-Path $env:TEMP ('deerflow-admin-init-' + [guid]::NewGuid().ToString('N') + '.out')
        $cmdLine = '(' + $inner + ') > "' + $stdoutFile + '" 2>> "' + $LogFile + '"'
    } else {
        $cmdLine = '(' + $inner + ') >> "' + $LogFile + '" 2>&1'
    }

    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $proc = Start-Process -FilePath $cmdExe `
                          -ArgumentList @('/d', '/s', '/c', $cmdLine) `
                          -PassThru -WindowStyle Hidden

    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)

    if (-not $finished) {
        # ⚠ 必须用 $proc.Id 而不是 $proc.ProcessId：Start-Process -PassThru 返回的是
        #   System.Diagnostics.Process，进程号属性叫 Id。ProcessId 是 CIM 上的名字
        #   （Common 模块里到处在用，很容易顺手写错），写错时 PowerShell 静默求值成
        #   $null，taskkill 收到空 PID 只会报参数错误并以 1 退出——结果就是
        #   「超时判定正确、进程树一个都没杀掉」（install.ps1 已实测确认）。
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        & $taskkill /F /T /PID $proc.Id 2>&1 | Out-Null
        return [pscustomobject]@{ TimedOut = $true; ExitCode = -1; CommandLine = $cmdLine; Stdout = '' }
    }

    $code = $proc.ExitCode
    if ($null -eq $code) { $code = -1 }

    $stdout = ''
    if ($stdoutFile -and (Test-Path $stdoutFile)) {
        try {
            $stdout = ([System.IO.File]::ReadAllText($stdoutFile, [System.Text.Encoding]::UTF8)).Trim()
        } catch {
            $stdout = ''
        }
        Remove-Item -Path $stdoutFile -Force -ErrorAction SilentlyContinue
    }

    return [pscustomobject]@{ TimedOut = $false; ExitCode = $code; CommandLine = $cmdLine; Stdout = $stdout }
}

function Get-DeerFlowBaseDir {
    <#
    .SYNOPSIS
        让 Python 自己报出 DeerFlow 的 base_dir（凭据文件所在目录）。

    .DESCRIPTION
        为什么必须问 Python，而不是在 PowerShell 里按 DEER_FLOW_HOME /
        <project_root>\.deer-flow 自己算一遍（详见文件头注释）：

          · config.yaml 的 sqlite_dir（数据目录）与 base_dir 没有任何关系，
            照数据目录拼会拼错；
          · .env 里的 DEER_FLOW_HOME 对 uv run 起的进程并不生效（实测目标机
            该进程里 DEER_FLOW_PROJECT_ROOT 为空，说明 .env 没被加载），
            PowerShell 侧读 .env 反而会读出一个「Python 并不认」的值；
          · 只能有唯一一份解析逻辑，而那份逻辑就在 deerflow.config.paths 里。

        解析失败（uv 报错 / stdout 不像路径）时返回 $fallbackBaseDir 并警告：
        此时凭据文件的回读仍以 CLI 自己打印的路径为准，不依赖这里的值。

    .OUTPUTS
        System.String —— 绝对路径。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$BootstrapPath,
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Fallback
    )

    $result = Invoke-UvCommand -Arguments @('run', 'python', $BootstrapPath, 'base-dir') `
                -LogFile $LogFile -TimeoutSeconds $script:StepTimeoutSeconds -CaptureStdout

    if ($result.TimedOut) {
        Write-Warn "解析 base_dir 超时（超过 $($script:StepTimeoutSeconds)s），改用兜底目录"
        return $Fallback
    }
    if ($result.ExitCode -ne 0) {
        Write-Warn "解析 base_dir 失败（退出码 $($result.ExitCode)），改用兜底目录"
        return $Fallback
    }

    $value = $result.Stdout
    if (-not $value) {
        Write-Warn '解析 base_dir 返回空值，改用兜底目录'
        return $Fallback
    }

    # 只取最后一行非空输出：uv 自己也可能往 stdout 打提示（如首次同步依赖）。
    $candidate = (@($value -split "`r?`n") |
                  Where-Object { $_.Trim() } |
                  Select-Object -Last 1).Trim()

    # 必须是绝对路径的样子（Windows 盘符或 UNC），否则宁可走兜底也别拼出怪路径。
    if ($candidate -notmatch '^[A-Za-z]:[\\/]' -and $candidate -notmatch '^\\\\') {
        Write-Warn "解析 base_dir 得到非路径输出: $candidate（改用兜底目录）"
        return $Fallback
    }

    return $candidate.TrimEnd('\', '/')
}

function Write-AdminCredentialHint {
    <#
    .SYNOPSIS
        把「当前管理员邮箱 + 凭据文件位置」写进日志（不含密码），并记录已执行的动作。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Email,
        [Parameter(Mandatory = $true)][string]$CredentialPath,
        [Parameter(Mandatory = $true)][string]$Action
    )

    Write-Log -LogFile $LogFile -Message "管理员邮箱: $Email"
    Write-Log -LogFile $LogFile -Message "已执行: $Action"
    Write-Log -LogFile $LogFile -Message "凭据文件: $CredentialPath (密码未写入日志)"
}

function Show-AdminCredentials {
    <#
    .SYNOPSIS
        在控制台醒目显示一次管理员账号与密码，并给出后续建议。

    .DESCRIPTION
        密码只在这里出现一次，且只走控制台（Write-Host），绝不写日志文件——
        日志会被打包、上传、贴进工单，等于把密钥散播出去。

        显示之后必须提醒「记录完就删文件」：上游的 0600 是 POSIX 权限语义，
        Windows 上 os.open 的 mode 参数基本不生效（NTFS 用 ACL，不是 mode 位），
        实际可读范围取决于目录 ACL，所以留在磁盘上的风险比 Linux 侧高。

        未指定 -ShowOnly 时额外提示「本次运行做了什么」，因为重置与创建对运维的
        后续动作要求不同（重置后旧会话已失效）。
    #>
    param(
        [Parameter(Mandatory = $true)]$Cred,
        [Parameter(Mandatory = $false)][string]$Action = ''
    )

    $loginUrl = "http://$($script:DisplayHost):3000/login"

    Write-Host ""
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host "  管理员凭据（仅本次显示，请立即记录）" -ForegroundColor Green
    Write-Host "==========================================================" -ForegroundColor Green
    Write-Host ""

    if ($Action) {
        Write-Host "  本次操作 : $Action" -ForegroundColor Green
    }

    Write-Host "  登录地址 : $loginUrl" -ForegroundColor Green
    Write-Host "  账号     : $($Cred.Email)" -ForegroundColor Green
    Write-Host "  密码     : $($Cred.Password)" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  凭据文件 : $($Cred.Path)" -ForegroundColor DarkGray
    Write-Host "  建议     : 记录完密码后删除该文件（Remove-Item '$($Cred.Path)'）；" -ForegroundColor DarkGray
    Write-Host "             登录后立即在「设置 - 账号」修改密码。" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  注册审批 : 新用户注册后是 pending 状态，需在管理后台审批后才能登录。" -ForegroundColor DarkGray
    Write-Host ""
}

# ── 进入主流程 ──────────────────────────────────────────────────────────────

Write-Step 'DeerFlow 管理员账号初始化'

Write-Info "部署根目录 : $rootDir"
Write-Info "代码目录   : $srcDir"
Write-Info "数据目录   : $dataDir"
Write-Info "日志       : $logFile"

# 内网 IP 只影响展示的登录地址，取不到就退回 localhost，不影响初始化本身。
$lanIp = Get-DeerFlowLanIp
if ($lanIp) { $script:DisplayHost = $lanIp }

# ── 前置检查 ────────────────────────────────────────────────────────────────

Write-Step '检查前置条件'

if (-not (Test-Path $configPath)) {
    Write-Fail "未找到 config.yaml: $configPath"
    Write-Info '请先运行 init-config.ps1 生成配置，或确认已同步代码仓库。'
    exit 1
}
Write-Ok "config.yaml: $configPath"

# CLI / 引导程序都要在 src\backend 下用 uv 执行，venv 缺失时 uv 会临时解析依赖，
# 结果不可预期（可能装出一套与部署不一致的环境），因此提前挡住。
$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Fail "后端虚拟环境缺失: $venvPython"
    Write-Info '请先运行 install.ps1（不带 -SkipBackend）安装后端依赖。'
    exit 1
}
Write-Ok '后端虚拟环境已就绪'

if (-not (Test-Path $uvExe)) {
    Write-Fail "未找到 uv: $uvExe"
    Write-Info '请先运行 install-toolchain.ps1 安装工具链，或确认 tools\uv 目录存在。'
    exit 1
}
Write-Ok "uv: $uvExe"

# ── 解析凭据文件位置 ────────────────────────────────────────────────────────
#
# 必须由 Python 给出（见 Get-DeerFlowBaseDir 的注释）。这一步只读，不写库、
# 不生成任何文件，因此 -ShowOnly 也照样走它。
Write-BootstrapScript -Path $bootstrapPath

Write-Step '解析凭据文件位置'

$baseDir = Get-DeerFlowBaseDir -BootstrapPath $bootstrapPath -LogFile $logFile -Fallback $fallbackBaseDir
$credentialPath = Join-Path $baseDir 'admin_initial_credentials.txt'

Write-Ok "base_dir: $baseDir"
Write-Info "凭据文件: $credentialPath"

if ($baseDir.TrimEnd('\', '/') -ne $dataDir.TrimEnd('\', '/')) {
    # 这不是错误，但必须讲清楚，否则运维会照着 data 目录去找文件而找不到。
    Write-Warn "base_dir 与数据目录不同（$baseDir != $dataDir）"
    Write-Info 'base_dir 由 DEER_FLOW_HOME 环境变量（缺失时为 <代码目录>\.deer-flow）决定，'
    Write-Info '与 config.yaml 里 database.sqlite_dir 指向的数据目录无关；凭据文件写在 base_dir 下。'
    Write-Info '后端进程解析 base_dir 用的是同一套逻辑，因此文件位置与服务一致。'
}

# ── -ShowOnly：只读分支 ─────────────────────────────────────────────────────

if ($ShowOnly) {
    Write-Step '读取已有凭据文件（-ShowOnly）'

    if ($Email) {
        Write-Info "-ShowOnly 只读取文件，-Email 被忽略（不会创建或重置任何账号）"
    }

    $cred = Read-AdminCredentialFile -Path $credentialPath
    if (-not $cred) {
        Write-Fail "凭据文件不存在或内容不完整: $credentialPath"
        Write-Info '该文件由 admin-init.ps1（首次创建 / 重置密码）生成；'
        Write-Info '若已被运维删除，请重新运行一次本脚本生成新凭据（会重置密码）。'
        exit 1
    }

    Write-Ok "凭据文件可读: $credentialPath"
    Show-AdminCredentials -Cred $cred
    exit 0
}

# ── 邮箱白名单预检 ──────────────────────────────────────────────────────────
#
# 只警告、不中止（需求如此，也确实该如此）：
#   · 白名单约束的是「注册」与「改邮箱」（enforce_email_domain_allowed），
#     本脚本的创建/重置动作不经过那道校验，不在白名单里也能建出来；
#   · 但建出来之后，该账号若要走注册/改邮箱流程会被 403，属于「能用但别扭」
#     的状态，运维需要提前知道；
#   · enforce_email_domain_on_login 为 false 时登录本身不校验域名，所以
#     不在白名单并不等于登不上去 —— 一刀切拒绝反而会挡住真实需求。
if ($Email) {
    if ($Email -notmatch '^[^@\s]+@([^@\s]+)$') {
        Write-Warn "邮箱格式可疑: '$Email'（期望 name@domain 形式）"
        Write-Info '格式最终由后端校验；这里只提示，脚本会继续执行。'
    } else {
        $domain  = $Matches[1].ToLowerInvariant()
        $allowed = @(Get-ConfigAllowedEmailDomains -Path $configPath)

        if ($allowed.Count -eq 0) {
            Write-Warn '未能从 config.yaml 解析出 auth.allowed_email_domains，跳过域名白名单校验'
        } elseif ($allowed -contains $domain) {
            Write-Ok "邮箱域名在白名单内: $domain"
        } else {
            Write-Warn "邮箱域名不在白名单内: $domain（白名单: $($allowed -join ', ')）"
            Write-Info '影响：该账号在「注册 / 修改邮箱」时会被后端以 403 拒绝（EMAIL_DOMAIN_NOT_ALLOWED）；'
            Write-Info '      登录不受影响（config.yaml 里 enforce_email_domain_on_login 为 false）。'
            Write-Info '若确实要用这个域名，请把它加进 config.yaml 的 auth.allowed_email_domains 后重新生成配置。'
            Write-Info '本次初始化不会被中止。'
        }
    }
}

# ── 覆盖提示（幂等性） ──────────────────────────────────────────────────────

Write-Step '执行方式'

if ($Email) {
    Write-Info "目标管理员: $Email"
} else {
    Write-Info '目标管理员: 库中第一个管理员（CLI 默认行为）'
}

Write-Warn '注意：若该管理员已存在，本次运行会重置它的密码。'
Write-Info '可能的后果：旧密码立即失效；token_version 递增会让已签发的登录会话全部作废'
Write-Info '（已登录的浏览器需要重新登录）。需保留原密码请直接 Ctrl+C 中止，改用 -ShowOnly 查看已有凭据。'

# ── 执行：先 CLI 重置，未命中管理员则引导创建 ───────────────────────────────

# Write-Log 会把同样的内容回显到控制台，所以下面三行在屏幕上会「裸奔」——
# 先给一句说明，免得运维以为是脚本的临时输出。
Write-Info "以下三行同时写入本次运行日志，便于事后复盘："
Write-AdminInitLogHead -LogFile $logFile
Write-Log -LogFile $logFile -Message "部署根目录: $rootDir"
Write-Log -LogFile $logFile -Message "代码目录: $srcDir"
Write-Log -LogFile $logFile -Message "base_dir: $baseDir"

# ⚠ PYTHONPATH 是本脚本的关键前置（实测不设会 ModuleNotFoundError: No module named 'app'）。
#   uv run 会在 src\backend 下创建子进程，但它的运行上下文会改变 sys.path 的起点，
#   CLI 的模块路径 app.gateway.auth.reset_admin 必须靠这个变量才能解析到。
#   PYTHONUTF8 让 Python 输出 UTF-8：cmd 重定向按字节写文件，中文才不会乱码。
#   刻意不动 DEER_FLOW_HOME：它与服务进程必须保持一致，由环境变量决定（见文件头注释）。
$env:PYTHONPATH = $backendDir
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

Write-Step '调用上游 CLI 重置密码'

$cliArgs = @('run', 'python', '-m', 'app.gateway.auth.reset_admin')
if ($Email) { $cliArgs += @('--email', $Email) }

Write-Info "工作目录: $backendDir"
Write-Info ("执行命令: uv " + ($cliArgs -join ' '))

$result = Invoke-UvCommand -Arguments $cliArgs -LogFile $logFile -TimeoutSeconds $script:StepTimeoutSeconds

if ($result.TimedOut) {
    Write-Fail "reset_admin 超时（超过 $($script:StepTimeoutSeconds)s），已终止进程树"
    Write-Log -LogFile $logFile -Message "超时: 超过 $($script:StepTimeoutSeconds)s，已 taskkill /F /T"
    Write-LogTail -LogFile $logFile
    exit 1
}

$output = (@(Read-LogFileLines -Path $logFile)) -join "`n"
$useBootstrap = $false

if ($result.ExitCode -eq 0) {
    Write-Ok 'CLI 执行成功 —— 管理员密码已重置，新密码已写入凭据文件'
    # 上游 CLI 成功时还会打印 "Next login will require setup (new email + password)."
    # 这是重置分支的真实后续动作（needs_setup 被置为 true，前端登录后强制跳
    # /setup 的「修改密码」表单），必须转达给运维 —— 否则他登录后被页面拦住，
    # 会误以为是凭据有问题。密码本身没有被重置破坏，仍是下面展示的那一个。
    Write-Info '提示：重置会把账号标记为 needs_setup —— 用下面的密码登录后前端会跳到 /setup，'
    Write-Info '      要求设置新的邮箱与密码；本次生成的密码就是登录时用的旧密码。'
} elseif ($output -match 'not found') {
    # CLI 只在「用户不存在」时用这两个措辞退出 1：
    #   Error: user '<email>' not found. / Error: no admin user found.
    Write-Warn '库中没有匹配的管理员（CLI 报 not found）——转入「创建首个管理员」流程'
    if (-not $Email) {
        Write-Fail '库中还没有管理员，且未提供 -Email，无法确定要创建哪位管理员'
        Write-Info '请用 -Email <邮箱> 指定首个管理员的邮箱后重试，例如：'
        Write-Info '  .\admin-init.ps1 -Email admin@sz-jlc.com'
        Write-LogTail -LogFile $logFile
        exit 1
    }
    $useBootstrap = $true
} else {
    Write-Fail "reset_admin 执行失败（退出码 $($result.ExitCode)）"
    Write-LogTail -LogFile $logFile
    Write-Info '常见原因：数据库不可写 / config.yaml 的 database 段配置有误 / 依赖缺失。'
    exit 1
}

# ── 分支 2：库中无管理员，创建首个管理员 ────────────────────────────────────
#
# 为什么不用 /api/v1/auth/initialize 端点代替：那要求 Gateway 正在运行，而
# 「初始化管理员」这一步在编排里可能发生在上线前（且我们是在初始化数据库）。
# 这里直接复用后端的 provider 与上游的凭据落盘函数，语义与端点一致、且不依赖服务。
if ($useBootstrap) {
    Write-Step '创建首个管理员'

    $bootArgs = @('run', 'python', $bootstrapPath, 'create', $Email)
    Write-Info "执行命令: uv " + ($bootArgs -join ' ')

    $result = Invoke-UvCommand -Arguments $bootArgs -LogFile $logFile -TimeoutSeconds $script:StepTimeoutSeconds

    if ($result.TimedOut) {
        Write-Fail "创建首个管理员超时（超过 $($script:StepTimeoutSeconds)s），已终止进程树"
        Write-LogTail -LogFile $logFile
        exit 1
    }

    if ($result.ExitCode -ne 0) {
        Write-Fail "创建首个管理员失败（退出码 $($result.ExitCode)）"
        Write-LogTail -LogFile $logFile
        Write-Info '常见原因：数据库不可写 / config.yaml 的 database 段配置有误 / 依赖缺失。'
        exit 1
    }

    Write-Ok '首个管理员已创建，密码已写入凭据文件'
}

# ── 回读凭据文件 ────────────────────────────────────────────────────────────
#
# 不信「退出码 0」这一个信号，直接把凭据文件按运维的读法读回来核对：
# 退出码为 0 但文件写到了别处（base_dir 与预期不一致）或内容不完整，
# 都只有回读能发现。

Write-Step '回读凭据文件'

$output = (@(Read-LogFileLines -Path $logFile)) -join "`n"

$reportedPath = ''
$matches = [regex]::Matches($output, 'Credentials written to:\s*(.+?)\s*\(mode')
if ($matches.Count -gt 0) {
    # 取最后一次匹配：CLI 分支若报错不会打印这一行，真正写入的那次才是真的。
    $reportedPath = $matches[$matches.Count - 1].Groups[1].Value.Trim()
}

if ($reportedPath -and ($reportedPath.TrimEnd('\', '/') -ne $credentialPath.TrimEnd('\', '/'))) {
    # CLI 自己报的路径是最强证据（它掌握 base_dir 的最终解析结果），以它为准。
    Write-Warn "CLI 报告的凭据路径与预期不同，以 CLI 为准"
    Write-Info "  预期: $credentialPath"
    Write-Info "  实际: $reportedPath"
    $credentialPath = $reportedPath
}

$cred = Read-AdminCredentialFile -Path $credentialPath
if (-not $cred -and $credentialPath -ne $fallbackBaseDir) {
    # 极少数情况：CLI 打印的路径带引号 / 格式变化导致读不到，退回 base_dir 兜底再试一次。
    $retryPath = Join-Path $fallbackBaseDir 'admin_initial_credentials.txt'
    if ($retryPath.TrimEnd('\', '/') -ne $credentialPath.TrimEnd('\', '/')) {
        Write-Warn "凭据文件不可读: $credentialPath，改按数据目录重试: $retryPath"
        $cred = Read-AdminCredentialFile -Path $retryPath
        if ($cred) { $credentialPath = $retryPath }
    }
}

if (-not $cred) {
    Write-Fail "凭据文件不存在或内容不完整: $credentialPath"
    Write-LogTail -LogFile $logFile
    Write-Info '密码已生成但无法回读，请检查上面的日志并人工确认；'
    Write-Info '若确实没有落盘，可重跑本脚本（会重新生成密码）。'
    exit 1
}

Write-Ok "凭据文件已生成且可读: $credentialPath"
Write-Info "文件大小: $((Get-Item $credentialPath).Length) 字节"

$actionLabel = '重置已有管理员密码'
if ($useBootstrap) { $actionLabel = '创建首个管理员' }
Write-AdminCredentialHint -LogFile $logFile -Email $cred.Email -CredentialPath $credentialPath -Action $actionLabel

# ── 展示（唯一一次明文） ────────────────────────────────────────────────────

Show-AdminCredentials -Cred $cred -Action $actionLabel

Write-Host "  日志（不含密码）: $logFile" -ForegroundColor DarkGray
Write-Host ""

exit 0
