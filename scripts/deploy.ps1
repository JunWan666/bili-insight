[CmdletBinding()]
param(
    [ValidateSet("Menu", "Deploy", "Update", "Restart", "Status", "Logs", "Uninstall", "Purge", "SelfTest", "Help")]
    [string]$Action = "Menu",
    [string]$DeployDir = $(if ($env:BILI_INSIGHT_DIR) { $env:BILI_INSIGHT_DIR } else { Join-Path $HOME "bili-insight" }),
    [string]$HostAddress = $(if ($env:BILI_INSIGHT_HOST) { $env:BILI_INSIGHT_HOST } else { "127.0.0.1" }),
    [ValidateRange(1, 65535)]
    [int]$Port = $(if ($env:BILI_INSIGHT_PORT) { [int]$env:BILI_INSIGHT_PORT } else { 8080 }),
    [string]$Version = $(if ($env:BILI_INSIGHT_VERSION) { $env:BILI_INSIGHT_VERSION } else { "latest" }),
    [ValidateSet("auto", "image", "source")]
    [string]$Mode = $(if ($env:BILI_INSIGHT_MODE) { $env:BILI_INSIGHT_MODE } else { "auto" }),
    [ValidateSet("all", "backend", "frontend")]
    [string]$LogTarget = "all"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$script:Repository = "JunWan666/bili-insight"
$script:DockerPath = $null
$script:ActiveStage = $null
$script:HostExplicit = $PSBoundParameters.ContainsKey("HostAddress") -or (Test-Path Env:BILI_INSIGHT_HOST)
$script:PortExplicit = $PSBoundParameters.ContainsKey("Port") -or (Test-Path Env:BILI_INSIGHT_PORT)

function Write-Title {
    param([string]$Text)
    Write-Host ""
    Write-Host $Text -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Green
}

function Write-InfoText {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Blue
}

function Write-WarningText {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Yellow
}

function Write-ErrorText {
    param([string]$Text)
    Write-Host $Text -ForegroundColor Red
}

function Show-Banner {
    Write-Host @"
  ____  _ _ _   ___           _       _     _
 | __ )(_) (_) |_ _|_ __  ___(_) __ _| |__ | |_
 |  _ \| | | |  | || '_ \/ __| |/ _`` | '_ \| __|
 | |_) | | | |  | || | | \__ \ | (_| | | | | |_
 |____/|_|_|_| |___|_| |_|___/_|\__, |_| |_|\__|
                                 |___/
"@ -ForegroundColor Cyan
    Write-Host "  Bili Insight Docker deployment manager" -ForegroundColor DarkGray
    Write-Host ""
}

function Show-Help {
    @"
用法：
  .\deploy.ps1
  .\deploy.ps1 -Action Deploy [-DeployDir PATH] [-HostAddress IPV4] [-Port 8080]
  .\deploy.ps1 -Action Update [-Version latest] [-Mode auto|image|source]
  .\deploy.ps1 -Action Logs [-LogTarget all|backend|frontend]

操作：
  Menu         打开中文管理菜单
  Deploy       部署
  Update       更新
  Restart      重启服务
  Status       查看状态和健康检查
  Logs         跟踪日志
  Uninstall    卸载容器但保留数据卷和部署文件
  Purge        彻底删除容器、数据卷和部署目录
  SelfTest     执行脚本内置自检
  Help         显示帮助

部署模式：
  auto         优先拉取 GHCR，失败时自动回退到同版本源码构建
  image        只允许使用 GHCR 镜像
  source       从公开 Release 源码构建
"@
}

function Get-DockerCliPath {
    if ($script:DockerPath) {
        return $script:DockerPath
    }
    $command = Get-Command docker -ErrorAction SilentlyContinue
    if ($command) {
        $script:DockerPath = $command.Source
        return $script:DockerPath
    }
    $candidate = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $candidate) {
        $script:DockerPath = $candidate
        return $script:DockerPath
    }
    throw "没有找到 docker.exe，请先安装并启动 Docker Desktop。"
}

