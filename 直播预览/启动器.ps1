param(
    [switch]$NoOpen,
    [ValidateRange(5, 180)][int]$StartupTimeoutSeconds = 90
)
& (Join-Path $PSScriptRoot '项目管理.ps1') -Action Start -NoOpen:$NoOpen -StartupTimeoutSeconds $StartupTimeoutSeconds