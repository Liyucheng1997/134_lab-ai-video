# 供 Windows 任务计划程序托管 Web 服务。不要直接关闭此脚本对应的任务进程。
$py = Join-Path $PSScriptRoot "tools\f5-tts-env\python.exe"
$server = Join-Path $PSScriptRoot "server.py"
$outLog = Join-Path $PSScriptRoot "web-server-task.out.log"
$errLog = Join-Path $PSScriptRoot "web-server-task.err.log"

if (-not (Test-Path -LiteralPath $py)) { exit 2 }
if (-not $env:PYTORCH_CUDA_ALLOC_CONF) {
  $env:PYTORCH_CUDA_ALLOC_CONF = "expandable_segments:True"
}
if (-not $env:F5_TTS_PARALLEL) {
  $env:F5_TTS_PARALLEL = "4"
}

$process = Start-Process -FilePath $py `
  -ArgumentList @("`"$server`"") `
  -WorkingDirectory $PSScriptRoot `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -WindowStyle Hidden `
  -PassThru `
  -Wait

exit $process.ExitCode
