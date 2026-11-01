<#
.SYNOPSIS
    非交互地生成 DeerFlow 的 config.yaml 与 .env。

.DESCRIPTION
    上游的 scripts\setup_wizard.py 是交互式的（含 _is_interactive() 检查），
    不适合无人值守部署。本脚本把「生成配置」这一步固化下来：读同目录的
    config.example.yaml 作为模板，只覆盖必须按部署环境定制的段，其余内容
    原样保留，因此上游新增配置项时无需改本脚本。

    产物（都在 <RootDir>\src 下）：
        config.yaml  —— 由 config.example.yaml 派生，重写 database / auth 两段
        .env         —— 写入 4 个环境变量（路径 + 两个随机密钥）

    ⚠ 为什么 config.yaml 要保留 BOM 而 .env 不能有 BOM
      · 本脚本自身（.ps1）必须 UTF-8 with BOM + CRLF，否则 PowerShell 5.1 会按
        ANSI(GBK) 解码，中文注释与字符串字面量全部损坏（见 Common 模块头注释）。
      · config.yaml 也写成 BOM + CRLF：后端用 open(..., encoding='utf-8') 读取，
        UTF-8 解码器会自行吃掉 BOM，且与仓库里其它文本文件（含 config.example.yaml）
        的编码习惯保持一致。
      · .env 绝对无 BOM：Python 的 dotenv 读到 "﻿KEY" 会当成另一个键名，
        表现为「配置明明写了却读不到」。Write-DotEnv（Common 模块）已保证无 BOM。

.PARAMETER RootDir
    部署根目录，默认取 DeerFlow.Common 的 Get-DeerFlowRoot()（即 D:\deer-flow，
    或环境变量 DEER_FLOW_DEPLOY_ROOT）。给出本参数时会设置
    DEER_FLOW_DEPLOY_ROOT 环境变量（仅当前进程），使 Common 模块的所有
    路径函数（src / data / cache / logs）统一以它为准。

.PARAMETER AllowedEmailDomains
    邮箱域名白名单，覆盖 config.example.yaml 的默认值（当前是 sz-jlc.com）。
    可传多个：-AllowedEmailDomains a.com,b.com

.PARAMETER Force
    目标文件已存在时也重新生成。覆盖前会先把原文件备份为
    <文件名>.bak.<yyyyMMdd-HHmmss>，绝不静默丢数据。

.EXAMPLE
    # 首次生成（已存在则跳过，不改动）
    .\init-config.ps1

.EXAMPLE
    # 指定部署根目录与邮箱白名单
    .\init-config.ps1 -RootDir D:\deer-flow -AllowedEmailDomains sz-jlc.com,example.com

.EXAMPLE
    # 重写配置（旧文件会先备份）
    .\init-config.ps1 -Force

.NOTES
    本机实测环境：Windows 10 20H2 / PowerShell 5.1。
    刻意避开 PowerShell 7 语法（??、三元 ?:、-Parallel 等）。
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$RootDir,

    [Parameter(Mandatory = $false)]
    [string[]]$AllowedEmailDomains,

    [Parameter(Mandatory = $false)]
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false

# ── 引入公共模块 ────────────────────────────────────────────────────────────
#
# -RootDir 必须在 Import 之前写进环境变量：Common 的 Get-DeerFlowRoot() 每次
# 调用都重读该变量（而不是 Import 时算一次），所以顺序反了也不影响，但先设置
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

# ── 辅助函数 ────────────────────────────────────────────────────────────────

function New-SecretHex {
    <#
    .SYNOPSIS
        生成 cryptographically strong 的随机密钥（hex 编码）。

    .DESCRIPTION
        用 System.Security.Cryptography.RandomNumberGenerator —— 这是
        PowerShell 5.1 上即可用的 CSPRNG（RandomNumberGenerator.Create() 在
        5.1 与 7 中行为一致；7 才有的 RandomNumberGenerator.GetBytes(int) 静态
        重载这里不用）。

        为什么用 hex 而不是 base64：
          base64 会产出 + / = 三种字符，虽然对 .env（按第一个 = 切分）和 HTTP
          头都合法，但 hex 完全落在 [0-9a-f] 内，不需要在任何一层转义，也不会被
          某些 dotenv / shell 实现误判成特殊语法。32 字节 → 64 字符，强度不受影响。

    .PARAMETER ByteCount
        随机字节数，默认 32（256 位）。
    #>
    param([int]$ByteCount = 32)

    $bytes = New-Object 'byte[]' $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }

    $sb = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) {
        [void]$sb.Append($b.ToString('x2'))
    }
    return $sb.ToString()
}

