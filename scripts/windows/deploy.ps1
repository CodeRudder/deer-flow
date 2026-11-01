<#
.SYNOPSIS
    DeerFlow Windows 一键部署编排。

.DESCRIPTION
    把「新机器从零到可访问」固化成一条命令：

        环境检查 → init-config.ps1 → install.ps1 → start.ps1 → 自检

    本脚本只负责「按顺序调用 + 失败即停 + 给出补救指引」，不复制各脚本的内部逻辑
    ——把生成配置、装依赖、起服务再实现一遍，两处逻辑会逐渐分叉，最终表现为
    「单独跑某个脚本正常，一起跑就不对」。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 工具链缺失时只提示、**不自动**调用 install-toolchain.ps1
      install-toolchain.ps1 做的是系统级动作：把 Node 解压到磁盘、下载 uv、写用户级
      PATH 与环境变量。这类动作应当由运维明确知情后执行：一旦中途失败（镜像不可达、
      磁盘不足、代理未配），系统会被留在「半装」状态，而运维并不知道编排动过系统
      配置、也不知道该从哪一步续跑。所以这里只做「检测 + 打印该执行哪条命令」。

    · 默认**不注册**开机自启（要注册必须显式加 -RegisterAutostart）
      注册自启会创建 Windows 计划任务、并让服务在每次开机后自动拉起，影响面超出
      「部署一套服务」本身。这种决定应由运维显式做出，不该藏在「跑一次 deploy.ps1」
      的副作用里。默认只打印后续命令。
      （start.ps1 自己会幂等创建 3000/8001 的防火墙规则——那是「服务能被内网访问」
        的前提，与「开机自启」是两件事，不受本开关影响。）

    · 子脚本一律用 `powershell.exe -File` 另起进程调用，不用 `& .\xxx.ps1`
      子脚本内部有 exit，而 `& script.ps1` 与调用方共享同一个运行空间，exit 会把
      deploy.ps1 自己也一起结束掉（表现为「deploy 只打印几行就退出」）。另起进程后
      $LASTEXITCODE 才是可靠的判据。start.ps1 调 stop.ps1 时踩过同一个坑。

    · 已经在跑的服务不会被无谓地重启
      「幂等」在本脚本里的含义是：重复执行不打断正在提供的服务。两个服务都在运行时
      跳过 start.ps1，只起了一半（或都没起）时才调用它（start.ps1 会先清理遗留再
      重启两个服务，这是它的既有语义）。

      ⚠ 这里刻意**不**按端口反查归属来判断「服务是不是在跑」。实测目标机上，
        前端监听 3000 的是链路末端 node.exe，它的祖先里有一层
        `cmd.exe /d /s /c next start` —— 那条命令行**不含部署根目录**，
        于是 Get-DeerFlowRootAncestorPid 上溯到它就断掉，按端口反查会把链路末端的
        node 误判成「别人的进程」而拒绝启动。用 PID 文件 + 命令行特征
        （Get-DeerFlowProcess）判断既准确又不会误杀。

    · 自检只看「本机 127.0.0.1 能不能通」，不宣称内网可达
      内网能否访问取决于对端网段与防火墙，从服务器本机测不出来。这里把本机链路
      打通作为判据，内网地址只打印出去由运维从别的机器验证——避免出现
      「脚本说部署成功、运维在内网却打不开」的落空。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准，并以
    -RootDir 的形式透传给每个子脚本。

.PARAMETER SkipToolchain
    跳过工具链就绪检测。已确认 tools\node、tools\uv 就位时可用（例如由镜像预置）。

.PARAMETER SkipBuild
    跳过前端构建（透传给 install.ps1 的 -SkipBuild）。
    ⚠ 只有在 .next\BUILD_ID 已存在时才有意义：start.ps1 依赖该产物，
      缺失时本脚本会提前警告（若同时给了 -NoStart 则仅警告不中止）。

.PARAMETER NoStart
    只装不启：跑完 init-config.ps1 与 install.ps1 就结束，不启动服务、不做自检。

.PARAMETER RegisterAutostart
    部署成功后注册开机自启（调用 register-autostart.ps1，需要管理员权限）。
    默认不注册，只打印后续命令——原因见上。

.PARAMETER SkipAutostart
    连「后续请手动注册自启」的提示一起省掉，适用于自动化流水线 / 镜像固化场景。
    与 -RegisterAutostart 同时给出时以本开关为准（跳过注册并警告）。

.EXAMPLE
    # 全新机器一键部署（需先手动跑过 install-toolchain.ps1）
    .\deploy.ps1

.EXAMPLE
    # 已装好依赖、只想重新构建并重启
    .\deploy.ps1 -SkipToolchain -NoStart

.EXAMPLE
    # 部署 + 注册开机自启（需管理员权限）
    .\deploy.ps1 -RegisterAutostart

.EXAMPLE
    # 非默认部署根
    .\deploy.ps1 -RootDir D:\apps\deer-flow

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
    除 -RegisterAutostart 外不需要管理员权限（防火墙放通由 start.ps1 尽力而为）。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [switch]$SkipToolchain,

    [Parameter(Mandatory = $false)]
    [switch]$SkipBuild,

    [Parameter(Mandatory = $false)]
    [switch]$NoStart,

    [Parameter(Mandatory = $false)]
    [switch]$RegisterAutostart,

    [Parameter(Mandatory = $false)]
    [switch]$SkipAutostart
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常终止脚本，但「终止」不等于
# 「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，退出码仍是
# 0。本脚本是部署的总入口，被 CI / 运维脚本当判据用，误报「部署成功」的代价最高。
# 这里显式接管，保证「异常 => 退出码 1 + 出错位置」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非部署失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

# ── 引入公共模块 ────────────────────────────────────────────────────────────

