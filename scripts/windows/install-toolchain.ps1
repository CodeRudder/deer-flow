<#
.SYNOPSIS
    在 Windows 上安装 DeerFlow 所需的工具链（Node.js / uv / pnpm）。

.DESCRIPTION
    采用「自包含工具链」方式：所有工具解压到指定根目录下，默认 PATH 中的
    旧版本（如系统自带的 Node 16）不受影响，卸载时直接删目录即可。

    根目录布局：
        <RootDir>\tools\node\       Node.js（解压版）
        <RootDir>\tools\uv\         uv
        <RootDir>\cache\            各类缓存（npm / uv / 下载临时文件）
        <RootDir>\src\              代码仓库（rsync 目标，工具链在此之外）
        <RootDir>\logs\             运行日志

    ⚠ tools\ 与 src\ 必须平级：同步代码时常用 rsync --delete，若工具链在
      src\ 之内会被一并删除。

    所有缓存都指向 <RootDir>\cache\，避免写入系统盘——Windows 系统的
    用户目录常因空间不足导致安装失败。

.PARAMETER RootDir
    部署根目录，默认 D:\deer-flow。

.PARAMETER NodeVersion
    Node.js 版本，默认 v22.23.2（LTS "Jod"）。前端要求 >= 22。

.PARAMETER UvVersion
    uv 版本，默认 0.12.13。

.PARAMETER PnpmVersion
    pnpm 版本，默认 10.26.2（与仓库 frontend/pnpm-lock.yaml 保持一致）。

.PARAMETER Proxy
    下载代理，例如 socks5h://127.0.0.1:1080。
    必须用 socks5:// 或 socks5h://；socks:// 会被 curl 当作 SOCKS4
    （SOCKS4 不支持远程 DNS，解析域名会失败）。

    说明：默认的 npmmirror 源国内可直连，通常无需代理。
    代理主要用于 GitHub 资产（uv）或镜像不可用时的回退。

.PARAMETER Force
    已安装的组件也重新下载覆盖。

.EXAMPLE
    # 默认使用 npmmirror 镜像，国内网络通常直连即可
    .\install-toolchain.ps1

.EXAMPLE
    # 镜像不可用时叠加本机 SOCKS5 代理
    .\install-toolchain.ps1 -Proxy socks5h://127.0.0.1:1080

.EXAMPLE
    # GitHub 下载走加速前缀
    .\install-toolchain.ps1 -GitHubMirror https://ghfast.top/

.NOTES
    本机实测环境：Windows 10 20H2 / AMD64 / PowerShell 5.1。
    脚本以纯 ASCII 之外的字符书写，因此本文件必须以 UTF-8 (BOM) 保存，
    否则 PowerShell 5.1 会按 ANSI(GBK) 解析导致中文乱码。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RootDir = 'D:\deer-flow',

    [Parameter(Mandatory = $false)]
    [string]$NodeVersion = 'v22.23.2',

    [Parameter(Mandatory = $false)]
    [string]$UvVersion = '0.12.13',

    [Parameter(Mandatory = $false)]
    [string]$PnpmVersion = '10.26.2',

    <#
      npm / pnpm 的软件源。默认使用 npmmirror（阿里云）：
      registry.npmjs.org 走 Cloudflare，本机经代理访问时 Node 的 CA 证书库
      会报 CERT_HAS_EXPIRED，而 npmmirror 的证书链可直接信任。
      传 'https://registry.npmjs.org' 可切回官方源。
    #>
    [Parameter(Mandatory = $false)]
    [string]$NpmRegistry = 'https://registry.npmmirror.com',

    <#
      Node.js 下载镜像。nodejs.org 直连超时，官方镜像站与 npmmirror 都提供
      完整分发。传 'https://nodejs.org/dist' 可切回官方。
    #>
    [Parameter(Mandatory = $false)]
    [string]$NodeMirror = 'https://npmmirror.com/mirrors/node',

    <#
      GitHub 加速前缀，用于下载 uv 的 release 资产。留空则直连 github.com。
      例如 'https://ghfast.top/'（注意结尾斜杠）。
    #>
    [Parameter(Mandatory = $false)]
    [string]$GitHubMirror = '',

    [Parameter(Mandatory = $false)]
    [string]$Proxy,

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 输出辅助 ────────────────────────────────────────────────────────────────

