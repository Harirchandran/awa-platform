param(
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
function Invoke-Checked {
    param([Parameter(Mandatory=$true)][string]$FilePath, [string[]]$Arguments = @())
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FilePath failed with exit code $LASTEXITCODE"
    }
}
Write-Host "Initializing AWA v1.0 in $Root"
if (-not (Test-Path ".env") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env for Docker Compose from .env.example"
}
if (-not (Test-Path "backend\.env.local") -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" "backend\.env.local"
    Write-Host "Created backend/.env.local for host-mode backend processes from .env.example"
}
if (-not $SkipBackend) {
    if (-not (Test-Path "venv\Scripts\python.exe")) {
        Write-Host "Creating Python virtual environment..."
        Invoke-Checked "py" @("-3.12", "-m", "venv", "venv")
    }
    $pythonVersion = & ".\venv\Scripts\python.exe" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ([version]$pythonVersion -ge [version]"3.13") {
        throw "The backend dependency set is pinned for Python 3.12, but this venv uses Python $pythonVersion. Install Python 3.12 and recreate venv, or run the Docker stack with npm run docker:up."
    }
    Write-Host "Installing backend dependencies..."
    Invoke-Checked ".\venv\Scripts\python.exe" @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Checked ".\venv\Scripts\python.exe" @("-m", "pip", "install", "-r", ".\backend\requirements.txt")
    if (Test-Path ".\backend\requirements-dev.txt") {
        Invoke-Checked ".\venv\Scripts\python.exe" @("-m", "pip", "install", "-r", ".\backend\requirements-dev.txt")
    }
}
if (-not $SkipFrontend) {
    Write-Host "Installing frontend dependencies..."
    Invoke-Checked "npm" @("--prefix", "frontend", "install")
}
Write-Host ""
Write-Host "Initialization complete."
Write-Host "Run npm run dev for local frontend + backend, or npm run docker:up for the Docker stack."
