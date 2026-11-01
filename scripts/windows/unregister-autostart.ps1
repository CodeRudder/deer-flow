<#
.SYNOPSIS
    注销 DeerFlow 开机自启：移除计划任务（可选移除防火墙规则）。

.DESCRIPTION
    本脚本只做「减法」，且只减 register-autostart.ps1 加上的东西：

        1. 计划任务   按「精确任务名」注销
        2. 防火墙     默认不动（原因见下）；显式给 -RemoveFirewall 才移除

    不删除任何数据、配置与日志，只在结尾提示它们的位置。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 为什么防火墙规则默认不删
      本脚本的注册端刻意复用 start.ps1 的规则名（"DeerFlow Frontend (Port 3000)"
      等），以避免同一端口出现两套名字。这意味着「这条规则是谁建的」在系统里
      无法区分 —— 目标机上这两条规则本来就是 start.ps1 建的。

      在这个前提下删规则是有害的：
        · 服务此刻可能仍在运行，删掉规则会让内网访问立刻中断，而「注销开机
          自启」的语义只是「下次开机不再自动起」，不该顺带切断现有服务
        · start.ps1 每次启动都会幂等重建这两条规则，删了也只是白删
      所以默认保留，只把「规则仍在，需要时用 -RemoveFirewall 或手动删」讲清楚。
      要做彻底清理（如卸载部署）时用 -RemoveFirewall。

    · 幂等：不存在时正常退出（退出码 0）
      注销的语义是「让系统到达未注册状态」，本来就没注册说明目标已达成。
      报错会让本脚本无法安全地反复调用（与 stop.ps1 的处理保持一致）。

    · 只按精确名称匹配，绝不模糊删除
      计划任务按 -TaskName 精确匹配；防火墙规则按 DisplayName 精确相等匹配。
      刻意不用 -like "*DeerFlow*"：那会波及同名前缀的其它部署实例或他人规则。

.PARAMETER TaskName
    要注销的计划任务名，必须与注册时一致，默认 'DeerFlow Auto Start'。

.PARAMETER RemoveFirewall
    连同 start.ps1 的入站规则一起移除。默认不移除（原因见上）。

.PARAMETER RootDir
    部署根目录，仅用于打印数据 / 配置 / 日志的位置提示，默认取
    DeerFlow.Common 的 Get-DeerFlowRoot()。

.EXAMPLE
    # 只注销开机自启，保留防火墙规则（服务可继续被内网访问）
    .\unregister-autostart.ps1

.EXAMPLE
    # 彻底清理：任务与防火墙规则都移除（准备卸载部署时用）
    .\unregister-autostart.ps1 -RemoveFirewall

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
    需要管理员权限：注销计划任务（RunLevel Highest 的任务）与移除防火墙规则。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$TaskName = 'DeerFlow Auto Start',

    [Parameter(Mandatory = $false)]
    [switch]$RemoveFirewall,

    [Parameter(Mandatory = $false)]
    [string]$RootDir
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。调用方据此会误判成「已注销」。这里显式接管成退出码 1。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非注销失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
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

# 与 register / start / stop 保持一致（本脚本不直接用工具链，但保证环境一致，
# 且缺工具链时能顺带给出提示）。
Add-ToolchainToPath

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$deployRoot = Get-DeerFlowRoot

$gatewayPort  = 8001
$frontendPort = 3000

# 与 register-autostart.ps1 的 Get-FirewallRuleName、start.ps1 的
# Ensure-DeerFlowFirewall 三处保持一致；分叉会导致认不出要删的规则。
$frontendRuleName = "DeerFlow Frontend (Port $frontendPort)"
$gatewayRuleName  = "DeerFlow Gateway (Port $gatewayPort)"

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Test-IsAdministrator {
    <#
    .SYNOPSIS
        当前进程是否以管理员身份运行。

    .DESCRIPTION
        不用 #Requires -RunAsAdministrator：那会在解析阶段直接终止脚本，
        连中文提示都打不出来（经 SSH 远程调用时只看到一行英文报错）。
        这里运行时判断，能给出可照做的提示。
    #>
    param()

    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Remove-DeerFlowTask {
    <#
    .SYNOPSIS
        按精确任务名注销计划任务，返回「是否真的删掉了」。

    .DESCRIPTION
        任务可能正在运行（有人刚手动触发过），此时 Unregister 会失败；
        先 Stop 再注销即可。仍失败则视为错误抛出，由顶层 trap 兜成退出码 1 ——
        「以为删掉了其实没删」是最坏的结果：下次开机服务又起来了。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $existing = @(Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) {
        Write-Ok "计划任务不存在，无需注销: $Name"
        return $false
    }

    Write-Info "当前状态: $($existing[0].State)"

    try {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
    } catch {
        Write-Warn "注销失败（任务可能正在运行），先停止再重试: $($_.Exception.Message)"
        Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
    }

    # 回读确认：注销命令没报错 != 任务真的没了
    $left = @(Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) {
        throw "注销后仍能查到任务: $Name"
    }

    Write-Ok "计划任务已注销: $Name"
    return $true
}

function Remove-DeerFlowFirewallRule {
    <#
    .SYNOPSIS
        按精确 DisplayName 移除防火墙规则，返回「是否真的删掉了」。

    .DESCRIPTION
        ⚠ 精确相等匹配（-eq），不是 -like。用 -like "*DeerFlow*" 会波及同前缀的
        其它部署实例或他人规则 —— 这是本脚本唯一的误删风险点，判据必须硬。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $existing = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
                  Where-Object { $_.DisplayName -eq $Name })

    if ($existing.Count -eq 0) {
        Write-Ok "防火墙规则不存在，无需移除: $Name"
        return $false
    }

    Remove-NetFirewallRule -DisplayName $Name -ErrorAction Stop

    $left = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
              Where-Object { $_.DisplayName -eq $Name })
    if ($left.Count -gt 0) {
        throw "移除后仍能查到防火墙规则: $Name"
    }

    Write-Ok "防火墙规则已移除: $Name"
    return $true
}

