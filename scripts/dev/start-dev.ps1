param(
    [switch]$SkipWorkers
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $Root
if (-not (Test-Path "venv\Scripts\python.exe")) {
    throw "Python virtual environment is missing. Run npm run init first."
}
if (-not (Test-Path "frontend\node_modules")) {
    throw "Frontend dependencies are missing. Run npm run init first."
}
function Test-PortListening {
    param([int]$Port)
    return [bool](Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Where-Object { $_.State -eq "Listen" })
}
function Test-ProcessCommandLine {
    param([string]$Needle)
    return [bool](
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Needle*" }
    )
}
function Start-DevProcess {
    param(
        [string]$Name,
        [string]$Command
    )
    $processArgs = "-NoExit", "-Command", "Set-Location '$Root'; `$env:PYTHONPATH='$Root'; $Command"
    Start-Process powershell -ArgumentList $processArgs -WindowStyle Hidden
    Write-Host "Started $Name"
}
function Get-DotEnvValue {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Name
    )
    if (-not (Test-Path $Path)) {
        return $null
    }
    $escapedName = [regex]::Escape($Name)
    $line = Get-Content $Path |
        Where-Object { $_ -match "^\s*$escapedName\s*=" } |
        Select-Object -Last 1
    if (-not $line) {
        return $null
    }
    $value = ($line -split '=', 2)[1].Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}
function Get-DevUrl {
    param([Parameter(Mandatory=$true)][string]$Name)
    $processValue = [Environment]::GetEnvironmentVariable($Name)
    if ($processValue) {
        return $processValue
    }
    $configuredValue = Get-DotEnvValue -Path (Join-Path $Root "backend\.env.local") -Name $Name
    if ($configuredValue) {
        return $configuredValue
    }
    throw "$Name is missing. Run npm run init, then replace the placeholder in backend/.env.local."
}
$DatabaseUrl = Get-DevUrl -Name "DATABASE_URL"
$RedisUrl = Get-DevUrl -Name "REDIS_URL"
Write-Host "This script does not start Redis. Start local Redis first, or use docker compose for the full containerized stack."
try {
    $redisUri = [Uri]$RedisUrl
    $redisHost = $redisUri.Host
    $redisPort = if ($redisUri.Port -gt 0) { $redisUri.Port } else { 6379 }
    Write-Host ("Redis expected at {0}://{1}:{2} (credentials redacted)" -f $redisUri.Scheme, $redisHost, $redisPort)
    if ($redisHost -in @("localhost", "127.0.0.1", "::1") -and -not (Test-PortListening $redisPort)) {
        Write-Warning "No local Redis listener detected on port $redisPort. Queue, worker, burst buffering, public chat, and health checks may be degraded."
    }
} catch {
    throw "Could not parse REDIS_URL from the process environment or backend/.env.local."
}
try {
    $databaseUri = [Uri]$DatabaseUrl
    $databasePort = if ($databaseUri.Port -gt 0) { $databaseUri.Port } else { 5432 }
    $databaseName = $databaseUri.AbsolutePath.TrimStart('/')
    Write-Host ("PostgreSQL expected at {0}://{1}:{2}/{3} (credentials redacted)" -f $databaseUri.Scheme, $databaseUri.Host, $databasePort, $databaseName)
} catch {
    throw "Could not parse DATABASE_URL from the process environment or backend/.env.local."
}
if (Test-PortListening 8000) {
    Write-Host "Backend already listening on http://localhost:8000"
} else {
    Start-DevProcess "backend API on http://localhost:8000" ".\venv\Scripts\python.exe -m uvicorn backend.api.app:app --reload --host 0.0.0.0 --port 8000"
    Start-Sleep -Seconds 2
}
if (Test-PortListening 5173) {
    Write-Host "Frontend already listening on http://localhost:5173"
} else {
    $frontendArgs = "-NoExit", "-Command", "Set-Location '$Root'; npm --prefix frontend run dev -- --host 0.0.0.0"
    Start-Process powershell -ArgumentList $frontendArgs -WindowStyle Hidden
    Write-Host "Started frontend on http://localhost:5173"
}
if ($SkipWorkers) {
    Write-Warning "Skipping workers by request. Queued public-chat/WhatsApp messages and scheduled reminders will not be processed."
} else {
    if (Test-ProcessCommandLine "backend.pipeline.worker") {
        Write-Host "Main worker already running"
    } else {
        Start-DevProcess "main queue worker" ".\venv\Scripts\python.exe -m backend.pipeline.worker"
    }
    if (Test-ProcessCommandLine "backend.pipeline.burst_worker") {
        Write-Host "Burst worker already running"
    } else {
        Start-DevProcess "burst worker" ".\venv\Scripts\python.exe -m backend.pipeline.burst_worker"
    }
    if (Test-ProcessCommandLine "backend.pipeline.reminder_scheduler") {
        Write-Host "Reminder scheduler already running"
    } else {
        Start-DevProcess "reminder scheduler" ".\venv\Scripts\python.exe -m backend.pipeline.reminder_scheduler"
    }
}
Write-Host "Health check: http://localhost:8000/api/v1/health"
Write-Host "Reminder scheduler: started unless -SkipWorkers was provided."
