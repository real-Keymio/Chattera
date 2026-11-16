# Builds Chattera.exe (the Windows launcher) with the C# compiler that ships with Windows (.NET Framework 4).
#   powershell -ExecutionPolicy Bypass -File launcher\Build-Launcher.ps1                  # -> <repository>\Chattera.exe
#   powershell -ExecutionPolicy Bypass -File launcher\Build-Launcher.ps1 -Output D:\x\Chattera.exe
# Keep this file ASCII: Windows PowerShell 5.1 reads BOM-less files as ANSI.
param([string]$Output = '')
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
if (-not $Output) { $Output = Join-Path $Root 'Chattera.exe' }
$Output = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path (Split-Path $Output -Parent) | Out-Null
$icon = Join-Path $Root 'src\static\mc\favicon.ico'
$source = Join-Path $PSScriptRoot 'ChatteraLauncher.cs'

# A running launcher locks its file; only a launcher at exactly this path is closed.
Get-Process -Name 'Chattera' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $Output } | ForEach-Object { $_ | Stop-Process -Force }

$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $csc)) { throw 'The .NET Framework C# compiler (csc.exe) was not found.' }
$result = & $csc /nologo /target:winexe /optimize+ /codepage:65001 "/out:$Output" "/win32icon:$icon" /r:System.Windows.Forms.dll /r:System.Drawing.dll $source 2>&1
if ($LASTEXITCODE -ne 0) { throw ("Compilation failed:`n" + ($result -join "`n")) }
Write-Output "Launcher: $Output"