function Get-DeerFlowFileHash {
    <#
    .SYNOPSIS
        返回文件的 SHA256（小写十六进制）；文件不存在时返回 $null。

    .DESCRIPTION
        用于「幂等」验证：运行前后对比哈希，就能确认文件没有被改写。
        刻意用 Get-FileHash 而不是比对时间戳——时间戳在 rsync / 复制场景下不可靠。
    #>
    param([string]$Path)

    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash -Path $Path -Algorithm SHA256).Hash.ToLower()
}

function Backup-DeerFlowFile {
    <#
    .SYNOPSIS
        把既有文件备份为 <文件名>.bak.<时间戳>，返回备份路径。

    .DESCRIPTION
        -Force 覆盖前必须先备份：配置里存着管理员上次手改的模型 key 等
        不可再生的信息，静默覆盖的代价太高。

        同一秒内重复备份会撞名，因此在时间戳后缀上再加 -N 保证唯一。
    #>
    param([string]$Path)

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$Path.bak.$stamp"
    $index = 1
    while (Test-Path $backup) {
        $backup = "$Path.bak.$stamp-$index"
        $index++
    }

    Copy-Item -Path $Path -Destination $backup -Force
    return $backup
}

function Remove-YamlSectionText {
    <#
    .SYNOPSIS
        从 YAML 文本中删除一个顶层段，返回剩余文本。

    .DESCRIPTION
        为什么不再引入一个 YAML 库（如 powershell-yaml）：
          · 目标机是内网离线环境，装不了模块；
          · 用官方 PyYAML 重写整份 config.example.yaml 会丢掉 1800 行注释，
            而注释正是这份配置模板的主要价值。
        因此只做最小必要处理：按缩进切掉要覆盖的顶层段，再把新段追加到文件末尾。

        段的边界判定：
          · 段头：列 0 起、形如 "key:" 的行
          · 段体：缩进行 / 空行 / # 注释行
          · 段尾：下一个列 0 起的顶层键
        追加到末尾是安全的——被覆盖的段已被彻底删除，不会出现重复键。

        已知不支持：顶层段的「内联映射」写法（key: {a: 1}）与文档结束符 ---。
        config.example.yaml 不使用这些写法。

    .PARAMETER Text
        原始 YAML 文本。

    .PARAMETER Key
        顶层键名，如 database / auth。

    .PARAMETER Found
        [ref] 布尔，输出是否真的找到了该段。找不到时调用方应报错退出，
        否则追加会在文件里留下重复键。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][ref]$Found
    )

    $Found.Value = $false
    $pattern = '^' + [regex]::Escape($Key) + '\s*:'
    $out = New-Object System.Collections.Generic.List[string]
    $inSection = $false

    foreach ($line in ($Text -split "`r?`n")) {
        if ($inSection) {
            if ($line -match '^[A-Za-z_]') {
                # 下一个顶层键：本段结束，这一行要保留
                $inSection = $false
            } else {
                # 缩进行 / 空行 / 注释都属于被替换掉的段
                continue
            }
        }

        if ($line -match $pattern) {
            $inSection = $true
            $Found.Value = $true
            continue
        }

        $out.Add($line)
    }

    # 去掉尾部空行，拼接结果统一用 CRLF（与仓库其它文本文件一致）
    while ($out.Count -gt 0 -and [string]::IsNullOrWhiteSpace($out[$out.Count - 1])) {
        $out.RemoveAt($out.Count - 1)
    }

    return ($out -join "`r`n")
}

function Get-ExampleAllowedEmailDomains {
    <#
    .SYNOPSIS
        从 config.example.yaml 的 auth 段里读出 allowed_email_domains 默认值。

    .DESCRIPTION
        需求要求「保留示例文件的默认域名」，所以不能把 sz-jlc.com 硬编码在脚本里
        ——上游改了默认值后脚本会悄悄把用户带偏。这里按结构解析（而不是全文正则），
        只在 auth 段内查找，避免误命中注释或别处的同名键。

        同时支持块序列（- a.com）与内联序列（[a.com, b.com]）两种写法。

    .PARAMETER Path
        config.example.yaml 路径。

    .OUTPUTS
        System.String[]，解析失败时返回空数组（由调用方决定是否报错）。
    #>
    param([string]$Path)

    $result = New-Object System.Collections.Generic.List[string]
    $lines = [System.IO.File]::ReadAllLines($Path, [System.Text.Encoding]::UTF8)

    $inAuth = $false
    $inList = $false
    $blockStyle = $false

    foreach ($line in $lines) {
        if (-not $inAuth) {
            if ($line -match '^auth\s*:') { $inAuth = $true }
            continue
        }

        # 顶层键 ⇒ auth 段结束
        if ($line -match '^[A-Za-z_]') { break }

        if ($inList) {
            if ($blockStyle) {
                if ($line -match '^\s+-\s*(.+?)\s*$') {
                    $value = $Matches[1].Trim().Trim('"', "'")
                    if ($value) { $result.Add($value) }
                    continue
                }
                # 列表项之后出现的下一个字段 ⇒ 列表结束
                if ($line -match '^\s*[A-Za-z_]') { $inList = $false }
                # 否则是注释或空行，继续留在列表内
                continue
            }
            $inList = $false
            continue
        }

        if ($line -match '^\s*allowed_email_domains\s*:\s*(.*)$') {
            $inline = $Matches[1].Trim()
            if ($inline) {
                # 内联写法：allowed_email_domains: [a.com, b.com]
                foreach ($item in ($inline.Trim('[', ']') -split ',')) {
                    $value = $item.Trim().Trim('"', "'")
                    if ($value) { $result.Add($value) }
                }
            } else {
                $inList = $true
                $blockStyle = $true
            }
        }
    }

    return $result.ToArray()
}

