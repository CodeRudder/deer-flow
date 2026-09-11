<#
.SYNOPSIS
    在内网 Windows 主机上安装并配置 OpenSSH Server，用于远程开发与自动化部署。

.DESCRIPTION
    完成以下配置：
      1. 安装 OpenSSH Server 可选功能（已装则跳过）
      2. 设置 sshd 服务为自动启动并立即拉起
      3. 放通防火墙入站端口
      4. 将默认 shell 设为 PowerShell（否则 ssh 登录后进入 cmd）
      5. 写入登录用户的公钥到正确的 authorized_keys 位置，并收紧 ACL
      6. 校验 sshd_config 的管理员 authorized_keys 指向
      7. 自检：服务状态、端口监听、配置可读性

    ⚠ 必须以管理员身份运行。脚本自带 #Requires 检查。

    ⚠ 管理员账户的公钥必须写入 %ProgramData%\ssh\administrators_authorized_keys，
      而不是用户目录下的 .ssh\authorized_keys——写错位置时 sshd 会静默回退到密码
      认证，不会给出任何错误提示。这是最常见的配置失败原因，脚本已代为处理。

.PARAMETER PublicKey
    直接传入公钥字符串，例如 "ssh-ed25519 AAAAC3Nza... user@host"。

.PARAMETER PublicKeyFile
    从文件读取公钥。在 macOS 上可用 scp 传过来，或直接粘贴到一个临时文件。

.PARAMETER Port
    SSH 监听端口，默认 22。

.PARAMETER SkipFirewall
    跳过防火墙规则配置（例如已由组策略统一管理时）。

.PARAMETER SkipKeyAuth
    只安装服务，不配置公钥认证。

.EXAMPLE
    # 在 macOS 上生成专用密钥（推荐，避免复用个人密钥）
    ssh-keygen -t ed25519 -f ~/.ssh/deerflow_win -C "deerflow-win"

    # 把公钥内容复制到 Windows 上执行
    .\install-sshd.ps1 -PublicKey "ssh-ed25519 AAAAC3Nza... deerflow-win"

.EXAMPLE
    .\install-sshd.ps1 -PublicKeyFile C:\Temp\deerflow_win.pub -Port 2222

.EXAMPLE
    # Windows 10 20H2 等无法使用可选功能的系统，走便携版 + 本机 SOCKS 代理
    .\install-sshd.ps1 -Method Portable -Proxy socks5://127.0.0.1:1080 `
        -PublicKey "ssh-ed25519 AAAAC3Nza... deerflow-win"

.NOTES
    Windows 10 1809+ / Server 2019+ 可用系统可选功能；更早版本走便携版即可。

    ⚠ Windows 10 20H2 及更早版本在 WSUS 管辖下无法从 Windows Update 取得
      Feature on Demand 内容，Add-WindowsCapability 会报 0x800F0954
      （更新服务器上找不到适用的可选内容）。这是更新源策略问题，与被安装的
      组件无关。默认的 -Method Auto 会自动回退到便携版；也可直接指定：
          .\install-sshd.ps1 -Method Portable

    便携版来自微软官方仓库 https://github.com/PowerShell/Win32-OpenSSH，
    完全绕开 Windows Update 与 WSUS。

    代理写法：SOCKS5 必须写成 socks5:// 或 socks5h://。
    curl 会把 socks:// 当作 SOCKS4，而 SOCKS4 不支持远程 DNS，访问 GitHub
    时会退回本地 DNS 解析（易被污染）。脚本会自动纠正并提示。
#>

