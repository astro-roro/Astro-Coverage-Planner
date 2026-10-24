<#
Test W2: ACP starts through Start ACP.bat and through a shortcut, runs as
pythonw.exe and has no console (conhost.exe) child. Runs in CI after smoke.py.

    pwsh packaging/windows/smoke-start.ps1 -Package C:\path\to\ACP

Quits ACP through POST /api/quit each time and fails if it does not stop.
#>
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$Package)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $Package 'python\python.exe'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("acp start " + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalPort
    $listener.Stop()
    return $port
}

function Test-Start($name, [scriptblock]$launch) {
    Write-Host "`n==> Starting through $name"
    $port = Get-FreePort
    $env:ACP_HOME = Join-Path $work "home $name"
    $env:ACP_NO_BROWSER = '1'
    $env:ACP_NO_TRAY = '1'
    $env:ACP_NO_DIALOG = '1'
    $env:PORT = "$port"
    & $launch

    $deadline = (Get-Date).AddSeconds(60)
    $up = $false
    while ((Get-Date) -lt $deadline -and -not $up) {
        try { $up = (Invoke-WebRequest "http://127.0.0.1:$port/api/version" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200 }
        catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $up) { throw "ACP did not answer when started through $name" }

    $procs = @(Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*launcher.py*' })
    if ($procs.Count -ne 1) { throw "Expected one pythonw.exe running launcher.py, found $($procs.Count)" }
    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($procs[0].ProcessId)" |
        ForEach-Object { $_.Name.ToLower() })
    if ($children -contains 'conhost.exe') { throw "A console window opened when started through ${name}: $children" }
    Write-Host "ok: pythonw.exe, no conhost child ($name)"

    Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port/api/quit" -ContentType 'application/json' -Body '{}' | Out-Null
    $gone = $false
    for ($i = 0; $i -lt 40 -and -not $gone; $i++) {
        $gone = -not (Get-Process -Id $procs[0].ProcessId -ErrorAction SilentlyContinue)
        if (-not $gone) { Start-Sleep -Milliseconds 500 }
    }
    if (-not $gone) { throw "ACP did not stop after quit ($name)" }
}

try {
    Test-Start 'Start ACP.bat' { Start-Process -FilePath (Join-Path $Package 'Start ACP.bat') -WindowStyle Hidden }

    $menu = Join-Path $work 'start menu'
    $desk = Join-Path $work 'desktop'
    & $python -X utf8 -B (Join-Path $repo 'packaging\windows\make_test_links.py') $Package $menu $desk
    if ($LASTEXITCODE -ne 0) { throw 'Could not make the test shortcuts' }
    Test-Start 'a shortcut' { Start-Process -FilePath (Join-Path $desk 'Astro Coverage Planner.lnk') }
    Write-Host "`nstart-up checks passed"
}
finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