function Write-Step { param([string]$M) ; Write-Host ""; Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([string]$M) ; Write-Host "  [ OK ] $M" -ForegroundColor Green }
function Write-Warn { param([string]$M) ; Write-Host "  [WARN] $M" -ForegroundColor Yellow }
function Write-Fail { param([string]$M) ; Write-Host "  [FAIL] $M" -ForegroundColor Red }
function Write-Info { param([string]$M) ; Write-Host "         $M" -ForegroundColor DarkGray }

# ── 路径解析 ────────────────────────────────────────────────────────────────

$toolsDir   = Join-Path $RootDir 'tools'
$nodeDir    = Join-Path $toolsDir 'node'
$uvDir      = Join-Path $toolsDir 'uv'
$cacheDir   = Join-Path $RootDir 'cache'
$srcDir     = Join-Path $RootDir 'src'
$logsDir    = Join-Path $RootDir 'logs'
$tempDir    = Join-Path $cacheDir 'temp'

# ── 镜像可达性 ──────────────────────────────────────────────────────────────

<#
  检测一个 URL 是否可访问，返回 HTTP 状态码字符串（如 '200'），
  失败时返回 '000'。用于在开始安装前暴露网络问题，而不是中途失败。
  直连不通时自动改走代理重试。
#>
function Test-UrlReachable {
    param(
        [string]$Url,
        [string]$ProxyUrl,
        [int]$TimeoutSeconds = 15
    )

    $curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (-not (Test-Path $curlExe)) { return '000' }

    $baseArgs = @('--silent', '--output', 'NUL', '--write-out', '%{http_code}',
                  '--connect-timeout', "$TimeoutSeconds", '--max-time', "$($TimeoutSeconds + 10)")

    # 先直连
    $code = & $curlExe @baseArgs $Url 2>&1
    if ($code -eq '200' -or $code -eq '301' -or $code -eq '302') { return $code }

    # 直连不通且有代理时，改走代理
    if ($ProxyUrl) {
        $code = & $curlExe @baseArgs --proxy $ProxyUrl $Url 2>&1
    }
    return $code
}

# ── 代理归一化 ──────────────────────────────────────────────────────────────

function Resolve-ProxyUrl {
    param([string]$ProxyUrl)
    if ([string]::IsNullOrWhiteSpace($ProxyUrl)) { return $null }

    $value = $ProxyUrl.Trim()

    if ($value -match '^socks://') {
        $normalized = $value -replace '^socks://', 'socks5h://'
        Write-Warn "socks:// 会被 curl 当作 SOCKS4（不支持远程 DNS）"
        Write-Info "已自动改为 $normalized"
        return $normalized
    }
    if ($value -match '^socks4://|^socks4a://') {
        Write-Warn "SOCKS4 不支持远程 DNS，解析域名可能失败，建议改用 socks5h://"
    }
    return $value
}

# ── 下载 ────────────────────────────────────────────────────────────────────

function Invoke-Download {
    param(
        [string]$Url,
        [string]$OutFile,
        [string]$ProxyUrl
    )

    # 用 curl.exe 而不是 Invoke-WebRequest：后者不支持 SOCKS 代理，
    # 且在 PowerShell 5.1 下进度条会显著拖慢下载速度。
    $curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
    if (-not (Test-Path $curlExe)) {
        throw "未找到 curl.exe（$curlExe）——本系统可能低于 Windows 10 1803"
    }

    $curlArgs = @(
        '--fail', '--location', '--silent', '--show-error',
        '--connect-timeout', '20',
        '--max-time', '900',
        '--output', $OutFile
    )
    if ($ProxyUrl) { $curlArgs += @('--proxy', $ProxyUrl) }
    $curlArgs += $Url

    $output = & $curlExe @curlArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        $detail = if ($output) { ($output | Select-Object -Last 3) -join ' ' } else { '' }
        throw "下载失败 (curl 返回码 $LASTEXITCODE): $Url`n$detail"
    }
    if (-not (Test-Path $OutFile) -or (Get-Item $OutFile).Length -eq 0) {
        throw "下载结果为空: $OutFile"
    }
}

