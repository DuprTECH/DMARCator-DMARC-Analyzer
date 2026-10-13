# Deploy on Windows Server (IIS + MariaDB)

For **Windows Server 2019 / 2022 / 2025**. Run everything in **PowerShell as Administrator**.
You only edit **step 0**.

| Step | What happens |
|---|---|
| 0 | settings |
| 1 | install software (Python, Git, MariaDB, NSSM) |
| 2 | configure MariaDB (database + user) |
| 3 | `git clone` + Python libraries |
| 4 | configure the application (`.env`) |
| 5 | run it as a Windows service (NSSM) |
| 6 | configure IIS as a reverse proxy |
| 7 | HTTPS certificate |
| 8 | scheduled tasks (backups, cleanup) |
| 9 | first login |

---

## Step 0 – settings

```powershell
$DmarcHost = "dmarc.example.com"          # web address of DMARCula
$AppDir    = "C:\dmarcula"                # application directory (NOT under C:\inetpub)
$bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$DbPass    = -join ($bytes | ForEach-Object { $_.ToString("x2") })     # 48 random hex characters
"Database password: $DbPass"              # write it down
```

## Step 1 – install software

With **winget** (Windows Server 2025, or 2019/2022 with App Installer):

```powershell
winget install -e --id Python.Python.3.12 --scope machine
winget install -e --id Git.Git
winget install -e --id MariaDB.Server      # the installer asks for a MariaDB root password
winget install -e --id NSSM.NSSM
```