# -RootDir 必须在 Import 之前写进环境变量：Common 的 Get-DeerFlowRoot() 每次调用
# 都重读该变量（而不是 Import 时算一次），顺序反了其实也不影响，但先设置语义更清楚。
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

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$deployRoot  = Get-DeerFlowRoot
$srcDir      = Get-DeerFlowSrcDir
$dataDir     = Get-DeerFlowDataDir
$logsDir     = Get-DeerFlowLogsDir
$toolsDir    = Get-DeerFlowToolsDir

$gatewayPort  = 8001
$frontendPort = 3000

$nodeExe = Join-Path $toolsDir 'node\node.exe'
$uvExe   = Join-Path $toolsDir 'uv\uv.exe'
$pnpmCmd = Join-Path $toolsDir 'node\pnpm.cmd'

$deployLogFile = Join-Path $logsDir 'deploy.log'

# 子脚本路径（每次调用前会再确认存在，这里只是为了打印补救命令时好看）
$initConfigScript = Join-Path $scriptDir 'init-config.ps1'
$installScript    = Join-Path $scriptDir 'install.ps1'
$startScript      = Join-Path $scriptDir 'start.ps1'
$statusScript     = Join-Path $scriptDir 'status.ps1'
$adminInitScript  = Join-Path $scriptDir 'admin-init.ps1'
$autostartScript  = Join-Path $scriptDir 'register-autostart.ps1'

# 脚本自身的可复现调用命令：补救提示里给出的命令必须与本次运行等价
# （部署根不在默认位置时，不带 -RootDir 的补救命令会指向错误的地方）。
$selfPath = Join-Path $scriptDir 'deploy.ps1'
$scriptPrefix = "& '$selfPath'"
if ($RootDir) { $scriptPrefix = $scriptPrefix + " -RootDir '$RootDir'" }

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Write-DeployMilestone {
    <#
    .SYNOPSIS
        记录一条部署里程碑到 logs\deploy.log（同时回显到控制台）。

    .DESCRIPTION
        只在阶段切换 / 失败 / 收尾时调用，不给每个细节都写日志——
        日志的价值在于「事后能还原这次部署走到哪一步、什么时候失败」，
        而不是复刻全部屏幕输出（各子脚本自己的日志更详细）。
    #>
    param([string]$Message)

    Write-Log -LogFile $deployLogFile -Message $Message
}

function Show-DeployRemedy {
    <#
    .SYNOPSIS
        打印「出了这个错，接下来该敲什么」的手动补救清单。

    .DESCRIPTION
        编排脚本失败时最没用的输出就是「失败了」。运维需要的是「现在敲哪条命令」，
        因此每个失败分支都必须给出具体的可复制命令，而不是让人去翻脚本源码。
    #>
    param([string[]]$Lines)

    Write-Host ""
    Write-Host "  手动补救：" -ForegroundColor Yellow
    foreach ($line in $Lines) {
        Write-Host "    $line" -ForegroundColor Yellow
    }
    Write-Host ""
}

function Stop-DeployWithFailure {
    <#
    .SYNOPSIS
        以「阶段失败」的形态中止部署：打印失败阶段 + 补救清单 + 非零退出。

    .PARAMETER Stage
        失败阶段的中文名（如「生成配置」）。

    .PARAMETER Reason
        一行失败原因（子脚本的退出码 / 探测结论）。

    .PARAMETER Remedy
        补救命令清单。
    #>
    param(
        [string]$Stage,
        [string]$Reason,
        [string[]]$Remedy
    )

    Write-Host ""
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host "  部署中止：$Stage 失败" -ForegroundColor Red
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host ""
    if ($Reason) { Write-Host "  原因: $Reason" -ForegroundColor Red }

    Show-DeployRemedy -Lines $Remedy

    Write-Host "  已完成的阶段不会回滚（配置与依赖都已落盘），修好问题后重跑本脚本即可，"
    Write-Host "  已就绪的步骤会自动跳过。"
    Write-Host ""
    Write-Host "  部署日志: $deployLogFile" -ForegroundColor DarkGray
    Write-Host ""

    Write-DeployMilestone "部署中止：$Stage 失败 ($Reason)"

    exit 1
}

function Test-IsDeerFlowAdministrator {
    <#
    .SYNOPSIS
        当前进程是否以管理员身份运行。

    .DESCRIPTION
        不用 #Requires -RunAsAdministrator：那会在解析阶段直接终止脚本，连中文提示
        都打不出来（经 SSH 远程调用时只看到一行英文报错）。运行时判断才能给出
        「用管理员身份重开一个 PowerShell」这种可照做的提示。
    #>
    param()

    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-DeerFlowChildScript {
    <#
    .SYNOPSIS
        另起 powershell 进程执行同目录下的子脚本，返回其退出码。

    .DESCRIPTION
        ⚠ 必须另起进程，不能用 `& $path`：子脚本内部有 exit，而 `& script.ps1`
        与调用方共享同一个运行空间，exit 会把 deploy.ps1 自己也一并结束掉。
        另起进程后 -File 会把子脚本的退出码原样带回来（$LASTEXITCODE）。

        ⚠ 必须加 -OutputFormat Text
        子进程的 stdout 被管道接走（PowerShell 需要它才能拿到退出码），而远端
        PowerShell 一旦发现输出被重定向，就会改用 CLIXML 序列化往 stderr 写一堆
        XML（实测直接刷屏，把控制台输出搅成一团）。-OutputFormat Text 让它老实输出
        纯文本，这也是 scripts\windows-remote\win-exec.sh 用同一招的原因。

        ⚠ 输出必须显式 pipe 到 Out-Host，不能让它裸着
        原生命令的 stdout 会进入 PowerShell 的输出流；裸着写的话这些行会被当成
        **本函数的返回值**一起返回，调用方拿到的就不是「退出码」而是一个字符串数组
        （实测表现为补救提示里插进整段子脚本输出，且 $LASTEXITCODE 永远不等于 0）。
        Out-Host 把输出直接交给宿主渲染（与单独运行子脚本时看到的完全一致），
        既不进返回值，也不缓冲。

        ⚠ 刻意不接 2>&1：PowerShell 5.1 在 $ErrorActionPreference='Stop' 下，原生命令
        往 stderr 写内容再经管道读取会抛 NativeCommandError，把「子脚本正常报了个错」
        变成「deploy 自己崩了」。stderr 让它直接落到控制台。

    .PARAMETER ScriptName
        子脚本文件名（同目录），如 'init-config.ps1'。

    .PARAMETER ChildArguments
        透传给子脚本的参数数组。

    .OUTPUTS
        System.Int32 —— 子脚本的退出码。路径不存在时返回 9009（与 cmd 的
        「找不到命令」一致，便于与脚本自身的 0/1 区分）。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$ScriptName,

        [Parameter(Mandatory = $false)]
        [string[]]$ChildArguments = @()
    )

    $path = Join-Path $scriptDir $ScriptName
    if (-not (Test-Path $path)) {
        Write-Fail "未找到子脚本: $path"
        return 9009
    }

    $childArgs = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-OutputFormat', 'Text',
        '-File', $path
    ) + $ChildArguments

    & powershell.exe @childArgs | Out-Host

    # 先落变量再返回：$LASTEXITCODE 会被后续任何原生命令覆盖，而且这里取的是
    # 紧邻子进程退出后的值，是唯一可靠的时机。
    $code = $LASTEXITCODE

    return $code
}

