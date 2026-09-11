<#
.SYNOPSIS
    DeerFlow Windows 部署脚本集的公共模块。

.DESCRIPTION
    供 scripts\windows\ 下的各部署脚本（init-config / install / start / stop /
    status / admin-init / register-autostart / deploy）通过 Import-Module 复用。

    统一约定目标机的目录布局（RootDir 默认 D:\deer-flow）：

        D:\deer-flow\
        ├── tools\      Node / uv（由 install-toolchain.ps1 创建）
        ├── src\        代码仓库
        ├── data\       SQLite 数据（DEER_FLOW_HOME 指向此处）
        ├── cache\      各类缓存
        └── logs\       日志
            └── run\    PID 文件

    ⚠ 本文件必须以「UTF-8 with BOM + CRLF」保存。
      Windows PowerShell 5.1 对无 BOM 的 .ps1/.psm1 会按 ANSI（简体中文系统上即
      GBK）解码，文件里的中文注释会全部变成乱码；更糟的是若中文落在字符串字面量
      里，模块的行为也会跟着错。同时 5.1 只认 BOM 判断 UTF-8，没有其它可靠信号。

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    代码刻意避开 PowerShell 7 语法（??、三元运算符 ?:、-Parallel 等），
    保证在目标机的 5.1 上可直接运行。
#>

# ── 模块级常量 ──────────────────────────────────────────────────────────────
#
# 只用 $script: 前缀放在模块会话状态里，不写成全局变量——
# 模块会被多个脚本反复 Import，污染调用方会话会很难排查。

# 默认部署根目录；可用环境变量 DEER_FLOW_DEPLOY_ROOT 覆盖（见 Get-DeerFlowRoot）。
$script:DefaultDeployRoot = 'D:\deer-flow'

# 工具链子目录名（相对 RootDir\tools）
$script:NodeDirName = 'node'
$script:UvDirName   = 'uv'

<#
  各进程的命令行特征与候选进程名，供 Get-DeerFlowProcess 使用。

  Patterns —— 命令行必须命中的正则（用 -match，忽略大小写）。
              这是「本项目进程」的第一判据。
  Names    —— 先按进程名初筛，避免对上万个无关进程跑正则；
              同时在 PID 文件校验里作为「该 PID 确实属于本项目」的旁证。

  注意 gateway 的判定：uvicorn 的命令行里必然出现 app.gateway.app:app，
  这条特征足够唯一；再加 \buvicorn\b 作为容错。
#>
$script:ProcessSpecs = @{
    'gateway' = @{
        Patterns = @('app\.gateway\.app:app', '\buvicorn\b')
        Names    = @('python.exe', 'pythonw.exe', 'uv.exe')
    }
    'frontend' = @{
        # next dev 实际进程形如：
        #   "D:\deer-flow\tools\node\node.exe" ...\next\dist\bin\next dev --turbo
        # 经 pnpm 拉起时还会有一个 cmd.exe /d /s /c "pnpm dev" 的外壳。
        Patterns = @('(^|["\s\\/])next(\.cmd|\.exe)?(["\s\\/]|$)', '(^|["\s\\/])pnpm(\.cmd|\.exe)?(["\s\\/]|$)')
        Names    = @('node.exe', 'cmd.exe', 'pnpm.exe', 'pnpm.cmd')
    }
}

# ── 输出辅助 ────────────────────────────────────────────────────────────────
#
# 与 install-sshd.ps1 / install-toolchain.ps1 保持完全一致的输出风格与配色：
# 部署脚本被人工盯屏时，一眼就能分辨「正常 / 需要注意 / 已失败」。

function Write-Step {
    <#
    .SYNOPSIS
        输出一个阶段的标题（空行 + "==> ..."），青色。
    .PARAMETER Message
        阶段名称。
    #>
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    <#
    .SYNOPSIS
        输出成功项（"  [ OK ] ..."），绿色。
    #>
    param([string]$Message)
    Write-Host "  [ OK ] $Message" -ForegroundColor Green
}

