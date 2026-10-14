param([string]$Python = "")
$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $workspace
if (-not $Python) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $Python = $bundledPython }
    else { $Python = (Get-Command python -ErrorAction Stop).Source }
}
$version = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.platform)"
if ($version[0] -ne '3.12' -or $version[1] -ne 'win32') { throw 'Use Windows CPython 3.12: .\scripts\start-demo.ps1 -Python C:\path\to\python.exe' }
$venvPython = Join-Path $workspace '.venv-cpython\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { & $Python -m venv .venv-cpython; if ($LASTEXITCODE) { throw 'venv failed' } }
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE) { throw 'Backend dependency installation failed' }
& $venvPython -m relief_platform.cli seed
if ($LASTEXITCODE) { throw 'Seed failed' }
Push-Location (Join-Path $workspace 'frontend')
try { npm ci; if ($LASTEXITCODE) { throw 'Frontend dependency installation failed' } } finally { Pop-Location }
foreach ($role in @('PLANNER','APPROVER','VIEWER')) {
    $name = 'RELIEF_'+$role+'_TOKEN'
    $existingToken = [Environment]::GetEnvironmentVariable($name, 'Process')
    if (-not $existingToken) {
        $bytes = New-Object byte[] 24
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $existingToken = [Convert]::ToBase64String($bytes)
        [Environment]::SetEnvironmentVariable($name, $existingToken, 'Process')
    }
    Write-Host "$role token: $existingToken"
}
$runtime = Join-Path $workspace 'relief_platform\runtime'
$backend = Start-Process -FilePath $venvPython -ArgumentList @('-m','uvicorn','relief_platform.api:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $workspace -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'backend.log') -RedirectStandardError (Join-Path $runtime 'backend-errors.log') -PassThru
$frontend = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm run dev -- --hostname 127.0.0.1') -WorkingDirectory (Join-Path $workspace 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtime 'frontend.log') -RedirectStandardError (Join-Path $runtime 'frontend-errors.log') -PassThru
Write-Host "Dashboard: http://localhost:3000 | API docs: http://localhost:8000/docs"
Write-Host "Backend PID: $($backend.Id); frontend launcher PID: $($frontend.Id). Logs: $runtime"
Write-Host "Stop backend with Stop-Process -Id $($backend.Id). Stop frontend and its child processes with taskkill /PID $($frontend.Id) /T /F."