function Get-DeerFlowLanIp {
    <#
    .SYNOPSIS
        返回本机用于内网访问的 IPv4 地址，取不到时返回 $null。

    .DESCRIPTION
        判据是「默认路由所在网卡的地址」，而不是「第一个非环回地址」。

        目标机装了 VMware / VirtualBox，会多出 192.168.137.1、192.168.56.1 等虚拟
        网卡地址；按「第一个」取几乎必然取到虚拟网卡，打印出来的 URL 从内网其它机器
        根本连不上。默认路由指向的才是真实出口网卡。

        ⚠ 本函数与 start.ps1 里的同名函数是同一份逻辑（那边未导出、Common 模块也
        没有提供），属于刻意的重复。日后要改判据请**两处一起改**。
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
        默认路由网卡有时并不是访问方所在的网段（多网卡机器很常见）。把候选都列出来，
        运维可以自己换一个试，不必再去翻 ipconfig。与 start.ps1 的同名函数一致。
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

function Get-CommandVersionText {
    <#
    .SYNOPSIS
        执行 `<exe> --version` 并返回输出文本；执行失败时返回 $null。

    .DESCRIPTION
        用于「工具链是否真的能跑」的探测——文件存在不等于可用（可能是被截断的
        下载产物、或缺少同目录的 dll）。

        ⚠ 必须临时把 $ErrorActionPreference 放回 Continue：原生命令往 stderr 写内容
        时，在 Stop 下经管道读取会抛 NativeCommandError，而探测失败恰恰是这里要
        处理的正常分支之一。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$ExePath
    )

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $ExePath '--version' 2>&1 | Out-String
        $text = ([string]$output).Trim()
        if ($LASTEXITCODE -ne 0) { return $null }
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text
    } catch {
        return $null
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Test-HttpEndpoint {
    <#
    .SYNOPSIS
        请求一个 HTTP 端点，返回结构化结果（不抛异常）。

    .DESCRIPTION
        自检环节需要「探测失败」也要能继续收集其它信息（比如前端没通但后端通了），
        因此这里把异常收敛成返回值，而不是让它冒泡成部署失败。

        2xx / 3xx 都算通过：Next.js 的 / 在首次部署时会 307 到 /setup，
        Invoke-WebRequest 默认跟随重定向，但边界情况下也可能把 3xx 原样返回。

    .PARAMETER Url
        完整 URL。

    .PARAMETER TimeoutSeconds
        超时秒数，默认 15。

    .OUTPUTS
        PSCustomObject（Ok / Status / Detail / BodyPreview）
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Url,

        [Parameter(Mandatory = $false)]
        [int]$TimeoutSeconds = 15
    )

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'

    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        $status = [int]$resp.StatusCode

        $body = ''
        try { $body = ([string]$resp.Content).Trim() } catch { $body = '' }
        if ($body.Length -gt 200) { $body = $body.Substring(0, 200) }

        return [pscustomobject]@{
            Ok          = ($status -ge 200 -and $status -lt 400)
            Status      = $status
            Detail      = ''
            BodyPreview = $body
        }
    } catch {
        $status = 0
        if ($_.Exception.Response) {
            try { $status = [int]$_.Exception.Response.StatusCode } catch { $status = 0 }
        }

        return [pscustomobject]@{
            Ok          = ($status -ge 200 -and $status -lt 400)
            Status      = $status
            Detail      = $_.Exception.Message
            BodyPreview = ''
        }
    } finally {
        $ErrorActionPreference = $prev
    }
}

# ── 开场 ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  DeerFlow 一键部署" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  部署根目录 : $deployRoot"
Write-Host "  代码目录   : $srcDir"
Write-Host "  数据目录   : $dataDir"
Write-Host "  日志目录   : $logsDir"
Write-Host ""

$modeNotes = New-Object System.Collections.Generic.List[string]
if ($SkipToolchain)     { $modeNotes.Add('-SkipToolchain：跳过工具链就绪检测') }
if ($SkipBuild)         { $modeNotes.Add('-SkipBuild：跳过前端构建') }
if ($NoStart)           { $modeNotes.Add('-NoStart：只装不启') }
if ($SkipAutostart)     { $modeNotes.Add('-SkipAutostart：不提示注册自启') }
if ($RegisterAutostart) { $modeNotes.Add('-RegisterAutostart：部署后注册开机自启') }