function Write-Warn {
    <#
    .SYNOPSIS
        输出警告项（"  [WARN] ..."），黄色。表示可继续但需留意。
    #>
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    <#
    .SYNOPSIS
        输出失败项（"  [FAIL] ..."），红色。调用方通常随后 exit 1。
    #>
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Write-Info {
    <#
    .SYNOPSIS
        输出补充说明（缩进对齐 + 深灰），用于给出诊断建议而不是结论。
    #>
    param([string]$Message)
    Write-Host "         $Message" -ForegroundColor DarkGray
}

function Write-Log {
    <#
    .SYNOPSIS
        把一行带时间戳的消息同时写到控制台与日志文件（追加）。

    .DESCRIPTION
        日志文件以 UTF-8 无 BOM 追加写入——有 BOM 时每次追加都会在文件中段插入
        EF BB BF，后续按行读取会看到乱码前缀。

    .PARAMETER Message
        日志正文。写入文件时会自动加 "[yyyy-MM-dd HH:mm:ss.fff]" 前缀，
        控制台只输出正文（避免刷屏时噪声过大）。

    .PARAMETER LogFile
        日志文件绝对路径。父目录不存在时自动创建。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message,

        [Parameter(Mandatory = $true)]
        [string]$LogFile
    )

    Write-Host $Message

    try {
        $dir = Split-Path -Parent $LogFile
        if ($dir -and -not (Test-Path $dir)) {
            New-Item -Path $dir -ItemType Directory -Force | Out-Null
        }
        $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'
        $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
        [System.IO.File]::AppendAllText($LogFile, "[$stamp] $Message`r`n", $utf8NoBom)
    } catch {
        # 日志写不进去不应该中断部署本身（例如文件被占用），退回控制台提示即可
        Write-Warn "写入日志失败: $($_.Exception.Message)"
    }
}

# ── 编码工具 ────────────────────────────────────────────────────────────────

function Write-TextFileNoBom {
    <#
    .SYNOPSIS
        以 UTF-8 无 BOM 写入文本文件（父目录不存在时自动创建）。

    .DESCRIPTION
        ⚠ 绝对不要改成 Set-Content / Out-File -Encoding UTF8。

        Windows PowerShell 5.1 的 -Encoding UTF8 一律带 BOM（UTF8Encoding($true)），
        而 BOM 会破坏下游解析：
          · Python 源文件首行变成 ﻿import ... → SyntaxError: invalid character
          · .env / 其它键值配置读到的第一个键名带不可见前缀，匹配不上
          · JSON 解析器同样会因首字节非 '{' 而报错
        已实测踩坑：deploy 早期版本用 Set-Content -Encoding UTF8 生成 Python 脚本，
        目标机上直接 import 失败。

        PS 7 才提供 -Encoding utf8NoBOM，因此这里统一走 .NET API，
        保证 5.1 与 7 行为一致。

    .PARAMETER Path
        目标文件绝对路径。

    .PARAMETER Content
        完整文件内容（原样写入，不做换行符归一化）。
    #>
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $false)]
        [string]$Content = ''
    )

    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ── 路径解析 ────────────────────────────────────────────────────────────────

function Get-DeerFlowRoot {
    <#
    .SYNOPSIS
        返回部署根目录（默认 D:\deer-flow）。

    .DESCRIPTION
        覆盖顺序：DEER_FLOW_DEPLOY_ROOT 环境变量（进程 → 用户 → 机器）
        → 默认值 D:\deer-flow。

        每次调用都重新读取环境变量，而不是在 Import-Module 时算一次——
        否则测试或脚本在 Import 之后再改环境变量就不生效了。

        返回值去掉结尾的分隔符；盘符根（如 "D:\"）除外，否则会退化成 "D:"，
        被 Join-Path 拼成当前目录下的相对路径。
    #>
    [CmdletBinding()]
    param()

    $value = $env:DEER_FLOW_DEPLOY_ROOT

    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable('DEER_FLOW_DEPLOY_ROOT', 'User')
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = [Environment]::GetEnvironmentVariable('DEER_FLOW_DEPLOY_ROOT', 'Machine')
    }
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = $script:DefaultDeployRoot
    }

    $value = $value.Trim().TrimEnd('\', '/')

    # 纯盘符要补回反斜杠，否则 Join-Path 'D:' 'src' 会拼出 'D:src'
    if ($value -match '^[A-Za-z]:$') {
        $value = $value + '\'
    }

    return $value
}

