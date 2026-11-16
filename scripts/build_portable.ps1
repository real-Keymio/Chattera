# Builds the Windows x64 portable package:  <out>\Chattera-Windows-x64.zip
#   powershell -ExecutionPolicy Bypass -File scripts\build_portable.ps1 [-Out D:\builds] [-Guides D:\guides]
# Needs: .venv created by scripts\setup.ps1 (it installs PyInstaller from requirements-dev.txt).
# -Guides: folder holding the two user-guide PDFs (01_..., 02_...; default: docs\user-guide\dist).
# Keep this file ASCII: Windows PowerShell 5.1 reads BOM-less files as ANSI.
param([string]$Out = '', [string]$Guides = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
if (-not $Out) { $Out = Join-Path $Root 'dist' }
if (-not $Guides) { $Guides = Join-Path $Root 'docs\user-guide\dist' }
$Out = [IO.Path]::GetFullPath($Out)
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Run scripts\setup.ps1 first (it creates .venv with PyInstaller).' }

$work = Join-Path $Out 'work'
$stage = Join-Path $Out 'Chattera-Windows-x64'
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $work, $stage | Out-Null

Write-Host '[1/5] Server (PyInstaller, folder build)...'
& $python -m PyInstaller --noconfirm --clean --log-level WARN --distpath (Join-Path $work 'dist') --workpath (Join-Path $work 'build') (Join-Path $PSScriptRoot 'chattera.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
Move-Item -LiteralPath (Join-Path $work 'dist\chattera-server') -Destination (Join-Path $stage 'runtime')

Write-Host '[2/5] Launcher...'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root 'launcher\Build-Launcher.ps1') -Output (Join-Path $stage 'Chattera.exe') | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed' }

Write-Host '[3/5] Licenses and notices...'
& $python (Join-Path $PSScriptRoot 'collect_licenses.py') $stage
if ($LASTEXITCODE -ne 0) { throw 'License collection failed' }
Copy-Item -LiteralPath (Join-Path $Root 'LICENSE') -Destination (Join-Path $stage 'LICENSE.txt')
Copy-Item -LiteralPath (Join-Path $Root 'VERSION') -Destination (Join-Path $stage 'VERSION')
Copy-Item -LiteralPath (Join-Path $Root 'docs\portable\README.txt') -Destination (Join-Path $stage 'README.txt')

Write-Host '[4/5] User guides...'
$found = 0
foreach ($pdf in Get-ChildItem -LiteralPath $Guides -Filter '*.pdf' -ErrorAction SilentlyContinue) { Copy-Item -LiteralPath $pdf.FullName -Destination $stage; $found++ }
if ($found -lt 2) { Write-Warning "Expected two guide PDFs in $Guides, found $found." }

Write-Host '[5/5] Zip...'
$zip = Join-Path $Out 'Chattera-Windows-x64.zip'
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $true, [Text.Encoding]::UTF8)
$size = (Get-Item -LiteralPath $zip).Length
Write-Host ("Package: {0} ({1:N1} MB)" -f $zip, ($size / 1MB))
