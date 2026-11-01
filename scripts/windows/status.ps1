<#
.SYNOPSIS
    查看 DeerFlow 两个服务的运行状态：Gateway(8001) + Frontend(3000)。

.DESCRIPTION
    每个服务输出一行概要 + 若干明细：

        State     运行中 / 未运行 / 端口被他人占用
        PID       进程 ID（CIM 属性名是 ProcessId，Start-Process 的是 Id）
        来源      PidFile（来自 PID 文件）/ CommandLine（按命令行扫描）
        端口      监听状态与监听地址（0.0.0.0 表示内网可访问）
        时长      进程启动至今的时长
        日志      日志文件路径与最后写入时间
        命令行    进程完整命令行（截断显示）

    -Tail N 时额外打印日志末尾 N 行。

    退出码约定（供 deploy.ps1 / 运维脚本判断）：
        0 —— 至少一个服务在运行
        1 —— 两个服务都没运行
    注意「端口被他人占用」不属于「我们的服务在运行」，因此不改变退出码。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 状态判定的唯一依据是「有没有本项目进程」，不是「端口有没有被监听」
      端口被别的 next / uvicorn 占着时，端口是通的，但我们的服务根本没起来。
      若按端口判断，status 会显示「运行中」，运维照着去访问却发现是别人的页面。
      因此这里以 Get-DeerFlowProcess（含部署根校验）为准，端口只作为补充信息。

    · 运行时长用 Win32_Process.CreationDate
      它返回的是 CIM_DATETIME 字符串（形如 20260912021500.500000+480），
      直接减法会得到奇怪的结果。用 [Management.ManagementDateTimeConverter]::ToDateTime
      转换——这是 .NET 自带的 CIM 时间转换器，PS 5.1 上可用，无需自己解析。

    · 日志「最后写入时间」比日志大小更有用
      服务卡死时日志文件可能很大但半小时没更新；只看大小会误判成「在正常干活」。
      这个字段配合运行时长就能一眼看出「进程活着但已经不输出了」。

    · 全部停止时退出码为 1
      这是刻意的：让 status.ps1 可以当条件用（if (.\status.ps1) { ... }）。
      所以「两个都没跑」虽然对交互式查看只是一条信息，退出码上仍要表达出来。

.PARAMETER Tail
    显示每个服务日志的末尾 N 行。不指定时不显示。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准。

.EXAMPLE
    .\status.ps1

.EXAMPLE
    # 顺便看一眼两个服务日志的尾部
    .\status.ps1 -Tail 30

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [int]$Tail = 0,

    [Parameter(Mandatory = $false)]
    [string]$RootDir
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。本脚本的退出码是被运维脚本当条件用的，静默返回 0 会把
# 「脚本崩了」误报成「至少一个服务在运行」。这里显式接管成退出码 1。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非状态查询失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
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

Add-ToolchainToPath

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$rootDir = Get-DeerFlowRoot
$logsDir = Get-DeerFlowLogsDir

$services = @(
    [pscustomobject]@{
        Name    = 'gateway'
        Label   = 'Gateway'
        Port    = 8001
        LogFile = Join-Path $logsDir 'gateway.log'
        Desc    = 'REST API + agent runtime'
    },
    [pscustomobject]@{
        Name    = 'frontend'
        Label   = 'Frontend'
        Port    = 3000
        LogFile = Join-Path $logsDir 'frontend.log'
        Desc    = 'Next.js'
    }
)

