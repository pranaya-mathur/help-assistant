# Create local Postgres user/database for mobcoder-sales-agent.
# Requires psql on PATH (installed with PostgreSQL).
#
# Usage (from repo root):
#   .\scripts\setup_postgres.ps1
#   .\scripts\setup_postgres.ps1 -PostgresPassword 'your_postgres_superuser_password'
#
# Note: this is the POSTGRES superuser password (set at PostgreSQL install),
# not the mobcoder app password. If you forgot it, run as Administrator:
#   .\scripts\reset_postgres_password.ps1 -NewPassword 'YourNewPostgresPassword123'

param(
    [string]$PostgresUser = "postgres",
    [string]$PostgresHost = "localhost",
    [int]$PostgresPort = 5432,
    [string]$PostgresPassword = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SqlFile = Join-Path $Root "db\setup_local.sql"

if (-not (Get-Command psql -ErrorAction SilentlyContinue)) {
    Write-Host "psql not found on PATH." -ForegroundColor Red
    Write-Host ""
    Write-Host "Option A - pgAdmin:"
    Write-Host "  1. Open pgAdmin, connect to your server"
    Write-Host "  2. Tools -> Query Tool"
    Write-Host "  3. Open and run: db\setup_local.sql"
    Write-Host ""
    Write-Host "Option B - add PostgreSQL bin to PATH, e.g.:"
    Write-Host '  $env:Path += ";C:\Program Files\PostgreSQL\18\bin"'
    Write-Host "  Then re-run: .\scripts\setup_postgres.ps1"
    Write-Host ""
    Write-Host "Option C - Docker (matches docker-compose.prod.yml):"
    Write-Host "  docker compose -f docker-compose.prod.yml up postgres -d"
    Write-Host '  Then set DATABASE_URL=postgresql://mobcoder:mobcoder@localhost:5432/mobcoder'
    exit 1
}

Write-Host ("Running db\setup_local.sql as user '{0}' on {1}:{2}..." -f $PostgresUser, $PostgresHost, $PostgresPort)
Write-Host "You may be prompted for the postgres superuser password."
Write-Host ""

$env:PGHOST = $PostgresHost
$env:PGPORT = "$PostgresPort"
if ($PostgresPassword) {
    $env:PGPASSWORD = $PostgresPassword
}

& psql -U $PostgresUser -v ON_ERROR_STOP=1 -f $SqlFile
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Setup failed." -ForegroundColor Red
    Write-Host "The password you entered must be for the POSTGRES superuser, not mobcoder@123."
    Write-Host "If you forgot the postgres password, open PowerShell as Administrator and run:"
    Write-Host "  .\scripts\reset_postgres_password.ps1 -NewPassword 'YourNewPostgresPassword123'"
    Write-Host "Or run db\setup_local.sql manually in pgAdmin Query Tool."
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Done. Your .env should use:" -ForegroundColor Green
Write-Host 'DATABASE_URL=postgresql://mobcoder:mobcoder%40123@localhost:5432/mobcoder'
Write-Host ""
Write-Host "Next:"
Write-Host "  python scripts/migrate_db.py"
Write-Host "  python main.py"
