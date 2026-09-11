<#
.SYNOPSIS
    安装 DeerFlow 的依赖（后端 uv / 前端 pnpm）并构建前端。

.DESCRIPTION
    三步，每步都能单独跳过：

        1. 后端依赖    <src>\backend    uv sync --all-packages --frozen
        2. 前端依赖    <src>\frontend   pnpm install --frozen-lockfile
        3. 前端构建    <src>\frontend   pnpm build（SKIP_ENV_VALIDATION=1）

    每步日志分别落在 <RootDir>\logs\install-backend.log / install-frontend.log /
    install-build.log；任一步失败都会打印该步日志尾部，并以非零码退出（不静默继续）。

    几个不显然的决策，写在这里避免日后被「顺手改回去」：

    · 后端不加 --extra postgres
      本部署的 database.backend 是 sqlite（见 init-config.ps1），加了只会多装
      一套 psycopg 及其编译依赖。

    · 后端不需要额外指定镜像
      backend\pyproject.toml 的 [tool.uv] index-url 已指向清华 tuna，
      命令行再传 --index-url 容易与项目内配置打架。

    · 前端不传 --registry
      用户级 NPM_CONFIG_REGISTRY 已由 install-toolchain.ps1 写成 npmmirror，
      pnpm 自动继承；在这里再写死一份，将来换源要改两处。

    · 构建必须设 SKIP_ENV_VALIDATION=1
      frontend\src\env.js 在构建时校验环境变量，缺一项直接 fail。
      这与 frontend\Makefile 的 build 目标保持一致。

    · 构建刻意不设 NEXT_PUBLIC_STATIC_WEBSITE_ONLY
      该变量为 true 时前端切到「纯静态演示站」模式，会禁用所有依赖后端的
      功能（登录、会话、工具调用）。本部署要的就是后端功能，而它一旦被设上，
      构建照样通过、页面功能却静默缺失——极难排查。因此脚本不仅不设它，
      还会在构建前把进程环境里的同名变量清掉。

    · 构建刻意不设 NEXT_CONFIG_BUILD_OUTPUT=standalone
      那是 frontend\Makefile 的 build-static 目标（产出可独立分发的包）；
      本方案用标准构建 + next start 起服务。

    ⚠ 长任务一律同步等待
      实测后台 Start-Process（不等待）时进程会在 60 秒左右消失且日志 0 字节。
      本脚本用 Start-Process + WaitForExit(超时)，既不丢子进程，又能兜住卡死。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会写入进程级
    DEER_FLOW_DEPLOY_ROOT，使 Common 模块的所有路径函数以它为准。

.PARAMETER SkipBackend
    跳过第 1 步（后端依赖）。

.PARAMETER SkipFrontend
    跳过第 2 步（前端依赖）。

.PARAMETER SkipBuild
    跳过第 3 步（前端构建）。
    只重跑构建：.\install.ps1 -SkipBackend -SkipFrontend

.PARAMETER Force
    忽略幂等检查，强制重装。会先删除对应产物再装：
        .venv / node_modules / .next
    ⚠ 后端重装要重新解析并下载全部依赖，实测 3-15 分钟；node_modules 约 2 GB。

.EXAMPLE
    # 首次安装（已装好的部分会自动跳过，可安全重复执行）
    .\install.ps1

.EXAMPLE
    # 只重跑前端构建
    .\install.ps1 -SkipBackend -SkipFrontend

.EXAMPLE
    # 强制重建前端（删除 .next 后重新 build）
    .\install.ps1 -SkipBackend -SkipFrontend -Force

.EXAMPLE
    # 指定部署根目录
    .\install.ps1 -RootDir D:\deer-flow

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
    [switch]$SkipBackend,

    [Parameter(Mandatory = $false)]
    [switch]$SkipFrontend,

    [Parameter(Mandatory = $false)]
    [switch]$SkipBuild,

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 顶层异常兜底 ────────────────────────────────────────────────────────────
#
# $ErrorActionPreference='Stop' 让任何未捕获的异常直接终止脚本，但「终止」不
# 等于「非零退出」：PowerShell 会把它当作命令错误继续往下跑调用方的脚本，
# 退出码仍是 0。实测踩坑：脚本里一处笔误（把 [Environment]::IsNullOrWhiteSpace
# 写成了 [string]::IsNullOrWhiteSpace）导致中途抛异常，控制台打了一行错误、
# 后面的步骤全部没执行，最终退出码却是 0 —— 部署编排会误判成「安装成功」。
# 这里显式接管，保证「异常 => 退出码 1 + 出错行号」。
trap {
    Write-Host ""
    Write-Host "  [FAIL] 脚本异常终止: $($_.Exception.Message)" -ForegroundColor Red
    if ($_.InvocationInfo -and $_.InvocationInfo.ScriptLineNumber) {
        Write-Host "         位置: $($_.InvocationInfo.PositionMessage)" -ForegroundColor DarkGray
    }
    Write-Host "         这是脚本缺陷（而非安装失败），请连同上面的位置信息反馈。" -ForegroundColor DarkGray
    Write-Host ""
    exit 1
}

# ── 引入公共模块 ────────────────────────────────────────────────────────────

# -RootDir 必须在 Import 之前写进环境变量：Common 的 Get-DeerFlowRoot() 每次
# 调用都重读该变量（而不是 Import 时算一次），顺序反了其实也不影响，但先设置
# 语义更清楚。
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

# 单步超时。需求要求：单步超过 30 分钟视为失败。
# 依赖解析 + 下载在慢网络下可能很久，30 分钟是「确定卡住」与「只是慢」的分界。
$script:StepTimeoutMinutes = 30

# 可用空间低于此值只警告、不阻断——uv / pnpm 的缓存可以在别处，
# 一刀切拒绝安装会让本来能装完的机器白跑一趟。
$script:MinFreeSpaceGb = 10

# 失败时回显的日志行数：够看到真正的报错，又不会把控制台刷爆。
$script:TailLines = 30

$rootDir     = Get-DeerFlowRoot
$srcDir      = Get-DeerFlowSrcDir
$logsDir     = Get-DeerFlowLogsDir
$backendDir  = Join-Path $srcDir 'backend'
$frontendDir = Join-Path $srcDir 'frontend'

$logBackend  = Join-Path $logsDir 'install-backend.log'
$logFrontend = Join-Path $logsDir 'install-frontend.log'
$logBuild    = Join-Path $logsDir 'install-build.log'

