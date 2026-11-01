<#
.SYNOPSIS
    注册 DeerFlow 开机自启：放通防火墙 + 注册 Windows 计划任务。

.DESCRIPTION
    本脚本做两件幂等的事：

        1. 防火墙   确保 3000 / 8001 的入站规则存在（只放本网段）
        2. 计划任务 注册「开机自启」任务，动作是调用同目录的 start.ps1

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 为什么绝对不能用 New-Service
      Gateway 是 uvicorn、Frontend 是 node —— 都是普通用户态进程，不实现
      Windows 服务控制协议（SCM）。用 New-Service 注册出来的「服务」在服务
      管理器里能看见，但 SCM 无法与之通信：启动请求发出去永远等不到应答，
      最终以超时失败收场（事件日志 "服务没有及时响应启动或控制请求"）。
      这类进程只能交给计划任务拉起。

    · 触发器为什么是 AtStartup + 延迟（默认 30 秒）
      开机瞬间网络栈、磁盘、用户配置文件都还没就绪，而 start.ps1 要探测端口、
      读 config.yaml / .env、让 uv/pnpm 解析依赖缓存。抢在这时启动容易出现
      依赖解析失败或端口绑定失败。延迟 30 秒是折中：够系统就绪，又不至于让
      运维等太久。用 -DelaySeconds 可调，设 0 则不加延迟。

      实现上给 $trigger.Delay 赋 ISO 8601 时长（"PT30S"）。实测
      New-ScheduledTaskTrigger -AtStartup 的对象直接赋值是会被持久化的
      （注册后 Export-ScheduledTask 能看到 <Delay>），不需要改 XML。

    · 运行账户为什么用「当前用户」而不是 SYSTEM
      服务运行时要读「用户级」环境变量（本机 UV_CACHE_DIR=D:\deer-flow\cache\uv
      就是用户级变量），并把依赖缓存写在 D:\deer-flow\cache。SYSTEM 账户看不到
      用户级变量，uv/pnpm 会退回各自的默认缓存位置（C 盘 systemprofile 或
      用户目录），表现为依赖重复下载甚至解析失败。

      账户类型选 S4U（任务计划程序里的「不管用户是否登录都要运行」+「不存储
      密码」）：
        · 不必在目标机存明文密码，也不会因用户改密而失效
        · 开机即运行，不要求该账户此刻处于登录状态
        · 实测 S4U 令牌下 UV_CACHE_DIR / USERPROFILE / USERNAME 与交互登录
          完全一致，服务行为不变
      前提：该账户在本机「至少登录过一次」（用户配置文件已创建）。从没登录过
      时任务注册会成功、执行时才失败，遇到这种情况请先登录一次该账户。

      ⚠ 取舍要记住：S4U 令牌不带网络凭据，访问远程 SMB 共享 / UNC 路径会失败。
        本项目的服务是纯本机进程（只对外发 HTTPS 请求），不受影响。
        若日后需要挂载网络盘，改用 -LogonType Password 并配置存储凭据。

    · 任务失败为什么配 -RestartCount 3
      开机自启失败（如网卡尚未就绪导致 start.ps1 退出非 0）时自动重试，避免
      运维每次都要手动补启动。失败后间隔 1 分钟重试，最多 3 次。

      ⚠ 边界：该重试只覆盖「任务本身失败（start.ps1 退出码非 0）」。
        服务起来之后进程再崩溃，计划任务不会再管 —— 自启不是进程守护。

    · -ExecutionTimeLimit 0 表示不限时长
      服务是长期运行的，默认的「3 天后强制结束任务」会把服务砍掉。这里显式
      设为 0（PT0S = 无限制）。
      另外服务进程本身是 start.ps1 用 WMI 派生后与任务会话脱离的，任务退出
      不影响服务存活；限制时长针对的是任务动作自身的执行。

    · 防火墙规则名与 start.ps1 完全一致
      两边都生成 "DeerFlow Frontend (Port 3000)" / "DeerFlow Gateway (Port 8001)"。
      刻意复用同一套名字：若各起一套，Get-NetFirewallRule 里会出现两条放通同一
      端口的规则，排查网络问题时无从判断哪条在生效。start.ps1 本身就会幂等创建
      这两条规则，所以本脚本只做「缺了才补」，已有则原样跳过。

    · 幂等靠「先注销再注册」
      Register-ScheduledTask -Force 对已存在任务的触发器 / 设置更新并不可靠
      （实测有时保留旧触发器）。先 Unregister 再 Register 最稳，且保证重复执行
      的结果只取决于本次命令行参数。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow）。
    该值会被写进计划任务的命令行（追加 -RootDir <值>），因此部署根变动后必须
    重新运行本脚本，任务才会指向新位置。

