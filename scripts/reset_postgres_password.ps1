# Reset the local PostgreSQL superuser (postgres) password on Windows.
# Requires running PowerShell as Administrator.
#
# Usage:
#   .\scripts\reset_postgres_password.ps1 -NewPassword 'YourNewPostgresPassword123'
#
# After reset, create the app user:
#   .\scripts\setup_postgres.ps1 -PostgresPassword 'YourNewPostgresPassword123'

param(
    [Parameter(Mandatory = $true)]
    [string]$NewPassword,
    [string]$PostgresService = "postgresql-x64-18",
    [string]$PostgresUser = "postgres"
)

$ErrorActionPreference = "Stop"

function Find-PgHbaConf {
    $candidates = @(
        "C:\Program Files\PostgreSQL\18\data\pg_hba.conf",
        "C:\Program Files\PostgreSQL\17\data\pg_hba.conf",
        "C:\Program Files\PostgreSQL\16\data\pg_hba.conf"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    throw "Could not find pg_hba.conf. Install PostgreSQL or pass -PgHbaPath manually."
}

$pgHba = Find-PgHbaConf
$backup = "$pgHba.bak-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $pgHba $backup

Write-Host "Backed up pg_hba.conf to:"
Write-Host "  $backup"
Write-Host ""

$content = Get-Content $pgHba -Raw
$trustBlock = @"
# TEMP mobcoder-sales-agent trust (local only) - added by reset_postgres_password.ps1
host    all             all             127.0.0.1/32            trust
host    all             all             ::1/128                 trust
"@

if ($content -notmatch "TEMP mobcoder-sales-agent trust") {
    Set-Content -Path $pgHba -Value ($trustBlock + "`r`n" + $content) -Encoding ASCII
}

Write-Host "Restarting service $PostgresService ..."
Restart-Service $PostgresService -Force

$psql = "C:\Program Files\PostgreSQL\18\bin\psql.exe"
if (-not (Test-Path $psql)) {
    $psql = (Get-Command psql -ErrorAction SilentlyContinue).Source
}
if (-not $psql) {
    throw "psql not found. Add PostgreSQL bin to PATH first."
}

$escaped = $NewPassword.Replace("'", "''")
$sql = "ALTER USER $PostgresUser WITH PASSWORD '$escaped';"
& $psql -U $PostgresUser -d postgres -v ON_ERROR_STOP=1 -c $sql

Write-Host "Restoring pg_hba.conf ..."
Copy-Item $backup $pgHba -Force
Restart-Service $PostgresService -Force

Write-Host ""
Write-Host "postgres superuser password updated." -ForegroundColor Green
Write-Host "Next:"
Write-Host ("  .\scripts\setup_postgres.ps1 -PostgresPassword '{0}'" -f $NewPassword)
Write-Host "  python scripts/migrate_db.py"
Write-Host "  python main.py"
