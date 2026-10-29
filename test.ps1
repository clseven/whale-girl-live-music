$ErrorActionPreference = 'Stop'
$appRoot = Join-Path $PSScriptRoot '直播预览'
$testTemp = Join-Path $PSScriptRoot '.cache\test-temp'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
Push-Location $appRoot
try {
    & '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests -p 'test_*.py'
    if ($LASTEXITCODE -ne 0) { throw 'Python离线测试失败。' }
    & node --test tests/test_actor.js tests/test_music_queue.cjs tests/test_music_queue.js tests/test_visual_parameters.cjs tests/test_voice_cancel.cjs tests/test_voice_player.js
    if ($LASTEXITCODE -ne 0) { throw '前端离线测试失败。' }
} finally { Pop-Location }