function Invoke-Docker {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure,
        [switch]$Quiet
    )
    $docker = Get-DockerCliPath
    $previousErrorAction = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            $null = & $docker @Arguments 2>&1
        }
        else {
            & $docker @Arguments 2>&1 | ForEach-Object {
                if ($_ -is [System.Management.Automation.ErrorRecord]) {
                    Write-Host $_.Exception.Message
                }
                else {
                    Write-Host ([string]$_)
                }
            }
        }
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "docker 命令执行失败，退出码：$exitCode"
    }
    return $exitCode
}

function Test-Requirements {
    $null = Get-DockerCliPath
    $composeExit = Invoke-Docker -Arguments @("compose", "version") -AllowFailure -Quiet
    if ($composeExit -ne 0) {
        throw "缺少 Docker Compose v2。"
    }
    $infoExit = Invoke-Docker -Arguments @("info") -AllowFailure -Quiet
    if ($infoExit -ne 0) {
        throw "Docker 服务未运行，请先启动 Docker Desktop。"
    }
}

function Get-AppDir {
    return Join-Path $DeployDir "app"
}

function Resolve-DeployDir {
    $script:DeployDir = [System.IO.Path]::GetFullPath($DeployDir)
    if ($script:DeployDir -eq [System.IO.Path]::GetPathRoot($script:DeployDir) -or $script:DeployDir -eq [System.IO.Path]::GetFullPath($HOME)) {
        throw "拒绝使用危险部署目录：$script:DeployDir"
    }
    New-Item -ItemType Directory -Force -Path $script:DeployDir | Out-Null
}

function Test-HostAddress {
    param([string]$Value)
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$parsed)) {
        return $false
    }
    return $parsed.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork
}

function Test-ReleaseVersion {
    param([string]$Value)
    return $Value -eq "latest" -or $Value -match '^v\d+\.\d+\.\d+$'
}

function Invoke-ComposeAt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [switch]$AllowFailure
    )
    $composeFile = Join-Path $Directory "docker-compose.yml"
    $envFile = Join-Path $Directory ".env"
    $allArguments = @(
        "compose",
        "--project-directory", $Directory,
        "--env-file", $envFile,
        "-f", $composeFile
    ) + $Arguments
    return Invoke-Docker -Arguments $allArguments -AllowFailure:$AllowFailure
}

function Invoke-Download {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,
        [Parameter(Mandatory = $true)]
        [string]$OutFile
    )
    $lastError = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $OutFile -MaximumRedirection 10 -TimeoutSec 20
            return
        }
        catch {
            $lastError = $_
            if ($attempt -lt 4) {
                Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 6))
            }
        }
    }
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --ssl-no-revoke --retry 5 --retry-all-errors --connect-timeout 15 --fail --location $Uri --output $OutFile
        if ($LASTEXITCODE -eq 0) {
            return
        }
    }
    throw "下载失败：$Uri`n$($lastError.Exception.Message)"
}

function Get-ReleaseTagFromUri {
    param([string]$Uri)
    $parsed = [System.Uri]$Uri
    $tag = $parsed.Segments[-1].Trim('/')
    if (Test-ReleaseVersion $tag -and $tag -ne "latest") {
        return $tag
    }
    throw "GitHub 返回了无效 Release 地址：$Uri"
}

function Resolve-Version {
    if ($Version -ne "latest") {
        return $Version
    }
    $latestUri = "https://github.com/$($script:Repository)/releases/latest"
    $lastError = $null
    for ($attempt = 1; $attempt -le 4; $attempt++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $latestUri -MaximumRedirection 10 -TimeoutSec 20
            $finalUri = $null
            if ($response.BaseResponse.PSObject.Properties.Name -contains "ResponseUri") {
                $finalUri = $response.BaseResponse.ResponseUri.AbsoluteUri
            }
            elseif ($response.BaseResponse.PSObject.Properties.Name -contains "RequestMessage") {
                $finalUri = $response.BaseResponse.RequestMessage.RequestUri.AbsoluteUri
            }
            if (-not $finalUri) {
                throw "无法读取 GitHub Release 重定向地址。"
            }
            return Get-ReleaseTagFromUri -Uri $finalUri
        }
        catch {
            $lastError = $_
            if ($attempt -lt 4) {
                Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 6))
            }
        }
    }
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $finalUri = & $curl.Source --ssl-no-revoke --retry 5 --retry-all-errors --connect-timeout 15 --fail --silent --show-error --location --output NUL --write-out "%{url_effective}" $latestUri
        if ($LASTEXITCODE -eq 0) {
            return Get-ReleaseTagFromUri -Uri ([string]$finalUri)
        }
    }
    throw "无法解析 Latest Release：$($lastError.Exception.Message)"
}

