$ErrorActionPreference = "Stop"

$deployUri = if ($env:BILI_INSIGHT_DEPLOY_SCRIPT_URL) {
    $env:BILI_INSIGHT_DEPLOY_SCRIPT_URL
}
else {
    "https://github.com/JunWan666/bili-insight/releases/latest/download/deploy.ps1"
}
$deployPath = Join-Path ([System.IO.Path]::GetTempPath()) ("bili-insight-deploy-" + [guid]::NewGuid().ToString("N") + ".ps1")

try {
    if ($env:BILI_INSIGHT_DEPLOY_SCRIPT_PATH) {
        [System.IO.File]::Copy([System.IO.Path]::GetFullPath($env:BILI_INSIGHT_DEPLOY_SCRIPT_PATH), $deployPath, $true)
    }
    else {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $deployUri -OutFile $deployPath -MaximumRedirection 10 -TimeoutSec 30
        }
        catch {
            $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
            if (-not $curl) {
                throw
            }
            & $curl.Source --ssl-no-revoke --retry 5 --retry-all-errors --connect-timeout 15 --fail --location $deployUri --output $deployPath
            if ($LASTEXITCODE -ne 0) {
                throw "Unable to download the Bili Insight deployment manager."
            }
        }
    }

    $hostCommand = $null
    foreach ($candidate in @("powershell.exe", "pwsh.exe", "pwsh")) {
        $hostCommand = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($hostCommand) {
            break
        }
    }
    if (-not $hostCommand) {
        throw "PowerShell executable was not found."
    }

    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $deployPath)
    if ($env:BILI_INSIGHT_DEPLOY_ACTION) {
        $arguments += @("-Action", $env:BILI_INSIGHT_DEPLOY_ACTION)
    }
    & $hostCommand.Source @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Bili Insight deployment manager exited with code $LASTEXITCODE."
    }
}
finally {
    if (Test-Path -LiteralPath $deployPath) {
        [System.IO.File]::Delete($deployPath)
    }
}