function Get-DeerFlowSrcDir {
    <#
    .SYNOPSIS
        返回代码仓库目录 <RootDir>\src。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowRoot) 'src')
}

function Get-DeerFlowDataDir {
    <#
    .SYNOPSIS
        返回数据目录 <RootDir>\data（DEER_FLOW_HOME 指向此处，存放 SQLite 数据）。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowRoot) 'data')
}

function Get-DeerFlowLogsDir {
    <#
    .SYNOPSIS
        返回日志目录 <RootDir>\logs。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowRoot) 'logs')
}

function Get-DeerFlowRunDir {
    <#
    .SYNOPSIS
        返回运行时目录 <RootDir>\logs\run（存放各服务的 PID 文件）。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowLogsDir) 'run')
}

function Get-DeerFlowToolsDir {
    <#
    .SYNOPSIS
        返回工具链目录 <RootDir>\tools（内有 node\ 与 uv\）。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowRoot) 'tools')
}

function Get-DeerFlowCacheDir {
    <#
    .SYNOPSIS
        返回缓存目录 <RootDir>\cache（npm / uv / 临时文件）。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowRoot) 'cache')
}

function Get-DeerFlowConfigPath {
    <#
    .SYNOPSIS
        返回 config.yaml 的路径。

    .DESCRIPTION
        DEER_FLOW_CONFIG_PATH 环境变量优先（与 scripts\deploy.sh 的约定一致），
        否则取仓库根下的 config.yaml（<RootDir>\src\config.yaml）。
    #>
    [CmdletBinding()]
    param()

    if (-not [string]::IsNullOrWhiteSpace($env:DEER_FLOW_CONFIG_PATH)) {
        return $env:DEER_FLOW_CONFIG_PATH
    }
    return (Join-Path (Get-DeerFlowSrcDir) 'config.yaml')
}

function Get-DeerFlowEnvPath {
    <#
    .SYNOPSIS
        返回 .env 的路径（仓库根下，即 <RootDir>\src\.env）。
    #>
    [CmdletBinding()]
    param()
    return (Join-Path (Get-DeerFlowSrcDir) '.env')
}

# ── PATH 前置 ───────────────────────────────────────────────────────────────

