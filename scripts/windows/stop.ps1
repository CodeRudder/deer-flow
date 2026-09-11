<#
.SYNOPSIS
    停止 DeerFlow 的两个服务：Gateway(8001) + Frontend(3000)。

.DESCRIPTION
    对每个服务执行：

        1. 定位进程  Common 的 Get-DeerFlowProcess（先 PID 文件，失效则按命令行
                     特征 + 部署根校验全量扫描）
        2. 停止进程  Common 的 Stop-DeerFlowProcessTree（先快照整棵树，再温和终止
                     根进程，最后把仍存活的后代强制清掉）
        3. 清 PID 文件

    最后再按端口复查一遍作为兜底：端口上若还有本项目的监听者，说明进程树没收
    干净（典型是子进程被重新挂靠、快照没抓到），这时按端口 owner 再收一次。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 为什么要按端口兜底复查
      Get-DeerFlowProcess 返回的是「一个」进程（第一个命中的）。若上一轮异常退出
      留下多份实例，只停一个端口仍然被占，而脚本会报「已停止」——下次 start 就会
      在端口预检那里失败，运维看到的却是 stop 说成功了。按端口收口能兜住这点。

    · 端口兜底必须再校验归属
      3000/8001 上完全可能是别人的进程（用户自己起的 next、别的 uvicorn）。
      本脚本只在「命令行含部署根目录」时才动手（Test-PathInRoot），否则只警告。
      这是本脚本唯一的误杀风险点，判据必须硬。

    · 幂等：没有服务在跑时打印「未运行」并 exit 0
      stop 的语义是「让系统到达停止状态」，本来就没跑说明目标已达成，报错会让
      stop.ps1 无法安全地反复调用（start.ps1 开头就要调它一次）。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准。

.PARAMETER WaitSeconds
    Stop-DeerFlowProcess 里「优雅终止」的等待秒数，默认 8。
    比 Common 模块默认的 10 略小：本脚本对两个服务依次操作，短一点能让交互式
    运维不至于等太久；超时后仍会 taskkill /F /T 强制收掉，不会停不下来。

.EXAMPLE
    # 停止本次部署的两个服务
    .\stop.ps1

.EXAMPLE
    # 指定部署根目录
    .\stop.ps1 -RootDir D:\deer-flow

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [int]$WaitSeconds = 8
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。start.ps1 会调用本脚本，若这里静默返回 0，它就以为端口已经
# 腾干净了。这里显式接管，保证「异常 => 退出码 1 + 出错位置」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非停止失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
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

# 与 start.ps1 保持一致。本脚本本身不直接调工具链，但 Common 的探测函数会用到，
# 且缺工具链时这里能顺带给出提示。
Add-ToolchainToPath

# ── 常量 ────────────────────────────────────────────────────────────────────

$rootDir = Get-DeerFlowRoot

$services = @(
    [pscustomobject]@{ Name = 'gateway';  Label = 'Gateway';  Port = 8001 },
    [pscustomobject]@{ Name = 'frontend'; Label = 'Frontend'; Port = 3000 }
)

$script:StoppedCount = 0
$script:FailCount    = 0

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