Without winget, download and install them manually:
[Python 3.12](https://www.python.org/downloads/windows/) ("Install for all users" and "Add to PATH"),
[Git](https://git-scm.com/download/win), [MariaDB 11.4 MSI](https://mariadb.org/download/),
and [NSSM](https://nssm.cc/download) (copy `win64\nssm.exe` to `C:\Windows\System32`).

**Close and reopen PowerShell (as Administrator)**, set the variables from step 0 again, then check:

```powershell
py -3.12 --version; git --version; nssm version
$Mariadb = (Get-ChildItem "C:\Program Files\MariaDB*\bin\mariadb.exe" | Select-Object -Last 1).FullName
$Mariadb
```

## Step 2 – configure MariaDB

```powershell
& $Mariadb -u root -p -e "CREATE DATABASE dmarcula CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE USER 'dmarcula'@'localhost' IDENTIFIED BY '$DbPass';
  CREATE USER 'dmarcula'@'127.0.0.1' IDENTIFIED BY '$DbPass';
  GRANT ALL PRIVILEGES ON dmarcula.* TO 'dmarcula'@'localhost';
  GRANT ALL PRIVILEGES ON dmarcula.* TO 'dmarcula'@'127.0.0.1';
  FLUSH PRIVILEGES;"
# (enter the MariaDB root password from the installer)

& $Mariadb -u dmarcula "-p$DbPass" -h 127.0.0.1 -e "SHOW DATABASES;"     # lists "dmarcula"
```

For 100+ domains, open `C:\Program Files\MariaDB 11.4\data\my.ini`, add
`innodb_buffer_pool_size=512M` under `[mysqld]`, and run `Restart-Service MariaDB`.

## Step 3 – download the application

```powershell
git clone https://github.com/DuprTECH/DMARCula---DMARC-Anylzer.git $AppDir    # private repo: a GitHub login window opens
Set-Location $AppDir
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

## Step 4 – configure the application

```powershell
Set-Location $AppDir
$Secret        = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
$AdminPassword = "Admin-" + (.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(6))")

$lines = (Get-Content .env.example) `
  -replace '^SECRET_KEY=.*',      "SECRET_KEY=$Secret" `
  -replace '^BASE_URL=.*',        "BASE_URL=https://$DmarcHost" `
  -replace '^COOKIE_SECURE=.*',   "COOKIE_SECURE=true" `
  -replace '^# DATABASE_URL=mysql.*', "DATABASE_URL=mysql+pymysql://dmarcula:$DbPass@127.0.0.1:3306/dmarcula" `
  -replace '^ADMIN_PASSWORD=.*',  "ADMIN_PASSWORD=$AdminPassword"
[IO.File]::WriteAllLines("$AppDir\.env", $lines)        # UTF-8 without BOM

"Web login: admin / $AdminPassword"                     # write it down

.\.venv\Scripts\python.exe -c "from app.db import init_db; init_db(); print('database OK')"
```

Restrict the file to administrators and the service (LocalSystem):

```powershell
icacls "$AppDir\.env" /inheritance:r /grant:r "Administrators:F" "SYSTEM:F"
```

## Step 5 – run as a Windows service

```powershell
New-Item -ItemType Directory -Force "$AppDir\logs" | Out-Null
nssm install DMARCula "$AppDir\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips 127.0.0.1"
nssm set DMARCula AppDirectory $AppDir
nssm set DMARCula DisplayName "DMARCula DMARC analyzer"
nssm set DMARCula Start SERVICE_AUTO_START
nssm set DMARCula AppStdout "$AppDir\logs\dmarcula.log"
nssm set DMARCula AppStderr "$AppDir\logs\dmarcula.log"
nssm set DMARCula AppRotateFiles 1
nssm set DMARCula AppRotateBytes 10485760
nssm start DMARCula

Start-Sleep 5
Invoke-RestMethod http://127.0.0.1:8000/healthz          # status: ok
```

If it doesn't start, check `C:\dmarcula\logs\dmarcula.log`.

## Step 6 – configure IIS as a reverse proxy

**6a. Install IIS and the two proxy modules**

```powershell
Install-WindowsFeature Web-Server -IncludeManagementTools
```

Then download and install (both are free Microsoft MSIs):
- **URL Rewrite 2.1**: https://www.iis.net/downloads/microsoft/url-rewrite
- **Application Request Routing 3.0**: https://www.iis.net/downloads/microsoft/application-request-routing

**6b. Enable the proxy and create the site**

```powershell
Import-Module WebAdministration
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/proxy' -Name enabled -Value $true
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/proxy' -Name preserveHostHeader -Value $true
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/proxy' -Name timeout -Value '00:02:00'

New-Item -ItemType Directory -Force C:\inetpub\dmarcula | Out-Null
Copy-Item "$AppDir\deploy\iis\web.config" C:\inetpub\dmarcula\
New-Website -Name DMARCula -PhysicalPath C:\inetpub\dmarcula -HostHeader $DmarcHost -Port 80
```

The site folder contains only `web.config`, which forwards every request to the service on
`127.0.0.1:8000` and redirects HTTP to HTTPS.

## Step 7 – HTTPS certificate

**7a. Company certificate (.pfx):**

```powershell
$cert = Import-PfxCertificate -FilePath C:\certs\dmarc.pfx -CertStoreLocation Cert:\LocalMachine\My `
        -Password (Read-Host -AsSecureString "PFX password")
New-WebBinding -Name DMARCula -Protocol https -Port 443 -HostHeader $DmarcHost -SslFlags 1
(Get-WebBinding -Name DMARCula -Protocol https).AddSslCertificate($cert.Thumbprint, "My")
```

**7b. Let's Encrypt:** download [win-acme](https://www.win-acme.com/), run `wacs.exe`, choose
*N: Create certificate (default settings)* and select the **DMARCula** site. It creates the
HTTPS binding and a renewal task.

**Firewall** (usually already open after installing IIS):

```powershell
New-NetFirewallRule -DisplayName "DMARCula HTTP/HTTPS" -Direction Inbound -Protocol TCP -LocalPort 80,443 -Action Allow
```

Test it: `Invoke-WebRequest https://$DmarcHost/healthz -UseBasicParsing`

## Step 8 – scheduled tasks (instead of cron)

Report fetching runs inside the service, so no task is needed for it. These tasks handle backups and cleanup:

```powershell
# credentials for the backup
"[client]`nuser=dmarcula`npassword=$DbPass`nhost=127.0.0.1" | Set-Content -Encoding ascii "$AppDir\.backup.my.cnf"
icacls "$AppDir\.backup.my.cnf" /inheritance:r /grant:r "Administrators:F" "SYSTEM:F"

# daily 02:30 – backup to C:\dmarcula-backup (30 days kept)
$a = New-ScheduledTaskAction -Execute "powershell.exe" `
     -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$AppDir\deploy\windows\backup.ps1`" -AppDir `"$AppDir`""
Register-ScheduledTask -TaskName "DMARCula backup" -Action $a -User "SYSTEM" -RunLevel Highest `
     -Trigger (New-ScheduledTaskTrigger -Daily -At 2:30am)

# Sundays 03:15 – delete reports older than 2 years
$a = New-ScheduledTaskAction -Execute "$AppDir\.venv\Scripts\python.exe" -Argument "-m app.manage prune --days 730" -WorkingDirectory $AppDir
Register-ScheduledTask -TaskName "DMARCula prune" -Action $a -User "SYSTEM" -RunLevel Highest `
     -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3:15am)

Start-ScheduledTask "DMARCula backup"; Start-Sleep 10; Get-ChildItem C:\dmarcula-backup     # test
```

For alerts, point your monitoring (PRTG, Zabbix, Uptime Kuma, …) at `https://dmarc.example.com/healthz`.
For mailbox health, it can run `C:\dmarcula\.venv\Scripts\python.exe -m app.manage status`
(working directory `C:\dmarcula`, exit code 1 = problem).

## Step 9 – first login

1. Open `https://dmarc.example.com`, then log in as **admin** with the password from step 4.
2. Remove the bootstrap password from `.env`:

   ```powershell
   $lines = (Get-Content "$AppDir\.env") -replace '^ADMIN_PASSWORD=.*', 'ADMIN_PASSWORD='
   [IO.File]::WriteAllLines("$AppDir\.env", $lines)
   ```

3. Connect the mailbox: *Administrácia → Poštové schránky* (see [Microsoft 365 setup](microsoft-365.md)).

---

## Later

**Update:**

```powershell
Set-Location C:\dmarcula
git pull
.\.venv\Scripts\pip.exe install -r requirements.txt
Restart-Service DMARCula
```

| Command | Purpose |
|---|---|
| `Get-Service DMARCula` / `Restart-Service DMARCula` | service state / restart |
| `Get-Content C:\dmarcula\logs\dmarcula.log -Tail 50 -Wait` | application log |
| `Set-Location C:\dmarcula; .\.venv\Scripts\python.exe -m app.manage set-password admin` | reset the admin password |
| IIS 502.3 error | the service isn't running, or ARR proxy is not enabled (step 6b) |
