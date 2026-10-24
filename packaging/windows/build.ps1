<#
Builds ACP-windows-x64.zip. Runs on windows-latest (see .github/workflows/release-windows.yml).
Spec: docs/specs/windows-download-and-setup.md, section 3.7.

    pwsh packaging/windows/build.ps1 [-OutDir dist] [-Tag v1.0.0]

Needs on PATH: python (3.12, the same minor as packaging/pins.txt), git.
Writes into -OutDir: ACP-windows-x64.zip and SHA256SUMS-windows.txt.
#>
[CmdletBinding()]
param(
    [string]$OutDir = 'dist',
    [string]$Tag = '',
    [int]$MaxZipMB = 400,
    [int]$MaxPathChars = 150
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Run-Native {
    # Runs a native command and stops the build when it fails. $ErrorActionPreference
    # does not cover exit codes.
    param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$out = New-Item -ItemType Directory -Force -Path $OutDir
$out = $out.FullName
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("acp-build-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$stage = Join-Path $work 'ACP'
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# --- Pins ---------------------------------------------------------------------
$pins = @{}
foreach ($line in Get-Content (Join-Path $repo 'packaging\pins.txt')) {
    if ($line -match '^\s*([A-Z0-9_]+)=(.*)$') { $pins[$Matches[1]] = $Matches[2].Trim() }
}
$pyVersion = $pins['PYTHON_VERSION']
$minor = ($pyVersion -split '\.')[0..1] -join ''          # 3.12.10 -> 312
$runnerMinor = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($runnerMinor -ne (($pyVersion -split '\.')[0..1] -join '.')) {
    throw "The build Python is $runnerMinor but the package uses $pyVersion. They must share a minor version, because .pyc files are not portable across minors."
}

# --- 1. The embeddable Python ---------------------------------------------------
Step "Downloading Python $pyVersion (embeddable, x64)"
$embedZip = Join-Path $work 'python-embed.zip'
Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" -OutFile $embedZip
$actual = (Get-FileHash $embedZip -Algorithm SHA256).Hash.ToLower()
$expected = $pins['PYTHON_WINDOWS_EMBED_SHA256'].ToLower()
if ($expected -eq 'todo') {
    throw "packaging/pins.txt has no SHA-256 for the Python download yet. This build downloaded one with SHA-256 $actual. Check it against the python.org $pyVersion release page, then put it in pins.txt."
}
if ($actual -ne $expected) {
    throw "The Python download does not match packaging/pins.txt. Expected $expected, got $actual."
}
Expand-Archive -Path $embedZip -DestinationPath (Join-Path $stage 'python')

# --- 2. python312._pth ----------------------------------------------------------
# A ._pth file also makes the embeddable Python ignore PYTHON* environment
# variables, which is why Start ACP.bat passes -X utf8 and -B on the command line.
Step 'Writing the ._pth file'
$pth = @("python$minor.zip", '.', '..\site-packages', '..\app') -join "`r`n"
[System.IO.File]::WriteAllText((Join-Path $stage "python\python$minor._pth"), $pth + "`r`n")

# --- 3. Dependencies -------------------------------------------------------------
Step 'Building wheels for packages that publish only a source package'
# xisf is pure Python with no wheel on PyPI. Build it here, with the build Python,
# so the install below can stay wheels-only for everything else.
$wheels = Join-Path $work 'wheels'
New-Item -ItemType Directory -Force -Path $wheels | Out-Null
foreach ($line in Get-Content (Join-Path $repo 'packaging\sdist-only.txt')) {
    $req = ($line -replace '#.*$', '').Trim()
    if (-not $req) { continue }
    Run-Native 'python' @('-m', 'pip', 'wheel', '--disable-pip-version-check', '--no-deps',
        '-c', (Join-Path $repo 'packaging\constraints.txt'), '-w', $wheels, $req)
}
if (-not (Get-ChildItem $wheels -Filter '*.whl')) { throw 'No wheels were built from packaging/sdist-only.txt' }

Step 'Installing dependencies (wheels only)'
$site = Join-Path $stage 'site-packages'
Run-Native 'python' @('-m', 'pip', 'install', '--disable-pip-version-check', '--target', $site,
    '--only-binary=:all:', '--find-links', $wheels, '-c', (Join-Path $repo 'packaging\constraints.txt'),
    '-r', (Join-Path $repo 'requirements.txt'), '-r', (Join-Path $repo 'requirements-packaged.txt'))

# --- 4. Trim ----------------------------------------------------------------------
Step 'Trimming test folders and bytecode'
# astropy imports astropy.tests at start-up, so its folder stays.
$keep = @((Join-Path $site 'astropy\tests'))
Get-ChildItem -Path $site -Recurse -Directory -Force |
    Where-Object { $_.Name -in @('tests', 'test', '__pycache__') -and ($keep -notcontains $_.FullName) } |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object { if (Test-Path -LiteralPath $_.FullName) { Remove-Item -LiteralPath $_.FullName -Recurse -Force } }

# --- 5. The app, icons and start files --------------------------------------------
Step 'Copying the app'
$excluded = @('tests/', 'docs/', '.github/', 'notes/', 'packaging/')
Push-Location $repo
try { $files = & git -c core.quotepath=off ls-files } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed' }
$appDir = Join-Path $stage 'app'
foreach ($rel in $files) {
    if ($excluded | Where-Object { $rel.StartsWith($_) }) { continue }
    $dest = Join-Path $appDir $rel
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo $rel) -Destination $dest
}

Step 'Drawing the icons'
$env:PYTHONPATH = $site            # Pillow comes from the wheels installed above
Run-Native 'python' @((Join-Path $repo 'packaging\icons\make_icons.py'), (Join-Path $appDir 'static'))
Remove-Item Env:\PYTHONPATH

Step 'Writing Start ACP.bat, README.txt and VERSION.txt'
$version = (Select-String -Path (Join-Path $repo 'app.py') -Pattern '^VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
function Write-Crlf($path, $text) {
    $normal = ($text -replace "`r?`n", "`r`n")
    [System.IO.File]::WriteAllText($path, $normal, (New-Object System.Text.UTF8Encoding($false)))
}
Write-Crlf (Join-Path $stage 'Start ACP.bat') (Get-Content -Raw (Join-Path $PSScriptRoot 'Start ACP.bat'))
Write-Crlf (Join-Path $stage 'README.txt') ((Get-Content -Raw (Join-Path $PSScriptRoot 'README.txt')) -replace '\{\{VERSION\}\}', $version)
Write-Crlf (Join-Path $stage 'VERSION.txt') $version

# --- 6. Bytecode -------------------------------------------------------------------
# checked-hash, not unchecked-hash as the spec first said: a timestamp-based .pyc
# goes stale when zip's two-second resolution shifts the source time, and a hash
# stays valid. Checking the hash on import also keeps "edit a .py file to fix it"
# working, which unchecked-hash would silently ignore.
Step 'Compiling bytecode'
Run-Native 'python' @('-m', 'compileall', '-q', '-j', '0', '--invalidation-mode', 'checked-hash',
    '-s', $work, $site, $appDir)

# --- 7. Zip and checksums ----------------------------------------------------------
Step 'Checking the tree'
$checkArgs = @((Join-Path $repo 'packaging\check_tree.py'), '--tree', $stage, '--platform', 'windows',
    '--max-path', $MaxPathChars)
Run-Native 'python' $checkArgs

Step 'Zipping'
$zip = Join-Path $out 'ACP-windows-x64.zip'
if (Test-Path $zip) { Remove-Item $zip }
Run-Native 'python' @((Join-Path $repo 'packaging\zip_tree.py'), $stage, $zip)
$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
[System.IO.File]::WriteAllText((Join-Path $out 'SHA256SUMS-windows.txt'), "$hash  ACP-windows-x64.zip`n")

Step 'Checking the archive and the tag'
$final = @((Join-Path $repo 'packaging\check_tree.py'), '--tree', $stage, '--zip', $zip,
    '--platform', 'windows', '--max-zip-mb', $MaxZipMB, '--max-path', $MaxPathChars)
if ($Tag) { $final += @('--tag', $Tag) }
Run-Native 'python' $final

$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)
$unpackedMB = [math]::Round(((Get-ChildItem $stage -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
$count = (Get-ChildItem $stage -Recurse -File | Measure-Object).Count
Write-Host "`nBuilt $zip"
Write-Host "Size: zip $sizeMB MB, unpacked $unpackedMB MB, $count files"
if ($env:GITHUB_STEP_SUMMARY) {
    "### Windows package`n`nVersion $version. Zip $sizeMB MB, unpacked $unpackedMB MB, $count files.`n" |
        Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
}
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