function Get-ConfigEnvRefs {
    <#
    .SYNOPSIS
        扫描 YAML 文本，返回被「整值引用」的环境变量名（不含 $ 前缀）。

    .DESCRIPTION
        后端的 AppConfig.from_file() 会对整份配置做一次递归的 $VAR 替换，遇到
        os.getenv 返回 None 时直接抛 ValueError 让配置加载失败。也就是说
        config.yaml 里出现一个 .env 没有的 $VAR，Gateway 根本起不来。

        config.example.yaml 恰好就有这种引用：image_generation / image_editing /
        video_generation 三个「可选 provider」段里的凭据字段
        （api_key: $QWEN_IMAGE_API_KEY、Authorization: $OPENAI_IMAGE_AUTHORIZATION 等）。
        后端对其中一部分做了宽容处理（_resolve_optional_api_keys 会把
        image_generation / video_generation 的 api_key 缺省成空串，UI 上显示
        「未配置」），但 image_editing 段用的是 Authorization 字段、也不在那个
        宽容名单里，缺一个变量就会硬失败——这是模板自身的坑，与本次生成的两段无关。

        因此本函数把配置里「整值引用」的变量名扫出来，由调用方在 .env 里补成空串：
        变量存在则 os.getenv 返回 '' 而不是 None，配置可以正常加载，UI 把这些
        provider 标成未配置；运维只要在 .env 里把对应的空值填成真 key，同一个
        $VAR 引用立刻生效，不需要回头改 config.yaml。

        只匹配「值恰好是一个 $VAR」的赋值行（含 "- key: $VAR" 形式），
        注释行整行跳过。因此不会碰 $VAR 出现在字符串中间或注释里的写法——
        那些情况仍需人工处理，脚本会在摘要里把它们漏过去的可能性一并说明。

    .PARAMETER ConfigText
        生成的 config.yaml 全文。

    .OUTPUTS
        System.String[]，去重后的变量名。
    #>
    param([string]$ConfigText)

    $refs = New-Object System.Collections.Generic.List[string]

    foreach ($line in ($ConfigText -split "`r?`n")) {
        $trimmed = $line.TrimStart()
        if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
        if ($trimmed.StartsWith('#')) { continue }

        if ($line -match '^\s*(?:-\s*)?[A-Za-z_][A-Za-z0-9_-]*\s*:\s*\$([A-Za-z_][A-Za-z0-9_]*)\s*$') {
            $refs.Add($Matches[1])
        }
    }

    return @($refs | Select-Object -Unique)
}

function Test-EmailDomainValue {
    <#
    .SYNOPSIS
        校验一个邮箱域名取值是否可用（与后端 auth_config 的约束对齐）。

    .DESCRIPTION
        提前失败胜过让 Gateway 启动时报 Pydantic ValidationError：
        后端要求非空、ASCII DNS 域名、不接受 @ 前缀（会被自动去掉）、
        拒绝国际化域名与 xn-- ACE 前缀。
    #>
    param([string]$Value)

    $candidate = $Value.Trim()
    if ($candidate.StartsWith('@')) { $candidate = $candidate.Substring(1) }
    $candidate = $candidate.ToLowerInvariant()

    if (-not $candidate) { return $null }
    if ($candidate -match '@') { return $null }
    if ($candidate -match '[^\x20-\x7E]') { return $null }          # 非 ASCII（国际化域名）
    if ($candidate -match '(^|\.)xn--') { return $null }            # ACE 前缀
    if ($candidate.Length -gt 253) { return $null }
    foreach ($label in ($candidate -split '\.')) {
        if ($label.Length -lt 1 -or $label.Length -gt 63) { return $null }
        if ($label -notmatch '^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$') { return $null }
    }

    return $candidate
}