if ($modeNotes.Count -gt 0) {
    Write-Host "  运行选项:" -ForegroundColor DarkGray
    foreach ($note in $modeNotes) { Write-Host "    $note" -ForegroundColor DarkGray }
    Write-Host ""
}

Write-DeployMilestone "=== 部署开始 (RootDir=$deployRoot, NoStart=$NoStart, SkipBuild=$SkipBuild, RegisterAutostart=$RegisterAutostart) ==="

# -RegisterAutostart 与 -SkipAutostart 语义冲突时以「更保守」的为准，
# 但必须说出来：静默让其中一个开关失效，运维会以为自己成功注册了自启。
if ($RegisterAutostart -and $SkipAutostart) {
    Write-Warn "同时给出 -RegisterAutostart 与 -SkipAutostart，按 -SkipAutostart 处理（不注册自启）"
    $RegisterAutostart = $false
}

# ══════════════════════════════════════════════════════════════════════════
# 阶段 0：环境检查
# ══════════════════════════════════════════════════════════════════════════

Write-Step '阶段 0/5 · 环境检查'

$envProblems = New-Object System.Collections.Generic.List[string]

# ── 0.1 管理员权限（只警告，不中止）────────────────────────────────────────
#
# 除「注册自启」外，部署本身不需要管理员：装依赖只写部署根，起服务时防火墙规则
# 是尽力而为（start.ps1 拿不到权限只警告）。所以这里不能因为不是管理员就中止，
# 否则本机调试时寸步难行；但必须提示，否则运维会在一连串「防火墙未放通」的
# 警告里找不到根因。
if (Test-IsDeerFlowAdministrator) {
    Write-Ok '当前以管理员身份运行'
} else {
    Write-Warn '当前不是管理员身份运行'
    Write-Info '装依赖与起服务不受影响；但防火墙规则可能无法放通（内网访问会不通），'
    Write-Info '且无法注册开机自启。需要时请用「以管理员身份运行」重开 PowerShell。'
}

# ── 0.2 代码与配置模板就位 ─────────────────────────────────────────────────
#
# 这两项是后续所有步骤的前提，缺了直接中止——继续跑只会在更靠后的地方报更难懂的错。
if (-not (Test-Path $srcDir)) {
    $envProblems.Add("代码目录不存在: $srcDir")
} else {
    Write-Ok "代码目录: $srcDir"
}

$configTemplate = Join-Path $srcDir 'config.example.yaml'
if (-not (Test-Path $configTemplate)) {
    $envProblems.Add("配置模板不存在: $configTemplate（init-config.ps1 依赖它生成 config.yaml）")
} else {
    Write-Ok "配置模板: config.example.yaml"
}

$backendDir  = Join-Path $srcDir 'backend'
$frontendDir = Join-Path $srcDir 'frontend'
foreach ($c in @(
    [pscustomobject]@{ Label = '后端目录'; Path = $backendDir },
    [pscustomobject]@{ Label = '前端目录'; Path = $frontendDir }
)) {
    if (-not (Test-Path $c.Path)) {
        $envProblems.Add("$($c.Label)不存在: $($c.Path)")
    } else {
        Write-Ok "$($c.Label): $($c.Path)"
    }
}

# ── 0.3 磁盘空间 ───────────────────────────────────────────────────────────
#
# 目标机 C 盘只剩几 GB，所有缓存与依赖都必须落在部署根所在盘。这里只做早期预警
# （install.ps1 自己还有一道更细的检查）：装依赖 + 构建实测要 3 GB 以上，
# 低于 10 GB 就该提醒运维先去清盘，而不是等构建到一半失败。
try {
    $driveLetter = (Split-Path -Qualifier $deployRoot).TrimEnd(':')
    $vol = Get-Volume -DriveLetter $driveLetter -ErrorAction SilentlyContinue
    if ($vol -and $vol.Size) {
        $freeGb = [math]::Round($vol.SizeRemaining / 1GB, 1)
        if ($freeGb -lt 10) {
            Write-Warn "部署根所在盘（${driveLetter}:）可用空间仅 ${freeGb} GB，建议先清理到 10 GB 以上"
        } else {
            Write-Ok "部署根所在盘（${driveLetter}:）可用空间 ${freeGb} GB"
        }
    } else {
        Write-Info "无法读取 ${driveLetter}: 的剩余空间，跳过磁盘检查"
    }
} catch {
    Write-Info "磁盘空间检查失败（不影响部署）: $($_.Exception.Message)"
}

if ($envProblems.Count -gt 0) {
    Stop-DeployWithFailure -Stage '环境检查' -Reason ($envProblems -join '；') -Remedy @(
        '确认代码已放到部署根下（目录布局必须是 <RootDir>\src）：'
        "    $srcDir"
        '若代码还没就位，先从仓库同步 / 拷贝整份代码，再重跑本脚本。'
        '若部署根不在默认位置，用 -RootDir 指定，或设置环境变量 DEER_FLOW_DEPLOY_ROOT。'
        "重新执行: $scriptPrefix"
    )
}

# ── 0.4 工具链就绪检测（只检测，不自动安装）────────────────────────────────

Write-Host ""
Write-Host "  ── 工具链" -ForegroundColor Cyan

