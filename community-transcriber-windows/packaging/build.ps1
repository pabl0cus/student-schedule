param(
    [string]$Python = "python",
    [string]$InnoCompiler = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildEnvironment = Join-Path $ProjectRoot ".venv-build"
$TestTemp = Join-Path $BuildEnvironment "pytest-tmp"

& $Python -m venv --clear $BuildEnvironment
if ($LASTEXITCODE -ne 0) { throw "Could not create the build virtual environment." }
$BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"
& $BuildPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& $BuildPython -m pip install -e "${ProjectRoot}[dev,build]"
if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }
& $BuildPython -m pytest $ProjectRoot --basetemp $TestTemp -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
& $BuildPython -m ruff check (Join-Path $ProjectRoot "src") (Join-Path $ProjectRoot "tests")
if ($LASTEXITCODE -ne 0) { throw "Ruff failed." }

Push-Location $ProjectRoot
try {
    & $BuildPython -m PyInstaller --noconfirm --clean "packaging\worker.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    $SmokeExecutable = Join-Path $ProjectRoot "dist\StudentScheduleWorker\StudentScheduleWorker.exe"
    $SmokeProcess = Start-Process -FilePath $SmokeExecutable -ArgumentList @("--self-test") -PassThru -WindowStyle Hidden
    if (-not $SmokeProcess.WaitForExit(30000)) {
        $SmokeProcess.Kill()
        throw "Frozen worker self-test did not exit within 30 seconds."
    }
    if ($SmokeProcess.ExitCode -ne 0) {
        throw "Frozen worker self-test failed with exit code $($SmokeProcess.ExitCode)."
    }
    if (Test-Path -LiteralPath $InnoCompiler) {
        & $InnoCompiler "packaging\installer.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed." }
    } else {
        Write-Warning "Inno Setup not found; the portable onedir build is ready in dist."
    }
} finally {
    Pop-Location
}
