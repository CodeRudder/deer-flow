<#
.SYNOPSIS
    启动 DeerFlow 的两个服务：Gateway(8001) + Frontend(3000)。

.DESCRIPTION
    编排顺序（对齐上游 scripts/serve.sh 的 run_service）：

        1. 前置检查    config.yaml + .env / 后端 .venv / 前端 .next 构建产物
        2. 清理遗留    调用 stop.ps1，避免上一轮残留进程占着端口
        3. 端口预检    8001 / 3000 若被「非本项目」进程占用则中止，绝不杀它
        4. 启动 Gateway
        5. 等待就绪    Gateway 60s
        6. 启动 Frontend
        7. 等待就绪    Frontend 120s
        8. 打印访问地址（内网 IP，而不只是 localhost）

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · Gateway 必须显式设 PYTHONPATH=<src>\backend
      uvicorn 的工作目录是 src\backend，但它 import 的是 app.gateway.app:app，
      而 app 包在 src\backend\app。不设 PYTHONPATH 时实测报
      ModuleNotFoundError: No module named 'app'。虽然 cd 到该目录后 Python 会把
      当前目录加进 sys.path，但 uv run 会先切一层自己的运行上下文，不能指望它。
      显式设置是唯一稳妥的做法。

    · Gateway 绑定 0.0.0.0 是有意为之
      内网其它机器要直接访问 8001，改成 127.0.0.1 会让这些调用全部连接失败。

    · 后台进程必须用 WMI（Win32_Process.Create）拉起，不能用 Start-Process
      这是本脚本最关键的一处实现约束。实测：Start-Process 拉起的进程会随宿主
      PowerShell 的退出而被一并结束——经 SSH 远程执行时，命令一返回进程就死，
      日志 0 字节、端口无人监听。原因是 Start-Process 属于「父进程派生」，
      父进程（ssh 会话里的 PowerShell）退出时其作业对象被回收。
      改用 Invoke-CimMethod -ClassName Win32_Process -MethodName Create，
      进程由 WMI 服务（WmiPrvSE）派生，与调用方会话完全脱离，可长期存活。
      已在目标机实测：会话结束后进程仍在、端口正常监听。

    · 因此 PATH 必须在命令行里显式设置，不能依赖父进程环境
      WMI 拉起的进程继承的是 WmiPrvSE 的环境，而不是本脚本进程的环境。
      Add-ToolchainToPath 改的是当前进程的 $env:Path，对子进程无效。
      所以启动命令行里必须自己 set PATH=<tools>\node;<tools>\uv;%PATH%，
      否则会用到系统 PATH 里的 Node 16（见 Common 模块 Add-ToolchainToPath 注释）。

    · PID 文件里存的是 cmd.exe 包装进程的 PID
      命令行是 cmd.exe /d /s /c <实际命令>，cmd 会一直等待前台子进程，因此这个
      包装进程的生命周期与服务一致。stop 时对它 taskkill /F /T 即可整棵树收掉
      （uv → uvicorn → python / pnpm → next → next-server）。
      ⚠ 取 PID 要用 $result.ProcessId（CIM 的属性名）；若用 Start-Process -PassThru
        则要写 .Id，两者不能混（写错时 PowerShell 静默返回 $null，不报错）。

    · 日志用 cmd 的 >> 追加重定向
      Start-Process 的 -RedirectStandardOutput 在 5.1 上要求 stdout/stderr 分开
      落到两个文件，读起来还得自己交错合并；cmd 的 >> file 2>&1 一行搞定。
      代价是 >> 本身不截断，所以启动前必须先由脚本把日志清空并写好运行头。

    · 失败要连带清理已启动的进程
      Gateway 起来了但 Frontend 挂了，如果留着 Gateway 就是「半启动状态」：
      下次 start 会因为 8001 被本项目进程占用而走「清理遗留」分支，运维很难判断
      当前到底处于什么状态。所以任何一步失败都停掉本次启动的全部进程再退出。

    · 防火墙：内网访问的前提
      目标机网卡被 Windows 判定为公用网络（Public profile），默认入站全拦。
      不放通 3000/8001 的话，本机 curl localhost 一切正常、内网其它机器却全部超时，
      而脚本打印的内网 URL 就是假的。因此这里按 install-sshd.ps1 的既有做法
      补入站规则（-RemoteAddress LocalSubnet，只放本网段）。需要管理员权限，
      拿不到时只警告不中止——服务本身在本机仍然是好的。

