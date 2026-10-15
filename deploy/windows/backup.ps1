# UnDMARC – MariaDB backup for Windows (run daily from Task Scheduler).
#   powershell -NoProfile -ExecutionPolicy Bypass -File C:\undmarc\deploy\windows\backup.ps1
param(
    [string]$AppDir = "C:\undmarc",
    [string]$BackupDir = "C:\undmarc-backup",
    [string]$Database = "undmarc",
    [int]$KeepDays = 30,
    [string]$DumpExe = ""
)
$ErrorActionPreference = "Stop"

if ($DumpExe) {
    $dump = Get-Item $DumpExe
} else {
    $dump = Get-ChildItem "C:\Program Files\MariaDB*\bin\mariadb-dump.exe" -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
}
if (-not $dump) { throw "mariadb-dump.exe not found under C:\Program Files\MariaDB* (use -DumpExe)" }

New-Item -ItemType Directory -Force $BackupDir | Out-Null
$target = Join-Path $BackupDir ("undmarc-{0:yyyy-MM-dd}.sql" -f (Get-Date))

& $dump.FullName "--defaults-extra-file=$AppDir\.backup.my.cnf" --single-transaction --quick "--result-file=$target" $Database
if ($LASTEXITCODE -ne 0) { throw "mariadb-dump failed with exit code $LASTEXITCODE" }

Compress-Archive -Path $target -DestinationPath "$target.zip" -Force
Remove-Item $target
Get-ChildItem $BackupDir -Filter "undmarc-*.sql.zip" |
    Where-Object LastWriteTime -lt (Get-Date).AddDays(-$KeepDays) | Remove-Item
Write-Output "Backup written to $target.zip"
