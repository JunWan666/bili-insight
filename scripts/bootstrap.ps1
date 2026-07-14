[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$venv = Join-Path $backend ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

& $Python -c "import sys; assert (3, 12) <= sys.version_info < (4, 0), 'Python 3.12 or newer is required'"
if ($LASTEXITCODE -ne 0) {
    throw "Python version validation failed; version 3.12 or newer is required."
}

& $Python -m venv $venv
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "${backend}[dev]"

Push-Location $frontend
try {
    & npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend dependency installation failed."
    }
}
finally {
    Pop-Location
}

foreach ($command in @("ffmpeg", "ffprobe")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required and was not found on PATH."
    }
}

Write-Host "Development dependencies are ready."