function Set-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Key,
        [Parameter(Mandatory = $true)]
        [string]$Value
    )
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_ -notmatch "^$([regex]::Escape($Key))=" })
    }
    $lines += "$Key=$Value"
    [System.IO.File]::WriteAllLines($Path, $lines, [System.Text.UTF8Encoding]::new($false))
}

function Copy-ExistingEnv {
    param([string]$Target)
    $existing = Join-Path (Get-AppDir) ".env"
    if (Test-Path -LiteralPath $existing) {
        Copy-Item -LiteralPath $existing -Destination $Target -Force
        return $true
    }
    return $false
}

function Configure-Env {
    param(
        [string]$Path,
        [string]$ReleaseTag,
        [ValidateSet("image", "source")]
        [string]$SelectedMode
    )
    Set-EnvValue -Path $Path -Key "WEB_HOST" -Value $HostAddress
    Set-EnvValue -Path $Path -Key "WEB_PORT" -Value ([string]$Port)
    if ($SelectedMode -eq "image") {
        Set-EnvValue -Path $Path -Key "BACKEND_IMAGE" -Value "ghcr.io/junwan666/bili-insight-backend:$ReleaseTag"
        Set-EnvValue -Path $Path -Key "FRONTEND_IMAGE" -Value "ghcr.io/junwan666/bili-insight-frontend:$ReleaseTag"
    }
    else {
        Set-EnvValue -Path $Path -Key "BACKEND_IMAGE" -Value "bili-insight-backend:local-$ReleaseTag"
        Set-EnvValue -Path $Path -Key "FRONTEND_IMAGE" -Value "bili-insight-frontend:local-$ReleaseTag"
    }
}

function Import-ExistingNetworkSettings {
    $envPath = Join-Path (Get-AppDir) ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        return
    }
    $content = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
    if (-not $script:HostExplicit) {
        $hostLine = $content | Where-Object { $_ -match '^WEB_HOST=' } | Select-Object -Last 1
        if ($hostLine) {
            $existingHost = $hostLine.Substring("WEB_HOST=".Length)
            if (Test-HostAddress $existingHost) {
                $script:HostAddress = $existingHost
            }
            else {
                Write-WarningText "忽略现有配置中的无效 WEB_HOST：$existingHost"
            }
        }
    }
    if (-not $script:PortExplicit) {
        $portLine = $content | Where-Object { $_ -match '^WEB_PORT=' } | Select-Object -Last 1
        if ($portLine) {
            $existingPort = $portLine.Substring("WEB_PORT=".Length)
            if ($existingPort -match '^\d+$' -and [int]$existingPort -ge 1 -and [int]$existingPort -le 65535) {
                $script:Port = [int]$existingPort
            }
            else {
                Write-WarningText "忽略现有配置中的无效 WEB_PORT：$existingPort"
            }
        }
    }
}

function Assert-InternalPath {
    param([string]$Path)
    $root = [System.IO.Path]::GetFullPath($DeployDir).TrimEnd('\') + '\'
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作部署目录之外的路径：$resolved"
    }
}