.PARAMETER Dev
    前端用 `pnpm run dev`（next dev --turbo）而不是 `pnpm run start`。
    开发模式不需要 .next 的生产构建产物，因此该模式下不再强制检查 BUILD_ID。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准。

.PARAMETER Timeout
    就绪等待上限（秒），同时作用于两个服务。不给时用默认值：
    Gateway 60s、Frontend 120s（前端首屏编译明显更慢）。

.PARAMETER SkipFirewall
    跳过防火墙放通。已由其它方式放通过时用。

.EXAMPLE
    # 生产模式启动（默认）
    .\start.ps1

.EXAMPLE
    # 开发模式启动
    .\start.ps1 -Dev

.EXAMPLE
    # 网络慢，放宽前端等待到 5 分钟
    .\start.ps1 -Timeout 300

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [switch]$Dev,

    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [int]$Timeout = 0,

    [Parameter(Mandatory = $false)]
    [switch]$SkipFirewall
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。部署编排据此会误判成「启动成功」。
# 这里显式接管，保证「异常 => 退出码 1 + 出错位置」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非服务启动失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
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

# ⚠ 必须调用：本脚本进程自己也要用 node/pnpm/uv（例如防火墙、探测），
#   且它保证后续拼进子进程命令行的工具链路径与实际一致。
Add-ToolchainToPath

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$rootDir     = Get-DeerFlowRoot
$srcDir      = Get-DeerFlowSrcDir
$logsDir     = Get-DeerFlowLogsDir
$toolsDir    = Get-DeerFlowToolsDir
$backendDir  = Join-Path $srcDir 'backend'
$frontendDir = Join-Path $srcDir 'frontend'

$nodeDir = Join-Path $toolsDir 'node'
$uvDir   = Join-Path $toolsDir 'uv'

$uvExe   = Join-Path $uvDir   'uv.exe'
$pnpmCmd = Join-Path $nodeDir 'pnpm.cmd'

$logGateway  = Join-Path $logsDir 'gateway.log'
$logFrontend = Join-Path $logsDir 'frontend.log'

$gatewayPort  = 8001
$frontendPort = 3000

# 就绪超时默认值：前端首次编译要现算页面，明显比后端慢；给足时间避免误判失败。
$gatewayTimeout  = 60
$frontendTimeout = 120
if ($Timeout -gt 0) {
    $gatewayTimeout  = $Timeout
    $frontendTimeout = $Timeout
}

# 失败时回显的日志行数：够看到真正的报错，又不会把控制台刷爆。
$script:TailLines = 25

# 记录本次启动的进程，失败时按此回滚（不留半启动状态）。
$script:Started = New-Object System.Collections.Generic.List[object]

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Test-PathInRoot {
    <#
    .SYNOPSIS
        判断一段文本（命令行 / 可执行文件路径）是否包含部署根目录。

    .DESCRIPTION
        用于「防止误杀他人进程」：只有路径落在部署根之下的才可能是本项目进程。
        Common 模块里有个同名私有函数 Test-DeerFlowPathInRoot，但没有导出，
        因此这里自己实现一份，语义保持一致。

        用 IndexOf + OrdinalIgnoreCase 而不是 -like "*$root*"：
        -like 会把路径里的 [ ] 当成通配符，且大小写与通配语义容易误判。
    #>
    param(
        [string]$Text,
        [string]$Root
    )

    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    if ([string]::IsNullOrWhiteSpace($Root)) { return $false }

    return ($Text.IndexOf($Root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Write-LogTail {
    <#
    .SYNOPSIS
        打印日志文件的最后若干行（启动失败诊断用）。

    .DESCRIPTION
        按 UTF-8 读取：子进程的 stdout/stderr 被 cmd 重定向进文件，node 与 uv 都
        写 UTF-8 字节；后端另设了 PYTHONUTF8=1，Python 输出同样是 UTF-8。
        .NET 的 UTF8 解码器遇到非法字节会替换成 U+FFFD 而不是抛异常，
        因此即便混入少量非 UTF-8 内容也不会让这里二次失败。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $false)][int]$Lines = 25
    )

    # ⚠ 必须用 Common 的 Read-LogFileLines 而不是 [System.IO.File]::ReadAllLines：
    #   失败时进程可能还活着并持有日志的写句柄，ReadAllLines 的 FileShare.Read
    #   会撞共享冲突抛异常，导致诊断信息一条都打不出来（详见模块内注释）。
    $all = @(Read-LogFileLines -Path $LogFile)

    if ($all.Count -eq 0) {
        if (Test-Path $LogFile) {
            Write-Fail "$Tag 失败，日志文件为空或正被独占: $LogFile"
        } else {
            Write-Fail "$Tag 失败，且没有生成日志文件: $LogFile"
        }
        return
    }

    $start = [math]::Max(0, $all.Count - $Lines)
    $shown = $all.Count - $start

    Write-Host ""
    Write-Host "  ──── $Tag 日志尾部（最后 $shown 行 / 共 $($all.Count) 行）────" -ForegroundColor Red
    for ($i = $start; $i -lt $all.Count; $i++) {
        Write-Host "  | $($all[$i])" -ForegroundColor DarkGray
    }
    Write-Host "  ──── 完整日志: $LogFile ────" -ForegroundColor Red
}