# 收集每步结果，最后统一汇总耗时。用 List 而不是数组 +=，避免重复分配。
$script:Results = New-Object System.Collections.Generic.List[object]

# ── 输出辅助 ────────────────────────────────────────────────────────────────

function Write-Skip {
    <#
    .SYNOPSIS
        输出「已跳过」项（"  [SKIP] ..."），深青色——与 OK / WARN / FAIL 区分开。
    #>
    param([string]$Message)
    Write-Host "  [SKIP] $Message" -ForegroundColor DarkCyan
}

function Write-StepLog {
    <#
    .SYNOPSIS
        写一个阶段标题：控制台用带颜色的 Write-Step，日志文件里落一行同样的标题。

    .DESCRIPTION
        刻意不复用 Common 的 Write-Log —— 它会同时回显到控制台，标题就会打印两遍。
        这里只把标题追加进日志，因此需要自己拼时间戳；编码沿用「UTF-8 无 BOM 追加」
        （有 BOM 时每次追加都会在文件中段插入 EF BB BF，按行读取会看到乱码前缀）。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][string]$LogFile
    )

    Write-Step $Message

    try {
        $dir = Split-Path -Parent $LogFile
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -Path $dir -ItemType Directory -Force | Out-Null
        }
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::AppendAllText($LogFile, "[$stamp] ==> $Message`r`n", $utf8NoBom)
    } catch {
        Write-Warn "写入日志标题失败: $($_.Exception.Message)"
    }
}

function Start-StepLog {
    <#
    .SYNOPSIS
        为一次「真正要执行」的步骤重建日志文件（清空旧内容 + 写运行头）。

    .DESCRIPTION
        为什么是清空而不是追加：这三个日志的用途是「诊断本次运行」，混着上几次的
        输出会让尾部刷屏、也看不出这次到底跑到哪。跳过（skip）的步骤不会调用本函数，
        因此上一步留下的日志仍是完整的，需要复盘时不会被清掉。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Title
    )

    $dir = Split-Path -Parent $LogFile
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }

    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $head = "================================================================`r`n" +
            "$Title`r`n" +
            "运行时间: $stamp`r`n" +
            "部署根目录: $rootDir`r`n" +
            "代码目录: $srcDir`r`n" +
            "================================================================`r`n"

    # Write-TextFileNoBom（Common 模块）保证无 BOM：cmd 的重定向按字节追加，
    # 带 BOM 会让第二次追加在文件中段插入 EF BB BF。
    Write-TextFileNoBom -Path $LogFile -Content $head
}

function Format-Duration {
    <#
    .SYNOPSIS
        把秒数格式化成 "12.3s" / "3m 05s"，便于扫读。
    #>
    param([double]$Seconds)

    if ($Seconds -lt 60) { return ('{0:N1}s' -f $Seconds) }

    $minutes = [math]::Floor($Seconds / 60)
    $rest = $Seconds - ($minutes * 60)
    return ('{0}m {1:N0}s' -f $minutes, $rest)
}

function Write-LogTail {
    <#
    .SYNOPSIS
        打印日志文件的最后若干行（失败诊断用）。

    .DESCRIPTION
        按 UTF-8 读取：子进程的 stdout/stderr 被 cmd 重定向进文件，node 与 uv 都
        写 UTF-8 字节。对后端另外设了 PYTHONUTF8=1，Python 的输出同样是 UTF-8。
        .NET 的 UTF8 解码器遇到非法字节会替换成 U+FFFD 而不是抛异常，因此即便
        混入少量非 UTF-8 内容也不会让这里二次失败。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $false)][int]$Lines = 30
    )

    if (-not (Test-Path $LogFile)) {
        Write-Fail "$Tag 失败，且没有生成日志文件: $LogFile"
        return
    }

    $all = @([System.IO.File]::ReadAllLines($LogFile, [System.Text.Encoding]::UTF8))
    if ($all.Count -eq 0) {
        Write-Fail "$Tag 失败，日志文件为空: $LogFile"
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

function Invoke-LoggedCommand {
    <#
    .SYNOPSIS
        在指定目录里执行一条外部命令，stdout/stderr 合并重定向到日志文件，
        同步等待（带超时），返回退出码。

    .DESCRIPTION
        必须经 cmd.exe 而不是直接用 `&` 调用，理由：

          · pnpm 是 .cmd 批处理，CreateProcess 无法直接执行批处理，
            Start-Process -FilePath pnpm.cmd 会报「不是有效的 Win32 应用程序」，
            所以统一走 `cmd /c`，.cmd 还要加 `call`（否则批处理里的 exit 会把
            cmd 自己一并结束，外层重定向与退出码都拿不到）。
          · 用 cmd 的重定向拿日志，比 Start-Process 的 -RedirectStandardOutput
            更省事：后者在 5.1 上要求把两条流分别落到两个文件，读起来还得自己
            交错合并。
          · 用追加 `>>` 而不是覆盖 `>`：`>` 会在子进程启动之前就把文件截断，
            实测把调用方刚写好的日志头（运行时间 / 路径）整段抹掉 —— 文件开头
            直接变成 uv / pnpm 的第一行输出。截断的职责已由 Start-StepLog 承担
            （Write-TextFileNoBom 是覆盖写），这里只需要追加。
          · 整条命令用 (...) 包起来，保证重定向作用于全部子命令。
            命令行以 `2>&1` 结尾而不是引号结尾，这样 cmd /s 的
            「剥掉首尾引号」语义会正好剥掉 Start-Process 自动加上的那对引号。
            实测：pnpm.cmd --version → 0，uv.exe --version → 0，
            非法参数 → 2 且报错文本完整落在日志里。

        同步等待用 WaitForExit(毫秒)。这比 -Wait 更强：-Wait 只能无限等，
        这里能在超时后强杀进程树，避免一个卡死的 pnpm 把整个部署挂住。

    .PARAMETER Exe
        可执行文件绝对路径（.exe / .cmd / .bat）。

    .PARAMETER Arguments
        参数数组。含空白或引号的参数会自动补双引号。

    .PARAMETER WorkingDir
        工作目录（用 cd /d 切换，因此不受 PowerShell 当前目录影响）。

    .PARAMETER LogFile
        日志文件绝对路径（覆盖写入；调用前应由 Start-StepLog 建好）。

    .PARAMETER TimeoutMinutes
        超时分钟数，超时会连子进程树一起杀掉。

    .OUTPUTS
        PSCustomObject（TimedOut / ExitCode / CommandLine）
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDir,
        [Parameter(Mandatory = $true)][string]$LogFile,
        [Parameter(Mandatory = $false)][int]$TimeoutMinutes = 30
    )

    # 参数含空白或引号时补双引号。本脚本用到的参数都是简单开关或路径，
    # 不含 cmd 元字符（& | < > ^ ( )），因此不需要更复杂的转义。
    $quoted = @()
    foreach ($a in $Arguments) {
        if ($a -match '[\s"]') {
            $quoted += '"' + ($a -replace '"', '\"') + '"'
        } else {
            $quoted += $a
        }
    }

    if ($Exe -match '\.(cmd|bat)$') {
        $invoke = 'call "' + $Exe + '"'
    } else {
        $invoke = '"' + $Exe + '"'
    }

    $inner = 'cd /d "' + $WorkingDir + '" && ' + $invoke
    if ($quoted.Count -gt 0) { $inner = $inner + ' ' + ($quoted -join ' ') }

    # 追加而不是覆盖：文件已由 Start-StepLog 写好头并清空过（详见函数注释）
    $cmdLine = '(' + $inner + ') >> "' + $LogFile + '" 2>&1'

    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $proc = Start-Process -FilePath $cmdExe `
                          -ArgumentList @('/d', '/s', '/c', $cmdLine) `
                          -PassThru

    $finished = $proc.WaitForExit($TimeoutMinutes * 60 * 1000)

    if (-not $finished) {
        # 超时：连同子进程树一起杀。pnpm -> node、uv -> 其子进程都挂在 cmd 之下，
        # 只杀 cmd 会留下孤儿继续占着 .venv / node_modules 的文件句柄，
        # 下一次重跑就会撞「文件被占用」。
        #
        # ⚠ 必须用 $proc.Id 而不是 $proc.ProcessId。
        #   Start-Process -PassThru 返回的是 System.Diagnostics.Process，它的进程号
        #   属性叫 Id；ProcessId 是 CIM/Win32_Process 上的名字（Common 模块里到处
        #   在用，很容易顺手写错）。写错时 PowerShell 不报错、只求值成 $null，
        #   taskkill 收到空 PID 后报 "Invalid syntax. Value expected for '/PID'"
        #   并以 1 退出 —— 结果就是「超时判定正确、进程树却一个都没杀掉」，
        #   静默留下孤儿进程。已实测确认两者差别。
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        & $taskkill /F /T /PID $proc.Id 2>&1 | Out-Null
        return [pscustomobject]@{
            TimedOut    = $true
            ExitCode    = -1
            CommandLine = $cmdLine
        }
    }

    $code = $proc.ExitCode
    if ($null -eq $code) { $code = -1 }

    return [pscustomobject]@{
        TimedOut    = $false
        ExitCode    = $code
        CommandLine = $cmdLine
    }
}