if ($SkipToolchain) {
    Write-Warn '已跳过工具链检测（-SkipToolchain）'
    Write-Info '若后续步骤报 node / uv / pnpm 找不到，请先运行 install-toolchain.ps1。'
    Add-ToolchainToPath
} else {
    $missing = New-Object System.Collections.Generic.List[string]

    foreach ($t in @(
        [pscustomobject]@{ Label = 'Node'; Path = $nodeExe },
        [pscustomobject]@{ Label = 'uv';   Path = $uvExe },
        [pscustomobject]@{ Label = 'pnpm'; Path = $pnpmCmd }
    )) {
        if (Test-Path $t.Path) {
            Write-Ok "$($t.Label): $($t.Path)"
        } else {
            Write-Fail "$($t.Label) 缺失: $($t.Path)"
            $missing.Add("$($t.Label) ($($t.Path))")
        }
    }

    if ($missing.Count -gt 0) {
        # ⚠ 这里刻意只提示、不代跑 install-toolchain.ps1：它做的是系统级动作
        #   （解压 Node 到磁盘、下载 uv、写用户级 PATH / 环境变量），应当由运维
        #   明确知情后执行。理由见文件头注释。
        $toolchainCmd = "& '$scriptDir\install-toolchain.ps1'"
        if ($RootDir) { $toolchainCmd = $toolchainCmd + " -RootDir '$deployRoot'" }

        Write-Host ""
        Write-Host "  工具链尚未就绪，本脚本**不会**自动安装。" -ForegroundColor Yellow
        Write-Host "  install-toolchain.ps1 会做系统级改动（解压 Node 到磁盘、" -ForegroundColor Yellow
        Write-Host "  下载 uv、写用户级 PATH 与环境变量），需要你明确知情后执行：" -ForegroundColor Yellow

        Stop-DeployWithFailure -Stage '工具链检测' -Reason ("缺少 " + ($missing -join '、')) -Remedy @(
            '请先手动执行工具链安装（约 3-10 分钟，取决于网络）：'
            "    $toolchainCmd"
            '装完后确认这三个文件存在：'
            "    $nodeExe"
            "    $uvExe"
            "    $pnpmCmd"
            '然后重跑本脚本（工具链部分会自动跳过）：'
            "    $scriptPrefix"
            '（确认工具链已由镜像 / 其它方式预置时，可用 -SkipToolchain 跳过本检测。）'
        )
    }

    # 文件存在 ≠ 可执行：下载被截断、缺少同目录 dll 时都能过 Test-Path。
    # 这里实际跑一次 --version，把「能跑」也验证掉——比在 install 阶段报一个
    # 难懂的 Node 崩溃要省事得多。
    Add-ToolchainToPath

    $versionFailures = New-Object System.Collections.Generic.List[string]
    foreach ($t in @(
        [pscustomobject]@{ Label = 'Node'; Path = $nodeExe },
        [pscustomobject]@{ Label = 'uv';   Path = $uvExe },
        [pscustomobject]@{ Label = 'pnpm'; Path = $pnpmCmd }
    )) {
        $ver = Get-CommandVersionText -ExePath $t.Path
        if ($ver) {
            Write-Ok "$($t.Label) 可用: $ver"
        } else {
            Write-Fail "$($t.Label) 无法执行: $($t.Path)"
            $versionFailures.Add($t.Label)
        }
    }

    if ($versionFailures.Count -gt 0) {
        $toolchainCmd = "& '$scriptDir\install-toolchain.ps1'"
        if ($RootDir) { $toolchainCmd = "& '$scriptDir\install-toolchain.ps1' -RootDir '$deployRoot'" }

        Stop-DeployWithFailure -Stage '工具链检测' -Reason ("$($versionFailures -join '、') 无法执行 --version") -Remedy @(
            '文件虽然存在，但执行失败——通常是下载被截断或被杀软隔离。'
            '重新安装工具链（会覆盖）：'
            "    $toolchainCmd -Force"
            '若怀疑是杀软拦截，请先放行部署根目录后重试。'
            "重新执行: $scriptPrefix"
        )
    }
}

# ── 0.5 构建产物与 -SkipBuild 的一致性（只警告）────────────────────────────
#
# -SkipBuild 与 start.ps1 的依赖是矛盾的：start.ps1 强制要求 .next\BUILD_ID。
# 提前把这件事说出来，避免运维看到「部署成功」却发现服务起不来。
if ($SkipBuild -and -not $NoStart) {
    $buildId = Join-Path $frontendDir '.next\BUILD_ID'
    if (-not (Test-Path $buildId)) {
        Write-Warn "-SkipBuild 已给出，但构建产物不存在: $buildId"
        Write-Info 'start.ps1 需要该产物，稍后启动会失败。请去掉 -SkipBuild 重新部署，'
        Write-Info '或单独执行: .\install.ps1 -SkipBackend -SkipFrontend'
    } else {
        Write-Ok '构建产物已存在，-SkipBuild 可用'
    }
}

Write-DeployMilestone '阶段 0/5 环境检查通过'

# ══════════════════════════════════════════════════════════════════════════
# 阶段 1：生成配置（init-config.ps1，幂等）
# ══════════════════════════════════════════════════════════════════════════

Write-Step '阶段 1/5 · 生成配置（config.yaml + .env）'

$configPath = Get-DeerFlowConfigPath
$envPath    = Get-DeerFlowEnvPath

$alreadyConfigured = Test-DeerFlowConfigured -ConfigPath $configPath -EnvPath $envPath

$initArgs = @()
if ($RootDir) { $initArgs += @('-RootDir', $RootDir) }

$initCode = Invoke-DeerFlowChildScript -ScriptName 'init-config.ps1' -ChildArguments $initArgs

if ($initCode -ne 0) {
    Stop-DeployWithFailure -Stage '生成配置' -Reason "init-config.ps1 退出码 $initCode" -Remedy @(
        '先看 config.yaml / .env 是否已生成，以及 config.example.yaml 是否完整：'
        "    $configPath"
        "    $envPath"
        "    $configTemplate"
        '若只是配置内容不对，可用 -Force 重新生成（覆盖前会自动备份为 *.bak.<时间戳>）：'
        "    & '$initConfigScript' -Force"
        '日志: init-config.ps1 直接输出到控制台，无独立日志文件。'
        "重新执行: $scriptPrefix"
    )
}

