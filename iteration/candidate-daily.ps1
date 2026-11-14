# I4 灰度观察名单：每日 17:00 生成默认 vs 候选信号对照 (只记录不改线上)
$ErrorActionPreference = "Continue"
$regEnv = Get-ItemProperty "HKCU:\Environment"
foreach ($name in @("TUSHARE_TOKEN","SHIMI_MARKET_USE","SHIMI_TUSHARE_PROXY")) {
    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        $v = $regEnv.$name
        if ($v) { [Environment]::SetEnvironmentVariable($name, $v, "Process") }
    }
}
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$logDir = "D:\Codex_Space\Jobs\shimi-local\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("candidate-daily-{0}.log" -f (Get-Date -Format "yyyyMMdd"))
& "D:\Codex_Space\Jobs\shimi-local\venv\Scripts\python.exe" -u "D:\Codex_Space\Jobs\shimi-local\iteration\candidate_daily.py" 2>&1 |
    Out-File -Append -Encoding utf8 $log
exit $LASTEXITCODE