function Add-ToolchainToPath {
    <#
    .SYNOPSIS
        把工具链目录（tools\node、tools\uv）前置到当前进程的 $env:Path。

    .DESCRIPTION
        ⚠ 这一步不能省，也不能改成「写用户级 PATH」。

        目标机的系统 PATH 里有 C:\Program Files\nodejs（Node 16），而 Windows 的
        有效 PATH = 系统 PATH + 用户 PATH（系统段在前）。用户级 PATH 无论怎么排，
        都无法覆盖系统级的同名列——把工具链写进用户 PATH 后，裸执行 node 仍然是 16。

        因此部署脚本必须在进程内显式前置：进程级 PATH 完全由我们控制，
        且只影响本次运行，不动系统配置（别的程序可能依赖那个 Node 16）。

        可重复调用：会先把已存在的同名条目摘掉再前置，不会不断堆叠。

    .NOTES
        只改当前进程，不写注册表。新开的会话仍会用到系统 Node 16。
    #>
    [CmdletBinding()]
    param()

    $toolsDir = Get-DeerFlowToolsDir
    $nodeDir  = Join-Path $toolsDir $script:NodeDirName
    $uvDir    = Join-Path $toolsDir $script:UvDirName

    if (-not (Test-Path (Join-Path $nodeDir 'node.exe'))) {
        Write-Warn "未找到 $nodeDir\node.exe —— 请先运行 install-toolchain.ps1"
    }

    $current = $env:Path
    if ([string]::IsNullOrEmpty($current)) { $current = '' }

    # 用 TrimEnd 比较，避免 "D:\deer-flow\tools\node" 与 "...\node\" 被当成两个条目
    $keep = @()
    foreach ($entry in ($current -split ';')) {
        if ([string]::IsNullOrWhiteSpace($entry)) { continue }
        $trimmed = $entry.Trim()
        $normalized = $trimmed.TrimEnd('\', '/')
        if ($normalized -ieq $nodeDir.TrimEnd('\', '/')) { continue }
        if ($normalized -ieq $uvDir.TrimEnd('\', '/')) { continue }
        $keep += $trimmed
    }

    $newPath = (@($nodeDir, $uvDir) + $keep) -join ';'

    if ($newPath -ne $current) {
        $env:Path = $newPath
        Write-Info "PATH 已前置工具链: $nodeDir;$uvDir"
    }
}

# ── 配置文件读写 ────────────────────────────────────────────────────────────

function Read-DotEnv {
    <#
    .SYNOPSIS
        把 .env 解析成 hashtable。

    .DESCRIPTION
        规则（贴合 dotenv 的常见写法，但不做完整实现）：
          · 跳过空行与 # 开头的注释行
          · 允许 "export KEY=VALUE" 形式，自动去掉 export 前缀
          · 按「第一个 =」切分，因此值里可以包含 =
          · 值两端空白被去掉；成对的单引号或双引号会被剥掉
          · 不解析行尾 # 注释（密码里出现 # 很常见，剥错代价比少剥大）

        已知不支持的写法：跨行的引号值、${VAR} 插值。部署用的 .env 不会用到。

        读取一律按 UTF-8：用 .NET 的 ReadAllText，它会自动识别 BOM，
        且对无 BOM 文件按 UTF-8 解码——Get-Content 在 5.1 下无 BOM 时会按
        ANSI(GBK) 解码，含中文的值会乱码。

    .PARAMETER Path
        .env 文件路径。

    .OUTPUTS
        hashtable。文件不存在时返回空表并给出警告（调用方通常先用
        Test-DeerFlowConfigured 判断，这里不抛异常以免打断部署）。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $result = @{}

    if (-not (Test-Path $Path)) {
        Write-Warn ".env 不存在: $Path"
        return $result
    }

    $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)

    foreach ($line in ($raw -split "`r?`n")) {
        $text = $line.Trim()
        if (-not $text) { continue }
        if ($text.StartsWith('#')) { continue }

        if ($text.StartsWith('export ')) {
            $text = $text.Substring(7).Trim()
        }

        $eq = $text.IndexOf('=')
        if ($eq -lt 1) { continue }   # 没有 = 或 = 在行首，不是合法条目

        $key = $text.Substring(0, $eq).Trim()
        if (-not $key) { continue }

        $value = $text.Substring($eq + 1).Trim()

        if ($value.Length -ge 2) {
            $first = $value[0]
            $last  = $value[$value.Length - 1]
            $quoted = (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'"))
            if ($quoted) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }

        $result[$key] = $value
    }

    return $result
}

function Write-DotEnv {
    <#
    .SYNOPSIS
        把 hashtable 写成 .env 文件（UTF-8 无 BOM）。

    .DESCRIPTION
        ⚠ 必须无 BOM：Python 的 dotenv 读到 "﻿KEY" 会当成另一个键名，
        表现为「配置明明写了却读不到」。

        键按字母序输出，保证同样的输入产出同样的文件（便于 diff 与幂等校验）。
        值含空白或 # 时自动加引号，与 Read-DotEnv 的剥引号逻辑对应。

    .PARAMETER Path
        目标 .env 路径（父目录自动创建）。

    .PARAMETER Entries
        键值表。值为 $null 时按空串处理。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [hashtable]$Entries
    )

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add('# DeerFlow 环境配置 —— 由 DeerFlow.Common.psm1 的 Write-DotEnv 生成')
    $lines.Add("# 生成时间: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
    $lines.Add('# 手工修改后若重新执行 init-config.ps1 会被覆盖，请改 config.yaml 或脚本入参。')

    foreach ($key in ($Entries.Keys | Sort-Object)) {
        $value = $Entries[$key]
        if ($null -eq $value) { $value = '' }
        $value = [string]$value

        $needsQuote = ($value -eq '') -or ($value -match '[\s#]')
        if ($needsQuote) {
            if ($value -match '"') {
                if ($value -match "'") {
                    # 同时含两种引号：原样输出，靠 Read-DotEnv 的「不剥行尾注释」兜住
                    Write-Warn "$key 的值同时含单双引号，已按原样写入，请人工确认"
                } else {
                    $value = "'" + $value + "'"
                }
            } else {
                $value = '"' + $value + '"'
            }
        }

        $lines.Add("$key=$value")
    }

    $content = ($lines -join "`r`n") + "`r`n"
    Write-TextFileNoBom -Path $Path -Content $content
}

function Test-DeerFlowConfigured {
    <#
    .SYNOPSIS
        检查 config.yaml 与 .env 是否都已存在。

    .DESCRIPTION
        用于让「配置是否就绪」只有一个判据：两个文件缺一不可时才算未配置，
        部署脚本据此决定是提示先跑 init-config.ps1 还是继续安装。

    .PARAMETER ConfigPath
        可选，覆盖 config.yaml 路径（默认取 Get-DeerFlowConfigPath）。

    .PARAMETER EnvPath
        可选，覆盖 .env 路径（默认取 Get-DeerFlowEnvPath）。

    .OUTPUTS
        System.Boolean
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $false)]
        [string]$ConfigPath,

        [Parameter(Mandatory = $false)]
        [string]$EnvPath
    )

    if (-not $ConfigPath) { $ConfigPath = Get-DeerFlowConfigPath }
    if (-not $EnvPath)    { $EnvPath    = Get-DeerFlowEnvPath }

    $hasConfig = Test-Path $ConfigPath
    $hasEnv    = Test-Path $EnvPath

    if (-not $hasConfig) { Write-Info "缺少 config.yaml: $ConfigPath" }
    if (-not $hasEnv)    { Write-Info "缺少 .env: $EnvPath" }

    return ($hasConfig -and $hasEnv)
}

# ── 端口与进程 ──────────────────────────────────────────────────────────────

function Test-PortListening {
    <#
    .SYNOPSIS
        判断本机某端口是否处于监听状态。

    .DESCRIPTION
        用 Get-NetTCPConnection -State Listen，不依赖 netstat 的文本输出
        （中文系统的 netstat 表头是本地化的，按列号解析很脆弱）。

    .PARAMETER Port
        端口号。

    .OUTPUTS
        System.Boolean
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$Port
    )

    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    return ($conns.Count -gt 0)
}

function Get-PortOwnerPid {
    <#
    .SYNOPSIS
        返回占用指定监听端口的进程 PID 数组。

    .DESCRIPTION
        同一端口可能被多个进程在不同地址上监听（如 0.0.0.0 与 127.0.0.1），
        因此返回数组而不是单个 PID。端口空闲时返回空数组。

    .PARAMETER Port
        端口号。

    .OUTPUTS
        System.Int32[]（可能为空）
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 65535)]
        [int]$Port
    )

    $conns = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($conns.Count -eq 0) { return @() }

    $pids = $conns |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -gt 0 }

    return @($pids)
}