# ── 进入主流程 ──────────────────────────────────────────────────────────────

$rootDir  = Get-DeerFlowRoot
$srcDir   = Get-DeerFlowSrcDir
$dataDir  = Get-DeerFlowDataDir
$configPath = Get-DeerFlowConfigPath
$envPath    = Get-DeerFlowEnvPath
$examplePath = Join-Path $srcDir 'config.example.yaml'

Write-Step '生成 DeerFlow 配置'

Write-Info "部署根目录 : $rootDir"
Write-Info "代码目录   : $srcDir"
Write-Info "数据目录   : $dataDir"
Write-Info "config.yaml: $configPath"
Write-Info ".env       : $envPath"

if (-not (Test-Path $examplePath)) {
    Write-Fail "未找到配置模板: $examplePath"
    Write-Info '请先同步代码仓库（scripts\windows-remote\sync-to-windows.sh）。'
    exit 1
}

# ── 幂等检查 ────────────────────────────────────────────────────────────────
#
# 已存在且未指定 -Force 时直接跳过。检查放在最前面：既避免无谓的写入，
# 也保证「已配置好的机器上重复跑 deploy 不会改坏任何东西」。
#
# 用文件哈希而不是存在性做最终判据（见下文的运行前后对比），但这里的
# 跳过判据只看存在性即可 —— 内容不符合预期时应由 -Force 显式重写。

$targets = @(
    [pscustomobject]@{ Name = 'config.yaml'; Path = $configPath },
    [pscustomobject]@{ Name = '.env';        Path = $envPath    }
)

$skipAll = $true
foreach ($t in $targets) {
    if (-not (Test-Path $t.Path)) { $skipAll = $false; break }
}

if ($skipAll -and -not $Force) {
    foreach ($t in $targets) {
        $hash = Get-DeerFlowFileHash -Path $t.Path
        Write-Ok "$($t.Name) 已存在，跳过（$($hash.Substring(0, 12))...）"
    }
    Write-Info ''
    Write-Info '如需重新生成，请加 -Force（旧文件会先备份为 *.bak.<时间戳>）。'
    Write-Info ''
    exit 0
}

# ── 校验参数：邮箱域名白名单 ────────────────────────────────────────────────

$domainSource = '参数 -AllowedEmailDomains'
$domainValues = @()

if ($AllowedEmailDomains -and $AllowedEmailDomains.Count -gt 0) {
    # 允许 PowerShell 习惯的逗号写法：-AllowedEmailDomains a.com,b.com
    foreach ($raw in $AllowedEmailDomains) {
        foreach ($part in ($raw -split ',')) {
            $part = $part.Trim()
            if (-not $part) { continue }
            $normalized = Test-EmailDomainValue -Value $part
            if (-not $normalized) {
                Write-Fail "非法的邮箱域名: '$part'"
                Write-Info '要求：非空、ASCII DNS 域名（不接受 @ 前缀、国际化域名与 xn-- 前缀）。'
                exit 1
            }
            $domainValues += $normalized
        }
    }
} else {
    $domainSource = 'config.example.yaml 的默认值'
    $domainValues = @(Get-ExampleAllowedEmailDomains -Path $examplePath | ForEach-Object {
        Test-EmailDomainValue -Value $_
    } | Where-Object { $_ })
}

$domainValues = @($domainValues | Select-Object -Unique)

if ($domainValues.Count -eq 0) {
    Write-Fail '邮箱域名白名单为空'
    Write-Info "无法从 $examplePath 解析默认值，请用 -AllowedEmailDomains 显式指定。"
    exit 1
}

Write-Info "邮箱域名   : $($domainValues -join ', ')（来源：$domainSource）"

# ── 生成密钥 ────────────────────────────────────────────────────────────────
#
# 一次生成、一次写入：重新运行本脚本（不带 -Force）不会重新生成密钥，
# 因此已登录用户的会话不会因为密钥漂移而失效。

$internalAuthToken = New-SecretHex -ByteCount 32   # 64 hex 字符
$betterAuthSecret  = New-SecretHex -ByteCount 32   # 64 hex 字符

# ── 组装 config.yaml ────────────────────────────────────────────────────────

$exampleText = [System.IO.File]::ReadAllText($examplePath, [System.Text.Encoding]::UTF8)
if (-not $exampleText.Trim()) {
    Write-Fail "配置模板内容为空: $examplePath"
    exit 1
}

$foundDatabase = $false
$body = Remove-YamlSectionText -Text $exampleText -Key 'database' -Found ([ref]$foundDatabase)
if (-not $foundDatabase) {
    Write-Fail 'config.example.yaml 中未找到顶层段 database:'
    Write-Info '模板结构可能已变化，请人工确认后再调整本脚本。'
    exit 1
}