.PARAMETER TaskName
    计划任务名，默认 'DeerFlow Auto Start'。

.PARAMETER SkipFirewall
    跳过防火墙的检查与补建。

.PARAMETER DelaySeconds
    开机触发器的延迟秒数，默认 30。设 0 表示立即触发（不加延迟）。

.EXAMPLE
    # 默认参数注册（部署根 D:\deer-flow，任务名 DeerFlow Auto Start）
    .\register-autostart.ps1

.EXAMPLE
    # 部署根不在默认位置 + 自定义任务名
    .\register-autostart.ps1 -RootDir D:\apps\deer-flow -TaskName 'DeerFlow Prod'

.EXAMPLE
    # 磁盘慢的机器，延迟放宽到 90 秒
    .\register-autostart.ps1 -DelaySeconds 90

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
    本文件必须保存为 UTF-8 with BOM + CRLF，否则 5.1 会按 ANSI(GBK) 解码中文。
    需要管理员权限：注册计划任务（RunLevel Highest）与创建防火墙规则。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [string]$TaskName = 'DeerFlow Auto Start',

    [Parameter(Mandatory = $false)]
    [switch]$SkipFirewall,

    [Parameter(Mandatory = $false)]
    [int]$DelaySeconds = 30
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。部署编排据此会误判成「注册成功」，运维随后发现开机根本没起。
# 这里显式接管，保证「异常 => 退出码 1 + 出错位置」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非注册失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
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

# ⚠ 必须调用：与 start.ps1 / stop.ps1 保持一致，缺工具链时也能顺带给出提示。
Add-ToolchainToPath

# ── 常量与路径 ──────────────────────────────────────────────────────────────

$deployRoot  = Get-DeerFlowRoot
$logsDir     = Get-DeerFlowLogsDir
$srcDir      = Get-DeerFlowSrcDir
$startScript = Join-Path $scriptDir 'start.ps1'

$gatewayPort  = 8001
$frontendPort = 3000

# 要放通的端口清单（顺序与 start.ps1 的调用一致，便于对照输出）
$ruleTargets = @(
    [pscustomobject]@{ Label = 'Frontend'; Port = $frontendPort },
    [pscustomobject]@{ Label = 'Gateway';  Port = $gatewayPort  }
)

# 计划任务里用哪个账户运行：当前用户，写成 机器名\用户名 以消除歧义。
# ⚠ 不能用 "$env:USERDOMAIN\$env:USERNAME"：域机器上没问题，但本机是
#   WORKGROUP 工作组，USERDOMAIN 就是字面量 "WORKGROUP"，拼出来
#   "WORKGROUP\gongdewei" 无法解析，Register-ScheduledTask 会报
#   HRESULT 0x80070534（No mapping between account names and security IDs）。
#   用 COMPUTERNAME 才是本地账户的正确限定形式。
$runAsUser = "$env:COMPUTERNAME\$env:USERNAME"

$script:FailCount = 0

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function Get-FirewallRuleName {
    <#
    .SYNOPSIS
        按 start.ps1 的命名规则生成防火墙规则名。

    .DESCRIPTION
        ⚠ 这里是「同一套名字」的唯一来源，必须与 start.ps1 的
        Ensure-DeerFlowFirewall 保持一致：
            $ruleName = "DeerFlow $label (Port $port)"
        两处一旦分叉，同一端口就会出现两条放通规则，且本脚本的「已存在则跳过」
        会失效（认不出 start.ps1 建的那条）。改动时两边一起改。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][int]$Port
    )

    return "DeerFlow $Label (Port $Port)"
}