function Get-ToolProbe {
    <#
    .SYNOPSIS
        探测一个命令是否可用，返回解析到的完整路径与版本文本。

    .DESCRIPTION
        版本文本合并 stderr —— py.exe -3.12 --version 走的就是 stderr，
        只收 stdout 会拿到空字符串（表现为「装了却报没有」）。

        同时临时把 ErrorActionPreference 降为 Continue：5.1 在 Stop 下对本机
        原生命令的 stderr 偶发抛 NativeCommandError，那会让「探测」变成「崩溃」。

    .OUTPUTS
        PSCustomObject（Found / Path / Version / ExitCode）
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @('--version')
    )

    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if (-not $cmd) {
        return [pscustomobject]@{ Found = $false; Path = $null; Version = ''; ExitCode = -1 }
    }

    $path = $cmd.Source
    if (-not $path) { $path = $cmd.Path }

    $oldPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = @(& $path @Arguments 2>&1)
        $code = $LASTEXITCODE
    } catch {
        $out = @($_.Exception.Message)
        $code = -1
    } finally {
        $ErrorActionPreference = $oldPreference
    }

    $text = (@($out) | ForEach-Object { ($_ | Out-String).Trim() }) -join ' '
    $text = ($text -replace '\s+', ' ').Trim()

    return [pscustomobject]@{
        Found    = $true
        Path     = $path
        Version  = $text
        ExitCode = $code
    }
}

# ── 前置检查 ────────────────────────────────────────────────────────────────

Write-Step 'DeerFlow 依赖安装与前端构建'

Write-Info "部署根目录 : $rootDir"
Write-Info "代码目录   : $srcDir"
Write-Info "日志目录   : $logsDir"

if ($Force) {
    Write-Warn '-Force：将忽略幂等检查，删除对应产物后重装'
    Write-Info '受影响目录：backend\.venv / frontend\node_modules / frontend\.next'
}

if ($SkipBackend -or $SkipFrontend -or $SkipBuild) {
    $skipped = @()
    if ($SkipBackend)  { $skipped += '后端' }
    if ($SkipFrontend) { $skipped += '前端依赖' }
    if ($SkipBuild)    { $skipped += '前端构建' }
    Write-Info "本次跳过: $($skipped -join ' / ')"
}

# ── 前置检查 1/4：目录 ──────────────────────────────────────────────────────

Write-Step '前置检查 1/4：部署目录'

$dirTargets = @(
    [pscustomobject]@{ Desc = '部署根目录';   Path = $rootDir },
    [pscustomobject]@{ Desc = '代码目录';     Path = $srcDir },
    [pscustomobject]@{ Desc = '后端目录';     Path = $backendDir },
    [pscustomobject]@{ Desc = '前端目录';     Path = $frontendDir },
    [pscustomobject]@{ Desc = '日志目录';     Path = $logsDir }
)

$dirOk = $true
foreach ($t in $dirTargets) {
    if (Test-Path $t.Path) {
        Write-Ok "$($t.Desc)：$($t.Path)"
    } else {
        Write-Fail "$($t.Desc) 不存在：$($t.Path)"
        $dirOk = $false
    }
}

if (-not $dirOk) {
    Write-Info '请先同步代码仓库：scripts\windows-remote\sync-to-windows.sh'
    exit 1
}