if ($alreadyConfigured) {
    Write-Ok '配置已存在且校验通过（本次未改动）'
} else {
    Write-Ok '已生成 config.yaml 与 .env'
}
Write-Info "config.yaml: $configPath"
Write-Info ".env       : $envPath"

Write-DeployMilestone '阶段 1/5 配置就绪'

# ══════════════════════════════════════════════════════════════════════════
# 阶段 2：安装依赖 + 构建前端（install.ps1，幂等）
# ══════════════════════════════════════════════════════════════════════════

Write-Step '阶段 2/5 · 安装依赖与构建前端'

$installArgs = @()
if ($RootDir) { $installArgs += @('-RootDir', $RootDir) }
if ($SkipBuild) { $installArgs += '-SkipBuild' }

$installStart = Get-Date
$installCode = Invoke-DeerFlowChildScript -ScriptName 'install.ps1' -ChildArguments $installArgs
$installSeconds = [math]::Round(((Get-Date) - $installStart).TotalSeconds, 1)

if ($installCode -ne 0) {
    Stop-DeployWithFailure -Stage '安装依赖' -Reason "install.ps1 退出码 $installCode（耗时 ${installSeconds}s）" -Remedy @(
        '先看三个步骤的日志尾部，定位是哪一步失败：'
        "    $(Join-Path $logsDir 'install-backend.log')"
        "    $(Join-Path $logsDir 'install-frontend.log')"
        "    $(Join-Path $logsDir 'install-build.log')"
        '只重跑某一类步骤（已就绪的步骤会自动跳过）：'
        "    & '$installScript'                     # 全部重试"
        "    & '$installScript' -SkipBackend        # 只跑前端依赖 + 构建"
        "    & '$installScript' -SkipBackend -SkipFrontend   # 只重跑构建"
        '网络类失败（下载中断、超时）直接重跑通常就能过；仍未过则检查镜像可达性。'
        "重新执行: $scriptPrefix"
    )
}

Write-Ok "依赖安装与构建完成（${installSeconds}s）"

Write-DeployMilestone "阶段 2/5 依赖与构建完成 (${installSeconds}s)"

# ══════════════════════════════════════════════════════════════════════════
# 阶段 3：启动服务（start.ps1）
# ══════════════════════════════════════════════════════════════════════════

$gatewayProc  = Get-DeerFlowProcess -Name 'gateway'
$frontendProc = Get-DeerFlowProcess -Name 'frontend'
$bothRunning  = ($null -ne $gatewayProc) -and ($null -ne $frontendProc)

if ($NoStart) {
    Write-Step '阶段 3/5 · 启动服务（已跳过：-NoStart）'
    Write-Warn '按 -NoStart 要求，只装不启。'
    Write-Info "需要时手动启动: & '$startScript'"
} elseif ($bothRunning) {
    # 幂等的关键：重复执行部署不该打断正在提供的服务。
    # 两个都在跑就直接跳过——start.ps1 会先 stop 再 start，那是「重启」不是「启动」。
    Write-Step '阶段 3/5 · 启动服务（已在运行，跳过）'
    Write-Ok "Gateway 运行中（PID $($gatewayProc.ProcessId)）"
    Write-Ok "Frontend 运行中（PID $($frontendProc.ProcessId)）"
    Write-Info "如需重启: & '$startScript'（它会先停止再启动）"
    Write-DeployMilestone '阶段 3/5 服务已在运行，跳过启动'
} else {
    $runningLabels = New-Object System.Collections.Generic.List[string]
    if ($gatewayProc)  { $runningLabels.Add("Gateway(PID $($gatewayProc.ProcessId))") }
    if ($frontendProc) { $runningLabels.Add("Frontend(PID $($frontendProc.ProcessId))") }
    if ($runningLabels.Count -gt 0) {
        Write-Warn "检测到部分服务在运行（$($runningLabels -join '、')），将先停止再启动两个服务"
    }

    $startArgs = @()
    if ($RootDir) { $startArgs += @('-RootDir', $RootDir) }

    $startCode = Invoke-DeerFlowChildScript -ScriptName 'start.ps1' -ChildArguments $startArgs

    if ($startCode -ne 0) {
        Stop-DeployWithFailure -Stage '启动服务' -Reason "start.ps1 退出码 $startCode" -Remedy @(
            '先看状态与日志尾部（start.ps1 失败时会自己打印日志尾部，往上翻一点）：'
            "    & '$statusScript' -Tail 40"
            '两个服务的日志：'
            "    $(Join-Path $logsDir 'gateway.log')"
            "    $(Join-Path $logsDir 'frontend.log')"
            '常见原因与处理：'
            '  · 端口被别的程序占用 -> status.ps1 会指出占用者，先停掉它再重试'
            '  · 残留进程占着端口   -> 先 & ''stop.ps1'' 清理，再重跑'
            '  · config.yaml 加载失败 -> 检查 models 段与 .env 里被 $VAR 引用的键是否齐全'
            '清理后重新启动：'
            "    & '$scriptDir\stop.ps1'"
            "    & '$startScript'"
            "（或直接重跑本脚本，已完成的步骤会自动跳过）：$scriptPrefix"
        )
    }

    Write-Ok '两个服务已启动'
    Write-DeployMilestone '阶段 3/5 服务已启动'
}

# ══════════════════════════════════════════════════════════════════════════
# 阶段 4：自检
# ══════════════════════════════════════════════════════════════════════════

$selfCheckFailures = New-Object System.Collections.Generic.List[string]