function Test-IsAdministrator {
    <#
    .SYNOPSIS
        当前进程是否以管理员身份运行。

    .DESCRIPTION
        不用 #Requires -RunAsAdministrator：那会在解析阶段直接终止脚本，
        连帮助和中文提示都打不出来（经 SSH 远程调用时只看到一行英文报错）。
        这里运行时判断，能给出一条能照着做的中文提示。
    #>
    param()

    $identity  = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-DeerFlowLanIp {
    <#
    .SYNOPSIS
        返回本机用于内网访问的 IPv4 地址，取不到时返回 $null。

    .DESCRIPTION
        判据是「默认路由所在网卡的地址」，而不是「第一个非环回地址」——
        与 start.ps1 / status.ps1 保持一致。

        目标机装了 VMware / VirtualBox，会多出 192.168.137.1、192.168.56.1 等
        虚拟网卡地址；按「第一个」取几乎必然取到虚拟网卡，打印出来的 URL 从
        内网其它机器根本连不上。默认路由指向的才是真实出口网卡。
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

function Ensure-DeerFlowFirewallRule {
    <#
    .SYNOPSIS
        确保一条入站规则存在（幂等），返回 $true 表示最终可用。

    .DESCRIPTION
        参数与 start.ps1 的 Ensure-DeerFlowFirewall 完全一致，尤其是
        -RemoteAddress LocalSubnet（只放本网段，不暴露到不可信网络）与
        -Profile Any（网卡被判定为「公用网络」时 public 配置也要放通，
        否则内网访问全部超时）。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$Port
    )

    # 按 DisplayName 精确匹配：不误判同端口的他人规则，也避免重复创建。
    $existing = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
                  Where-Object { $_.DisplayName -eq $Name })

    if ($existing.Count -gt 0) {
        Write-Ok "防火墙规则已存在，跳过创建: $Name"
        return $true
    }

    try {
        New-NetFirewallRule `
            -DisplayName $Name `
            -Direction Inbound `
            -Protocol TCP `
            -LocalPort $Port `
            -Action Allow `
            -RemoteAddress LocalSubnet `
            -Profile Any `
            -ErrorAction Stop | Out-Null
        Write-Ok "已创建防火墙规则: $Name（入站 TCP $Port，仅本网段）"
        return $true
    } catch {
        Write-Fail "创建防火墙规则失败: $Name —— $($_.Exception.Message)"
        Write-Info "可手动执行: New-NetFirewallRule -DisplayName '$Name' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -RemoteAddress LocalSubnet -Profile Any"
        return $false
    }
}