# 仓库的「锚文件」——只有目录没有这些文件，说明同步不完整或同步到了别的目录。
$anchorFiles = @(
    [pscustomobject]@{ Desc = '后端 pyproject.toml'; Path = (Join-Path $backendDir 'pyproject.toml') },
    [pscustomobject]@{ Desc = '前端 package.json';   Path = (Join-Path $frontendDir 'package.json') },
    [pscustomobject]@{ Desc = '前端 pnpm-lock.yaml'; Path = (Join-Path $frontendDir 'pnpm-lock.yaml') }
)

foreach ($f in $anchorFiles) {
    if (Test-Path $f.Path) {
        Write-Ok "$($f.Desc) 存在"
    } else {
        Write-Fail "$($f.Desc) 缺失：$($f.Path)"
        Write-Info '代码同步不完整，请重新同步后再试。'
        exit 1
    }
}

# ── 前置检查 2/4：工具链 ────────────────────────────────────────────────────

Write-Step '前置检查 2/4：工具链'

# ⚠ 这一步不能省。目标机系统 PATH 里有 C:\Program Files\nodejs（Node 16），
# 而 Windows 的有效 PATH = 系统 PATH + 用户 PATH（系统段在前），用户级 PATH
# 无法覆盖系统级的同名列。只能在进程内显式前置，详见 Common 模块的注释。
Add-ToolchainToPath

$toolChecks = @(
    [pscustomobject]@{ Name = 'node'; Exe = 'node';    Args = @('--version'); Required = $true;  Hint = '请先运行 install-toolchain.ps1' },
    [pscustomobject]@{ Name = 'pnpm'; Exe = 'pnpm';    Args = @('--version'); Required = $true;  Hint = '请先运行 install-toolchain.ps1' },
    [pscustomobject]@{ Name = 'uv';   Exe = 'uv';      Args = @('--version'); Required = $true;  Hint = '请先运行 install-toolchain.ps1' },
    [pscustomobject]@{ Name = 'py -3.12'; Exe = 'py';  Args = @('-3.12', '--version'); Required = $false; Hint = 'uv 通常会用自带/已装的 Python，缺失不一定会失败' }
)

$toolOk = $true
foreach ($t in $toolChecks) {
    $probe = Get-ToolProbe -Exe $t.Exe -Arguments $t.Args

    if (-not $probe.Found) {
        if ($t.Required) {
            Write-Fail "$($t.Name) 不可用 —— 未在 PATH 中找到"
            $toolOk = $false
        } else {
            Write-Warn "$($t.Name) 不可用 —— 未在 PATH 中找到"
        }
        Write-Info $t.Hint
        continue
    }

    if ($probe.ExitCode -ne 0) {
        # 找到了但执行失败：多半是 Microsoft Store 的 python 存根之类
        if ($t.Required) {
            Write-Fail "$($t.Name) 执行失败（退出码 $($probe.ExitCode)）：$($probe.Path)"
            $toolOk = $false
        } else {
            Write-Warn "$($t.Name) 执行失败（退出码 $($probe.ExitCode)）：$($probe.Path)"
        }
        Write-Info $t.Hint
        continue
    }

    Write-Ok "$($t.Name.PadRight(8)) $($probe.Version)"
    Write-Info "路径: $($probe.Path)"
}

if (-not $toolOk) {
    Write-Info '工具链缺失会让后续步骤必然失败，请先跑 install-toolchain.ps1。'
    exit 1
}