if ($NoStart) {
    Write-Step '阶段 4/5 · 自检（已跳过：-NoStart）'
    Write-Info '服务未启动，不做 HTTP 自检。'
} else {
    Write-Step '阶段 4/5 · 自检'

    # 先看端口是否在监听：没监听时 HTTP 探测会白等一个超时，且错误信息更含糊。
    foreach ($p in @(
        [pscustomobject]@{ Label = 'Gateway';  Port = $gatewayPort },
        [pscustomobject]@{ Label = 'Frontend'; Port = $frontendPort }
    )) {
        if (Test-PortListening -Port $p.Port) {
            Write-Ok "$($p.Label) 端口 $($p.Port) 监听中"
        } else {
            Write-Fail "$($p.Label) 端口 $($p.Port) 未监听"
            $selfCheckFailures.Add("$($p.Label) 端口 $($p.Port) 未监听")
        }
    }

    # 用 127.0.0.1 而不是 localhost：目标机上 localhost 会先解析到 ::1，
    # 若服务只绑了 IPv4 会多一次无谓的失败往返（实测本机两者都通，
    # 但用显式回环地址能排除名称解析这一层变量）。
    $gwCheck = Test-HttpEndpoint -Url "http://127.0.0.1:$gatewayPort/health"
    if ($gwCheck.Ok) {
        Write-Ok "Gateway /health -> $($gwCheck.Status)"
        if ($gwCheck.BodyPreview) { Write-Info $gwCheck.BodyPreview }
    } else {
        Write-Fail "Gateway /health 探测失败: $($gwCheck.Detail)"
        $selfCheckFailures.Add("Gateway /health 失败")
    }

    $feCheck = Test-HttpEndpoint -Url "http://127.0.0.1:$frontendPort/"
    if ($feCheck.Ok) {
        Write-Ok "Frontend / -> $($feCheck.Status)"
    } else {
        Write-Fail "Frontend / 探测失败: $($feCheck.Detail)"
        $selfCheckFailures.Add("Frontend / 失败")
    }

    # 前端 -> 后端的链路验证：/api/models 需要认证，未登录时返回 401 属于**正常**，
    # 恰恰证明 rewrite 已把请求转到了 Gateway（若链路不通会是 404/502）。
    # 因此 401 视为通过，只把它标成「链路已通」而不是失败。
    $apiCheck = Test-HttpEndpoint -Url "http://127.0.0.1:$frontendPort/api/models"
    if ($apiCheck.Status -eq 401 -or $apiCheck.Status -eq 403) {
        Write-Ok "Frontend /api/* -> Gateway 链路已通（$($apiCheck.Status)，需登录属预期）"
    } elseif ($apiCheck.Ok) {
        Write-Ok "Frontend /api/* -> $($apiCheck.Status)"
    } else {
        Write-Warn "Frontend /api/* 探测异常（$($apiCheck.Status)）: $($apiCheck.Detail)"
        Write-Info '这一项不影响服务本身；若页面功能异常，请检查 Gateway 是否健康、'
        Write-Info '以及 frontend\next.config.js 的 rewrites 是否指向 8001。'
    }

    # 数据目录：SQLite 是 WAL 模式，主库文件可能极小（几 KB），数据在 -wal 里，
    # 这是正常的。这里只确认目录存在，避免运维看到「deerflow.db 只有 4 KB」时误判。
    if (Test-Path $dataDir) {
        $dbFiles = @(Get-ChildItem -Path $dataDir -Filter 'deerflow.db*' -ErrorAction SilentlyContinue)
        if ($dbFiles.Count -gt 0) {
            Write-Ok "SQLite 数据文件已生成（$($dbFiles.Count) 个：主库 + WAL/SHM）"
            Write-Info "位置: $(Join-Path $dataDir 'deerflow.db')（WAL 模式下数据主要在 -wal 文件里，主库小属正常）"
        } else {
            Write-Warn "数据目录存在但还没有 deerflow.db：$dataDir"
            Write-Info '首次访问触发建库时会生成，此时未生成不算失败。'
        }
    } else {
        Write-Warn "数据目录不存在: $dataDir"
    }

    if ($selfCheckFailures.Count -gt 0) {
        Stop-DeployWithFailure -Stage '自检' -Reason ($selfCheckFailures -join '；') -Remedy @(
            '服务进程起来了但端口不通，最常见的原因是启动后还在预热（前端首屏首次编译较慢）。'
            '等 30-60 秒后重试：'
            "    & '$statusScript' -Tail 40"
            '若仍不通，看日志定位：'
            "    $(Join-Path $logsDir 'gateway.log')"
            "    $(Join-Path $logsDir 'frontend.log')"
            '必要时重启：'
            "    & '$scriptDir\stop.ps1'"
            "    & '$startScript'"
        )
    }
}

Write-DeployMilestone '阶段 4/5 自检通过'

# ══════════════════════════════════════════════════════════════════════════
# 阶段 5（可选）：注册开机自启
# ══════════════════════════════════════════════════════════════════════════

$autostartState = '未注册'

if ($NoStart) {
    Write-Step '阶段 5/5 · 开机自启（已跳过：-NoStart）'
    Write-Info '服务未启动，注册自启没有意义。启动服务后再执行 register-autostart.ps1。'
    $autostartState = '未注册（-NoStart）'
} elseif (-not $RegisterAutostart) {
    Write-Step '阶段 5/5 · 开机自启（默认不注册）'
    Write-Info '注册自启会创建 Windows 计划任务、让服务在每次开机后自动拉起，'
    Write-Info '影响面超出「部署一套服务」本身，因此默认不自动执行。'
    Write-Info "需要时显式执行（需管理员权限）: & '$autostartScript'"
    Write-Info '或在下次部署时加 -RegisterAutostart。'
    $autostartState = '未注册（默认）'
} else {
    Write-Step '阶段 5/5 · 注册开机自启'

    if (-not (Test-IsDeerFlowAdministrator)) {
        Stop-DeployWithFailure -Stage '注册开机自启' -Reason '当前不是管理员身份，无法创建计划任务与防火墙规则' -Remedy @(
            '用「以管理员身份运行」重开 PowerShell，然后执行：'
            "    & '$autostartScript'"
            '（服务本身已在运行，不受影响；这一步只影响「下次开机是否自动拉起」。）'
            "查看当前状态: & '$statusScript'"
        )
    }

    $autostartArgs = @()
    if ($RootDir) { $autostartArgs += @('-RootDir', $RootDir) }

    $autostartCode = Invoke-DeerFlowChildScript -ScriptName 'register-autostart.ps1' -ChildArguments $autostartArgs

    if ($autostartCode -ne 0) {
        Stop-DeployWithFailure -Stage '注册开机自启' -Reason "register-autostart.ps1 退出码 $autostartCode" -Remedy @(
            '服务已在运行，这一步失败不影响本次部署。排查后手动重试：'
            "    & '$autostartScript'"
            '确认任务与防火墙规则是否创建成功：'
            "    Get-ScheduledTask -TaskName 'DeerFlow Auto Start'"
            "    Get-NetFirewallRule -DisplayName 'DeerFlow*'"
            '不需要自启时可用 unregister-autostart.ps1 清理。'
        )
    }

    Write-Ok '开机自启已注册'
    $autostartState = '已注册'
}

