# Chattera from source on Windows: check Python, create .venv, install dependencies, run the tests, start.
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1            # everything, then start Chattera
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -NoStart   # install and test only
#   ... -WithOcr    also install the optional OCR component (large)
#   ... -SkipTests  skip the test run
# Nothing outside this folder is changed, except the data folder Chattera itself creates when it starts
# (%LOCALAPPDATA%\Chattera by default).
param([switch]$NoStart, [switch]$WithOcr, [switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $Root

function Find-Python {
    foreach ($candidate in @(@('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'), @('python'), @('python3'))) {
        $exe = $candidate[0]
        if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { continue }
        $rest = @($candidate | Select-Object -Skip 1)
        $version = & $exe @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $parts = $version.Trim().Split('.')
            if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { return ,$candidate }
        }
    }
    return $null
}

Write-Host '[1/5] Looking for Python 3.11 or newer...'
$py = Find-Python
if (-not $py) { throw 'Python 3.11+ was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this script again.' }
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Write-Host ('      using: ' + ($py -join ' '))

$venv = Join-Path $Root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host '[2/5] Creating the virtual environment .venv ...'
    & $pyExe @pyArgs -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv' }
} else { Write-Host '[2/5] .venv already exists; reusing it.' }

Write-Host '[3/5] Installing dependencies (this can take a few minutes the first time)...'
& $python -m pip install --disable-pip-version-check -q --upgrade pip
& $python -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed (see the messages above).' }
if ($WithOcr) {
    & $python -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements-ocr.txt')
    if ($LASTEXITCODE -ne 0) { throw 'OCR component installation failed.' }
}

if (-not $SkipTests) {
    Write-Host '[4/5] Running the tests...'
    & $python -m pytest -q -p no:cacheprovider (Join-Path $Root 'tests')
    if ($LASTEXITCODE -ne 0) { throw 'Some tests failed; Chattera was not started.' }
} else { Write-Host '[4/5] Tests skipped.' }

if ($NoStart) { Write-Host '[5/5] Done. Start later with:  .venv\Scripts\python.exe src\chattera.py'; return }
Write-Host '[5/5] Starting Chattera (Ctrl+C to stop). The address is printed below; open it in your browser.'
& $python (Join-Path $Root 'src\chattera.py')