# 裸执行 node 必须命中工具链，而不是系统那个 Node 16。
# 若这里解析到别处，说明 Add-ToolchainToPath 没生效或工具链目录不完整——
# 提前暴露，而不是等 pnpm build 用错 Node 版本报一堆莫名其妙的错。
#
# 这里逐字比对路径而不是用 Common 模块的 Test-DeerFlowPathInRoot：后者是模块
# 内部辅助函数，刻意没有导出（见模块的 Export-ModuleMember 清单），脚本里调不到。
$nodeProbe = Get-ToolProbe -Exe 'node' -Arguments @('--version')
$toolsNodeExe = Join-Path (Join-Path (Get-DeerFlowToolsDir) 'node') 'node.exe'
if ($nodeProbe.Path) {
    if (-not ($nodeProbe.Path.TrimEnd('\', '/') -ieq $toolsNodeExe.TrimEnd('\', '/'))) {
        Write-Warn "node 解析到 $($nodeProbe.Path)"
        Write-Info "期望命中 $toolsNodeExe —— 系统 Node 16 可能遮蔽了工具链"
    }
}

# ── 前置检查 3/4：磁盘空间 ──────────────────────────────────────────────────

Write-Step '前置检查 3/4：磁盘空间'

# 只看部署根所在盘：依赖与构建产物都落在那里。
$qualifier = Split-Path -Qualifier $rootDir
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$qualifier'" -ErrorAction SilentlyContinue

if (-not $drive) {
    Write-Warn "无法读取 $qualifier 的空间信息，跳过检查"
} else {
    $freeGb = [math]::Round($drive.FreeSpace / 1GB, 1)
    $sizeGb = [math]::Round($drive.Size / 1GB, 1)
    if ($freeGb -lt $script:MinFreeSpaceGb) {
        # 只警告不阻断：依赖缓存可能已在别处、也可能只是空间紧张但仍能装完。
        Write-Warn "$qualifier 可用空间偏低：$freeGb GB / $sizeGb GB（建议 >= $($script:MinFreeSpaceGb) GB）"
        Write-Info 'node_modules 约 2 GB、.next 约 1 GB；uv / pnpm 缓存默认在 <RootDir>\cache。'
    } else {
        Write-Ok "$qualifier 可用 $freeGb GB / $sizeGb GB"
    }
}

# ── 前置检查 4/4：环境变量 ──────────────────────────────────────────────────

Write-Step '前置检查 4/4：构建环境变量'

# 这两个变量任何一个被设上，都会让前端构建「过了但功能不对」：
#   NEXT_PUBLIC_STATIC_WEBSITE_ONLY=true  -> 纯静态站模式，禁用后端相关功能
#   NEXT_CONFIG_BUILD_OUTPUT=standalone   -> 产出 standalone 包（本方案用 next start）
# 用户级/机器级历史上被设过时，本脚本只提示不擅自改注册表（可能别处在用）；
# 进程级的会在构建前清掉，保证本次构建一定不受污染。
foreach ($name in @('NEXT_PUBLIC_STATIC_WEBSITE_ONLY', 'NEXT_CONFIG_BUILD_OUTPUT')) {
    $userValue = [Environment]::GetEnvironmentVariable($name, 'User')
    $machineValue = [Environment]::GetEnvironmentVariable($name, 'Machine')

    if (-not [string]::IsNullOrWhiteSpace($userValue) -or -not [string]::IsNullOrWhiteSpace($machineValue)) {
        Write-Warn "$name 已在用户/机器级被设置（User='$userValue' Machine='$machineValue'）"
        Write-Info '本次构建会清除其进程级取值，但其它程序仍会读到该变量。'
    }
}

if (-not [string]::IsNullOrWhiteSpace($env:NEXT_PUBLIC_STATIC_WEBSITE_ONLY)) {
    Write-Warn "进程环境里存在 NEXT_PUBLIC_STATIC_WEBSITE_ONLY=$($env:NEXT_PUBLIC_STATIC_WEBSITE_ONLY)，构建前会清除"
}
Write-Ok '构建将只设 SKIP_ENV_VALIDATION=1'

# ── 第 1 步：后端依赖 ───────────────────────────────────────────────────────

function Install-Backend {
    <#
    .SYNOPSIS
        第 1 步：在 src\backend 下执行 uv sync --all-packages --frozen。

    .DESCRIPTION
        幂等判据是 .venv\Scripts\python.exe 存在 —— 刻意不看 .venv 目录本身：
        一个空目录或上次装到一半留下的残缺 .venv 同样「存在」，但里面没有可用的
        解释器。用严格判据后，残缺状态会自动走重装，而不是被误判成「已安装」而跳过。

        --frozen 失败时去掉重试一次：--frozen 要求 lock 与 pyproject 完全一致，
        代码更新过 lock 时它会直接失败；去掉后 uv 会按 pyproject 重新解析
        （可能顺带更新 uv.lock）。
    #>
    param([Parameter(Mandatory = $true)][string]$LogFile)

    $venvDir = Join-Path $backendDir '.venv'
    $pythonExe = Join-Path $venvDir 'Scripts\python.exe'
    $uvExe = Join-Path (Get-DeerFlowToolsDir) 'uv\uv.exe'
    if (-not (Test-Path $uvExe)) { $uvExe = 'uv' }   # 退回 PATH 查找

    $installed = Test-Path $pythonExe

    if ($installed -and -not $Force) {
        Write-Skip ".venv 已存在且可用（$pythonExe），跳过后端依赖安装"
        Write-Info '如需强制重装：-Force（会删除 .venv 后重新下载全部依赖，耗时较长）'
        return [pscustomobject]@{ Step = '后端依赖'; Status = '跳过'; Seconds = 0 }
    }

    Start-StepLog -LogFile $LogFile -Title 'DeerFlow 后端依赖安装 (uv sync)'
    Write-StepLog -Message '第 1 步：后端依赖 (uv sync)' -LogFile $LogFile

    if (Test-Path $venvDir) {
        if ($Force) {
            Write-Warn "-Force：删除现有 .venv（$venvDir）"
        } else {
            Write-Warn ".venv 不完整（缺少 $pythonExe），删除后重装"
        }
        Write-Log -LogFile $LogFile -Message "删除 $venvDir"
        Remove-Item -Path $venvDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $venvDir) {
            Write-Fail "无法删除 $venvDir —— 可能有进程正在使用它（如已启动的 gateway）"
            Write-Info '请先停止后端服务再重试。'
            Write-LogTail -LogFile $LogFile -Tag '后端依赖' -Lines $script:TailLines
            return [pscustomobject]@{ Step = '后端依赖'; Status = '失败'; Seconds = 0 }
        }
    }

    # Python 的输出编码统一成 UTF-8：cmd 的重定向按字节写文件，若 Python 用
    # 系统默认的 GBK 输出，日志里的中文会与 UTF-8 混杂，读取时出现乱码。
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $attempts = @(
        [pscustomobject]@{ Args = @('sync', '--all-packages', '--frozen'); Desc = 'uv sync --all-packages --frozen' },
        [pscustomobject]@{ Args = @('sync', '--all-packages');                 Desc = 'uv sync --all-packages（去掉 --frozen 重试）' }
    )

    $result = $null
    for ($i = 0; $i -lt $attempts.Count; $i++) {
        $attempt = $attempts[$i]

        Write-Log -LogFile $LogFile -Message ("工作目录: " + $backendDir)
        Write-Log -LogFile $LogFile -Message ("执行命令: " + $attempt.Desc)
        Write-Log -LogFile $LogFile -Message ("说明: 镜像源来自 backend\pyproject.toml 的 [tool.uv] index-url（清华 tuna）；本部署用 SQLite，故不加 --extra postgres")

        $result = Invoke-LoggedCommand -Exe $uvExe -Arguments $attempt.Args `
                    -WorkingDir $backendDir -LogFile $LogFile `
                    -TimeoutMinutes $script:StepTimeoutMinutes

        if ($result.TimedOut) {
            Write-Fail "后端依赖安装超时（超过 $($script:StepTimeoutMinutes) 分钟），已终止进程树"
            Write-Log -LogFile $LogFile -Message "超时: 超过 $($script:StepTimeoutMinutes) 分钟，已 taskkill /F /T"
            break
        }

        if ($result.ExitCode -eq 0) {
            Write-Log -LogFile $LogFile -Message "退出码 0（成功）"
            break
        }

        Write-Log -LogFile $LogFile -Message "退出码 $($result.ExitCode)"

        if ($i -lt ($attempts.Count - 1)) {
            Write-Warn "uv sync --frozen 失败（退出码 $($result.ExitCode)），去掉 --frozen 重试一次"
            Write-Log -LogFile $LogFile -Message '---- --frozen 失败，回退重试 ----'
        }
    }

    $sw.Stop()
    $seconds = $sw.Elapsed.TotalSeconds

    # ── 回读校验：不看退出码，直接确认可用的解释器真的存在 ──
    if ($null -eq $result -or $result.TimedOut -or $result.ExitCode -ne 0) {
        Write-Fail "后端依赖安装失败"
        Write-LogTail -LogFile $LogFile -Tag '后端依赖' -Lines $script:TailLines
        Write-Info '常见原因：网络不可达镜像源 / lock 与 pyproject 不一致 / 磁盘空间不足。'
        return [pscustomobject]@{ Step = '后端依赖'; Status = '失败'; Seconds = $seconds }
    }

    if (-not (Test-Path $pythonExe)) {
        Write-Fail "uv sync 报成功，但未找到 $pythonExe —— 虚拟环境可能未正确创建"
        Write-Log -LogFile $LogFile -Message "校验失败: 未找到 $pythonExe"
        Write-LogTail -LogFile $LogFile -Tag '后端依赖' -Lines $script:TailLines
        return [pscustomobject]@{ Step = '后端依赖'; Status = '失败'; Seconds = $seconds }
    }

    $pkgDir = Join-Path $venvDir 'Lib\site-packages'
    $pkgCount = 0
    if (Test-Path $pkgDir) {
        $pkgCount = @(Get-ChildItem -Path $pkgDir -Directory -ErrorAction SilentlyContinue).Count
    }

    Write-Log -LogFile $LogFile -Message "完成，耗时 $(Format-Duration -Seconds $seconds)"
    Write-Log -LogFile $LogFile -Message "校验通过: $pythonExe（site-packages 目录数 $pkgCount）"

    Write-Ok "后端依赖安装完成（$pythonExe，site-packages 目录数 $pkgCount）"
    return [pscustomobject]@{ Step = '后端依赖'; Status = '成功'; Seconds = $seconds }
}

# ── 第 2 步：前端依赖 ───────────────────────────────────────────────────────

function Install-FrontendDeps {
    <#
    .SYNOPSIS
        第 2 步：在 src\frontend 下执行 pnpm install --frozen-lockfile。

    .DESCRIPTION
        幂等判据取 node_modules\.modules.yaml（pnpm 每次成功安装都会写）或
        node_modules\.pnpm —— 只看 node_modules 目录会漏判「装到一半」的情况。

        --frozen-lockfile 失败时去掉重试一次：它要求 lock 与 package.json 一致，
        依赖清单更新过而 lock 没跟上时会直接失败；去掉后 pnpm 会按 package.json
        重新解析并更新 pnpm-lock.yaml。
    #>
    param([Parameter(Mandatory = $true)][string]$LogFile)

    $nodeModulesDir = Join-Path $frontendDir 'node_modules'
    $pnpmMarker = Join-Path $nodeModulesDir '.modules.yaml'
    $pnpmStore = Join-Path $nodeModulesDir '.pnpm'
    $pnpmExe = Join-Path (Get-DeerFlowToolsDir) 'node\pnpm.cmd'
    if (-not (Test-Path $pnpmExe)) { $pnpmExe = 'pnpm' }   # 退回 PATH 查找

    $installed = (Test-Path $nodeModulesDir) -and ((Test-Path $pnpmMarker) -or (Test-Path $pnpmStore))

    if ($installed -and -not $Force) {
        Write-Skip "node_modules 已存在且完整，跳过前端依赖安装（$nodeModulesDir）"
        Write-Info '如需强制重装：-Force（会删除 node_modules 后重新下载，约 2 GB）'
        return [pscustomobject]@{ Step = '前端依赖'; Status = '跳过'; Seconds = 0 }
    }

    Start-StepLog -LogFile $LogFile -Title 'DeerFlow 前端依赖安装 (pnpm install)'
    Write-StepLog -Message '第 2 步：前端依赖 (pnpm install)' -LogFile $LogFile

    if (Test-Path $nodeModulesDir) {
        if ($Force) {
            Write-Warn "-Force：删除现有 node_modules（$nodeModulesDir）"
        } else {
            Write-Warn "node_modules 不完整（缺少 pnpm 标记文件），删除后重装"
        }
        Write-Log -LogFile $LogFile -Message "删除 $nodeModulesDir"
        Remove-Item -Path $nodeModulesDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $nodeModulesDir) {
            Write-Fail "无法删除 $nodeModulesDir —— 可能有进程正在使用它（如已启动的前端）"
            Write-Info '请先停止前端服务再重试。'
            Write-LogTail -LogFile $LogFile -Tag '前端依赖' -Lines $script:TailLines
            return [pscustomobject]@{ Step = '前端依赖'; Status = '失败'; Seconds = 0 }
        }
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $attempts = @(
        [pscustomobject]@{ Args = @('install', '--frozen-lockfile'); Desc = 'pnpm install --frozen-lockfile' },
        [pscustomobject]@{ Args = @('install');                     Desc = 'pnpm install（去掉 --frozen-lockfile 重试）' }
    )

    $result = $null
    for ($i = 0; $i -lt $attempts.Count; $i++) {
        $attempt = $attempts[$i]

        Write-Log -LogFile $LogFile -Message ("工作目录: " + $frontendDir)
        Write-Log -LogFile $LogFile -Message ("执行命令: " + $attempt.Desc)
        Write-Log -LogFile $LogFile -Message ("说明: npm 源继承用户级 NPM_CONFIG_REGISTRY=$($env:NPM_CONFIG_REGISTRY)")

        $result = Invoke-LoggedCommand -Exe $pnpmExe -Arguments $attempt.Args `
                    -WorkingDir $frontendDir -LogFile $LogFile `
                    -TimeoutMinutes $script:StepTimeoutMinutes

        if ($result.TimedOut) {
            Write-Fail "前端依赖安装超时（超过 $($script:StepTimeoutMinutes) 分钟），已终止进程树"
            Write-Log -LogFile $LogFile -Message "超时: 超过 $($script:StepTimeoutMinutes) 分钟，已 taskkill /F /T"
            break
        }

        if ($result.ExitCode -eq 0) {
            Write-Log -LogFile $LogFile -Message '退出码 0（成功）'
            break
        }

        Write-Log -LogFile $LogFile -Message "退出码 $($result.ExitCode)"

        if ($i -lt ($attempts.Count - 1)) {
            Write-Warn "pnpm install --frozen-lockfile 失败（退出码 $($result.ExitCode)），去掉该参数重试一次"
            Write-Log -LogFile $LogFile -Message '---- --frozen-lockfile 失败，回退重试 ----'
        }
    }

    $sw.Stop()
    $seconds = $sw.Elapsed.TotalSeconds

    if ($null -eq $result -or $result.TimedOut -or $result.ExitCode -ne 0) {
        Write-Fail '前端依赖安装失败'
        Write-LogTail -LogFile $LogFile -Tag '前端依赖' -Lines $script:TailLines
        Write-Info '常见原因：registry 不可达 / lock 与 package.json 不一致 / 磁盘空间不足。'
        return [pscustomobject]@{ Step = '前端依赖'; Status = '失败'; Seconds = $seconds }
    }

    if (-not (Test-Path $pnpmMarker) -and -not (Test-Path $pnpmStore)) {
        Write-Fail "pnpm install 报成功，但未找到 pnpm 标记文件 —— node_modules 可能不完整"
        Write-Log -LogFile $LogFile -Message "校验失败: 未找到 $pnpmMarker 或 $pnpmStore"
        Write-LogTail -LogFile $LogFile -Tag '前端依赖' -Lines $script:TailLines
        return [pscustomobject]@{ Step = '前端依赖'; Status = '失败'; Seconds = $seconds }
    }

    Write-Log -LogFile $LogFile -Message "完成，耗时 $(Format-Duration -Seconds $seconds)"
    Write-Ok "前端依赖安装完成（耗时 $(Format-Duration -Seconds $seconds)）"
    return [pscustomobject]@{ Step = '前端依赖'; Status = '成功'; Seconds = $seconds }
}

# ── 第 3 步：前端构建 ───────────────────────────────────────────────────────

function Build-Frontend {
    <#
    .SYNOPSIS
        第 3 步：在 src\frontend 下执行 pnpm build，环境变量 SKIP_ENV_VALIDATION=1。

    .DESCRIPTION
        幂等判据取 .next\BUILD_ID —— 它是 Next.js 每次成功构建都会写、且失败时
        不会留下的标记文件。

        构建前显式清除 NEXT_PUBLIC_STATIC_WEBSITE_ONLY / NEXT_CONFIG_BUILD_OUTPUT
        的进程级取值（见脚本头部注释），确保产物一定是「带后端功能的标准构建」。
    #>
    param([Parameter(Mandatory = $true)][string]$LogFile)

    $nextDir = Join-Path $frontendDir '.next'
    $buildIdFile = Join-Path $nextDir 'BUILD_ID'
    $pnpmExe = Join-Path (Get-DeerFlowToolsDir) 'node\pnpm.cmd'
    if (-not (Test-Path $pnpmExe)) { $pnpmExe = 'pnpm' }

    $installed = Test-Path $buildIdFile

    if ($installed -and -not $Force) {
        $buildId = ([System.IO.File]::ReadAllText($buildIdFile, [System.Text.Encoding]::UTF8)).Trim()
        Write-Skip ".next\BUILD_ID 已存在（$buildId），跳过前端构建"
        Write-Info '如需强制重建：-Force（会删除 .next 后重新构建）'
        return [pscustomobject]@{ Step = '前端构建'; Status = '跳过'; Seconds = 0 }
    }

    Start-StepLog -LogFile $LogFile -Title 'DeerFlow 前端构建 (pnpm build)'
    Write-StepLog -Message '第 3 步：前端构建 (pnpm build)' -LogFile $LogFile

    # 先确认 node_modules 在，否则 pnpm build 会以一堆「找不到模块」告终，
    # 不如在这里直接给一句人话。
    if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
        Write-Fail '缺少 node_modules —— 请先执行第 2 步（去掉 -SkipFrontend）'
        Write-Log -LogFile $LogFile -Message '缺少 node_modules，无法构建'
        return [pscustomobject]@{ Step = '前端构建'; Status = '失败'; Seconds = 0 }
    }

    if (Test-Path $nextDir) {
        if ($Force) {
            Write-Warn "-Force：删除现有 .next（$nextDir）"
        } else {
            Write-Warn ".next 不完整（缺少 BUILD_ID），删除后重建"
        }
        Write-Log -LogFile $LogFile -Message "删除 $nextDir"
        Remove-Item -Path $nextDir -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $nextDir) {
            Write-Fail "无法删除 $nextDir —— 可能有进程正在使用它（如已启动的前端）"
            Write-Info '请先停止前端服务再重试。'
            Write-LogTail -LogFile $LogFile -Tag '前端构建' -Lines $script:TailLines
            return [pscustomobject]@{ Step = '前端构建'; Status = '失败'; Seconds = 0 }
        }
    }

    # ── 构建环境变量 ──────────────────────────────────────────────────────
    # 只设 SKIP_ENV_VALIDATION=1（与 frontend\Makefile 的 build 目标一致）：
    # frontend\src\env.js 会校验环境变量，构建机上缺项时直接 fail。
    $env:SKIP_ENV_VALIDATION = '1'

    # 顺序相反：这两个变量一旦存在，构建能过但功能不对，必须确保不存在。
    # Remove-Item Env: 只删进程级取值；子进程（cmd -> pnpm -> node）继承的是
    # 本进程的环境块，因此删掉之后构建进程一定读不到。
    foreach ($name in @('NEXT_PUBLIC_STATIC_WEBSITE_ONLY', 'NEXT_CONFIG_BUILD_OUTPUT')) {
        if (Test-Path "Env:$name") {
            Remove-Item -Path "Env:$name" -Force -ErrorAction SilentlyContinue
            Write-Info "已清除环境变量 $name"
        }
    }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $result = Invoke-LoggedCommand -Exe $pnpmExe -Arguments @('build') `
                -WorkingDir $frontendDir -LogFile $LogFile `
                -TimeoutMinutes $script:StepTimeoutMinutes
    $sw.Stop()
    $seconds = $sw.Elapsed.TotalSeconds

    Write-Log -LogFile $LogFile -Message ("工作目录: " + $frontendDir)
    Write-Log -LogFile $LogFile -Message '执行命令: pnpm build'
    Write-Log -LogFile $LogFile -Message '环境变量: SKIP_ENV_VALIDATION=1；未设 NEXT_PUBLIC_STATIC_WEBSITE_ONLY / NEXT_CONFIG_BUILD_OUTPUT'

    if ($result.TimedOut) {
        Write-Fail "前端构建超时（超过 $($script:StepTimeoutMinutes) 分钟），已终止进程树"
        Write-Log -LogFile $LogFile -Message "超时: 超过 $($script:StepTimeoutMinutes) 分钟，已 taskkill /F /T"
        Write-LogTail -LogFile $LogFile -Tag '前端构建' -Lines $script:TailLines
        return [pscustomobject]@{ Step = '前端构建'; Status = '失败'; Seconds = $seconds }
    }

    Write-Log -LogFile $LogFile -Message "退出码 $($result.ExitCode)"

    if ($result.ExitCode -ne 0) {
        Write-Fail "前端构建失败（退出码 $($result.ExitCode)）"
        Write-LogTail -LogFile $LogFile -Tag '前端构建' -Lines $script:TailLines
        Write-Info '常见原因：Node 版本不符 / node_modules 不完整 / 缺少环境变量。'
        return [pscustomobject]@{ Step = '前端构建'; Status = '失败'; Seconds = $seconds }
    }

    if (-not (Test-Path $buildIdFile)) {
        Write-Fail "pnpm build 报成功，但未找到 $buildIdFile —— 构建产物不完整"
        Write-Log -LogFile $LogFile -Message "校验失败: 未找到 $buildIdFile"
        Write-LogTail -LogFile $LogFile -Tag '前端构建' -Lines $script:TailLines
        return [pscustomobject]@{ Step = '前端构建'; Status = '失败'; Seconds = $seconds }
    }

    $buildId = ([System.IO.File]::ReadAllText($buildIdFile, [System.Text.Encoding]::UTF8)).Trim()
    Write-Log -LogFile $LogFile -Message "完成，耗时 $(Format-Duration -Seconds $seconds)"
    Write-Log -LogFile $LogFile -Message "校验通过: BUILD_ID = $buildId"

    Write-Ok "前端构建完成（BUILD_ID = $buildId，耗时 $(Format-Duration -Seconds $seconds)）"
    return [pscustomobject]@{ Step = '前端构建'; Status = '成功'; Seconds = $seconds }
}