function Remove-InternalPath {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Assert-InternalPath $Path
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function New-StageDir {
    $path = Join-Path $DeployDir (".stage-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $path | Out-Null
    $script:ActiveStage = $path
    return $path
}

function Prepare-ImageStage {
    param(
        [string]$Stage,
        [string]$ReleaseTag
    )
    $baseUrl = "https://github.com/$($script:Repository)/releases/download/$ReleaseTag"
    Write-InfoText "下载 $ReleaseTag Compose 配置..."
    Invoke-Download -Uri "$baseUrl/docker-compose.yml" -OutFile (Join-Path $Stage "docker-compose.yml")
    $envPath = Join-Path $Stage ".env"
    if (-not (Copy-ExistingEnv -Target $envPath)) {
        Invoke-Download -Uri "$baseUrl/ghcr-compose.env" -OutFile $envPath
    }
    Configure-Env -Path $envPath -ReleaseTag $ReleaseTag -SelectedMode "image"
    $null = Invoke-ComposeAt -Directory $Stage -Arguments @("config", "--quiet")
    Write-InfoText "拉取 $ReleaseTag GHCR 镜像..."
    $pullExit = Invoke-ComposeAt -Directory $Stage -Arguments @("pull") -AllowFailure
    return $pullExit -eq 0
}

function Prepare-SourceStage {
    param(
        [string]$Stage,
        [string]$ReleaseTag
    )
    $archive = Join-Path $DeployDir ".source-$ReleaseTag-$([guid]::NewGuid().ToString('N')).zip"
    $extractDir = Join-Path $DeployDir (".extract-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $extractDir | Out-Null
    try {
        Write-InfoText "下载 $ReleaseTag 源码归档并进行本地构建..."
        Invoke-Download -Uri "https://github.com/$($script:Repository)/archive/refs/tags/$ReleaseTag.zip" -OutFile $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDir -Force
        $sourceRoot = Get-ChildItem -LiteralPath $extractDir -Directory | Select-Object -First 1
        if (-not $sourceRoot -or -not (Test-Path -LiteralPath (Join-Path $sourceRoot.FullName "docker-compose.yml"))) {
            throw "源码归档结构无效。"
        }
        Get-ChildItem -LiteralPath $sourceRoot.FullName -Force | ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $Stage -Recurse -Force
        }
    }
    finally {
        if (Test-Path -LiteralPath $archive) {
            Remove-Item -LiteralPath $archive -Force
        }
        Remove-InternalPath $extractDir
    }

    $envPath = Join-Path $Stage ".env"
    if (-not (Copy-ExistingEnv -Target $envPath)) {
        Copy-Item -LiteralPath (Join-Path $Stage ".env.example") -Destination $envPath -Force
    }
    Configure-Env -Path $envPath -ReleaseTag $ReleaseTag -SelectedMode "source"
    $null = Invoke-ComposeAt -Directory $Stage -Arguments @("config", "--quiet")
    $buildExit = Invoke-ComposeAt -Directory $Stage -Arguments @("build", "--pull") -AllowFailure
    if ($buildExit -ne 0) {
        Write-WarningText "拉取最新基础镜像失败，正在使用本机 Docker 缓存重试构建。"
        $null = Invoke-ComposeAt -Directory $Stage -Arguments @("build")
    }
}

function Activate-Stage {
    param([string]$Stage)
    $current = Get-AppDir
    $previous = Join-Path $DeployDir ".previous"
    $failed = Join-Path $DeployDir ".failed"
    Remove-InternalPath $previous
    Remove-InternalPath $failed
    if (Test-Path -LiteralPath $current) {
        Move-Item -LiteralPath $current -Destination $previous
    }
    Move-Item -LiteralPath $Stage -Destination $current
    $script:ActiveStage = $null

    $upExit = Invoke-ComposeAt -Directory $current -Arguments @("up", "--detach", "--no-build", "--force-recreate", "--wait") -AllowFailure
    if ($upExit -eq 0) {
        Remove-InternalPath $previous
        return
    }

    Write-ErrorText "新版本启动失败，正在恢复上一份部署配置。"
    Move-Item -LiteralPath $current -Destination $failed
    if (Test-Path -LiteralPath $previous) {
        Move-Item -LiteralPath $previous -Destination $current
        $null = Invoke-ComposeAt -Directory $current -Arguments @("up", "--detach", "--no-build", "--wait") -AllowFailure
    }
    Remove-InternalPath $failed
    throw "新版本启动失败。"
}

function Get-LanAddress {
    try {
        $address = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object { $_.IPAddress -notlike "127.*" -and $_.PrefixOrigin -ne "WellKnown" } |
            Sort-Object InterfaceMetric |
            Select-Object -First 1 -ExpandProperty IPAddress
        if ($address) {
            return $address
        }
    }
    catch {
    }
    return "<主机局域网IP>"
}

function Show-AccessUrl {
    $displayHost = if ($HostAddress -eq "0.0.0.0") { Get-LanAddress } else { $HostAddress }
    Write-Ok "访问地址：http://$($displayHost):$Port"
    Write-Host "健康检查：http://$($displayHost):$Port/healthz" -ForegroundColor DarkGray
}

function Install-OrUpdate {
    Test-Requirements
    Resolve-DeployDir
    Import-ExistingNetworkSettings
    if (-not (Test-HostAddress $HostAddress)) {
        throw "无效 IPv4 监听地址：$HostAddress"
    }
    if (-not (Test-ReleaseVersion $Version)) {
        throw "无效版本：$Version"
    }
    $releaseTag = Resolve-Version
    $stage = New-StageDir
    $selectedMode = $Mode
    try {
        if ($Mode -eq "source") {
            Prepare-SourceStage -Stage $stage -ReleaseTag $releaseTag
            $selectedMode = "source"
        }
        else {
            $imageReady = Prepare-ImageStage -Stage $stage -ReleaseTag $releaseTag
            if ($imageReady) {
                $selectedMode = "image"
            }
            elseif ($Mode -eq "auto") {
                Write-WarningText "GHCR 镜像无法匿名拉取，自动回退到同版本源码构建。"
                Remove-InternalPath $stage
                $stage = New-StageDir
                Prepare-SourceStage -Stage $stage -ReleaseTag $releaseTag
                $selectedMode = "source"
            }
            else {
                throw "GHCR 镜像拉取失败。可使用 -Mode source 从公开源码构建。"
            }
        }
        Activate-Stage -Stage $stage
        [System.IO.File]::WriteAllText((Join-Path $DeployDir ".deployment-mode"), $selectedMode, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText((Join-Path $DeployDir ".deployment-version"), $releaseTag, [System.Text.UTF8Encoding]::new($false))
        Write-Ok "Bili Insight $releaseTag 部署完成，模式：$selectedMode"
        Show-AccessUrl
    }
    finally {
        if ($script:ActiveStage -and (Test-Path -LiteralPath $script:ActiveStage)) {
            Remove-InternalPath $script:ActiveStage
        }
        $script:ActiveStage = $null
    }
}

function Assert-Deployment {
    Resolve-DeployDir
    $app = Get-AppDir
    if (-not (Test-Path -LiteralPath (Join-Path $app "docker-compose.yml")) -or -not (Test-Path -LiteralPath (Join-Path $app ".env"))) {
        throw "未找到部署配置：$app。请先执行部署。"
    }
    $envPath = Join-Path $app ".env"
    $hostLine = Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -match '^WEB_HOST=' } | Select-Object -Last 1
    $portLine = Get-Content -LiteralPath $envPath -Encoding UTF8 | Where-Object { $_ -match '^WEB_PORT=' } | Select-Object -Last 1
    if ($hostLine) {
        $script:HostAddress = $hostLine.Substring("WEB_HOST=".Length)
    }
    if ($portLine) {
        $portValue = $portLine.Substring("WEB_PORT=".Length)
        if ($portValue -match '^\d+$' -and [int]$portValue -ge 1 -and [int]$portValue -le 65535) {
            $script:Port = [int]$portValue
        }
    }
}

function Restart-Services {
    Test-Requirements
    Assert-Deployment
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments @("restart")
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments @("up", "--detach", "--no-build", "--wait")
    Write-Ok "服务已重启。"
    Show-AccessUrl
}

function Show-Status {
    Test-Requirements
    Assert-Deployment
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments @("ps")
    $healthHost = if ($HostAddress -eq "0.0.0.0") { "127.0.0.1" } else { $HostAddress }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "http://$($healthHost):$Port/healthz" -TimeoutSec 10 | Out-Null
        Write-Ok "端到端健康检查通过。"
    }
    catch {
        Write-WarningText "健康检查未通过，请查看日志。"
    }
}

function Show-Logs {
    Test-Requirements
    Assert-Deployment
    $arguments = @("logs", "--follow", "--tail=200")
    if ($LogTarget -ne "all") {
        $arguments += $LogTarget
    }
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments $arguments
}

function Uninstall-KeepData {
    Test-Requirements
    Assert-Deployment
    Write-WarningText "将移除容器和网络，但保留数据库、产物、Cookie 密钥及部署文件。"
    $answer = Read-Host "确认继续？[y/N]"
    if ($answer -notmatch '^(?i:y|yes|是)$') {
        Write-InfoText "已取消。"
        return
    }
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments @("down", "--remove-orphans")
    Write-Ok "容器已卸载，数据卷和部署文件已保留。"
}

function Purge-All {
    Test-Requirements
    Assert-Deployment
    Write-ErrorText "危险操作：将删除容器、bili-insight-runtime、bili-insight-secrets 和部署目录。"
    $confirmation = Read-Host "请输入 DELETE 确认彻底删除"
    if ($confirmation -cne "DELETE") {
        Write-InfoText "确认文本不匹配，已取消。"
        return
    }
    $null = Invoke-ComposeAt -Directory (Get-AppDir) -Arguments @("down", "--volumes", "--remove-orphans")
    $resolved = [System.IO.Path]::GetFullPath($DeployDir)
    if ($resolved -eq [System.IO.Path]::GetPathRoot($resolved) -or $resolved -eq [System.IO.Path]::GetFullPath($HOME)) {
        throw "拒绝删除危险路径：$resolved"
    }
    Remove-Item -LiteralPath $resolved -Recurse -Force
    Write-Ok "Bili Insight 容器、数据卷和部署目录已彻底删除。"
}

function Invoke-SelfTest {
    $testDir = Join-Path ([System.IO.Path]::GetTempPath()) ("bili-insight-deploy-test-" + [guid]::NewGuid().ToString("N"))
    $originalDeployDir = $script:DeployDir
    $originalHost = $script:HostAddress
    $originalPort = $script:Port
    $originalHostExplicit = $script:HostExplicit
    $originalPortExplicit = $script:PortExplicit
    New-Item -ItemType Directory -Path $testDir | Out-Null
    try {
        $envPath = Join-Path $testDir ".env"
        [System.IO.File]::WriteAllText($envPath, "WEB_HOST=127.0.0.1`nWEB_PORT=8080`n", [System.Text.UTF8Encoding]::new($false))
        Set-EnvValue -Path $envPath -Key "WEB_PORT" -Value "18080"
        Set-EnvValue -Path $envPath -Key "BACKEND_IMAGE" -Value "test-backend:v1"
        $content = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
        if (@($content | Where-Object { $_ -eq "WEB_PORT=18080" }).Count -ne 1) {
            throw "环境变量覆盖自检失败。"
        }
        if (@($content | Where-Object { $_ -eq "BACKEND_IMAGE=test-backend:v1" }).Count -ne 1) {
            throw "环境变量追加自检失败。"
        }
        if (-not (Test-HostAddress "127.0.0.1") -or (Test-HostAddress "999.0.0.1")) {
            throw "IPv4 校验自检失败。"
        }
        if (-not (Test-ReleaseVersion "latest") -or -not (Test-ReleaseVersion "v1.2.5") -or (Test-ReleaseVersion "main")) {
            throw "版本校验自检失败。"
        }
        $script:HostAddress = "0.0.0.0"
        $script:Port = 18080
        Configure-Env -Path $envPath -ReleaseTag "v1.2.5" -SelectedMode "source"
        $content = @(Get-Content -LiteralPath $envPath -Encoding UTF8)
        foreach ($expected in @(
            "WEB_HOST=0.0.0.0",
            "WEB_PORT=18080",
            "BACKEND_IMAGE=bili-insight-backend:local-v1.2.5",
            "FRONTEND_IMAGE=bili-insight-frontend:local-v1.2.5"
        )) {
            if (@($content | Where-Object { $_ -eq $expected }).Count -ne 1) {
                throw "部署环境生成自检失败：$expected"
            }
        }
        if ((Get-ReleaseTagFromUri -Uri "https://github.com/JunWan666/bili-insight/releases/tag/v1.2.5") -ne "v1.2.5") {
            throw "Release 重定向解析自检失败。"
        }
        $existingDeploy = Join-Path $testDir "deploy"
        $existingApp = Join-Path $existingDeploy "app"
        New-Item -ItemType Directory -Path $existingApp -Force | Out-Null
        [System.IO.File]::WriteAllText((Join-Path $existingApp ".env"), "WEB_HOST=0.0.0.0`nWEB_PORT=19090`n", [System.Text.UTF8Encoding]::new($false))
        $script:DeployDir = $existingDeploy
        $script:HostAddress = "127.0.0.1"
        $script:Port = 8080
        $script:HostExplicit = $false
        $script:PortExplicit = $false
        Import-ExistingNetworkSettings
        if ($script:HostAddress -ne "0.0.0.0" -or $script:Port -ne 19090) {
            throw "现有网络配置保留自检失败。"
        }
        $script:HostAddress = "127.0.0.1"
        $script:Port = 18080
        $script:HostExplicit = $true
        $script:PortExplicit = $true
        Import-ExistingNetworkSettings
        if ($script:HostAddress -ne "127.0.0.1" -or $script:Port -ne 18080) {
            throw "显式网络参数优先级自检失败。"
        }
        Write-Ok "deploy.ps1 自检通过。"
    }
    finally {
        $script:DeployDir = $originalDeployDir
        $script:HostAddress = $originalHost
        $script:Port = $originalPort
        $script:HostExplicit = $originalHostExplicit
        $script:PortExplicit = $originalPortExplicit
        if (Test-Path -LiteralPath $testDir) {
            Remove-Item -LiteralPath $testDir -Recurse -Force
        }
    }
}

function Read-Default {
    param(
        [string]$Prompt,
        [string]$DefaultValue
    )
    $answer = Read-Host "$Prompt [$DefaultValue]"
    if ([string]::IsNullOrWhiteSpace($answer)) {
        return $DefaultValue
    }
    return $answer.Trim()
}

function Invoke-InteractiveDeploy {
    $script:DeployDir = Read-Default -Prompt "部署目录" -DefaultValue $DeployDir
    Resolve-DeployDir
    Import-ExistingNetworkSettings
    Write-Host "访问范围：1) 仅本机  2) 可信局域网"
    $defaultAccessChoice = if ($HostAddress -eq "0.0.0.0") { "2" } else { "1" }
    $accessChoice = Read-Default -Prompt "请选择" -DefaultValue $defaultAccessChoice
    $script:HostAddress = if ($accessChoice -eq "2") { "0.0.0.0" } else { "127.0.0.1" }
    $script:HostExplicit = $true
    $portValue = Read-Default -Prompt "Web 端口" -DefaultValue ([string]$Port)
    if ($portValue -notmatch '^\d+$' -or [int]$portValue -lt 1 -or [int]$portValue -gt 65535) {
        throw "无效端口：$portValue"
    }
    $script:Port = [int]$portValue
    $script:PortExplicit = $true
    $script:Version = Read-Default -Prompt "版本（latest 或 vX.Y.Z）" -DefaultValue $Version
    Write-Host "部署模式：1) 自动  2) 仅镜像  3) 源码构建"
    $modeChoice = Read-Default -Prompt "请选择" -DefaultValue "1"
    $script:Mode = switch ($modeChoice) {
        "2" { "image" }
        "3" { "source" }
        default { "auto" }
    }
    Install-OrUpdate
}

function Show-Menu {
    while ($true) {
        Clear-Host
        Show-Banner
        Write-Host "当前部署目录：$DeployDir"
        Write-Host ""
        Write-Host "  1. 部署 / 更新" -ForegroundColor Green
        Write-Host "  2. 重启服务" -ForegroundColor Blue
        Write-Host "  3. 查看状态" -ForegroundColor Blue
        Write-Host "  4. 查看全部日志" -ForegroundColor Blue
        Write-Host "  5. 卸载但保留数据" -ForegroundColor Yellow
        Write-Host "  6. 彻底卸载" -ForegroundColor Red
        Write-Host "  0. 退出"
        Write-Host ""
        $choice = Read-Host "请选择操作"
        try {
            switch ($choice) {
                "1" { Invoke-InteractiveDeploy }
                "2" { Restart-Services }
                "3" { Show-Status }
                "4" { $script:LogTarget = "all"; Show-Logs }
                "5" { Uninstall-KeepData }
                "6" { Purge-All }
                "0" { return }
                default { Write-WarningText "无效选项。" }
            }
        }
        catch {
            Write-ErrorText $_.Exception.Message
        }
        if ($choice -ne "4" -and $choice -ne "0") {
            Read-Host "按回车继续" | Out-Null
        }
    }
}

switch ($Action) {
    "Menu" { Show-Menu }
    "Deploy" { Install-OrUpdate }
    "Update" { Install-OrUpdate }
    "Restart" { Restart-Services }
    "Status" { Show-Status }
    "Logs" { Show-Logs }
    "Uninstall" { Uninstall-KeepData }
    "Purge" { Purge-All }
    "SelfTest" { Invoke-SelfTest }
    "Help" { Show-Help }
}
