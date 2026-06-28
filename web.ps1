# 启动 Web 界面（复用 114 听书软件的 venv）
# 用法: powershell -ExecutionPolicy Bypass -File .\web.ps1
$py = "F:\我的编程项目\114_听书软件\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "找不到 114 项目的 venv: $py"; exit 1 }
Write-Host "启动中… 浏览器打开 http://127.0.0.1:8800" -ForegroundColor Green
& $py "$PSScriptRoot\server.py"