# ── 执行三步 ────────────────────────────────────────────────────────────────

$failures = 0

if ($SkipBackend) {
    Write-Step '第 1 步：后端依赖（已跳过）'
    Write-Skip '-SkipBackend：未执行后端依赖安装'
} else {
    $r = Install-Backend -LogFile $logBackend
    $script:Results.Add($r)
    if ($r.Status -eq '失败') { $failures++ }
}

if ($failures -eq 0) {
    if ($SkipFrontend) {
        Write-Step '第 2 步：前端依赖（已跳过）'
        Write-Skip '-SkipFrontend：未执行前端依赖安装'
    } else {
        $r = Install-FrontendDeps -LogFile $logFrontend
        $script:Results.Add($r)
        if ($r.Status -eq '失败') { $failures++ }
    }
}

if ($failures -eq 0) {
    if ($SkipBuild) {
        Write-Step '第 3 步：前端构建（已跳过）'
        Write-Skip '-SkipBuild：未执行前端构建'
    } else {
        $r = Build-Frontend -LogFile $logBuild
        $script:Results.Add($r)
        if ($r.Status -eq '失败') { $failures++ }
    }
}

# ── 汇总 ────────────────────────────────────────────────────────────────────

Write-Step '耗时汇总'

if ($script:Results.Count -eq 0) {
    Write-Info '本次没有执行任何步骤（全部被 -SkipXxx 跳过）'
} else {
    $total = 0.0
    foreach ($r in $script:Results) {
        $total += $r.Seconds

        if ($r.Status -eq '成功') {
            $mark = '[ OK ]'; $color = 'Green'
        } elseif ($r.Status -eq '跳过') {
            $mark = '[SKIP]'; $color = 'DarkCyan'
        } else {
            $mark = '[FAIL]'; $color = 'Red'
        }

        Write-Host ("  {0} {1}  ——  {2}  ({3})" -f $mark, $r.Step, $r.Status, (Format-Duration -Seconds $r.Seconds)) -ForegroundColor $color
    }
    Write-Host ("  合计耗时（已执行步骤）: {0}" -f (Format-Duration -Seconds $total)) -ForegroundColor Cyan
}