# ── 前置检查 ────────────────────────────────────────────────────────────────

Write-Step "前置检查"

$os = Get-CimInstance Win32_OperatingSystem
Write-Ok "$($os.Caption) (Build $($os.BuildNumber), $env:PROCESSOR_ARCHITECTURE)"
Write-Ok "PowerShell $($PSVersionTable.PSVersion)"

$driveLetter = (Split-Path -Qualifier $RootDir).TrimEnd(':')
$drive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='${driveLetter}:'" -ErrorAction SilentlyContinue
if (-not $drive) {
    Write-Fail "目标盘不存在: ${driveLetter}:"
    exit 1
}
$freeGb = [math]::Round($drive.FreeSpace / 1GB, 1)
Write-Ok "目标盘 ${driveLetter}: 可用 $freeGb GB"

if ($freeGb -lt 10) {
    Write-Fail "空间不足（需至少 10 GB，当前 $freeGb GB）"
    Write-Info "Node.js 与依赖缓存建议保留 5 GB 以上余量。"
    exit 1
}

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Write-Ok "当前用户为管理员"
} else {
    Write-Warn "当前用户非管理员——本脚本装到用户范围，通常仍可继续"
}

$resolvedProxy = Resolve-ProxyUrl -ProxyUrl $Proxy
if ($resolvedProxy) { Write-Ok "下载代理: $resolvedProxy" } else { Write-Ok "下载代理: 不使用" }

Write-Info "npm 源      : $NpmRegistry"
Write-Info "Node 镜像   : $NodeMirror"
if ($GitHubMirror) { Write-Info "GitHub 加速 : $GitHubMirror" }

Write-Info ""
Write-Info "检测下载源可达性（直连不通时自动尝试代理）..."
$npmCode = Test-UrlReachable -Url "$($NpmRegistry.TrimEnd('/'))/pnpm/$PnpmVersion" -ProxyUrl $resolvedProxy
$nodeCode = Test-UrlReachable -Url "$($NodeMirror.TrimEnd('/'))/$NodeVersion/node-$NodeVersion-win-x64.zip" -ProxyUrl $resolvedProxy

if ($npmCode -match '^2\d\d' -or $npmCode -match '^3\d\d') {
    Write-Ok "npm 源可达 ($npmCode)"
} else {
    Write-Warn "npm 源返回 $npmCode —— pnpm 安装可能失败"
}
if ($nodeCode -match '^2\d\d' -or $nodeCode -match '^3\d\d') {
    Write-Ok "Node 镜像可达 ($nodeCode)"
} else {
    Write-Warn "Node 镜像返回 $nodeCode —— 将回退到 nodejs.org"
}

# ── 目录结构 ────────────────────────────────────────────────────────────────

Write-Step "创建目录结构"

foreach ($d in @($RootDir, $toolsDir, $cacheDir, $srcDir, $logsDir, $tempDir,
                 (Join-Path $cacheDir 'npm'), (Join-Path $cacheDir 'uv'),
                 (Join-Path $cacheDir 'uv-python'))) {
    if (-not (Test-Path $d)) {
        New-Item -Path $d -ItemType Directory -Force | Out-Null
        Write-Info "创建 $d"
    }
}
Write-Ok "目录结构就绪: $RootDir"

# 下载期间把临时目录指向目标盘，避免写满系统盘
$env:TEMP = $tempDir
$env:TMP = $tempDir

# ── Node.js ─────────────────────────────────────────────────────────────────

Write-Step "安装 Node.js $NodeVersion"