function New-ServiceLog {
    <#
    .SYNOPSIS
        清空服务日志并写入本次运行头。

    .DESCRIPTION
        必须在启动前调用：子进程用 `>>` 追加，如果不清空，上几次运行的输出会
        一直堆在文件里，失败时 tail 到的「日志尾部」可能是几天前的旧报错。
        写运行头（时间 / 根目录 / 命令行）是为了让事后复盘能直接看出这次是怎么起的。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$CommandText
    )

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $head = "================================================================`r`n" +
            "$Title`r`n" +
            "启动时间: $stamp`r`n" +
            "部署根目录: $rootDir`r`n" +
            "代码目录: $srcDir`r`n" +
            "启动命令: $CommandText`r`n" +
            "================================================================`r`n"

    # Write-TextFileNoBom（Common 模块）保证无 BOM：cmd 的重定向按字节追加，
    # 带 BOM 会让第二次追加在文件中段插入 EF BB BF。
    Write-TextFileNoBom -Path $LogFile -Content $head
}

function Start-DeerFlowService {
    <#
    .SYNOPSIS
        用 WMI 后台拉起一个服务，记录 PID 文件，返回进程 PID。

    .DESCRIPTION
        为什么用 WMI 而不是 Start-Process，见文件头注释（简言之：Start-Process
        拉起的进程会随宿主 PowerShell 退出而被回收，远程执行时必然早夭）。

        子进程环境由命令行自己设置：WMI 派生出的进程继承的是 WmiPrvSE 的环境，
        不是本脚本的，所以工具链 PATH 必须在命令行里前置。

    .PARAMETER Name
        逻辑名（gateway / frontend），同时决定 PID 文件名。

    .PARAMETER Inner
        cmd 语句：用 && 串起 set / cd / 实际服务命令，并以 >> 日志 2>&1 结尾。

    .PARAMETER DisplayName
        控制台展示用的名字。

    .PARAMETER Port
        该服务的监听端口。记进 $script:Started 供失败回滚按端口收尾 ——
        只停包装进程 PID 是不够的（子进程会变成孤儿继续占着端口，详见
        Stop-StartedServices 的注释）。

    .OUTPUTS
        System.Int32 —— 包装进程（cmd.exe）的 PID。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Inner,
        [Parameter(Mandatory = $true)][string]$DisplayName,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'

    # /d 跳过 AutoRun 注册表项（某些机器上的 AutoRun 会污染环境变量）
    # /s /c 与后面整条命令的引号配合，保证重定向作用于全部子命令
    $cmdLine = '"' + $cmdExe + '" /d /s /c ' + $Inner

    Write-Info "$DisplayName 包装命令: $cmdLine"

    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
                               -Arguments @{ CommandLine = $cmdLine }

    if ($null -eq $result -or $result.ReturnValue -ne 0) {
        $code = -1
        if ($result) { $code = $result.ReturnValue }
        # 常见返回码：2 = 拒绝访问，3 = 权限不足，9 = 路径不存在，21 = 参数非法
        throw "WMI 创建进程失败（ReturnValue=$code）。请检查路径是否存在、以及当前用户是否有权限。"
    }

    $procId = [int]$result.ProcessId
    if ($procId -le 0) {
        throw "WMI 创建进程返回了非法 PID: $procId"
    }

    Write-PidFile -Name $Name -ProcessId $procId
    Write-Ok "$DisplayName 已拉起，包装进程 PID = $procId"

    $script:Started.Add([pscustomobject]@{
        Name        = $Name
        DisplayName = $DisplayName
        ProcessId   = $procId
        Port        = $Port
    })
    return $procId
}