function Get-ProcessCommandLine {
    <#
    .SYNOPSIS
        返回指定 PID 的完整命令行；进程不存在或无权限时返回 $null。

    .DESCRIPTION
        必须用 Get-CimInstance Win32_Process：Get-Process 的对象上没有命令行，
        而命令行是「这个进程到底是谁」的唯一可靠依据（ExecutablePath 只能说明
        用了哪个解释器）。

    .PARAMETER ProcessId
        进程 ID。

    .OUTPUTS
        System.String 或 $null
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    return $proc.CommandLine
}

function Test-DeerFlowPathInRoot {
    <#
    .SYNOPSIS
        判断一段文本（命令行 / 可执行文件路径）是否包含部署根目录。

    .DESCRIPTION
        内部辅助函数，用于「防止误杀他人进程」：
        只有路径落在 D:\deer-flow 之下的进程才可能是本项目的进程。

        用 IndexOf + OrdinalIgnoreCase 而不是 -like "*$root*"：
        -like 会把路径里的 [ ] 当成通配符，且大小写与通配语义容易误判。

    .PARAMETER Text
        待检查的文本。
    #>
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }

    $root = (Get-DeerFlowRoot).TrimEnd('\', '/')
    return ($Text.IndexOf($root, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
}

function Test-DeerFlowProcessMatch {
    <#
    .SYNOPSIS
        判断一个 Win32_Process 是否属于本项目（内部辅助函数）。

    .DESCRIPTION
        三个判据，按「命令行特征」为必要条件组合：

          PatternHit —— 命令行命中该服务的特征正则。这是必要条件：
                        没有它，任何路径恰好在部署根下的进程都会被认领。
          PathInRoot —— 命令行或可执行文件路径包含部署根目录。
          NameHit    —— 进程名属于该服务的候选名（如 cmd.exe / node.exe）。

        两个开关：
          -AllowNameFallback 为真时，PathInRoot 或 NameHit 满足其一即可。
            只在「PID 文件已指明就是它」的分支打开——经 pnpm 拉起的前端外层是
            cmd.exe，路径不在部署根内、命令行里也没有部署根，只有进程名能佐证；
            而 PID 文件本身已是强证据，放宽这一步不会引入误杀。
          严格模式（默认，用于全量扫描）则要求 PathInRoot 必须成立，因为扫描面
            覆盖整机所有进程，判据必须更硬。

    .PARAMETER Process
        Get-CimInstance Win32_Process 返回的对象。

    .PARAMETER Patterns
        命令行特征正则数组（-match 语义，忽略大小写）。

    .PARAMETER Names
        候选进程名数组。

    .PARAMETER AllowNameFallback
        见上。
    #>
    param(
        [Parameter(Mandatory = $true)]
        $Process,

        [Parameter(Mandatory = $true)]
        [string[]]$Patterns,

        [Parameter(Mandatory = $false)]
        [string[]]$Names = @(),

        [Parameter(Mandatory = $false)]
        [switch]$AllowNameFallback
    )

    if ($null -eq $Process) { return $false }

    $cmd = $Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }

    $patternHit = $false
    foreach ($pattern in $Patterns) {
        if ($cmd -match $pattern) { $patternHit = $true; break }
    }
    if (-not $patternHit) { return $false }

    $pathInRoot = (Test-DeerFlowPathInRoot -Text $cmd) -or
                  (Test-DeerFlowPathInRoot -Text $Process.ExecutablePath)
    if ($pathInRoot) { return $true }

    if ($AllowNameFallback -and ($Names -contains $Process.Name)) { return $true }

    return $false
}

function Get-DeerFlowProcess {
    <#
    .SYNOPSIS
        按逻辑名（gateway / frontend）查找本项目的进程。

    .DESCRIPTION
        两级策略：
          1. 先读 PID 文件（logs\run\<name>.pid）——脚本自己写的，最精确。
             记录失效（进程已退出或 PID 被复用）时删除陈旧文件并继续第 2 步，
             调用方无需自己清理。
          2. 兜底扫描 Win32_Process：命令行命中该名称的特征正则，
             且命令或可执行路径包含部署根目录。

        为什么两级都要校验「特征正则」和「部署根」，而不是只看其一：
          · 只看特征正则 —— 会匹配到别处跑的 uvicorn / next，停错别人的服务
          · 只看部署根   —— 会把部署根下任何 node.exe（比如某个构建脚本）当服务
        两个条件叠加，才能既找得到、又不会误杀。

        PID 文件分支放宽为「特征命中 且（路径在部署根内 或 进程名属于候选名）」，
        原因见 Test-DeerFlowProcessMatch 的注释。

    .PARAMETER Name
        逻辑名，内置 gateway 与 frontend 两套特征；其它名字退化为
        「命令行包含该名字且在部署根内」。

    .OUTPUTS
        PSCustomObject（ProcessId / Name / CommandLine / ExecutablePath / Source），
        未找到时返回 $null。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    if ($script:ProcessSpecs.ContainsKey($Name)) {
        $patterns = $script:ProcessSpecs[$Name].Patterns
        $names    = $script:ProcessSpecs[$Name].Names
    } else {
        $patterns = @([regex]::Escape($Name))
        $names    = @()
    }

    # ── 1) PID 文件 ────────────────────────────────────────────────────────
    $filePid = Read-PidFile -Name $Name
    if ($filePid) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$filePid" -ErrorAction SilentlyContinue
        if ($proc -and (Test-DeerFlowProcessMatch -Process $proc -Patterns $patterns `
                        -Names $names -AllowNameFallback)) {
            return [pscustomobject]@{
                Name           = $Name
                ProcessId      = $proc.ProcessId
                CommandLine    = $proc.CommandLine
                ExecutablePath = $proc.ExecutablePath
                Source         = 'PidFile'
            }
        }

        if ($proc) {
            Write-Warn "PID 文件记录的进程 $filePid 不像 $Name 的服务进程（PID 可能已被复用），忽略"
        }

        # 走到这里说明记录已失效（进程已退出，或 PID 被复用成了别的进程）。
        # 一并删除：Read-PidFile 只管「返回 null」，不负责清理，若这里不清，
        # 陈旧的 <name>.pid 会一直留着，运维看到文件在就以为服务还在跑。
        Remove-PidFile -Name $Name
    } elseif (Test-Path (Get-PidFilePath -Name $Name)) {
        # 文件存在但 Read-PidFile 返回 null（进程已退出 / 内容非法）——同样清掉
        Remove-PidFile -Name $Name
    }

    # ── 2) 按命令行特征扫描 ────────────────────────────────────────────────
    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
    if ($names.Count -gt 0) {
        # 先按进程名粗筛，避免对整机进程逐个跑正则（该机进程数可达数百）
        $candidates = $candidates | Where-Object { $names -contains $_.Name }
    }

    foreach ($proc in $candidates) {
        if (-not (Test-DeerFlowProcessMatch -Process $proc -Patterns $patterns -Names $names)) {
            continue
        }

        return [pscustomobject]@{
            Name           = $Name
            ProcessId      = $proc.ProcessId
            CommandLine    = $proc.CommandLine
            ExecutablePath = $proc.ExecutablePath
            Source         = 'CommandLine'
        }
    }

    return $null
}

function Stop-DeerFlowProcess {
    <#
    .SYNOPSIS
        停止指定进程：先温和终止，超时未退出则用 taskkill /F /T 兜底。

    .DESCRIPTION
        先 Stop-Process 是为了给 uvicorn / next 一个正常退出的机会（释放端口、
        关闭 SQLite 连接、刷新日志）。若在 WaitSeconds 内没退出，再上
        taskkill /F /T。

        /T（连同子进程树）是必需的：next dev 会派生 next-server 子进程，
        只杀父进程会留下孤儿继续占用 3000 端口，下次启动就报端口被占。

    .PARAMETER ProcessId
        要停止的进程 ID。

    .PARAMETER WaitSeconds
        温和终止的等待秒数，默认 10。

    .OUTPUTS
        System.Boolean —— 进程最终已不存在则为 $true。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId,

        [Parameter(Mandatory = $false)]
        [int]$WaitSeconds = 10
    )

    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $proc) {
        Write-Info "进程 $ProcessId 不存在，无需停止"
        return $true
    }

    $procName = $proc.ProcessName
    Write-Info "停止进程 $ProcessId ($procName)"

    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
        if (-not (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
            Write-Ok "进程 $ProcessId 已退出"
            return $true
        }
    }

    Write-Warn "进程 $ProcessId 未在 ${WaitSeconds}s 内退出，改用 taskkill /F /T"
    $output = & taskkill.exe /F /T /PID $ProcessId 2>&1 | Out-String
    $output = $output.Trim()
    if ($output) { Write-Info $output }

    Start-Sleep -Milliseconds 500
    if (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue) {
        Write-Fail "进程 $ProcessId 仍然存在"
        return $false
    }

    Write-Ok "进程 $ProcessId 已强制终止"
    return $true
}

# ── PID 文件 ────────────────────────────────────────────────────────────────

function Get-PidFilePath {
    <#
    .SYNOPSIS
        返回某逻辑名对应的 PID 文件路径（logs\run\<name>.pid）。

    .PARAMETER Name
        逻辑名，如 gateway / frontend。
    #>
    param([string]$Name)
    return (Join-Path (Get-DeerFlowRunDir) "$Name.pid")
}

function Write-PidFile {
    <#
    .SYNOPSIS
        记录某逻辑名对应的进程 PID 到 logs\run\<name>.pid。

    .DESCRIPTION
        文件内容只有一行 PID，便于 Read-PidFile 与外部工具（如
        Get-Content *.pid）直接消费。日志\run 目录不存在时自动创建。

    .PARAMETER Name
        逻辑名，如 gateway / frontend。

    .PARAMETER ProcessId
        要记录的进程 ID。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $path = Get-PidFilePath -Name $Name
    $dir = Split-Path -Parent $path
    if (-not (Test-Path $dir)) {
        New-Item -Path $dir -ItemType Directory -Force | Out-Null
    }

    Write-TextFileNoBom -Path $path -Content "$ProcessId`r`n"
}

function Read-PidFile {
    <#
    .SYNOPSIS
        读取 PID 文件，返回 PID；文件不存在、内容非法或进程已退出时返回 $null。

    .DESCRIPTION
        「进程已退出就返回 $null」把调用方从「读到 PID 还要自己判断死活」里
        解放出来，这是脚本反复踩坑的地方：只读文件会把上一次运行的陈旧 PID
        当成在跑，导致 status 显示「运行中」而 start 又不敢启动。

    .PARAMETER Name
        逻辑名，如 gateway / frontend。

    .OUTPUTS
        System.Int32 或 $null
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $path = Get-PidFilePath -Name $Name
    if (-not (Test-Path $path)) { return $null }

    $text = ([System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)).Trim()
    if (-not $text) { return $null }

    # 只取第一行，容忍文件末尾的换行或人工追加的说明
    $firstLine = ($text -split "`r?`n" | Select-Object -First 1).Trim()

    $value = 0
    if (-not [int]::TryParse($firstLine, [ref]$value)) { return $null }
    if ($value -le 0) { return $null }

    if (-not (Get-Process -Id $value -ErrorAction SilentlyContinue)) { return $null }

    return $value
}

function Remove-PidFile {
    <#
    .SYNOPSIS
        删除某逻辑名的 PID 文件（不存在时静默返回）。

    .PARAMETER Name
        逻辑名，如 gateway / frontend。
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $path = Get-PidFilePath -Name $Name
    if (Test-Path $path) {
        Remove-Item -Path $path -Force -ErrorAction SilentlyContinue
    }
}

# ── 导出 ────────────────────────────────────────────────────────────────────
#
# 显式列出导出清单：不写 Export-ModuleMember 时模块会导出全部函数，
# 内部辅助函数（如 Get-PidFilePath、Test-DeerFlowPathInRoot）会一起暴露，
# 下游脚本可能误用后与实现细节耦合。

Export-ModuleMember -Function @(
    # 输出
    'Write-Step', 'Write-Ok', 'Write-Warn', 'Write-Fail', 'Write-Info', 'Write-Log'
    # 编码
    'Write-TextFileNoBom'
    # PATH
    'Add-ToolchainToPath'
    # 路径
    'Get-DeerFlowRoot', 'Get-DeerFlowSrcDir', 'Get-DeerFlowDataDir',
    'Get-DeerFlowLogsDir', 'Get-DeerFlowRunDir', 'Get-DeerFlowToolsDir',
    'Get-DeerFlowCacheDir', 'Get-DeerFlowConfigPath', 'Get-DeerFlowEnvPath'
    # 配置
    'Read-DotEnv', 'Write-DotEnv', 'Test-DeerFlowConfigured'
    # 端口与进程
    'Test-PortListening', 'Get-PortOwnerPid', 'Get-ProcessCommandLine',
    'Get-DeerFlowProcess', 'Stop-DeerFlowProcess'
    # PID 文件
    'Write-PidFile', 'Read-PidFile', 'Remove-PidFile'
)
