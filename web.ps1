# 启动 Web 界面（使用本项目自己的环境）
# 用法: powershell -ExecutionPolicy Bypass -File .\web.ps1
$py = Join-Path $PSScriptRoot "tools\qwen3-tts-env\python.exe"
if (-not (Test-Path $py)) { Write-Error "找不到本项目环境: $py"; exit 1 }
Write-Host "启动中… 浏览器打开 http://127.0.0.1:8800" -ForegroundColor Green
& $py "$PSScriptRoot\server.py"