function Wait-DeerFlowPort {
    <#
    .SYNOPSIS
        轮询等待端口进入监听状态。

    .DESCRIPTION
        用 Test-PortListening（Get-NetTCPConnection -State Listen）而不是
        netstat 文本解析：中文系统的 netstat 表头是本地化的，按列号解析很脆弱。

        间隔 700ms 是折中：3000/8001 的就绪通常在数秒内完成，这个频率下最坏
        只多等不到 1 秒；再密一点纯属空转烧 CPU。

    .OUTPUTS
        System.Boolean
    #>
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortListening -Port $Port) { return $true }
        Start-Sleep -Milliseconds 700
    }

    # 最后一搏：超时边界上刚好就绪的情况
    return (Test-PortListening -Port $Port)
}

function Stop-StartedServices {
    <#
    .SYNOPSIS
        回滚本次启动的进程（失败清理用），只动本脚本自己拉起的 PID。

    .DESCRIPTION
        刻意不调用 stop.ps1：那会按命令行特征全量扫描，可能波及更早就在跑的
        实例；这里只收自己拉起的、明确知道的 PID，范围最小。

        ⚠ 必须用「清整棵树」而不是「停单个进程」（实测踩坑）：服务是
        cmd.exe /d /s /c <命令> 包装拉起的，Stop-DeerFlowProcess 温和终止只杀掉
        cmd 自己 —— 它会立刻退出，于是「超时未退出」的前提不成立、/F /T 分支
        根本执行不到；而它的子进程（uv → uvicorn → python /
        pnpm → next → next-server）已被重新挂靠，继续存活。

        实测两种失败形态：
          · 端口已就绪时回滚 —— 端口被 python 持着，uv.exe / uvicorn.exe 成孤儿，
            端口看着空了、进程却还在
          · 端口还没绑上就回滚（如 -Timeout 给得极小）—— 连「按端口兜底」都
            无从下手，因为端口上没有监听者，整条链原样留着

        因此这里用 Common 的 Stop-DeerFlowProcessTree（先快照整棵树再逐个清）。
        之后仍按端口复查一次作为兜底 —— 它覆盖「进程被外部重新挂靠、
        快照没抓到」的极端情况，且带部署根归属校验，不会误伤别人的进程。
    #>
    param()

    foreach ($item in $script:Started) {
        Write-Info "回滚 $($item.DisplayName)（PID $($item.ProcessId)）"
        Stop-DeerFlowProcessTree -ProcessId $item.ProcessId -WaitSeconds 5 | Out-Null

        $left = Stop-DeerFlowPortOwner -Port $item.Port
        if ($left -gt 0) {
            Write-Warn "$($item.DisplayName) 端口 $($item.Port) 仍有 $left 个本项目进程未清掉"
        }

        Remove-PidFile -Name $item.Name
    }
}

function Get-DeerFlowLanIp {
    <#
    .SYNOPSIS
        返回本机用于内网访问的 IPv4 地址，取不到时返回 $null。

    .DESCRIPTION
        判据是「默认路由所在网卡的地址」，而不是「第一个非环回地址」。

        目标机装了 VMware / VirtualBox，会多出 192.168.137.1、192.168.56.1 等
        虚拟网卡地址；按「第一个」取几乎必然取到虚拟网卡，打印出来的 URL 从
        内网其它机器根本连不上。默认路由指向的才是真实出口网卡，实测该机
        取到 192.168.2.10（以太网），正确。
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

function Get-AllLanCandidates {
    <#
    .SYNOPSIS
        列出本机全部可用的内网 IPv4 候选（含虚拟网卡），供人工挑选。

    .DESCRIPTION
        默认路由网卡有时不是访问方所在的网段（如多网卡机器），把候选都列出来，
        运维可以自己换一个试，不必再去翻 ipconfig。
    #>
    param()

    $rows = New-Object System.Collections.Generic.List[string]

    $addrs = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
               Where-Object {
                   $_.IPAddress -ne '127.0.0.1' -and
                   -not $_.IPAddress.StartsWith('169.254.')
               } | Sort-Object InterfaceIndex)

    foreach ($a in $addrs) {
        $rows.Add("$($a.IPAddress)  ($($a.InterfaceAlias))")
    }

    return $rows
}

