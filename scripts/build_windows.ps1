$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT') { throw 'Build the Windows package on Windows.' }
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m PyInstaller --noconfirm --clean OWON_Tester.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
    $bundle = Join-Path $root 'dist/OWON-Tester-Windows'
    Copy-Item 'docs/WINDOWS_CUSTOMER.txt' (Join-Path $bundle 'README.txt')
    $report = Join-Path $root 'build/windows-smoke-test.json'
    $exe = Join-Path $bundle 'OWON_Tester.exe'
    $process = Start-Process -FilePath $exe -ArgumentList @('--smoke-test', "`"$report`"") -PassThru
    if (-not $process.WaitForExit(60000)) {
        $process.Kill()
        throw 'Packaged application smoke test timed out.'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Packaged smoke test failed; see $report" }
    if (-not (Test-Path $report)) { throw 'Smoke test did not create a report.' }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if (-not $result.ok) { throw "Smoke check failed: $($result.error)" }
    Compress-Archive -Path $bundle -DestinationPath 'dist/OWON-Tester-Windows.zip' -Force
    Write-Host 'Windows ZIP ready: dist/OWON-Tester-Windows.zip'
} finally {
    Pop-Location
}