function Register-DeerFlowAutostartTask {
    <#
    .SYNOPSIS
        注册（或重建）开机自启计划任务。

    .DESCRIPTION
        动作：powershell.exe -NoProfile -ExecutionPolicy Bypass -File <start.ps1> -RootDir <root>
          · -NoProfile 避免用户 profile 里的脚本污染任务进程环境
          · -ExecutionPolicy Bypass 避免受组策略限制的执行策略挡住 start.ps1
          · 追加 -RootDir 让任务自带部署根，与本次注册时解析出的根一致

        触发器：AtStartup（+ 可选延迟，见文件头说明）
        账户  ：$runAsUser / S4U / RunLevel Highest（见文件头说明）
        设置  ：不限时长、失败重试 3 次、电池场景不中断
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$StartPath,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][int]$Delay
    )

    # 幂等：同名任务先注销。顺序很重要 —— 若先 Register -Force 再指望它覆盖，
    # 旧的触发器可能残留（见文件头说明）。
    $existing = @(Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue)
    if ($existing.Count -gt 0) {
        Write-Info "已存在同名任务，先注销再重建: $Name"
        try {
            Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
        } catch {
            # 任务正在运行时可能注销失败：先停掉再试一次。
            Write-Warn "注销失败（任务可能正在运行），尝试停止后重试: $($_.Exception.Message)"
            Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction Stop
        }
        Write-Ok "已注销旧任务"
    }

    # ⚠ 整个命令行拼成一个字符串再交给 -Argument。PowerShell 会给含空格的值
    #   自动加引号，但我们要的是「路径带空格也能正确解析」，所以手动加引号，
    #   不依赖其内部的引号推断。
    $argText = '-NoProfile -ExecutionPolicy Bypass -File "' + $StartPath + '"' +
               ' -RootDir "' + $Root + '"'

    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argText

    $trigger = New-ScheduledTaskTrigger -AtStartup
    if ($Delay -gt 0) {
        # ISO 8601 时长，如 PT30S。实测该属性经对象注册后会被持久化。
        $trigger.Delay = 'PT' + $Delay + 'S'
    }

    $principal = New-ScheduledTaskPrincipal -UserId $runAsUser -LogonType S4U -RunLevel Highest

    # -ExecutionTimeLimit 0：不限时长（服务长期运行，默认 3 天会被砍）
    # -MultipleInstances IgnoreNew：开机触发与手动触发重叠时，后者被忽略，
    #   避免两个 start.ps1 并发抢端口。
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew

    $description = "DeerFlow 开机自启：调用 $StartPath 拉起 Gateway(8001) 与 Frontend(3000)。" +
                   "运行账户 $runAsUser（S4U，需要该账户至少登录过一次）；" +
                   "需要用户级环境变量（UV_CACHE_DIR 等），故不用 SYSTEM 账户。" +
                   "由 register-autostart.ps1 注册。"

    try {
        Register-ScheduledTask `
            -TaskName $Name `
            -Action $action `
            -Trigger $trigger `
            -Principal $principal `
            -Settings $settings `
            -Description $description `
            -Force `
            -ErrorAction Stop | Out-Null
    } catch {
        throw "注册计划任务失败: $($_.Exception.Message)（账户 $runAsUser 是否存在于本机？）"
    }

    $delayLabel = "无延迟"
    if ($Delay -gt 0) { $delayLabel = "延迟 $Delay 秒" }

    Write-Ok "计划任务已注册: $Name"
    Write-Info "触发器: 开机时（$delayLabel）"
    Write-Info "账户  : $runAsUser（S4U，最高权限，不需登录）"
}

# ── 1. 前置检查 ─────────────────────────────────────────────────────────────

Write-Step "检查前置条件"

if (-not (Test-IsAdministrator)) {
    Write-Fail "本脚本需要管理员权限（注册计划任务与创建防火墙规则）"
    Write-Info "请在「以管理员身份运行」的 PowerShell 中执行，"
    Write-Info "或从管理员会话调用: powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 1
}
Write-Ok "已以管理员身份运行"

if (-not (Test-Path $startScript)) {
    Write-Fail "未找到 start.ps1: $startScript"
    Write-Info "本脚本必须与 start.ps1 放在同一目录。"
    exit 1
}
Write-Ok "start.ps1 已就位: $startScript"

Write-Info "部署根目录: $deployRoot"
Write-Info "运行账户  : $runAsUser"

# ── 2. 防火墙（幂等） ───────────────────────────────────────────────────────

Write-Step "配置防火墙"

if ($SkipFirewall) {
    Write-Warn "已跳过防火墙检查（-SkipFirewall）"
    Write-Info "若内网其它机器访问不了，请确认 $frontendPort / $gatewayPort 已放通。"
} else {
    foreach ($target in $ruleTargets) {
        $ruleName = Get-FirewallRuleName -Label $target.Label -Port $target.Port
        $ok = Ensure-DeerFlowFirewallRule -Name $ruleName -Port $target.Port
        if (-not $ok) { $script:FailCount++ }
    }
}

# ── 3. 注册计划任务 ─────────────────────────────────────────────────────────

Write-Step "注册开机自启计划任务"