$nodeExe = Join-Path $nodeDir 'node.exe'
$installedNodeVersion = $null
if (Test-Path $nodeExe) {
    try { $installedNodeVersion = (& $nodeExe --version 2>&1) } catch { $installedNodeVersion = $null }
}

if ($installedNodeVersion -eq $NodeVersion -and -not $Force) {
    Write-Ok "已安装 $installedNodeVersion，跳过"
} else {
    if ($installedNodeVersion) {
        Write-Info "当前 $installedNodeVersion，将替换为 $NodeVersion"
    }

    $nodeZip = Join-Path $tempDir "node-$NodeVersion-win-x64.zip"
    $fileName = "node-$NodeVersion-win-x64.zip"

    # 依次尝试镜像与官方源：镜像在国内通常直连即可，不需要代理
    $nodeCandidates = @(
        "$($NodeMirror.TrimEnd('/'))/$NodeVersion/$fileName",
        "https://nodejs.org/dist/$NodeVersion/$fileName"
    ) | Where-Object { $_ } | Select-Object -Unique

    $downloaded = $false
    foreach ($url in $nodeCandidates) {
        try {
            Write-Info "下载 $url"
            Invoke-Download -Url $url -OutFile $nodeZip -ProxyUrl $resolvedProxy
            Write-Ok "下载完成 ($([math]::Round((Get-Item $nodeZip).Length/1MB,1)) MB)"
            $downloaded = $true
            break
        } catch {
            Write-Warn "失败: $($_.Exception.Message -split "`n" | Select-Object -First 1)"
        }
    }
    if (-not $downloaded) {
        Write-Fail "所有 Node.js 下载源均失败"
        Write-Info "可用 -NodeMirror 指定其他镜像，或 -Proxy 配置代理。"
        exit 1
    }

    $extractDir = Join-Path $tempDir "node-extract"
    Remove-Item -Path $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Info "解压..."
    Expand-Archive -Path $nodeZip -DestinationPath $extractDir -Force

    # 压缩包顶层目录名含版本号，按 node.exe 定位而不是硬编码
    $inner = Get-ChildItem -Path $extractDir -Directory |
        Where-Object { Test-Path (Join-Path $_.FullName 'node.exe') } |
        Select-Object -First 1
    if (-not $inner) {
        Write-Fail "解压后未找到 node.exe，压缩包结构可能已变化"
        exit 1
    }

    if (Test-Path $nodeDir) { Remove-Item -Path $nodeDir -Recurse -Force }
    New-Item -Path $nodeDir -ItemType Directory -Force | Out-Null
    Copy-Item -Path (Join-Path $inner.FullName '*') -Destination $nodeDir -Recurse -Force

    Remove-Item -Path $nodeZip, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "Node.js 已安装到 $nodeDir"
}

$nodeVer = (& $nodeExe --version 2>&1)
Write-Ok "node $nodeVer"

# ── uv ──────────────────────────────────────────────────────────────────────

Write-Step "安装 uv $UvVersion"

$uvExe = Join-Path $uvDir 'uv.exe'
$installedUvVersion = $null
if (Test-Path $uvExe) {
    try { $installedUvVersion = (& $uvExe --version 2>&1 | Select-Object -First 1) } catch { $installedUvVersion = $null }
}

if ($installedUvVersion -and $installedUvVersion -match [regex]::Escape($UvVersion) -and -not $Force) {
    Write-Ok "已安装 $installedUvVersion，跳过"
} else {
    $uvZip = Join-Path $tempDir "uv-$UvVersion.zip"
    $uvAsset = "uv-x86_64-pc-windows-msvc.zip"
    $uvUpstream = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$uvAsset"

    # GitHub 直连在受限网络下常超时，通过加速前缀（-GitHubMirror）可绕开
    $uvCandidates = @()
    if ($GitHubMirror) {
        $uvCandidates += "$($GitHubMirror.TrimEnd('/'))/$uvUpstream"
        $uvCandidates += "$($GitHubMirror.TrimEnd('/'))/https://github.com/astral-sh/uv/releases/download/$UvVersion/$uvAsset"
    }
    $uvCandidates += $uvUpstream

    $downloaded = $false
    foreach ($url in ($uvCandidates | Select-Object -Unique)) {
        try {
            Write-Info "下载 $url"
            Invoke-Download -Url $url -OutFile $uvZip -ProxyUrl $resolvedProxy
            Write-Ok "下载完成 ($([math]::Round((Get-Item $uvZip).Length/1MB,1)) MB)"
            $downloaded = $true
            break
        } catch {
            Write-Warn "失败: $($_.Exception.Message -split "`n" | Select-Object -First 1)"
        }
    }
    if (-not $downloaded) {
        Write-Fail "所有 uv 下载源均失败"
        Write-Info "建议用代理重试：-Proxy socks5h://127.0.0.1:1080"
        Write-Info "或用加速前缀：-GitHubMirror https://ghfast.top/"
        exit 1
    }

    $uvExtract = Join-Path $tempDir "uv-extract"
    Remove-Item -Path $uvExtract -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $uvZip -DestinationPath $uvExtract -Force

    if (Test-Path $uvDir) { Remove-Item -Path $uvDir -Recurse -Force }
    New-Item -Path $uvDir -ItemType Directory -Force | Out-Null

    # uv 的压缩包把 uv.exe / uvx.exe 放在顶层
    $uvFiles = Get-ChildItem -Path $uvExtract -Filter 'uv*.exe' -Recurse
    if ($uvFiles.Count -eq 0) {
        Write-Fail "解压后未找到 uv.exe"
        exit 1
    }
    $uvFiles | ForEach-Object { Copy-Item $_.FullName -Destination $uvDir -Force }

    Remove-Item -Path $uvZip, $uvExtract -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "uv 已安装到 $uvDir"
}