function Ensure-DeerFlowFirewall {
    <#
    .SYNOPSIS
        确保 3000/8001 的入站规则存在（幂等），失败只警告不中止。

    .DESCRIPTION
        目标机网卡在 Windows 防火墙里属于「公用网络」配置，默认入站全拦——不放通
        的话本机一切正常、内网访问全部超时。做法与 install-sshd.ps1 保持一致：
        -RemoteAddress LocalSubnet 只放本网段，不暴露到不可信网络。

        为什么只警告不中止：服务本身在本机已经跑起来了，防火墙失败不该让整个启动
        判定为失败（运维仍可本机使用，也可事后手动放通）。但必须把「内网访问可能
        不通」这件事明确讲出来，否则打印的内网 URL 会误导人。
    #>
    param(
        [Parameter(Mandatory = $true)][int[]]$Ports
    )

    if ($SkipFirewall) {
        Write-Warn "已跳过防火墙放通（-SkipFirewall）"
        Write-Info "若内网其它机器访问不了，请确认 $($Ports -join ' / ') 已放通。"
        return
    }

    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $isAdmin   = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

    if (-not $isAdmin) {
        Write-Warn "当前不是管理员，无法配置防火墙（服务本身不受影响）"
        Write-Info "内网访问可能需要手动放通: $($Ports -join ' / ')"
        Write-Info "以管理员身份执行: New-NetFirewallRule -DisplayName 'DeerFlow Gateway (Port 8001)' -Direction Inbound -Protocol TCP -LocalPort 8001 -Action Allow -RemoteAddress LocalSubnet -Profile Any"
        return
    }

    foreach ($port in $Ports) {
        $label = 'Frontend'
        if ($port -eq $gatewayPort) { $label = 'Gateway' }
        $ruleName = "DeerFlow $label (Port $port)"

        $existing = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
                      Where-Object { $_.DisplayName -eq $ruleName })

        if ($existing.Count -gt 0) {
            Write-Ok "防火墙规则已存在: $ruleName"
            continue
        }

        try {
            New-NetFirewallRule `
                -DisplayName $ruleName `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $port `
                -Action Allow `
                -RemoteAddress LocalSubnet `
                -Profile Any `
                -ErrorAction Stop | Out-Null
            Write-Ok "已放通入站 TCP $port（限本网段）"
        } catch {
            Write-Warn "创建防火墙规则失败: $($_.Exception.Message)"
            Write-Info "可手动执行: New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Protocol TCP -LocalPort $port -Action Allow"
        }
    }
}

# ── 1. 前置检查 ─────────────────────────────────────────────────────────────

Write-Step "检查部署前置条件"

if (-not (Test-DeerFlowConfigured)) {
    Write-Fail "配置未就绪 —— 请先运行 init-config.ps1 生成 config.yaml 与 .env"
    exit 1
}
Write-Ok "config.yaml 与 .env 已就绪"

$venvPython = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Fail "后端虚拟环境缺失: $venvPython"
    Write-Info "请先运行 install.ps1（不带 -SkipBackend）安装后端依赖。"
    exit 1
}
Write-Ok "后端虚拟环境已就绪"

if (-not (Test-Path $uvExe)) {
    Write-Fail "未找到 uv: $uvExe"
    Write-Info "请先运行 install-toolchain.ps1 安装工具链。"
    exit 1
}

$buildIdPath = Join-Path $frontendDir '.next\BUILD_ID'
if (-not (Test-Path $buildIdPath)) {
    if ($Dev) {
        # 开发模式跑的是 next dev，源码现编译，不需要生产构建产物。
        # 这里只提示，不阻断 —— 否则在一棵没构建过的干净树上无法起开发模式。
        Write-Warn "未找到前端构建产物（$buildIdPath），-Dev 模式可继续"
    } else {
        Write-Fail "前端构建产物缺失: $buildIdPath"
        Write-Info "请先运行 install.ps1（不带 -SkipFrontend / -SkipBuild）完成前端构建，"
        Write-Info "或改用 -Dev 以开发模式启动。"
        exit 1
    }
} else {
    Write-Ok "前端构建产物已就绪"
}