$script:RunningCount = 0

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Test-PathInRoot {
    <#
    .SYNOPSIS
        判断一段文本（命令行 / 可执行文件路径）是否包含部署根目录。

    .DESCRIPTION
        Common 模块里有个同名私有函数 Test-DeerFlowPathInRoot，但没有导出，
        因此这里自己实现一份，语义保持一致（用于「端口占用者是不是我们的」）。
    #>
    param(
        [string]$Text,
        [string]$Root
    )

    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    if ([string]::IsNullOrWhiteSpace($Root)) { return $false }

    return ($Text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Format-Uptime {
    <#
    .SYNOPSIS
        把进程启动时间格式化成 "3d 04:15:22" / "04:15:22"，便于扫读。

    .DESCRIPTION
        传 $null 或无效时间时返回 "-"，调用方不必先判空。
    #>
    param([System.Nullable[datetime]]$StartTime)

    if (-not $StartTime) { return '-' }

    $span = (Get-Date) - $StartTime
    if ($span.TotalSeconds -lt 0) { return '-' }

    $days = [math]::Floor($span.TotalDays)
    $text = '{0:00}:{1:00}:{2:00}' -f $span.Hours, $span.Minutes, $span.Seconds
    if ($days -ge 1) {
        return ('{0}d {1}' -f $days, $text)
    }
    return $text
}

function Get-ProcessStartTime {
    <#
    .SYNOPSIS
        返回进程的启动时间；取不到时返回 $null。

    .DESCRIPTION
        ⚠ 不要无条件套 ManagementDateTimeConverter::ToDateTime —— 实测踩坑。

        Win32_Process.CreationDate 在不同取用路径下类型不同：
          · Get-CimInstance（PS 5.1）  → 已经转成 System.DateTime
          · Get-WmiObject / 原始 WMI   → CIM_DATETIME 字符串
                                          （形如 20260912021500.500000+480）

        本机实测：对 Get-CimInstance 拿到的值再调 ToDateTime 会抛
          ArgumentOutOfRangeException: 指定的参数已超出有效值的范围。参数名: dmtfDate
        被 catch 吞掉后运行时长就永远显示 "-"（进程明明在跑）。这个 bug 很隐蔽：
        脚本不报错、其他字段全对，只有时长是空的。

        所以这里按实际类型分支：已经是 DateTime 就直接用，是字符串才转换。
        用 try/catch 兜住个别受保护进程返回空值/非法值的情况，
        不让整个 status 崩掉。
    #>
    param([int]$ProcessId)

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }

    $value = $proc.CreationDate
    if ($null -eq $value) { return $null }

    if ($value -is [datetime]) { return $value }

    if ($value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($value)) { return $null }
        try {
            return [System.Management.ManagementDateTimeConverter]::ToDateTime($value)
        } catch {
            return $null
        }
    }

    return $null
}

function Get-ListenerAddress {
    <#
    .SYNOPSIS
        返回某端口监听地址的字符串（可能多个），端口未被监听时返回 $null。

    .DESCRIPTION
        监听地址很关键：0.0.0.0 / :: 表示内网可访问，127.0.0.1 表示只能本机访问。
        Gateway 若被误改成 127.0.0.1，这里就能立刻看出来。
    #>
    param([int]$Port)

    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($conns.Count -eq 0) { return $null }

    $addrs = @($conns | Select-Object -ExpandProperty LocalAddress -Unique)
    return ($addrs -join ', ')
}

function Write-LogTailInline {
    <#
    .SYNOPSIS
        打印日志末尾 N 行（-Tail 用），按 UTF-8 读取。

    .DESCRIPTION
        .NET 的 UTF8 解码器遇到非法字节会替换成 U+FFFD 而不是抛异常，
        因此即便日志里混入少量非 UTF-8 内容也不会让这里二次失败。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][int]$Lines
    )

    # ⚠ 必须用 Common 的 Read-LogFileLines 而不是 [System.IO.File]::ReadAllLines：
    #   本函数的主要使用场景就是「服务正在跑、想看最近输出」，而此时日志的写句柄
    #   正被服务持有，ReadAllLines 的 FileShare.Read 必然抛共享冲突 —— 也就是说
    #   换回 ReadAllLines 会让 -Tail 在最需要它的场景下 100% 失败（已实测）。
    $all = @(Read-LogFileLines -Path $LogFile)

    if ($all.Count -eq 0) {
        if (Test-Path $LogFile) {
            Write-Info "（日志文件为空或正被独占: $LogFile）"
        } else {
            Write-Info "（日志文件不存在: $LogFile）"
        }
        return
    }

    $start = [math]::Max(0, $all.Count - $Lines)
    $shown = $all.Count - $start

    Write-Host "      ──── 日志末尾 $shown 行 / 共 $($all.Count) 行 ────" -ForegroundColor DarkGray
    for ($i = $start; $i -lt $all.Count; $i++) {
        Write-Host "      | $($all[$i])" -ForegroundColor DarkGray
    }
}

# ── 主流程 ──────────────────────────────────────────────────────────────────

Write-Step "DeerFlow 服务状态"