Register-DeerFlowAutostartTask -Name $TaskName -StartPath $startScript `
                               -Root $deployRoot -Delay $DelaySeconds

# ── 4. 回读校验 ─────────────────────────────────────────────────────────────
#
# 注册完立刻回读一次：确认任务真的落盘、触发器是开机、动作指向 start.ps1。
# 「注册命令没报错」不等于「任务符合预期」——例如账户名解析异常时可能注册成
# 别的形式，这里当场就能看出来。

Write-Step "回读校验"

$task = @(Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
if ($task.Count -eq 0) {
    Write-Fail "回读不到刚注册的任务: $TaskName"
    exit 1
}

$task = $task[0]

$triggerKind = '未知'
if ($task.Triggers.Count -gt 0) {
    $triggerKind = $task.Triggers[0].CimClass.CimClassName
}

Write-Host "  任务名     : $($task.TaskName)"
Write-Host "  状态       : $($task.State)"
Write-Host "  触发器     : $triggerKind"
if ($task.Triggers.Count -gt 0 -and $task.Triggers[0].Delay) {
    Write-Host "  触发延迟   : $($task.Triggers[0].Delay)"
}
Write-Host "  运行账户   : $($task.Principal.UserId) / LogonType=$($task.Principal.LogonType) / RunLevel=$($task.Principal.RunLevel)"

foreach ($act in $task.Actions) {
    Write-Host "  动作       : $($act.Execute) $($act.Arguments)"
}

if ($triggerKind -ne 'MSFT_TaskBootTrigger') {
    Write-Fail "触发器不是开机触发（实际: $triggerKind）"
    $script:FailCount++
}

$actionText = ''
if ($task.Actions.Count -gt 0) { $actionText = [string]$task.Actions[0].Arguments }
if ($actionText.IndexOf('start.ps1', [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
    Write-Fail "任务动作未指向 start.ps1（实际: $actionText）"
    $script:FailCount++
}

# ── 5. 汇总 ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
if ($script:FailCount -eq 0) {
    Write-Host "  DeerFlow 开机自启已注册" -ForegroundColor Cyan
} else {
    Write-Host "  DeerFlow 开机自启注册完成，但有 $($script:FailCount) 项失败" -ForegroundColor Yellow
}
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$lanIp = Get-DeerFlowLanIp
if ($lanIp) {
    Write-Host "  内网访问地址（其它机器用这个）:" -ForegroundColor Green
    Write-Host "    http://${lanIp}:$frontendPort" -ForegroundColor Green
    Write-Host "    http://${lanIp}:$gatewayPort/health" -ForegroundColor Green
} else {
    Write-Warn "未能识别默认路由网卡，无法给出内网地址。候选如下，请自行挑选:"
    foreach ($row in (Get-AllLanCandidates)) { Write-Info $row }
}

Write-Host ""
Write-Host "  计划任务 : $TaskName" -ForegroundColor Yellow
if (-not $SkipFirewall) {
    foreach ($target in $ruleTargets) {
        $ruleName = Get-FirewallRuleName -Label $target.Label -Port $target.Port
        Write-Host "  防火墙   : $ruleName（入站 TCP $($target.Port)，仅本网段）" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "  开机行为 : 系统启动 ${DelaySeconds} 秒后自动调用 start.ps1" -ForegroundColor DarkGray
Write-Host "             运行账户 $runAsUser 需在本机至少登录过一次" -ForegroundColor DarkGray
Write-Host "             自启只保证「开机拉起」，不负责进程崩溃后的守护" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  验证方法 :" -ForegroundColor Yellow
Write-Host "    # 1. 确认任务已注册（能看见触发器/动作/账户）" -ForegroundColor DarkGray
Write-Host "    Get-ScheduledTask -TaskName '$TaskName' | Format-List TaskName,State,Triggers,Actions" -ForegroundColor DarkGray
Write-Host "    (Get-ScheduledTask -TaskName '$TaskName').Triggers[0].Delay" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    # 2. 手动触发一次（会先停掉现有服务再重新拉起，约需 1-2 分钟）" -ForegroundColor DarkGray
Write-Host "    Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "    .\status.ps1        # 退出码 0 且两个服务「运行中」即成功" -ForegroundColor DarkGray
Write-Host ""
Write-Host "    # 3. 看任务执行历史（事件查看器 → 应用程序和服务日志 → Microsoft" -ForegroundColor DarkGray
Write-Host "    #    → Windows → TaskScheduler → Operational）" -ForegroundColor DarkGray
Write-Host "    Get-ScheduledTaskInfo -TaskName '$TaskName' | Format-List LastRunTime,LastTaskResult,NumberOfMissedRuns" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  取消自启 : .\unregister-autostart.ps1" -ForegroundColor Yellow
Write-Host ""

if ($script:FailCount -gt 0) { exit 1 }
exit 0