# ── 1. 前置检查 ─────────────────────────────────────────────────────────────

Write-Step "检查前置条件"

if (-not (Test-IsAdministrator)) {
    Write-Fail "本脚本需要管理员权限（注销计划任务与移除防火墙规则）"
    Write-Info "请在「以管理员身份运行」的 PowerShell 中执行，"
    Write-Info "或从管理员会话调用: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}
Write-Ok "已以管理员身份运行"

Write-Info "部署根目录: $deployRoot"

# ── 2. 注销计划任务 ─────────────────────────────────────────────────────────

Write-Step "注销开机自启计划任务"

$removedTask = Remove-DeerFlowTask -Name $TaskName

# ── 3. 防火墙规则 ───────────────────────────────────────────────────────────

Write-Step "处理防火墙规则"

if ($RemoveFirewall) {
    Remove-DeerFlowFirewallRule -Name $frontendRuleName | Out-Null
    Remove-DeerFlowFirewallRule -Name $gatewayRuleName  | Out-Null
} else {
    # 默认保留：见文件头说明（规则与 start.ps1 共用名字，且服务此刻可能仍在跑）
    Write-Ok "已保留防火墙规则（-RemoveFirewall 未指定）"
    Write-Info "规则 $frontendRuleName / $gatewayRuleName 仍在，服务的内网访问不受影响；"
    Write-Info "start.ps1 每次启动都会幂等重建它们，删除没有实际收益。"
    Write-Info "需要彻底清理时重新执行: .\unregister-autostart.ps1 -RemoveFirewall"
}

# ── 4. 数据 / 配置 / 日志位置提示（不删除） ─────────────────────────────────

Write-Step "保留的数据（本脚本不删除任何数据）"

$dataDir   = Get-DeerFlowDataDir
$configDir = $deployRoot

Write-Host "  数据库 / 会话数据 : $dataDir" -ForegroundColor DarkGray
Write-Host "  配置              : $(Get-DeerFlowConfigPath)" -ForegroundColor DarkGray
Write-Host "  环境变量          : $(Get-DeerFlowEnvPath)" -ForegroundColor DarkGray
Write-Host "  日志              : $(Get-DeerFlowLogsDir)" -ForegroundColor DarkGray
Write-Host "  代码              : $(Get-DeerFlowSrcDir)" -ForegroundColor DarkGray
Write-Host "  缓存              : $(Get-DeerFlowCacheDir)" -ForegroundColor DarkGray
Write-Info "如需彻底卸载，请人工确认后自行删除上述目录（配置根: $configDir）。"

# ── 5. 汇总 ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  DeerFlow 开机自启已注销" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

if ($removedTask) {
    Write-Host "  计划任务 : $TaskName —— 已移除" -ForegroundColor Green
} else {
    Write-Host "  计划任务 : $TaskName —— 本就不存在" -ForegroundColor DarkGray
}

if ($RemoveFirewall) {
    Write-Host "  防火墙   : 已移除（内网访问将在规则重建前不可用）" -ForegroundColor Yellow
} else {
    Write-Host "  防火墙   : 保留（内网访问不受影响）" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  注意：注销自启不会停止正在运行的服务。" -ForegroundColor Yellow
Write-Host "        停止服务请执行: .\stop.ps1" -ForegroundColor DarkGray
Write-Host "        重新注册自启  : .\register-autostart.ps1" -ForegroundColor DarkGray
Write-Host ""

exit 0