# ── 2. 清理遗留 ─────────────────────────────────────────────────────────────

Write-Step "清理遗留进程"

# ⚠ 必须另起一个 powershell 子进程调用 stop.ps1，不能用 & 直接调。
#   stop.ps1 内部有 exit，而 `& script.ps1` 是在同一个运行空间里执行，
#   exit 会把 start.ps1 自己也一并结束掉（表现为「start 只打印了几行就退出」）。
$stopScript = Join-Path $scriptDir 'stop.ps1'
if (-not (Test-Path $stopScript)) {
    Write-Warn "未找到 stop.ps1，跳过遗留清理: $stopScript"
} else {
    $stopArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $stopScript)
    if ($RootDir) { $stopArgs += @('-RootDir', $RootDir) }

    & powershell.exe @stopArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "stop.ps1 退出码为 $LASTEXITCODE（可能仍有残留进程）"
    }
}

# ── 3. 端口预检 ─────────────────────────────────────────────────────────────

Write-Step "检查端口占用"

$conflicts = New-Object System.Collections.Generic.List[string]

foreach ($svc in @(
    [pscustomobject]@{ Label = 'Gateway';  Port = $gatewayPort;  Name = 'gateway'  },
    [pscustomobject]@{ Label = 'Frontend'; Port = $frontendPort; Name = 'frontend' }
)) {
    $ownerPids = @(Get-PortOwnerPid -Port $svc.Port)
    if ($ownerPids.Count -eq 0) {
        Write-Ok "$($svc.Label) 端口 $($svc.Port) 空闲"
        continue
    }

    foreach ($ownerPid in $ownerPids) {
        $cmdLine = Get-ProcessCommandLine -ProcessId $ownerPid
        $inRoot  = Test-PathInRoot -Text $cmdLine -Root $rootDir

        if ($inRoot) {
            # 本项目自己的进程没被 stop.ps1 干掉（stop 失败或进程僵死）
            $conflicts.Add("$($svc.Label) 端口 $($svc.Port) 仍被本项目进程占用（PID $ownerPid）—— stop.ps1 未能停止它")
            Write-Fail "$($svc.Label) 端口 $($svc.Port) 被本项目残留进程占用: PID $ownerPid"
            if ($cmdLine) { Write-Info "命令行: $cmdLine" }
            Write-Info "请手动执行 taskkill /F /T /PID $ownerPid 后重试。"
        } else {
            # 别人的进程 —— 绝不杀，直接中止并说清是谁
            $procName = '未知'
            $p = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
            if ($p) { $procName = $p.ProcessName }

            $conflicts.Add("$($svc.Label) 端口 $($svc.Port) 被非本项目进程占用（PID $ownerPid / $procName）")
            Write-Fail "$($svc.Label) 端口 $($svc.Port) 已被非本项目进程占用: PID $ownerPid ($procName)"
            if ($cmdLine) { Write-Info "命令行: $cmdLine" }
        }
    }
}

if ($conflicts.Count -gt 0) {
    Write-Host ""
    Write-Fail "端口被占用，已中止启动（不会终止上述任何进程，请自行处理）"
    foreach ($c in $conflicts) { Write-Info "· $c" }
    Write-Info "确认该进程可以结束后，可手动执行: Stop-Process -Id <PID>"
    exit 1
}

# ── 4. 防火墙 ───────────────────────────────────────────────────────────────

Write-Step "配置防火墙"
Ensure-DeerFlowFirewall -Ports @($frontendPort, $gatewayPort)

# ── 5. 启动 Gateway ─────────────────────────────────────────────────────────

Write-Step "启动 Gateway (端口 $gatewayPort)"

# PYTHONPATH 见文件头说明；PYTHONUNBUFFERED / PYTHONUTF8 让日志能实时落盘且中文不乱码。
$gatewayInner = 'set "PYTHONPATH=' + $backendDir + '"' +
                '&& set "PYTHONUNBUFFERED=1"' +
                '&& set "PYTHONUTF8=1"' +
                '&& set "PATH=' + $nodeDir + ';' + $uvDir + ';%PATH%"' +
                '&& cd /d "' + $backendDir + '"' +
                '&& "' + $uvExe + '" run uvicorn app.gateway.app:app --host 0.0.0.0 --port ' + $gatewayPort +
                ' >> "' + $logGateway + '" 2>&1'