$foundAuth = $false
$body = Remove-YamlSectionText -Text $body -Key 'auth' -Found ([ref]$foundAuth)
if (-not $foundAuth) {
    Write-Fail 'config.example.yaml 中未找到顶层段 auth:'
    Write-Info '模板结构可能已变化，请人工确认后再调整本脚本。'
    exit 1
}

# sqlite_dir 写成正斜杠并加引号：
#   · Python 侧 Path('D:/deer-flow/data') 完全等价于 Path('D:\deer-flow\data')，
#     正斜杠在 Windows 上也是合法分隔符，没有任何解析差异；
#   · 反斜杠在 YAML 双引号串里是转义字符（"D:\deer-flow" 中的 \d 会被当成转义，
#     须写 \\ 或改用单引号），正斜杠没有这个坑；
#   · 不用「反斜杠 + 单引号」是因为单引号串里两个连续单引号才是转义，路径里
#     出现单引号同样会炸。正斜杠是唯一不需要考虑任何转义的写法。
$sqliteDirYaml = ($dataDir -replace '\\', '/')
$firstDomain = $domainValues[0]

$domainYaml = "    # 来源：$domainSource`r`n"
foreach ($domain in $domainValues) {
    $domainYaml += "    - $domain`r`n"
}

# 顺带把模板的 config_version 抄进注释，方便日后判断这份 config.yaml 是
# 从哪一版模板派生的（后端启动时会自行比对版本并提示 make config-upgrade）。
$templateVersion = 'unknown'
if ($exampleText -match '(?m)^config_version:\s*(\d+)\s*$') { $templateVersion = $Matches[1] }

# 为什么 approval_email.enabled 必须是 true（而不是任务描述里的「不启用」）：
#   后端 auth_config.LocalRegistrationConfig 有一条 model_validator：
#       require_admin_approval and not approval_email.enabled  ⇒ ValueError
#   即「需要管理员审批」与「启用审批邮件」在 schema 上是强耦合的，
#   只写 require_admin_approval: true 会让 Gateway 启动时直接校验失败。
#   本部署的目标是「审批走管理后台、不依赖 SMTP」，而审批动作本身
#   （/api/admin/users 的 approve）与邮件投递是解耦的：投递失败只记一条
#   warning 并把 approval_email_status 标成 failed，不会回滚已生效的审批，
#   前端也不展示该字段。所以这里让 approval_email 处于「已启用但必然投递失败」
#   的状态（指向本机回环地址，连接立即被拒绝，不产生外部依赖与等待），
#   既满足 schema，又实现「不依赖 SMTP 完成审批」。
$generatedSections = @"

# ============================================================================
# 以下 database / auth 两段由 scripts\windows\init-config.ps1 生成
# ============================================================================
# 本脚本会先删除 config.example.yaml 中的同名段，再把下面两段追加到文件末尾，
# 其余内容（模型、工具等）原样保留。重新执行 init-config.ps1 -Force 会用当前
# 参数重写这两段；要保留手工修改请改脚本入参，不要直接改这里。
# 派生自 config.example.yaml（config_version: $templateVersion）

# ============================================================================
# Database（部署定制：数据放数据盘，不随代码同步被覆盖）
# ============================================================================
# sqlite 模式下 checkpointer 与业务数据共用一个 deerflow.db（WAL 模式）。
database:
  backend: sqlite
  sqlite_dir: '$sqliteDirYaml'

# ============================================================================
# Auth（部署定制：仅允许公司邮箱注册，注册后需管理员审批）
# ============================================================================
auth:
  allowed_email_domains:
$domainYaml  # 登录时不再校验域名（仅注册与改邮箱时校验），逾期离职账号由管理员在后台禁用。
  enforce_email_domain_on_login: false
  local_registration:
    require_admin_approval: true
    approval_email:
      enabled: true
      # from_address 必须是合法邮箱；SMTP 段是占位配置，投递必然失败（见上方说明），
      # 故意指向本机回环地址：连接会被立即拒绝，不会产生外部网络等待。
      from_address: noreply@$firstDomain
      smtp:
        host: 127.0.0.1
        port: 465
        security: ssl
        username: disabled
        password: disabled
        timeout_seconds: 5
"@

# 末尾统一收一个换行，再补 CRLF；Get-Content / 编辑器读起来更规整
$configContent = $body.TrimEnd([char[]]@("`r", "`n")) + "`r`n" +
                 ($generatedSections -replace "`r?`n", "`r`n") + "`r`n"

# ── 写入 config.yaml ────────────────────────────────────────────────────────