$uvVer = (& $uvExe --version 2>&1 | Select-Object -First 1)
Write-Ok "$uvVer"

# ── pnpm ────────────────────────────────────────────────────────────────────

Write-Step "安装 pnpm $PnpmVersion"

# 用刚装好的 Node 22 来装 pnpm。
# 不用 corepack：随 Node 16 附带的 corepack 版本过旧，无法正确准备 pnpm 10。
$npmCmd = Join-Path $nodeDir 'npm.cmd'
if (-not (Test-Path $npmCmd)) {
    Write-Fail "未找到 npm.cmd（$npmCmd）"
    exit 1
}

$pnpmCmd = Join-Path $nodeDir 'pnpm.cmd'
$installedPnpmVersion = $null
if (Test-Path $pnpmCmd) {
    try { $installedPnpmVersion = (& $pnpmCmd --version 2>&1 | Select-Object -First 1) } catch { $installedPnpmVersion = $null }
}

if ($installedPnpmVersion -eq $PnpmVersion -and -not $Force) {
    Write-Ok "已安装 $installedPnpmVersion，跳过"
} else {
    Write-Info "源: $NpmRegistry"
    Write-Info "通过 npm 全局安装到 $nodeDir ..."

    # 缓存与全局目录都指到数据盘，避免写满系统盘
    $env:npm_config_cache = Join-Path $cacheDir 'npm'
    $env:npm_config_prefix = $nodeDir
    $env:npm_config_registry = $NpmRegistry

    # 不使用代理：npmmirror 在国内可直连，走代理反而可能触发证书校验问题
    # （registry.npmjs.org 经代理访问时 Node 会报 CERT_HAS_EXPIRED）。
    $npmArgs = @('install', '-g', "pnpm@$PnpmVersion", '--no-fund', '--no-audit',
                 "--registry=$NpmRegistry")

    $npmOutput = & $npmCmd @npmArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "pnpm 安装失败 (npm 返回码 $LASTEXITCODE)"
        Write-Info ($npmOutput | Select-Object -Last 8)
        Write-Host ""
        Write-Host "  可尝试：" -ForegroundColor Yellow
        Write-Host "    1. 换其他镜像源：-NpmRegistry https://registry.npmmirror.com"
        Write-Host "    2. 若报证书错误，改用本机下载后离线安装："
        Write-Host "       npm pack pnpm@$PnpmVersion        # 在可联网机器上执行"
        Write-Host "       npm install -g .\pnpm-$PnpmVersion.tgz"
        Write-Host ""
        exit 1
    }
    Write-Ok "pnpm 已安装"
}