New-ServiceLog -LogFile $logGateway -Title 'DeerFlow Gateway (uvicorn, port 8001)' -CommandText $gatewayInner

$gatewayPid = Start-DeerFlowService -Name 'gateway' -Inner $gatewayInner -DisplayName 'Gateway' -Port $gatewayPort

Write-Info "等待端口 $gatewayPort 就绪（上限 ${gatewayTimeout}s）..."
if (-not (Wait-DeerFlowPort -Port $gatewayPort -TimeoutSeconds $gatewayTimeout)) {
    Write-Fail "Gateway 未在 ${gatewayTimeout}s 内监听 $gatewayPort"
    Write-LogTail -LogFile $logGateway -Tag 'Gateway' -Lines $script:TailLines
    Stop-StartedServices
    exit 1
}
Write-Ok "Gateway 就绪: 0.0.0.0:$gatewayPort"

# ── 6. 启动 Frontend ────────────────────────────────────────────────────────

Write-Step "启动 Frontend (端口 $frontendPort)"

# PORT 显式指定：next start / next dev 都读它，避免 Next 自己挑一个别的端口
# 导致后面的就绪探测误判失败。
$frontendMode = 'start'
if ($Dev) { $frontendMode = 'dev' }

$frontendInner = 'set "PORT=' + $frontendPort + '"' +
                 '&& set "PATH=' + $nodeDir + ';' + $uvDir + ';%PATH%"' +
                 '&& cd /d "' + $frontendDir + '"' +
                 '&& call "' + $pnpmCmd + '" run ' + $frontendMode +
                 ' >> "' + $logFrontend + '" 2>&1'

New-ServiceLog -LogFile $logFrontend -Title "DeerFlow Frontend (pnpm run $frontendMode, port 3000)" -CommandText $frontendInner

$frontendPid = Start-DeerFlowService -Name 'frontend' -Inner $frontendInner -DisplayName 'Frontend' -Port $frontendPort

Write-Info "等待端口 $frontendPort 就绪（上限 ${frontendTimeout}s）..."
if (-not (Wait-DeerFlowPort -Port $frontendPort -TimeoutSeconds $frontendTimeout)) {
    Write-Fail "Frontend 未在 ${frontendTimeout}s 内监听 $frontendPort"
    Write-LogTail -LogFile $logFrontend -Tag 'Frontend' -Lines $script:TailLines
    Write-Info "回滚已启动的进程，避免留下半启动状态。"
    Stop-StartedServices
    exit 1
}
Write-Ok "Frontend 就绪: 0.0.0.0:$frontendPort"

# ── 7. 汇总 ─────────────────────────────────────────────────────────────────

$lanIp = Get-DeerFlowLanIp

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  DeerFlow 已启动" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$modeLabel = '生产模式 (pnpm run start)'
if ($Dev) { $modeLabel = '开发模式 (pnpm run dev)' }

Write-Host "  运行模式 : $modeLabel"
Write-Host "  Gateway  : PID $gatewayPid  →  http://0.0.0.0:$gatewayPort"
Write-Host "  Frontend : PID $frontendPid  →  http://0.0.0.0:$frontendPort"
Write-Host ""

if ($lanIp) {
    Write-Host "  内网访问地址（其它机器用这个）:" -ForegroundColor Green
    Write-Host "    http://${lanIp}:$frontendPort" -ForegroundColor Green
    Write-Host "    http://${lanIp}:$gatewayPort/health" -ForegroundColor Green
} else {
    Write-Warn "未能识别默认路由网卡，无法给出内网地址。候选如下，请自行挑选:"
    foreach ($row in (Get-AllLanCandidates)) { Write-Info $row }
}

Write-Host ""
Write-Host "  本机访问:" -ForegroundColor DarkGray
Write-Host "    http://localhost:$frontendPort" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  日志:" -ForegroundColor DarkGray
Write-Host "    $logGateway" -ForegroundColor DarkGray
Write-Host "    $logFrontend" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  停止: .\stop.ps1    查看状态: .\status.ps1" -ForegroundColor Yellow
Write-Host ""

exit 0