#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$PublicKey,

    [Parameter(Mandatory = $false)]
    [string]$PublicKeyFile,

    [Parameter(Mandatory = $false)]
    [ValidateRange(1, 65535)]
    [int]$Port = 22,

    [Parameter(Mandatory = $false)]
    [switch]$SkipFirewall,

    [Parameter(Mandatory = $false)]
    [switch]$SkipKeyAuth,

    <#
      离线安装：内网主机通常无法访问 Windows Update，Add-WindowsCapability 会失败。
      此时在有网的机器上执行下面命令下载 FOD 包，再拷贝到内网：

        # 在有网的 Windows 上（版本须与目标机一致）
        New-Item -Path C:\Temp\OpenSSH-FOD -ItemType Directory -Force
        # 从 https://www.catalog.update.microsoft.com 搜索 "OpenSSH" 下载对应 .cab，
        # 或直接导出已安装功能的包
        # 目标机以管理员身份执行：
        dism /Online /Add-Capability /CapabilityName:OpenSSH.Server~~~~0.0.1.0 /Source:C:\Temp\OpenSSH-FOD

      下载到的目录路径通过此参数传入，脚本会改用本地源安装。
    #>
    [Parameter(Mandatory = $false)]
    [string]$SourcePath,

    <#
      安装方式：
        Auto     — 先试官方可选功能，失败则自动回退便携版（默认）
        Feature  — 仅用 Windows 可选功能（Feature on Demand）
        Portable — 仅用 GitHub 上的 Win32-OpenSSH 便携版
        Source   — 仅用本地 FOD 离线源（需配合 -SourcePath）

      Windows 10 20H2 及更早版本在 WSUS 管辖下无法从 Windows Update 取得
      FoD 内容，Add-WindowsCapability 会报 0x800F0954（找不到适用的可选内容），
      此时必须用 Portable。
    #>
    [Parameter(Mandatory = $false)]
    [ValidateSet('Auto', 'Feature', 'Portable', 'Source')]
    [string]$Method = 'Auto',

    <#
      便携版版本号，默认 v9.5.0.0p1-Beta。
      注意：Win32-OpenSSH 的所有 release 都带 Beta/Preview 后缀——这是微软官方
      仓库的一贯命名习惯，并非质量未达标，广泛用于生产环境。
      可用版本见 https://github.com/PowerShell/Win32-OpenSSH/releases
    #>
    [Parameter(Mandatory = $false)]
    [string]$OpenSshVersion = 'v9.5.0.0p1-Beta',

    <#
      本地已下载的 OpenSSH-Win64.zip 路径。指定后跳过下载直接解压安装，
      适用于目标机无法访问 github.com 的情况。
    #>
    [Parameter(Mandatory = $false)]
    [string]$PortableZipPath,

    <#
      下载便携版时使用的代理，例如 socks5://127.0.0.1:1080。

      注意：必须写成 socks5:// 或 socks5h://。
      写成 socks:// 时 curl 会按 SOCKS4 处理，而 SOCKS4 不支持远程 DNS，
      解析 github.com 会走本地 DNS（易被污染）——因此脚本会自动把
      socks:// 归一化为 socks5h://，并在检测到 socks4 时给出警告。

      本参数同时作用于原生命令的 git/curl。
    #>
    [Parameter(Mandatory = $false)]
    [string]$Proxy
)

$ErrorActionPreference = 'Stop'

# PowerShell 7.3+ 会把原生命令的 stderr 也当作 terminating error，
# 导致 icacls / sshd -t 的正常警告输出中断脚本。显式关闭该行为。
# 该变量在 PowerShell 5.1 中不存在，赋值无副作用。
$PSNativeCommandUseErrorActionPreference = $false

# ── 输出辅助 ────────────────────────────────────────────────────────────────

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "  [ OK ] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [WARN] $Message" -ForegroundColor Yellow
}

function Write-Fail {
    param([string]$Message)
    Write-Host "  [FAIL] $Message" -ForegroundColor Red
}

function Write-Info {
    param([string]$Message)
    Write-Host "         $Message" -ForegroundColor DarkGray
}

# ── 前置检查 ────────────────────────────────────────────────────────────────

Write-Step "检查运行环境"

$osInfo = Get-CimInstance -ClassName Win32_OperatingSystem
Write-Ok "操作系统: $($osInfo.Caption) (Build $($osInfo.BuildNumber))"

$currentUser = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdminUser = (New-Object Security.Principal.WindowsPrincipal($currentUser)).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdminUser) {
    Write-Fail "当前用户不是管理员。请以管理员身份重新打开 PowerShell。"
    exit 1
}
Write-Ok "当前用户: $($currentUser.Name)（管理员）"

# 预计算登录名（DOMAIN\user 或 MACHINE\user 去掉域前缀）。
# 不写成 $($name.Split('\')[-1]) 内联在双引号串里，是为了避免嵌套引号
# 与外层字符串定界符产生解析歧义，同时便于复用。
# 用 [char]92 表示反斜杠，杜绝转义歧义。
$backslash = [char]92
$loginName = $currentUser.Name
if ($loginName -like "*$backslash*") {
    $loginName = $loginName.Split($backslash)[-1]
}