if (Test-Path $configPath) {
    # 只要旧文件存在就先备份——走到这里必然要覆盖它。
    # 刻意不写成「只在 -Force 时备份」：上面跳过分支的判据是「两个文件都存在」，
    # 因此「config.yaml 在、.env 不在」这种半配置状态同样会走到这里，那时若跳过
    # 备份，就把用户手改过的模型 key / base_url 静默丢了。
    $backup = Backup-DeerFlowFile -Path $configPath
    Write-Warn "已备份原 config.yaml → $(Split-Path -Leaf $backup)"
} else {
    $configDir = Split-Path -Parent $configPath
    if ($configDir -and -not (Test-Path $configDir)) {
        New-Item -Path $configDir -ItemType Directory -Force | Out-Null
        Write-Info "创建目录 $configDir"
    }
}

# config.yaml 用 UTF-8 with BOM：后端 open(..., encoding='utf-8') 能正确吃掉 BOM，
# 而带 BOM 能让 Windows 下的编辑器 / PowerShell 正确识别中文注释。
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($configPath, $configContent, $utf8Bom)
Write-Ok "已写入 config.yaml"

# ── 写入 .env ───────────────────────────────────────────────────────────────
#
# 只用 Common 模块的 Write-DotEnv：它保证 UTF-8 无 BOM（Python dotenv 的硬要求）、
# 键按字母序输出（便于 diff 与幂等校验）、值含空白或 # 时自动加引号。
#
# ⚠ 刻意不写入的两个变量：
#   DEER_FLOW_AUTH_DISABLED —— 本部署启用登录认证，写了等于关掉认证。
#   DEER_FLOW_ENV=production —— 它不是「部署模式开关」，只用于 Langfuse 打
#     env: 标签，以及 auth_disabled 的生产保护（生产环境下 DEER_FLOW_AUTH_DISABLED=1
#     会被强制忽略）。本场景两者都不需要，写进去只会造成误解。

$envEntries = @{
    DEER_FLOW_PROJECT_ROOT          = $srcDir
    DEER_FLOW_HOME                  = $dataDir
    DEER_FLOW_INTERNAL_AUTH_TOKEN   = $internalAuthToken
    BETTER_AUTH_SECRET              = $betterAuthSecret
}

# 补上 config.yaml 里被整值引用的 $VAR（见 Get-ConfigEnvRefs 的注释）。
# 已存在的变量保持原值，绝不覆盖——运维手工填进去的真 key 优先级最高。
$placeholderKeys = New-Object System.Collections.Generic.List[string]
foreach ($name in (Get-ConfigEnvRefs -ConfigText $configContent)) {
    if ($envEntries.ContainsKey($name)) { continue }
    if ([Environment]::GetEnvironmentVariable($name, 'Process')) { continue }
    if ($name -match '^(DEER_FLOW_|BETTER_AUTH_)') {
        # 这几个前缀属于「有就能跑、没有就不该跑」的变量，补空值会掩盖真实问题
        Write-Warn "config.yaml 引用了 $name 但未在 .env 中赋值，请人工确认"
        continue
    }
    $envEntries[$name] = ''
    $placeholderKeys.Add($name)
}

if (Test-Path $envPath) {
    # 同 config.yaml：存在即备份，避免半配置状态下的静默覆盖
    $backup = Backup-DeerFlowFile -Path $envPath
    Write-Warn "已备份原 .env → $(Split-Path -Leaf $backup)"
}

Write-DotEnv -Path $envPath -Entries $envEntries
Write-Ok "已写入 .env"

# 数据目录顺手建好：SQLite 落在这里，目录不存在时后端虽会自动创建，
# 但提前建可以尽早暴露「盘符不存在 / 无写权限」这类问题。
if (-not (Test-Path $dataDir)) {
    New-Item -Path $dataDir -ItemType Directory -Force | Out-Null
    Write-Info "创建数据目录 $dataDir"
}

# ── 回读校验 ────────────────────────────────────────────────────────────────
#
# 不信「写成功」这个返回值，直接按后端的读法把文件读回来核对：
#   · .env 用 Read-DotEnv（UTF-8，能识别 BOM），确认 4 个必需键都在、密钥长度达标，
#     且 config.yaml 引用的每个 $VAR 都能在 .env / 进程环境中找到；
#   · config.yaml 检查关键行，确认替换与追加没有产生重复段。

Write-Step '回读校验'

# 顺带确认「config.yaml 里每个整值引用的 $VAR 都能在 .env 中找到」。
# 这条是本脚本唯一能挡住「Gateway 启动即 ValueError」的静态检查，务必跑。
$configText = [System.IO.File]::ReadAllText($configPath, [System.Text.Encoding]::UTF8)
$refs = @(Get-ConfigEnvRefs -ConfigText $configText)
$satisfiedRefs = @()
$missingRefs = @()
foreach ($name in $refs) {
    if ($envEntries.ContainsKey($name)) { $satisfiedRefs += $name }
    elseif ([Environment]::GetEnvironmentVariable($name, 'Process')) { $satisfiedRefs += $name }
    else { $missingRefs += $name }
}