$pnpmVer = (& $pnpmCmd --version 2>&1 | Select-Object -First 1)
Write-Ok "pnpm $pnpmVer"

# ── 环境变量 ────────────────────────────────────────────────────────────────

Write-Step "配置环境变量（用户范围）"

# 仅写用户范围，不改系统变量——避免影响依赖系统 Node 的其他程序。
function Set-UserEnv {
    param([string]$Name, [string]$Value)
    $current = [Environment]::GetEnvironmentVariable($Name, 'User')
    if ($current -ne $Value) {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
        Write-Info "设置 $Name = $Value"
    }
    Set-Item -Path "Env:$Name" -Value $Value -ErrorAction SilentlyContinue
}

# npm/pnpm 永久使用镜像源。后续 `pnpm install` 装上千个依赖时同样受益，
# 且避开 registry.npmjs.org 在代理下的证书问题。
Set-UserEnv -Name 'NPM_CONFIG_REGISTRY' -Value $NpmRegistry

# 注意：这里**不设置** DEER_FLOW_HOME。
#
# 曾经把它设成 $RootDir，结果运行时状态（SQLite、admin_initial_credentials.txt）
# 落到部署根而非 data\，实测踩过（admin-init.ps1 报「base_dir 与数据目录不同」）。
#
# ⚠ 但**不要**由此得出「.env 是权威来源」的结论 —— .env 是靠不住的：
#   base_dir 取自 os.getenv('DEER_FLOW_HOME')，而它能被读到只是因为
#   app_config.py 顶层有一次 load_dotenv()，那次查找是从 cwd 向上找 .env。
#   从部署根 D:\deer-flow 启动时够不到 src\.env，变量就「缺失」，base_dir 会
#   静默回落到 <root>\.deer-flow（实测：cwd=src\backend→data\ ✓，
#   cwd=D:\deer-flow→.deer-flow ✗）。
#
# 所以这里既不该设、也不能指望 .env 兜底。真正的权威在**服务启动处**：
# start.ps1 会在 Gateway 命令行里显式 set DEER_FLOW_HOME=<root>\data，
# 使 base_dir 与 cwd 无关。排查路径问题时先看那里。
Set-UserEnv -Name 'NPM_CONFIG_CACHE' -Value (Join-Path $cacheDir 'npm')
Set-UserEnv -Name 'UV_CACHE_DIR' -Value (Join-Path $cacheDir 'uv')
# uv 自带的 Python 也放到数据盘，避免占满系统盘
Set-UserEnv -Name 'UV_PYTHON_INSTALL_DIR' -Value (Join-Path $cacheDir 'uv-python')

# PATH 前置工具链目录（去重后再拼，保证重复运行不会不断堆叠）
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ([string]::IsNullOrEmpty($userPath)) { $userPath = '' }

