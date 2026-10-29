param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$releaseRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if ([IO.Path]::GetPathRoot($releaseRoot) -eq 'C:\') { throw '请先把项目放到D盘或F盘，依赖和缓存保存在项目内。' }
$appRoot = Join-Path $releaseRoot '直播预览'
$cacheRoot = Join-Path $releaseRoot '.cache'
New-Item -ItemType Directory -Path (Join-Path $cacheRoot 'temp') -Force | Out-Null
$env:TEMP = Join-Path $cacheRoot 'temp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:npm_config_cache = Join-Path $cacheRoot 'npm'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:DOTNET_CLI_HOME = Join-Path $cacheRoot 'dotnet'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
Get-Command node -ErrorAction Stop | Out-Null
Get-Command npm.cmd -ErrorAction Stop | Out-Null
& $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw '需要Python 3.11或更高版本。' }
$venvPython = Join-Path $appRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv (Join-Path $appRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '创建项目虚拟环境失败。' }
}
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $appRoot 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Python依赖安装失败。' }
$hubRoot = Join-Path $releaseRoot '参考项目\dyhub'
Push-Location $hubRoot
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'DyHub依赖安装失败。' }
} finally { Pop-Location }
$config = Join-Path $appRoot 'local-config.json'
if (-not (Test-Path -LiteralPath $config)) { Copy-Item -LiteralPath (Join-Path $appRoot 'local-config.example.json') -Destination $config }
& $venvPython (Join-Path $appRoot 'setup_integrations.py')
if ($LASTEXITCODE -ne 0) { throw '连接器下载或DyHub编译失败。请检查网络后重试，原有配置不会被覆盖。' }
Write-Output '依赖安装完成。运行 configure.ps1 配置自己的模型，然后运行 start.ps1。'
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { Write-Output '网易云连接器还需要.NET 8 x64 Runtime，请按README安装。' }