$readBack = Read-DotEnv -Path $envPath
$envOk = $true
foreach ($key in @('DEER_FLOW_PROJECT_ROOT', 'DEER_FLOW_HOME', 'DEER_FLOW_INTERNAL_AUTH_TOKEN', 'BETTER_AUTH_SECRET')) {
    $value = $readBack[$key]
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Fail ".env 缺少 $key"
        $envOk = $false
    }
}

# 密钥长度下限：32 字节 hex = 64 字符。校验的是「> 32」这条底线（防止取值被
# 截断或误写成了占位符），实际长度会在摘要里打印出来供人工复核。
foreach ($key in @('DEER_FLOW_INTERNAL_AUTH_TOKEN', 'BETTER_AUTH_SECRET')) {
    $value = $readBack[$key]
    if (-not $value) { continue }   # 缺失已在上一轮报过，避免重复报错
    if ($value.Length -le 32) {
        Write-Fail "$key 长度异常（$($value.Length) 字符，期望 > 32）"
        $envOk = $false
    }
}

if ($readBack['DEER_FLOW_PROJECT_ROOT'] -ne $srcDir) {
    Write-Fail "DEER_FLOW_PROJECT_ROOT 回读不一致: '$($readBack['DEER_FLOW_PROJECT_ROOT'])'"
    $envOk = $false
}
if ($readBack['DEER_FLOW_HOME'] -ne $dataDir) {
    Write-Fail "DEER_FLOW_HOME 回读不一致: '$($readBack['DEER_FLOW_HOME'])'"
    $envOk = $false
}

# .env 里绝不能出现这两个变量（误写会关掉认证 / 解除生产保护）
foreach ($key in @('DEER_FLOW_AUTH_DISABLED', 'DEER_FLOW_ENV')) {
    if ($readBack.ContainsKey($key)) {
        Write-Fail ".env 不应包含 $key"
        $envOk = $false
    }
}

if ($missingRefs.Count -gt 0) {
    Write-Fail "config.yaml 引用了以下环境变量，但 .env 与进程环境里都没有: $($missingRefs -join ', ')"
    Write-Info '后端加载配置时会对整份 YAML 做 $VAR 替换，缺一个就直接抛 ValueError（Gateway 起不来）。'
    Write-Info '请在 .env 里补上这些变量（可以是空值），或删掉 config.yaml 中对应的 provider 段。'
    $envOk = $false
} elseif ($satisfiedRefs.Count -gt 0) {
    # 注意："`$VAR" 里的反引号是转义符——写成 "\$VAR" 会让 PowerShell 把 $VAR
    # 当成变量插值掉（实测输出成了「引用的 4 个 \」）。
    Write-Ok "config.yaml 引用的 $($satisfiedRefs.Count) 个 `$VAR 均已在 .env 中就位（$($satisfiedRefs -join ', ')）"
}

if ($envOk) { Write-Ok '.env 回读校验通过（4 个必需键齐全，无多余认证开关）' }
else { exit 1 }

$configChecks = @(
    @{ Pattern = '(?m)^database:\s*$';                     Desc = 'database: 顶层段存在' },
    @{ Pattern = '(?m)^\s+backend:\s*sqlite\s*$';          Desc = 'database.backend = sqlite' },
    @{ Pattern = '(?m)^\s+sqlite_dir:\s*''(.+)''\s*$';     Desc = 'database.sqlite_dir 已设置' },
    @{ Pattern = '(?m)^auth:\s*$';                         Desc = 'auth: 顶层段存在' },
    @{ Pattern = '(?m)^\s+require_admin_approval:\s*true\s*$'; Desc = 'require_admin_approval = true' }
)

$configOk = $true
foreach ($check in $configChecks) {
    if ($configText -match $check.Pattern) { Write-Ok $check.Desc }
    else { Write-Fail "$($check.Desc) —— 未在生成的 config.yaml 中找到"; $configOk = $false }
}

# 逐条核对取到的值，而不是只确认「模式能匹配」——匹配到了但值是 D:\data 这种
# 反斜杠写法时，后端仍能解析，但就不是脚本想写的形态了。
if ($configText -match '(?m)^\s+sqlite_dir:\s*''(.+)''\s*$') {
    $writtenDir = $Matches[1]
    if ($writtenDir -eq $sqliteDirYaml) { Write-Ok "sqlite_dir = $writtenDir" }
    else { Write-Fail "sqlite_dir 回读不一致: '$writtenDir' != '$sqliteDirYaml'"; $configOk = $false }
} else {
    Write-Fail 'database.sqlite_dir 未写成带单引号的值'
    $configOk = $false
}