# ── 产物校验 ────────────────────────────────────────────────────────────────
#
# 不看中间过程，直接确认三个交付物在磁盘上真的可用。
# 被 -SkipXxx 跳过的步骤同样在这里校验：如果产物缺失，说明「跳过」这个决定
# 是错的（比如 -SkipBackend 但 .venv 根本没有），必须让调用方知道。

Write-Step '产物校验'

$artifactChecks = @(
    [pscustomobject]@{
        Desc = '后端虚拟环境'
        Path = (Join-Path $backendDir '.venv\Scripts\python.exe')
    },
    [pscustomobject]@{
        Desc = '前端依赖'
        Path = (Join-Path $frontendDir 'node_modules\.modules.yaml')
    },
    [pscustomobject]@{
        Desc = '前端构建产物'
        Path = (Join-Path $frontendDir '.next\BUILD_ID')
    }
)

$missing = @()
foreach ($c in $artifactChecks) {
    if (Test-Path $c.Path) {
        Write-Ok "$($c.Desc)：$($c.Path)"
    } else {
        Write-Warn "$($c.Desc) 缺失：$($c.Path)"
        $missing += $c.Desc
    }
}

if ($missing.Count -gt 0) {
    Write-Info "缺失项: $($missing -join '、')"
    Write-Info '若对应步骤被 -SkipXxx 跳过，请去掉该开关重新运行。'
}

