[CmdletBinding()]
param(
    [ValidateSet('auto', 'codex', 'claude-code', 'both')]
    [string]$Client = 'auto',
    [string]$StateDir,
    [string]$CodexHome,
    [string]$ClaudeHome,
    [switch]$IncludeHeap
)

$ErrorActionPreference = 'Stop'
$winautoRepo = $PSScriptRoot
$winautoPython = Join-Path $winautoRepo '.venv\Scripts\python.exe'
$winautoStateRoot = if ($StateDir) { $StateDir } else { Join-Path $env:LOCALAPPDATA 'winauto-mcp' }
New-Item -ItemType Directory -Path $winautoStateRoot -Force | Out-Null
@{ok=$false; phase='runtime_dependencies'; started_at=[DateTime]::UtcNow.ToString('o')} |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $winautoStateRoot 'installation-result.json') -Encoding UTF8

function Invoke-WinAutoCommand {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Executable failed (exit $LASTEXITCODE). Installation is incomplete."
    }
}

function Install-WinAutoPackage {
    param([string]$PackageId)
    $winautoWinget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winautoWinget) {
        throw "Missing prerequisite $PackageId and winget is unavailable. Install the prerequisite and rerun."
    }
    Invoke-WinAutoCommand $winautoWinget.Source @(
        'install', '--id', $PackageId, '--exact', '--source', 'winget',
        '--silent', '--accept-package-agreements', '--accept-source-agreements'
    )
}

if (-not (Test-Path -LiteralPath $winautoPython)) {
    $winautoLauncher = Get-Command py -ErrorAction SilentlyContinue
    $winautoSystemPython = Get-Command python -ErrorAction SilentlyContinue
    if ($winautoLauncher) {
        Invoke-WinAutoCommand $winautoLauncher.Source @('-3', '-m', 'venv', (Join-Path $winautoRepo '.venv'))
    } elseif ($winautoSystemPython -and $winautoSystemPython.Source -notlike '*\WindowsApps\*') {
        Invoke-WinAutoCommand $winautoSystemPython.Source @('-m', 'venv', (Join-Path $winautoRepo '.venv'))
    } else {
        Install-WinAutoPackage 'Python.Python.3.12'
        $winautoNewPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        if (-not (Test-Path -LiteralPath $winautoNewPython)) {
            throw 'Python installed, but its executable could not be located. Restart PowerShell and rerun.'
        }
        Invoke-WinAutoCommand $winautoNewPython @('-m', 'venv', (Join-Path $winautoRepo '.venv'))
    }
}
Invoke-WinAutoCommand $winautoPython @('-c', 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required"')
Invoke-WinAutoCommand $winautoPython @('-m', 'pip', 'install', '--quiet', 'pip>=26.2.1')
Invoke-WinAutoCommand $winautoPython @('-m', 'pip', 'install', '--quiet', '-r', (Join-Path $winautoRepo 'requirements.txt'))
Invoke-WinAutoCommand $winautoPython @('-m', 'pip', 'check')

$winautoPlan = Get-Content -Raw -LiteralPath (Join-Path $winautoRepo 'installation.json') | ConvertFrom-Json
if ($winautoPlan.browser_extension.enabled) {
    $winautoNode = Get-Command node -ErrorAction SilentlyContinue
    if (-not $winautoNode) {
        Install-WinAutoPackage 'OpenJS.NodeJS.LTS'
        $winautoNodeFolder = Join-Path $env:ProgramFiles 'nodejs'
        $env:PATH = "$winautoNodeFolder;$env:PATH"
    }
}

if ($IncludeHeap) {
    $winautoDotnet = Get-Command dotnet -ErrorAction SilentlyContinue
    if (-not $winautoDotnet -or -not (& $winautoDotnet.Source --list-sdks)) {
        Install-WinAutoPackage 'Microsoft.DotNet.SDK.10'
        $winautoDotnetPath = Join-Path $env:ProgramFiles 'dotnet\dotnet.exe'
    } else {
        $winautoDotnetPath = $winautoDotnet.Source
    }
    $winautoGcDump = Join-Path $env:USERPROFILE '.dotnet\tools\dotnet-gcdump.exe'
    if (-not (Get-Command dotnet-gcdump -ErrorAction SilentlyContinue) -and -not (Test-Path -LiteralPath $winautoGcDump)) {
        Invoke-WinAutoCommand $winautoDotnetPath @('tool', 'install', '-g', 'dotnet-gcdump')
    }
    $winautoGcDumpCommand = Get-Command dotnet-gcdump -ErrorAction SilentlyContinue
    $winautoGcDumpPath = if ($winautoGcDumpCommand) { $winautoGcDumpCommand.Source } else { $winautoGcDump }
    Invoke-WinAutoCommand $winautoGcDumpPath @('--version')
}

$winautoInstallerArgs = @((Join-Path $winautoRepo 'scripts\install.py'), '--client', $Client)
if ($StateDir) { $winautoInstallerArgs += @('--state-dir', $StateDir) }
if ($CodexHome) { $winautoInstallerArgs += @('--codex-home', $CodexHome) }
if ($ClaudeHome) { $winautoInstallerArgs += @('--claude-home', $ClaudeHome) }
Invoke-WinAutoCommand $winautoPython $winautoInstallerArgs
