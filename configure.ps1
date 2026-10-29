param([ValidateSet('codex','deepseek')][string]$Provider = 'codex', [string]$CodexProxy = '')
$ErrorActionPreference = 'Stop'
$appRoot = Join-Path $PSScriptRoot '直播预览'
$configPath = Join-Path $appRoot 'local-config.json'
if (-not (Test-Path -LiteralPath $configPath)) { Copy-Item -LiteralPath (Join-Path $appRoot 'local-config.example.json') -Destination $configPath }
$config = [IO.File]::ReadAllText($configPath) | ConvertFrom-Json
$config.provider = $Provider
$config.codex_proxy = $CodexProxy
if ($Provider -eq 'deepseek') {
    $secretDirectory = Join-Path $appRoot '.runtime'
    New-Item -ItemType Directory -Path $secretDirectory -Force | Out-Null
    $secretPath = Join-Path $secretDirectory 'secrets.json'
    $secrets = if (Test-Path -LiteralPath $secretPath) { [IO.File]::ReadAllText($secretPath) | ConvertFrom-Json } else { [pscustomobject]@{} }
    $secureKey = Read-Host '输入你自己的DeepSeek API Key（输入不回显，仅保存在本机）' -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try { $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer) }
    if ([string]::IsNullOrWhiteSpace($plainKey)) { throw 'API Key不能为空。' }
    $secrets | Add-Member -NotePropertyName deepseek_api_key -NotePropertyValue $plainKey -Force
    [IO.File]::WriteAllText($secretPath, ($secrets | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    $plainKey = $null
}
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
Write-Output '本机模型配置已保存。正在运行的后台需要重启后读取。没有修改全局Codex配置或系统代理。'
