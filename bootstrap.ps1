param([string]$GamePath = '', [ValidateSet(5,10)][int]$LureMinutes)
$ErrorActionPreference = 'Stop'
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw '64-bit Windows is required.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'FishingBot\Python311\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        'C:\Python311\python.exe'
    )
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $discovered = & $launcher.Source -3.11 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += [string]$discovered }
        } catch { }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') { $candidates += $pythonCommand.Source }
    $pythonExe = $null
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            & $candidate -c 'import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,11) and struct.calcsize(chr(80)) == 8 else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { $pythonExe = $candidate; break }
        }
    }
    if (-not $pythonExe) {
        Write-Host 'Python 3.11 x64 is missing. Downloading Python 3.11.9 from python.org...'
        $cacheDir = Join-Path $env:TEMP ('FishingBot-Python-' + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $cacheDir | Out-Null
        $pythonInstaller = Join-Path $cacheDir 'python-3.11.9-amd64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $pythonInstaller
        $expected = '5EE42C4EEE1E6B4464BB23722F90B45303F79442DF63083F05322F1785F5FDDE'
        if ((Get-FileHash -LiteralPath $pythonInstaller -Algorithm SHA256).Hash -ne $expected) { throw 'Python download checksum mismatch.' }
        $runtimeDir = Join-Path $env:LOCALAPPDATA 'FishingBot\Python311'
        $arguments = '/quiet InstallAllUsers=0 Include_launcher=0 Include_test=0 Include_doc=0 Include_pip=1 PrependPath=0 TargetDir="' + $runtimeDir + '"'
        $process = Start-Process -FilePath $pythonInstaller -ArgumentList $arguments -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw ('Python installation failed: ' + $process.ExitCode) }
        $pythonExe = Join-Path $runtimeDir 'python.exe'
        if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Installed Python was not found.' }
    }
    $env:PYTHONUTF8 = '1'
    $installerArgs = @((Join-Path $PSScriptRoot 'install.py'))
    if ($PSBoundParameters.ContainsKey('LureMinutes')) { $installerArgs += @('--lure-minutes', [string]$LureMinutes) }
    if ($GamePath) { $installerArgs += @('--game', $GamePath) }
    & $pythonExe @installerArgs
    exit $LASTEXITCODE
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
