# 用本项目自己的环境运行流水线
# 用法: powershell -ExecutionPolicy Bypass -File .\run.ps1 "https://www.youtube.com/watch?v=XXXX" [其它参数]
$py = Join-Path $PSScriptRoot "tools\qwen3-tts-env\python.exe"
if (-not (Test-Path $py)) { Write-Error "找不到本项目环境: $py"; exit 1 }
& $py "$PSScriptRoot\pipeline.py" @args