$parts = @($userPath -split ';' | Where-Object { $_ -and $_.Trim() })
$parts = $parts | Where-Object {
    $p = $_.TrimEnd('\')
    $p -ne $nodeDir.TrimEnd('\') -and $p -ne $uvDir.TrimEnd('\')
}

$newPath = (@($nodeDir, $uvDir) + $parts) -join ';'
if ($newPath -ne $userPath) {
    [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
    Write-Ok "PATH 已更新（工具链目录置前）"
} else {
    Write-Ok "PATH 无需变更"
}

# ── 验证 ────────────────────────────────────────────────────────────────────

Write-Step "验证"

# 用绝对路径验证，不依赖 PATH 是否已在当前会话生效
$checks = @(
    @{ Name = 'node';   Path = Join-Path $nodeDir 'node.exe' },
    @{ Name = 'npm';    Path = Join-Path $nodeDir 'npm.cmd'  },
    @{ Name = 'pnpm';   Path = Join-Path $nodeDir 'pnpm.cmd' },
    @{ Name = 'uv';     Path = Join-Path $uvDir   'uv.exe'   }
)

$allOk = $true
foreach ($c in $checks) {
    if (Test-Path $c.Path) {
        try {
            $v = (& $c.Path --version 2>&1 | Select-Object -First 1)
            Write-Ok "$($c.Name.PadRight(6)) $v"
        } catch {
            Write-Warn "$($c.Name.PadRight(6)) 存在但无法执行 --version"
            $allOk = $false
        }
    } else {
        Write-Fail "$($c.Name.PadRight(6)) 未找到: $($c.Path)"
        $allOk = $false
    }
}

# Python：确认 py 启动器能找到 3.12（避开 Microsoft Store 存根）
Write-Info ""
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if ($pyCmd) {
    $pyVer = (& py -3.12 --version 2>&1)
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "python $pyVer (通过 py -3.12)"
    } else {
        Write-Warn "py -3.12 不可用；后端需要 Python >= 3.12"
    }
} else {
    Write-Warn "未找到 py 启动器；请确认已安装 Python 3.12+"
}

# ── 完成 ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  工具链安装完成" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  根目录 : $RootDir"
Write-Host "  Node   : $nodeDir"
Write-Host "  uv     : $uvDir"
Write-Host "  缓存   : $cacheDir"
Write-Host "  代码   : $srcDir"
Write-Host ""
Write-Host "  注意：环境变量写在「用户」范围，当前会话不会自动生效。" -ForegroundColor Yellow
Write-Host "        新开的 SSH 会话会读取到。"

# ── PATH 遮蔽检测 ───────────────────────────────────────────────────────────
#
# Windows 的有效 PATH = 系统 PATH + 用户 PATH（系统在前）。
# 因此若系统 PATH 里已有其他 node.exe，即使把它写进用户 PATH 也无法优先命中。
# 本机就属于这种情况：系统 PATH 含 C:\Program Files\nodejs（Node 16）。
#
# 不擅自修改系统 PATH —— 其他程序可能依赖那个 Node。
# 改为在部署脚本中显式前置工具链目录（进程级，确定且可逆）。
$shadowingNode = $null
$systemPath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if ($systemPath) {
    foreach ($entry in ($systemPath -split ';' | Where-Object { $_ -and $_.Trim() })) {
        $candidate = Join-Path $entry.Trim() 'node.exe'
        if (Test-Path $candidate) {
            $v = try { (& $candidate --version 2>&1) } catch { '?' }
            if ($v -ne $NodeVersion) {
                $shadowingNode = [pscustomobject]@{ Path = $candidate; Version = $v }
                break
            }
        }
    }
}

Write-Host ""
if ($shadowingNode) {
    Write-Warn "检测到系统 PATH 中存在其他 Node.js，会遮蔽本工具链："
    Write-Info "  $($shadowingNode.Path)  ($($shadowingNode.Version))"
    Write-Info ""
    Write-Info "裸执行 'node' 会用到上面这个版本。"
    Write-Info "pnpm 不受影响（它调用自身目录下的 node.exe）。"
    Write-Info ""
    Write-Info "当前会话内使用本工具链："
    Write-Host "    `$env:Path = '$nodeDir;$uvDir;' + `$env:Path" -ForegroundColor DarkGray
    Write-Info ""
    Write-Info "若要让所有会话默认生效，需把工具链目录加到「系统」PATH 最前面"
    Write-Info "（脚本不擅自修改：其他程序可能依赖该 Node 版本）"
} else {
    Write-Ok "PATH 中无其他 Node.js 遮蔽本工具链"
}
Write-Host ""

if (-not $allOk) { exit 1 }
