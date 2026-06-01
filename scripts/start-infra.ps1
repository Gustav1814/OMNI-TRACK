# Start PostgreSQL (pgvector) + Redis for local backend development.
# Run from repo root:  .\scripts\start-infra.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "Starting OmniTrack infrastructure (postgres + redis)..." -ForegroundColor Cyan
docker compose -f "$Root\docker-compose.yml" up -d postgres redis

Write-Host "Waiting for health checks..." -ForegroundColor Cyan
$deadline = (Get-Date).AddMinutes(2)
do {
    Start-Sleep -Seconds 2
    $ps = docker compose -f "$Root\docker-compose.yml" ps --format json | ConvertFrom-Json
    $healthy = ($ps | Where-Object { $_.Service -in @('postgres','redis') -and $_.Health -eq 'healthy' }).Count -eq 2
} until ($healthy -or (Get-Date) -gt $deadline)

if (-not $healthy) {
    Write-Error "Postgres/Redis did not become healthy in time. Run: docker compose ps"
}

Write-Host "Infrastructure ready." -ForegroundColor Green
Write-Host "  PostgreSQL: localhost:5432  (omnitrack / omnitrack_secret / omnitrack_db)"
Write-Host "  Redis:      localhost:6379"
Write-Host ""
Write-Host "Next:" -ForegroundColor Yellow
Write-Host "  cd backend"
Write-Host "  .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