# ── 结束 ────────────────────────────────────────────────────────────────────

Write-Host ""

if ($failures -gt 0) {
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host "  依赖安装失败（$failures 个步骤）" -ForegroundColor Red
    Write-Host "==========================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  日志：" -ForegroundColor Yellow
    Write-Host "    后端依赖 : $logBackend"
    Write-Host "    前端依赖 : $logFrontend"
    Write-Host "    前端构建 : $logBuild"
    Write-Host ""
    exit 1
}

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  依赖安装与前端构建完成" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  后端 venv      : $(Join-Path $backendDir '.venv')"
Write-Host "  前端依赖       : $(Join-Path $frontendDir 'node_modules')"
Write-Host "  前端构建产物   : $(Join-Path $frontendDir '.next')"
Write-Host ""
Write-Host "  日志：" -ForegroundColor DarkGray
Write-Host "    后端依赖 : $logBackend" -ForegroundColor DarkGray
Write-Host "    前端依赖 : $logFrontend" -ForegroundColor DarkGray
Write-Host "    前端构建 : $logBuild" -ForegroundColor DarkGray
Write-Host ""

if ($missing.Count -gt 0) {
    Write-Host "  注意：仍有产物缺失（$($missing -join '、')），部署无法继续。" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Write-Host "  下一步：跑 scripts\windows\start.ps1 启动服务，" -ForegroundColor Yellow
Write-Host "          或直接跑 scripts\windows\deploy.ps1 走完整个部署流程。" -ForegroundColor Yellow
Write-Host ""

exit 0