# ── 1. 安装 OpenSSH Server ─────────────────────────────────────────────────

Write-Step "安装 OpenSSH Server"

# 便携版安装目录与安装脚本
$portableInstallDir = Join-Path $env:ProgramFiles 'OpenSSH'

<#
  用 Windows 可选功能（Feature on Demand）安装。
  返回 $true 表示安装成功。

  已知失败：Windows 10 20H2 及更早版本在 WSUS 管辖下无法从 Windows Update
  取到 FoD 内容，会报 0x800F0954（CBS_E_NO_OPTIONAL_CONTENT_FOUND_ON_UPDATE_SERVERS）。
  该错误由更新源策略造成，与被安装的组件本身无关，此时应改用便携版。
#>
function Install-OpenSshViaFeature {
    param([string]$LocalSourcePath)

    $capability = $null
    try {
        $capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        Write-Warn "无法查询 Windows 可选功能：$($_.Exception.Message)"
        return $false
    }

    if ($null -eq $capability) {
        Write-Warn "本系统未提供 OpenSSH Server 可选功能"
        return $false
    }

    if ($capability.State -eq 'Installed') {
        Write-Ok "OpenSSH Server 可选功能已安装"
        return $true
    }

    if ($LocalSourcePath) {
        if (-not (Test-Path $LocalSourcePath)) {
            Write-Fail "离线源目录不存在: $LocalSourcePath"
            return $false
        }
        Write-Info "使用离线源安装: $LocalSourcePath"
        $dismArgs = @(
            '/Online', '/Add-Capability',
            '/CapabilityName:OpenSSH.Server~~~~0.0.1.0',
            "/Source:$LocalSourcePath"
        )
        $dismOutput = & dism.exe @dismArgs 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "OpenSSH Server 已从离线源安装"
            return $true
        }
        Write-Fail "离线安装失败 (dism 返回码 $LASTEXITCODE)"
        Write-Info ($dismOutput | Select-Object -Last 6)
        Write-Info "请确认离线包版本与本机 Windows 版本完全一致。"
        return $false
    }

    Write-Info "当前状态: $($capability.State)，开始安装（可能需要几分钟）..."

    $failureDetail = ''
    try {
        Add-WindowsCapability -Online -Name $capability.Name -ErrorAction Stop | Out-Null
    } catch {
        $failureDetail = $_.Exception.Message
        Write-Warn "在线安装失败: $failureDetail"
    }

    # Add-WindowsCapability 有时不抛异常但也没有实际安装，必须复查真实状态
    $recheck = Get-WindowsCapability -Online -Name 'OpenSSH.Server*' -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($recheck -and $recheck.State -eq 'Installed') {
        Write-Ok "OpenSSH Server 安装完成"
        return $true
    }

    if ($failureDetail -match '0x800f0954') {
        Write-Info "错误 0x800F0954：更新服务器上找不到适用的可选内容。"
        Write-Info "该组件以 Feature on Demand 形式发布，其内容只能从 Microsoft Update 获取，"
        Write-Info "而 Windows 10 20H2 及更早版本无法通过 WSUS 取得 FoD 内容。"
        Write-Info "这是更新源策略问题，与组件本身无关——改用便携版即可绕开。"
    }

    return $false
}

<#
  用 GitHub 上的微软官方 Win32-OpenSSH 便携版安装。
  完全绕开 Windows Update / WSUS，适用于任何 Windows 版本。
  返回 $true 表示安装成功。
