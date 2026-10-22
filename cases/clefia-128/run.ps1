param(
    [ValidateSet('build', 'flash', 'capture', 'analyze')][string]$Action = 'build',
    [int]$Count = 100,
    [string]$Python = 'python',
    [string]$CaptureDirectory = ''
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    switch ($Action) {
        'build' { & ./build.ps1 }
        'flash' { & $Python -u scripts/flash_verify.py }
        'capture' { & $Python -u scripts/capture.py --count $Count }
        'analyze' {
            if (-not $CaptureDirectory) { throw 'Set -CaptureDirectory to a local capture folder.' }
            & $Python scripts/analyze.py $CaptureDirectory
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "$Action failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}