$lanIp = $null
$routes = @(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue)
if ($routes.Count -gt 0) {
    $best = $routes | Sort-Object RouteMetric, ifMetric | Select-Object -First 1
    if ($best) {
        $addr = @(Get-NetIPAddress -InterfaceIndex $best.ifIndex -AddressFamily IPv4 `
                                    -ErrorAction SilentlyContinue |
                  Where-Object { $_.IPAddress -ne '127.0.0.1' } |
                  Select-Object -First 1)
        if ($addr.Count -gt 0) { $lanIp = $addr[0].IPAddress }
    }
}

foreach ($svc in $services) {
    Write-Host ""
    Write-Host "  ── $($svc.Label) · $($svc.Desc)" -ForegroundColor Cyan

    $proc = Get-DeerFlowProcess -Name $svc.Name
    $listenAddr = Get-ListenerAddress -Port $svc.Port

    if ($proc) {
        $script:RunningCount++

        $uptime = Format-Uptime -StartTime (Get-ProcessStartTime -ProcessId $proc.ProcessId)

        Write-Host "     状态      : " -NoNewline
        Write-Host "运行中" -ForegroundColor Green

        Write-Host "     PID       : $($proc.ProcessId)"
        Write-Host "     来源      : $($proc.Source)"

        if ($listenAddr) {
            Write-Host "     端口      : $($svc.Port) 监听中 ($listenAddr)"
        } else {
            # 进程活着但端口没监听：可能正在启动，也可能已经卡死
            Write-Warn "端口 $($svc.Port) 尚未监听（服务可能仍在启动，或已卡死）"
        }

        Write-Host "     运行时长  : $uptime"

        if ($proc.CommandLine) {
            $cmdShort = $proc.CommandLine
            if ($cmdShort.Length -gt 140) { $cmdShort = $cmdShort.Substring(0, 140) + '...' }
            Write-Host "     命令行    : $cmdShort"
        }
    } else {
        Write-Host "     状态      : " -NoNewline
        Write-Host "未运行" -ForegroundColor DarkGray

        if ($listenAddr) {
            # 端口有监听但不是我们的进程 —— 说清是谁，避免运维误以为服务正常
            $ownerPids = @(Get-PortOwnerPid -Port $svc.Port)
            $descParts = New-Object System.Collections.Generic.List[string]
            foreach ($ownerPid in $ownerPids) {
                $procName = '未知'
                $p = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
                if ($p) { $procName = $p.ProcessName }

                $cmdLine = Get-ProcessCommandLine -ProcessId $ownerPid
                $inRoot  = Test-PathInRoot -Text $cmdLine -Root $rootDir
                $tag = '外部进程'
                if ($inRoot) { $tag = '本项目路径' }

                $descParts.Add("PID $ownerPid ($procName, $tag)")
            }

            Write-Warn "端口 $($svc.Port) 被占用但不属于本项目服务: $($descParts -join '；')"
            Write-Info "监听地址: $listenAddr"
        } else {
            Write-Host "     端口      : $($svc.Port) 未监听"
        }
    }

    # 日志文件信息
    if (Test-Path $svc.LogFile) {
        $item = Get-Item $svc.LogFile -ErrorAction SilentlyContinue
        if ($item) {
            $sizeKb = [math]::Round($item.Length / 1KB, 1)
            Write-Host "     日志      : $($svc.LogFile)"
            Write-Host "                 最后写入 $($item.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))，$sizeKb KB"
        }
    } else {
        Write-Host "     日志      : $($svc.LogFile)（尚未生成）" -ForegroundColor DarkGray
    }

    if ($Tail -gt 0) {
        Write-LogTailInline -LogFile $svc.LogFile -Lines $Tail
    }
}

# ── 汇总 ────────────────────────────────────────────────────────────────────

Write-Host ""

if ($lanIp) {
    Write-Host "  内网访问: http://${lanIp}:3000" -ForegroundColor Green
    Write-Host "  健康检查: http://${lanIp}:8001/health" -ForegroundColor DarkGray
}
Write-Host "  本机访问: http://localhost:3000" -ForegroundColor DarkGray

Write-Host ""

if ($script:RunningCount -eq 0) {
    Write-Host "==========================================" -ForegroundColor DarkGray
    Write-Host "  两个服务均未运行（$script:RunningCount/2）" -ForegroundColor DarkGray
    Write-Host "==========================================" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

if ($script:RunningCount -eq $services.Count) {
    Write-Host "==========================================" -ForegroundColor Green
    Write-Host "  两个服务均在运行（$script:RunningCount/2）" -ForegroundColor Green
    Write-Host "==========================================" -ForegroundColor Green
} else {
    Write-Host "==========================================" -ForegroundColor Yellow
    Write-Host "  部分服务在运行（$script:RunningCount/2）" -ForegroundColor Yellow
    Write-Host "==========================================" -ForegroundColor Yellow
}

Write-Host ""

exit 0