#>
# 把用户传入的代理地址归一化为 curl 能正确处理的方案名。
#
# curl 识别的 SOCKS 方案只有 socks4:// socks4a:// socks5:// socks5h://。
# 裸写 socks:// 时 curl 按 SOCKS4 处理，而 SOCKS4 不支持远程 DNS——
# github.com 会在本地解析，被污染时直接失败。
# socks5h:// 的 h 表示由代理解析域名，是访问 GitHub 的正确选择。
function Resolve-ProxyUrl {
    param([string]$ProxyUrl)

    if ([string]::IsNullOrWhiteSpace($ProxyUrl)) {
        return $null
    }

    $value = $ProxyUrl.Trim()

    if ($value -match '^socks://') {
        $normalized = $value -replace '^socks://', 'socks5h://'
        Write-Warn "代理方案 socks:// 会被 curl 当作 SOCKS4（不支持远程 DNS）"
        Write-Info "已自动改为 $normalized"
        return $normalized
    }

    if ($value -match '^socks4://|^socks4a://') {
        Write-Warn "SOCKS4 不支持远程 DNS，解析 github.com 可能失败"
        Write-Info "建议改用 socks5h://"
    }

    return $value
}

function Install-OpenSshPortable {
    param(
        [string]$Version,
        [string]$LocalZipPath,
        [string]$ProxyUrl
    )

    # 已装过便携版则复用
    $existingInstall = Join-Path $portableInstallDir 'install-sshd.ps1'
    if (Test-Path $existingInstall) {
        Write-Ok "检测到已安装的便携版: $portableInstallDir"
        return $true
    }

    $tempRoot = Join-Path $env:TEMP ("openssh-setup-" + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -Path $tempRoot -ItemType Directory -Force | Out-Null

    try {
        $zipPath = $null

        if ($LocalZipPath) {
            if (-not (Test-Path $LocalZipPath)) {
                Write-Fail "指定的便携版压缩包不存在: $LocalZipPath"
                return $false
            }
            $zipPath = $LocalZipPath
            Write-Info "使用本地压缩包: $LocalZipPath"
        } else {
            $url = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/$Version/OpenSSH-Win64.zip"
            $zipPath = Join-Path $tempRoot 'OpenSSH-Win64.zip'

            Write-Info "下载便携版 $Version ..."
            Write-Info $url
            if ($ProxyUrl) {
                Write-Info "经代理: $ProxyUrl"
            }

            # 用 curl.exe 而不是 Invoke-WebRequest：
            # Invoke-WebRequest 的 -Proxy 只支持 HTTP/HTTPS，不支持 SOCKS。
            # curl.exe 自 Windows 10 1803 起随系统内置。
            $curlExe = Join-Path $env:SystemRoot 'System32\curl.exe'
            if (-not (Test-Path $curlExe)) {
                Write-Fail "未找到 curl.exe（$curlExe）。本系统可能低于 Windows 10 1803。"
                Write-Info "请手动下载后通过 -PortableZipPath 指定：$url"
                return $false
            }

            $curlArgs = @(
                '--fail',                     # HTTP 错误码视为失败，避免把错误页当压缩包
                '--location',                 # GitHub release 会 302 到 CDN
                '--silent',
                '--show-error',
                '--connect-timeout', '20',
                '--max-time', '300',
                '--output', $zipPath
            )
            if ($ProxyUrl) {
                $curlArgs += @('--proxy', $ProxyUrl)
            }
            $curlArgs += $url

            # 2>&1 与 --show-error 配合：把 curl 的错误文本捕获下来供诊断
            $curlOutput = & $curlExe @curlArgs 2>&1
            $curlExit = $LASTEXITCODE

            if ($curlExit -ne 0) {
                Write-Fail "下载失败 (curl 返回码 $curlExit)"
                if ($curlOutput) {
                    Write-Info ($curlOutput | Select-Object -Last 4)
                }
                Write-Host ""
                Write-Host "  诊断建议：" -ForegroundColor Yellow
                if ($ProxyUrl) {
                    Write-Host "    - 确认代理正在运行且端口正确: $ProxyUrl"
                    Write-Host "    - SOCKS5 需用 socks5:// 或 socks5h://，不要写 socks://"
                } else {
                    Write-Host "    - 若需经代理访问 GitHub，加 -Proxy socks5://127.0.0.1:1080"
                }
                Write-Host "    - 或手动下载后指定: -PortableZipPath C:\Temp\OpenSSH-Win64.zip"
                Write-Host "      $url"
                Write-Host ""
                return $false
            }

            # curl --fail 已保证非 2xx 返回非零，但输出被截断时仍需复查文件
            if (-not (Test-Path $zipPath) -or (Get-Item $zipPath).Length -eq 0) {
                Write-Fail "下载后文件为空: $zipPath"
                return $false
            }

            $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
            Write-Ok "下载完成 ($sizeMb MB)"
        }

        Write-Info "解压到 $portableInstallDir ..."
        Expand-Archive -Path $zipPath -DestinationPath $tempRoot -Force

        # ZIP 解压后顶层目录名随版本变化（OpenSSH-Win64 / OpenSSH-Win64-v9.5...），
        # 因此按特征文件定位而不是硬编码目录名。
        $extractedDir = Get-ChildItem -Path $tempRoot -Directory |
            Where-Object { Test-Path (Join-Path $_.FullName 'install-sshd.ps1') } |
            Select-Object -First 1

        if (-not $extractedDir) {
            Write-Fail "解压后未找到 install-sshd.ps1，压缩包结构可能已变化。"
            Write-Info "请检查 $tempRoot 内容。"
            return $false
        }

        if (Test-Path $portableInstallDir) {
            Write-Warn "$portableInstallDir 已存在，将覆盖其中的 OpenSSH 文件"
        }
        New-Item -Path $portableInstallDir -ItemType Directory -Force | Out-Null
        Copy-Item -Path (Join-Path $extractedDir.FullName '*') -Destination $portableInstallDir -Recurse -Force
        Write-Ok "文件已释放到 $portableInstallDir"

        # 卸载旧服务（若有）以便干净重装
        $oldService = Get-Service -Name sshd -ErrorAction SilentlyContinue
        if ($oldService) {
            Write-Info "检测到已有 sshd 服务，先移除以便重新注册"
            Stop-Service -Name sshd -Force -ErrorAction SilentlyContinue
            & sc.exe delete sshd | Out-Null
            Start-Sleep -Seconds 2
        }

        Write-Info "注册 sshd 服务..."
        $installScript = Join-Path $portableInstallDir 'install-sshd.ps1'
        $installOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installScript 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-Fail "install-sshd.ps1 执行失败 (返回码 $LASTEXITCODE)"
            Write-Info ($installOutput | Select-Object -Last 8)
            return $false
        }

        Write-Ok "便携版 OpenSSH 安装完成"
        return $true
    } finally {
        Remove-Item -Path $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$alreadyInstalled = Get-Service -Name sshd -ErrorAction SilentlyContinue
if ($alreadyInstalled) {
    Write-Ok "sshd 服务已存在（$($alreadyInstalled.Status)），跳过安装"
} else {
    $installed = $false

    # 归一化一次，供所有便携版安装分支复用
    $resolvedProxy = Resolve-ProxyUrl -ProxyUrl $Proxy

    switch ($Method) {
        'Feature' {
            $installed = Install-OpenSshViaFeature -LocalSourcePath $SourcePath
        }
        'Portable' {
            $installed = Install-OpenSshPortable -Version $OpenSshVersion `
                -LocalZipPath $PortableZipPath -ProxyUrl $resolvedProxy
        }
        'Source' {
            if (-not $SourcePath) {
                Write-Fail "-Method Source 需要同时指定 -SourcePath"
                exit 1
            }
            $installed = Install-OpenSshViaFeature -LocalSourcePath $SourcePath
        }
        'Auto' {
            if ($SourcePath) {
                $installed = Install-OpenSshViaFeature -LocalSourcePath $SourcePath
            } else {
                $installed = Install-OpenSshViaFeature
                if (-not $installed) {
                    Write-Warn "可选功能方式不可用，自动回退到便携版安装"
                }
            }
            if (-not $installed) {
                $installed = Install-OpenSshPortable -Version $OpenSshVersion `
                    -LocalZipPath $PortableZipPath -ProxyUrl $resolvedProxy
            }
        }
    }

    if (-not $installed) {
        Write-Fail "OpenSSH Server 安装失败"
        Write-Host ""
        Write-Host "  可尝试的方式：" -ForegroundColor Yellow
        Write-Host "    1. 便携版（推荐，绕开 Windows Update）："
        Write-Host "       .\install-sshd.ps1 -Method Portable"
        Write-Host "    2. 手动下载后指定压缩包："
        Write-Host "       https://github.com/PowerShell/Win32-OpenSSH/releases"
        Write-Host "       .\install-sshd.ps1 -Method Portable -PortableZipPath C:\Temp\OpenSSH-Win64.zip"
        Write-Host "    3. 用匹配版本的 FOD 离线源："
        Write-Host "       .\install-sshd.ps1 -Method Source -SourcePath C:\Temp\OpenSSH-FOD"
        Write-Host ""
        exit 1
    }
}

# ── 2. 服务自启动并拉起 ─────────────────────────────────────────────────────

Write-Step "配置 sshd 服务"

try {
    Set-Service -Name sshd -StartupType Automatic -ErrorAction Stop
    Write-Ok "启动类型已设为「自动」"
} catch {
    Write-Fail "设置启动类型失败: $($_.Exception.Message)"
    exit 1
}

$sshdService = Get-Service -Name sshd
if ($sshdService.Status -ne 'Running') {
    try {
        Start-Service -Name sshd -ErrorAction Stop
        Write-Ok "sshd 服务已启动"
    } catch {
        Write-Fail "启动 sshd 失败: $($_.Exception.Message)"
        Write-Info "排查: Get-EventLog -LogName Application -Source sshd -Newest 10"
        exit 1
    }
} else {
    Write-Ok "sshd 服务已在运行"
}

# ── 3. 防火墙 ───────────────────────────────────────────────────────────────

Write-Step "配置防火墙"

if ($SkipFirewall) {
    Write-Warn "已跳过（-SkipFirewall）"
    Write-Info "请确认 $Port 端口已由其他方式放通，否则远程无法连接。"
} else {
    $ruleName = "OpenSSH Server (sshd) - Port $Port"
    $existingRules = @(Get-NetFirewallRule -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq $ruleName })

    if ($existingRules.Count -gt 0) {
        Write-Ok "防火墙规则已存在: $ruleName"
    } else {
        try {
            # -RemoteAddress LocalSubnet: 仅允许同网段访问，避免暴露到不可信网络。
            # 若需更严格，改为具体网段如 -RemoteAddress 192.168.1.0/24。
            New-NetFirewallRule `
                -DisplayName $ruleName `
                -Direction Inbound `
                -Protocol TCP `
                -LocalPort $Port `
                -Action Allow `
                -RemoteAddress LocalSubnet `
                -Profile Any `
                -ErrorAction Stop | Out-Null
            Write-Ok "已放通入站 TCP $Port（限本网段）"
        } catch {
            Write-Fail "创建防火墙规则失败: $($_.Exception.Message)"
            Write-Info "可手动执行: New-NetFirewallRule -DisplayName '$ruleName' -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow"
            exit 1
        }
    }
}