Write-DeployMilestone "阶段 5/5 自启状态: $autostartState"

# ══════════════════════════════════════════════════════════════════════════
# 收尾：访问地址 + 后续命令
# ══════════════════════════════════════════════════════════════════════════

$lanIp = Get-DeerFlowLanIp

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
if ($NoStart) {
    Write-Host "  DeerFlow 部署完成（未启动服务）" -ForegroundColor Cyan
} else {
    Write-Host "  DeerFlow 部署完成" -ForegroundColor Cyan
}
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

if ($NoStart) {
    Write-Host "  服务未启动（-NoStart）。需要时执行:" -ForegroundColor Yellow
    Write-Host "    & '$startScript'" -ForegroundColor Yellow
    Write-Host ""
} elseif ($lanIp) {
    Write-Host "  内网访问地址（其它机器用这个）:" -ForegroundColor Green
    Write-Host "    http://${lanIp}:$frontendPort" -ForegroundColor Green
    Write-Host ""
    Write-Host "  后端健康检查:" -ForegroundColor DarkGray
    Write-Host "    http://${lanIp}:$gatewayPort/health" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  本机自测（已在部署时验证通过）:" -ForegroundColor DarkGray
    Write-Host "    http://localhost:$frontendPort" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "  ⚠ 从内网其它机器打不开时，先确认本机防火墙已放通 $frontendPort：" -ForegroundColor Yellow
    Write-Host "      Get-NetFirewallRule -DisplayName 'DeerFlow*'" -ForegroundColor Yellow
} else {
    Write-Warn '未能识别默认路由网卡，无法给出内网地址。候选如下，请自行挑选:'
    foreach ($row in (Get-AllLanCandidates)) { Write-Info $row }
    Write-Host ""
}

Write-Host "  ── 后续步骤" -ForegroundColor Cyan
Write-Host ""

# 管理员初始化：本部署启用了管理员审批（require_admin_approval = true），
# 新注册用户是 pending 状态、必须管理员审批才能登录；而系统里还没有管理员时
# 无从审批。所以这是「部署完成后必须做的一步」，不是可选优化。
Write-Host "  1) 初始化管理员账号（必做——没有管理员就无法审批新用户）:" -ForegroundColor Yellow
Write-Host "       & '$adminInitScript' -Email admin@<你的邮箱域名>" -ForegroundColor White
Write-Host "     邮箱必须落在 auth.allowed_email_domains 白名单内（默认 sz-jlc.com）。" -ForegroundColor DarkGray
Write-Host "     执行后会打印一次初始密码，并落盘到 data\admin_initial_credentials.txt。" -ForegroundColor DarkGray
Write-Host ""

if ($SkipAutostart) {
    Write-Host "  2) 开机自启: 已按 -SkipAutostart 跳过（未提示）" -ForegroundColor DarkGray
} elseif ($autostartState -eq '已注册') {
    Write-Host "  2) 开机自启: 已注册" -ForegroundColor Green
    Write-Host "     确认: Get-ScheduledTask -TaskName 'DeerFlow Auto Start'" -ForegroundColor DarkGray
} else {
    Write-Host "  2) 注册开机自启（可选，需管理员权限）:" -ForegroundColor Yellow
    Write-Host "       & '$autostartScript'" -ForegroundColor White
    Write-Host "     它会创建计划任务（开机延迟 30s 拉起服务）并确保防火墙规则存在。" -ForegroundColor DarkGray
}
Write-Host ""

Write-Host "  3) 填入真实模型配置后才能使用 LLM 功能:" -ForegroundColor Yellow
Write-Host "       config.yaml 的 models 段 + .env 里的 *_API_KEY" -ForegroundColor White
Write-Host "     默认生成的 models 段是空的，页面上会显示「未配置」。" -ForegroundColor DarkGray
Write-Host ""

Write-Host "  ── 日常运维" -ForegroundColor Cyan
Write-Host "    查看状态 : & '$statusScript'          # -Tail 40 可看日志尾部"
Write-Host "    停止服务 : & '$scriptDir\stop.ps1'"
Write-Host "    启动服务 : & '$startScript'"
Write-Host ""

Write-Host "  ── 日志" -ForegroundColor Cyan
Write-Host "    部署日志   : $deployLogFile"
Write-Host "    安装日志   : $(Join-Path $logsDir 'install-backend.log')"
Write-Host "                 $(Join-Path $logsDir 'install-frontend.log')"
Write-Host "                 $(Join-Path $logsDir 'install-build.log')"
Write-Host "    服务日志   : $(Join-Path $logsDir 'gateway.log')"
Write-Host "                 $(Join-Path $logsDir 'frontend.log')"
Write-Host "    管理员日志 : $(Join-Path $logsDir 'admin-init.log')"
Write-Host ""

Write-DeployMilestone "=== 部署完成 (自启=$autostartState) ==="

exit 0