foreach ($domain in $domainValues) {
    if ($configText -match '(?m)^\s+-\s*' + [regex]::Escape($domain) + '\s*$') {
        Write-Ok "allowed_email_domains 包含 $domain"
    } else {
        Write-Fail "allowed_email_domains 缺少 $domain"
        $configOk = $false
    }
}

# 重复顶层键是「删段 + 追加」策略最危险的失败模式（追加没删干净），
# 单独查一遍，避免后端启动时报难以定位的 YAML 重复键问题。
$duplicates = @()
foreach ($key in @('database', 'auth')) {
    $count = ([regex]::Matches($configText, '(?m)^' + $key + ':\s*$')).Count
    if ($count -ne 1) { $duplicates += "$key（出现 $count 次）" }
}
if ($duplicates.Count -gt 0) {
    Write-Fail "config.yaml 存在重复或缺失的顶层段: $($duplicates -join ', ')"
    $configOk = $false
} else {
    Write-Ok '顶层段无重复（database / auth 各一处）'
}

if (-not $configOk) { exit 1 }

# ── 摘要（脱敏） ────────────────────────────────────────────────────────────

function Format-MaskedSecret {
    <#
    .SYNOPSIS
        密钥脱敏显示：只保留前 4 位 + 省略号。
    .DESCRIPTION
        摘要会被贴进工单 / 聊天记录里，完整密钥绝不能出现在输出中。
        前 4 位足够人工比对「重新生成后确实是新的」。
    #>
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return '(空)' }

    # 少于 4 位时不能整串回显——那等于把密钥原样打出来
    if ($Value.Length -le 4) { return '(长度不足 5 位，不展示)' }

    return $Value.Substring(0, 4) + '...' + "（$($Value.Length) 字符）"
}

Write-Step '配置摘要'

# 先取到局部变量再进字符串插值：哈希表索引写在 "$(...)" 里在 5.1 上可用，
# 但拆出来读起来更直白，也避免把 $readBack 的解析细节混进格式化表达式。
$internalAuthMaskedSource = $readBack['DEER_FLOW_INTERNAL_AUTH_TOKEN']
$betterAuthMaskedSource = $readBack['BETTER_AUTH_SECRET']

Write-Host "  config.yaml : $configPath"
Write-Host "    database.backend          : sqlite"
Write-Host "    database.sqlite_dir       : $sqliteDirYaml"
Write-Host "    auth.allowed_email_domains: $($domainValues -join ', ')"
Write-Host "    require_admin_approval    : true（审批在管理后台手动完成）"
Write-Host "    approval_email.enabled    : true（schema 强制；SMTP 指向回环地址，投递失败不影响审批）"
Write-Host ""
Write-Host "  .env        : $envPath"
Write-Host "    DEER_FLOW_PROJECT_ROOT         = $srcDir"
Write-Host "    DEER_FLOW_HOME                 = $dataDir"
Write-Host "    DEER_FLOW_INTERNAL_AUTH_TOKEN  = $(Format-MaskedSecret -Value $internalAuthMaskedSource)"
Write-Host "    BETTER_AUTH_SECRET             = $(Format-MaskedSecret -Value $betterAuthMaskedSource)"
Write-Host "    （未设置 DEER_FLOW_AUTH_DISABLED / DEER_FLOW_ENV —— 本部署启用登录认证）"

if ($placeholderKeys.Count -gt 0) {
    Write-Host ""
    Write-Host "    为让 config.yaml 能加载，下列被 `$VAR 引用的可选 provider 凭据已写成空值：" -ForegroundColor DarkGray
    Write-Host "      $($placeholderKeys -join ', ')" -ForegroundColor DarkGray
    Write-Host "    要用哪个 provider，就把对应变量填成真 key（不必改 config.yaml）；" -ForegroundColor DarkGray
    Write-Host "    用不到的直接留空，前端会显示为未配置。" -ForegroundColor DarkGray
}
Write-Host ""

Write-Ok '配置生成完成'

Write-Host ""
Write-Host "  下一步：确认 config.yaml 的 models 段已填好可用的模型与 API Key，" -ForegroundColor Yellow
Write-Host "          然后在 .env 中补齐模型需要的 *_API_KEY（如 DEEPSEEK_API_KEY）。" -ForegroundColor Yellow
Write-Host "          最后用 python scripts\doctor.py 或后端 get_app_config() 自检一次。" -ForegroundColor Yellow
Write-Host ""

exit 0
