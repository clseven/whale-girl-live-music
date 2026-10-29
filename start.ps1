param([ValidateSet('Start','Restart','Stop','Status')][string]$Action = 'Start', [switch]$NoOpen)
$ErrorActionPreference = 'Stop'
$releaseRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if ([IO.Path]::GetPathRoot($releaseRoot) -eq 'C:\') { throw '请将项目解压到D盘或F盘后启动。' }
$cacheRoot = Join-Path $releaseRoot '.cache'
New-Item -ItemType Directory -Path (Join-Path $cacheRoot 'temp') -Force | Out-Null
$env:TEMP = Join-Path $cacheRoot 'temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:WHALE_NO_WARMUP = '1'
$appRoot = Join-Path $releaseRoot '直播预览'
if (-not (Test-Path -LiteralPath (Join-Path $appRoot '.venv\Scripts\python.exe'))) { throw '请先运行 install.ps1 安装项目依赖。' }
& (Join-Path $appRoot '项目管理.ps1') -Action $Action -NoOpen:$NoOpen
exit $LASTEXITCODE