# ── 4. 默认 shell 设为 PowerShell ────────────────────────────────────────────

Write-Step "设置默认 shell"

# 不设置时 ssh 登录会进入 cmd.exe，导致 .ps1 与 PowerShell 语法无法直接执行。
$openSshRegPath = 'HKLM:\SOFTWARE\OpenSSH'
$powershellPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

try {
    if (-not (Test-Path $openSshRegPath)) {
        New-Item -Path $openSshRegPath -Force | Out-Null
    }

    $currentShell = (Get-ItemProperty -Path $openSshRegPath -Name DefaultShell -ErrorAction SilentlyContinue).DefaultShell

    if ($currentShell -eq $powershellPath) {
        Write-Ok "默认 shell 已是 PowerShell"
    } else {
        New-ItemProperty `
            -Path $openSshRegPath `
            -Name DefaultShell `
            -Value $powershellPath `
            -PropertyType String `
            -Force | Out-Null
        Write-Ok "默认 shell 已设为 PowerShell"
        if ($currentShell) {
            Write-Info "原值: $currentShell"
        }
    }
} catch {
    Write-Warn "设置默认 shell 失败: $($_.Exception.Message)"
    Write-Info "影响：ssh 登录后进入 cmd.exe，需手动输入 powershell 切换。"
}

# ── 5. 配置公钥认证 ─────────────────────────────────────────────────────────

Write-Step "配置公钥认证"

if ($SkipKeyAuth) {
    Write-Warn "已跳过（-SkipKeyAuth）"
} else {
    # 解析公钥来源
    $keyText = $null

    if ($PublicKeyFile) {
        if (-not (Test-Path $PublicKeyFile)) {
            Write-Fail "公钥文件不存在: $PublicKeyFile"
            exit 1
        }
        $keyText = (Get-Content -Path $PublicKeyFile -Raw).Trim()
        Write-Info "已从文件读取公钥: $PublicKeyFile"
    } elseif ($PublicKey) {
        $keyText = $PublicKey.Trim()
    }

    # 处理多行输入（粘贴时可能带换行）与 CRLF
    if ($keyText) {
        $keyText = ($keyText -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1).Trim()
    }

    if (-not $keyText) {
        Write-Warn "未提供公钥（-PublicKey / -PublicKeyFile 均为空）"
        Write-Info "sshd 已安装，但仍需密码认证才能登录。"
        Write-Info "推荐做法：在 macOS 上执行"
        Write-Info "  ssh-keygen -t ed25519 -f ~/.ssh/deerflow_win -C `"deerflow-win`""
        Write-Info "然后重新运行本脚本并传入 -PublicKey 或 -PublicKeyFile。"
    } else {
        # 校验公钥格式，避免写入垃圾内容后 sshd 静默拒绝
        $validPrefixes = @('ssh-ed25519', 'ssh-rsa', 'ecdsa-sha2-nistp256', 'ecdsa-sha2-nistp384', 'ecdsa-sha2-nistp521', 'ssh-dss')
        $keyPrefix = ($keyText -split '\s+')[0]

        if ($validPrefixes -notcontains $keyPrefix) {
            Write-Fail "公钥格式无法识别，开头是: '$keyPrefix'"
            Write-Info "期望以 ssh-ed25519 / ssh-rsa / ecdsa-sha2-* 开头。"
            Write-Info "请确认传入的是 .pub 公钥文件内容，而非私钥。"
            exit 1
        }

        # 判断目标用户是否为管理员：决定 authorized_keys 的落盘位置
        # 管理员必须用 %ProgramData%\ssh\administrators_authorized_keys
        $sshDir = Join-Path $env:ProgramData 'ssh'
        if (-not (Test-Path $sshDir)) {
            New-Item -Path $sshDir -ItemType Directory -Force | Out-Null
        }
        $authKeysPath = Join-Path $sshDir 'administrators_authorized_keys'

        # 已有内容则追加，避免覆盖其他管理员的既有密钥
        $existingKeys = @()
        if (Test-Path $authKeysPath) {
            $existingKeys = @((Get-Content -Path $authKeysPath) | Where-Object { $_.Trim() })
        }

        $alreadyPresent = $false
        foreach ($existing in $existingKeys) {
            $existingLine = ($existing -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
            if ($existingLine -and ($existingLine.Trim() -eq $keyText)) {
                $alreadyPresent = $true
                break
            }
        }

        if ($alreadyPresent) {
            Write-Ok "该公钥已存在，无需重复写入"
        } else {
            $allKeys = @($existingKeys) + @($keyText)
            # 以 UTF-8 无 BOM + LF 写入：BOM 会让 sshd 解析失败
            $fileContent = ($allKeys -join "`n") + "`n"
            $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($authKeysPath, $fileContent, $utf8NoBom)
            Write-Ok "公钥已写入: $authKeysPath"
            if ($existingKeys.Count -gt 0) {
                Write-Info "保留原有 $($existingKeys.Count) 个公钥"
            }
        }

        # ACL 必须是 Administrators + SYSTEM 且禁用继承。
        # 用 SID 而非名称，避免非英文系统上「Administrators」本地化导致命令失败。
        try {
            $icaclsOutput = icacls.exe $authKeysPath /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' 2>&1
            if ($LASTEXITCODE -ne 0) {
                Write-Warn "icacls 返回码 ${LASTEXITCODE}: $icaclsOutput"
            } else {
                Write-Ok "文件权限已收紧（仅 Administrators 与 SYSTEM）"
            }
        } catch {
            Write-Warn "设置 ACL 失败: $($_.Exception.Message)"
            Write-Info "ACL 不正确时 sshd 会静默拒绝密钥认证，务必手动确认："
            Write-Info "  icacls `"$authKeysPath`""
        }
    }
}

# ── 6. 校验 sshd_config ─────────────────────────────────────────────────────

Write-Step "校验 sshd_config"

$sshdConfigPath = Join-Path $env:ProgramData 'ssh\sshd_config'

if (-not (Test-Path $sshdConfigPath)) {
    Write-Warn "未找到 sshd_config: $sshdConfigPath"
} else {
    $configContent = Get-Content -Path $sshdConfigPath -Raw

    # 标准安装会附带该 Match 块，指向管理员的 authorized_keys。
    # 缺失时管理员的公钥认证会失败，且不会有明显报错。
    if ($configContent -match 'Match\s+Group\s+administrators') {
        if ($configContent -match 'administrators_authorized_keys') {
            Write-Ok "管理员 authorized_keys 指向正确"
        } else {
            Write-Warn "存在 Match Group administrators 块，但未指向 administrators_authorized_keys"
            Write-Info "需在 $sshdConfigPath 中确认该块的 AuthorizedKeysFile 设置。"
        }
    } else {
        Write-Warn "sshd_config 中缺少 Match Group administrators 块"
        Write-Info "管理员账户的公钥认证可能不生效。可在该文件中追加："
        Write-Info "  Match Group administrators"
        Write-Info "         AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys"
    }

    if ($configContent -match '(?m)^\s*Port\s+(\d+)') {
        $configPort = [int]$Matches[1]
        if ($configPort -ne $Port) {
            Write-Warn "sshd_config 中 Port=$configPort，与本次指定的 $Port 不一致"
            Write-Info "实际生效的是 sshd_config 的值。如需改端口，请一并修改该文件后重启 sshd。"
        } else {
            Write-Ok "监听端口配置一致: $Port"
        }
    }

    # 校验配置文件语法
    $sshdExe = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'
    if (Test-Path $sshdExe) {
        $testOutput = & $sshdExe -t 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "配置语法校验通过"
        } else {
            Write-Warn "配置语法校验未通过: $testOutput"
        }
    }
}

# ── 7. 自检 ─────────────────────────────────────────────────────────────────

Write-Step "自检"

$sshdService = Get-Service -Name sshd -ErrorAction SilentlyContinue
if ($sshdService -and $sshdService.Status -eq 'Running') {
    Write-Ok "服务运行中 (StartType: $($sshdService.StartType))"
} else {
    Write-Fail "服务未运行"
}

$listening = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
if ($listening.Count -gt 0) {
    $bindings = ($listening | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique) -join ', '
    Write-Ok "端口 $Port 正在监听 ($bindings)"
} else {
    Write-Fail "端口 $Port 未在监听"
    Write-Info "排查: Get-Service sshd; Get-EventLog -LogName Application -Source sshd -Newest 20"
}

# ── 完成 ────────────────────────────────────────────────────────────────────

$localAddresses = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress)
$primaryIp = if ($localAddresses.Count -gt 0) { $localAddresses[0] } else { '<本机IP>' }

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  OpenSSH Server 配置完成" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  本机 IP:  $($localAddresses -join ', ')"
Write-Host "  SSH 端口: $Port"
Write-Host ""
Write-Host "  在 macOS 上测试连接：" -ForegroundColor White
if (-not $SkipKeyAuth -and -not [string]::IsNullOrWhiteSpace($keyText)) {
    Write-Host "    ssh -i ~/.ssh/deerflow_win -p $Port ${loginName}@$primaryIp"
} else {
    Write-Host "    ssh -p $Port ${loginName}@$primaryIp"
}
Write-Host ""
Write-Host "  验证通过后，把连接信息写入 ~/.ssh/config 便于日常使用：" -ForegroundColor DarkGray
Write-Host "    Host deerflow-win" -ForegroundColor DarkGray
Write-Host "      HostName $primaryIp" -ForegroundColor DarkGray
Write-Host "      User $loginName" -ForegroundColor DarkGray
Write-Host "      Port $Port" -ForegroundColor DarkGray
Write-Host "      IdentityFile ~/.ssh/deerflow_win" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  安全提示：" -ForegroundColor Yellow
Write-Host "    - 防火墙规则当前仅允许本网段访问。"
Write-Host "    - 建议确认公钥认证可用后，在 sshd_config 中设置 PasswordAuthentication no。"
Write-Host "    - 修改 sshd_config 后需 Restart-Service sshd 生效。"
Write-Host ""