function Stop-PortOwner {
    <#
    .SYNOPSIS
        按端口兜底收尾：停掉仍占着该端口、且属于本项目的进程树。

    .DESCRIPTION
        为什么要「按端口再收一次」：Stop-DeerFlowProcess 只温和终止了 PID 文件里
        记录的 cmd.exe 包装进程，其子进程（uv → uvicorn → python /
        pnpm → next → next-server）会变成孤儿继续占着端口。详见 Common 模块
        Stop-DeerFlowPortOwner 的注释（那里有完整的实测记录）。

        本函数只是 Common 的 Stop-DeerFlowPortOwner 的一层薄封装：补上中文提示。
        归属校验、误杀防护、taskkill /F /T 都在 Common 里，与 start.ps1 回滚时
        用的是同一份实现 —— 避免两处各写一遍后行为漂移。

        ⚠ 返回码只表达「我们自己的进程收干净了没有」，不表达「端口空了没有」。
        端口被别人占着不算 stop.ps1 的失败：它已经把该停的服务停了，
        剩下的不在职责范围内。若把外部占用也算失败，stop 会返回非零，而
        start.ps1 开头恰好要调它一次清理遗留，运维就会看到「stop 失败」这种
        完全错误的信号。真正的端口冲突由 start.ps1 的端口预检负责报错
        （那里才有上下文说明该端口该由谁使用）。

    .OUTPUTS
        System.Int32 —— 0 = 我们自己的进程已收干净（端口上可能仍有别人的进程，
                        已通过 WARN 明确告知）；1 = 我们自己的进程没停掉。
    #>
    param(
        [Parameter(Mandatory = $true)]$Service
    )

    # 先把「端口上有别人」讲清楚：Common 的实现内部只写 Verbose，
    # 交互式运维看不到，而这恰恰是最需要解释清楚的状态。
    $pids = @(Get-PortOwnerPid -Port $Service.Port)
    foreach ($ownerPid in $pids) {
        $cmdLine = Get-ProcessCommandLine -ProcessId $ownerPid
        if (Test-PathInRoot -Text $cmdLine -Root $rootDir) { continue }

        $procName = '未知'
        $p = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        if ($p) { $procName = $p.ProcessName }

        Write-Warn "$($Service.Label) 端口 $($Service.Port) 被非本项目进程占用（PID $ownerPid / $procName），未终止"
        if ($cmdLine) { Write-Info "命令行: $cmdLine" }
        Write-Info "该进程不属于本部署，请自行判断是否需要处理（不影响本脚本的退出码）。"
    }

    $left = Stop-DeerFlowPortOwner -Port $Service.Port

    if ($left -gt 0) {
        Write-Fail "$($Service.Label) 端口 $($Service.Port) 仍被本项目进程占用（$left 个）"
        return 1
    }

    if ($pids.Count -gt 0 -and -not (Test-PortListening -Port $Service.Port)) {
        Write-Ok "端口 $($Service.Port) 已释放"
    }

    return 0
}
# ── 主流程 ──────────────────────────────────────────────────────────────────

Write-Step "停止 DeerFlow 服务"

foreach ($svc in $services) {
    Write-Host ""
    Write-Host "  ── $($svc.Label) (端口 $($svc.Port))" -ForegroundColor Cyan

    $proc = Get-DeerFlowProcess -Name $svc.Name

    if (-not $proc) {
        Write-Warn "$($svc.Label) 未运行"
    } else {
        $cmdShort = ''
        if ($proc.CommandLine) {
            $cmdShort = $proc.CommandLine
            if ($cmdShort.Length -gt 120) { $cmdShort = $cmdShort.Substring(0, 120) + '...' }
        }

        Write-Info "来源: $($proc.Source)（PidFile=来自 PID 文件，CommandLine=按命令行扫描）"
        if ($cmdShort) { Write-Info "命令行: $cmdShort" }

        # 用 ProcessTree 而不是 Stop-DeerFlowProcess：后者温和终止 cmd.exe 包装进程
        # 后，其子进程（uv → uvicorn → python / pnpm → next → next-server）会变成
        # 孤儿继续存活。详见 Common 模块里 Stop-DeerFlowProcessTree 的注释。
        $ok = Stop-DeerFlowProcessTree -ProcessId $proc.ProcessId -WaitSeconds $WaitSeconds

        if ($ok) {
            Write-Ok "已停止 $($svc.Label): PID $($proc.ProcessId)"
            $script:StoppedCount++
        } else {
            Write-Fail "$($svc.Label) 停止失败: PID $($proc.ProcessId)"
            $script:FailCount++
        }
    }

    # PID 文件无论进程是否找到都要清：留着陈旧 PID 会让下次的 status / stop
    # 把上一次运行的残留当成在跑。Get-DeerFlowProcess 已经会顺手清，这里
    # 再清一次是为了覆盖「进程没找到但文件还在」的分支。
    Remove-PidFile -Name $svc.Name

    # ── 按端口兜底复查 ──────────────────────────────────────────────────────
    # 返回码只反映「我们自己的进程是否收干净」；外部进程占用端口不算失败
    # （理由见 Stop-PortOwner 的注释）。
    $portRc = Stop-PortOwner -Service $svc
    if ($portRc -ne 0) {
        $script:FailCount++
    }
}

# ── 汇总 ────────────────────────────────────────────────────────────────────

Write-Host ""

if ($script:FailCount -gt 0) {
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host "  停止过程有 $($script:FailCount) 处异常" -ForegroundColor Red
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  已停止 $($script:StoppedCount) 个服务；端口可能仍被占用，" -ForegroundColor Yellow
    Write-Host "  请按上方提示手动处理后再执行 start.ps1。" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

if ($script:StoppedCount -eq 0) {
    Write-Host "==========================================" -ForegroundColor DarkGray
    Write-Host "  两个服务均未运行（无需停止）" -ForegroundColor DarkGray
    Write-Host "==========================================" -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  已停止 $($script:StoppedCount) 个服务" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

exit 0
