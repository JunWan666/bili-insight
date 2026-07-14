[CmdletBinding()]
param(
    [ValidateSet("127.0.0.1", "0.0.0.0")]
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$root = Split-Path -Parent $PSScriptRoot
$backendDirectory = Join-Path $root "backend"
$frontendDirectory = Join-Path $root "frontend"
$python = Join-Path $backendDirectory ".venv\Scripts\python.exe"
$runtime = Join-Path $root "runtime"
$data = Join-Path $runtime "data"
$artifacts = Join-Path $runtime "artifacts"
$temporary = Join-Path $runtime "temp"
$logs = Join-Path $runtime "logs"
$secrets = Join-Path $runtime "secrets"
$keyFile = Join-Path $secrets "cookie-encryption.key"
$database = (Join-Path $data "bili_insight.db").Replace("\", "/")

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend virtual environment is missing. Run scripts/bootstrap.ps1 first."
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendDirectory "node_modules"))) {
    throw "Frontend dependencies are missing. Run scripts/bootstrap.ps1 first."
}

foreach ($directory in @($data, $artifacts, $temporary, $logs, $secrets)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}

if (-not (Test-Path -LiteralPath $keyFile)) {
    $key = & $python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($key)) {
        throw "Cookie encryption key generation failed."
    }
    Set-Content -LiteralPath $keyFile -Value $key.Trim() -Encoding ascii -NoNewline
}

$env:APP_ENVIRONMENT = "development"
$env:APP_HOST = "127.0.0.1"
$env:APP_PORT = "8000"
$env:APP_NETWORK_MODE = "local"
$env:APP_DATABASE_URL = "sqlite+aiosqlite:///$database"
$env:APP_DATA_DIR = $data
$env:APP_ARTIFACT_DIR = $artifacts
$env:APP_TEMP_DIR = $temporary
$env:APP_LOG_DIR = $logs
$env:APP_COOKIE_ENCRYPTION_KEY_FILE = $keyFile
$env:APP_LOG_JSON = "false"
$env:APP_AUTO_CREATE_SCHEMA = "false"
$env:APP_CORS_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173"
$env:VITE_PROXY_TARGET = "http://127.0.0.1:8000"
$env:VITE_DEV_HOST = $HostAddress

Push-Location $backendDirectory
try {
    & $python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        throw "Database migration failed."
    }
}
finally {
    Pop-Location
}

$processArguments = @{
    FilePath = $python
    ArgumentList = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload")
    WorkingDirectory = $backendDirectory
    NoNewWindow = $true
    PassThru = $true
}
$backendProcess = Start-Process @processArguments

try {
    Push-Location $frontendDirectory
    try {
        & npm run dev
        if ($LASTEXITCODE -ne 0) {
            throw "Frontend development server stopped with an error."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    if (-not $backendProcess.HasExited) {
        Stop-Process -Id $backendProcess.Id
        $backendProcess.WaitForExit()
    }
}
