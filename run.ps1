# 用 114 听书软件的 venv（含 GPU torch / faster-whisper / F5-TTS）运行流水线
# 用法: powershell -ExecutionPolicy Bypass -File .\run.ps1 "https://www.youtube.com/watch?v=XXXX" [其它参数]
$py = "F:\我的编程项目\114_听书软件\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "找不到 114 项目的 venv: $py"; exit 1 }
& $py "$PSScriptRoot\pipeline.py" @args
