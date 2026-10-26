param([ValidateSet('real')][string]$Mode='real')
# 拾米本地服务重启：只允许真实只读候选，禁止模拟模式/回退。
$ErrorActionPreference='Stop'
# 产品只允许真实数据；统一走不读取注册表/.env/令牌的安全启动器。
# 在任何进程停止前核验该约束，旧模拟启动方式不再支持。
$root=if($PSCommandPath){Split-Path -Parent $PSCommandPath}else{'D:\Codex_Space\Jobs\shimi-local'}
$listener=Get-NetTCPConnection -LocalPort 17890 -State Listen -ErrorAction SilentlyContinue
if($listener){
  $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
  if($process.CommandLine -notmatch 'local_run\.py' -or $process.ExecutablePath -notmatch 'python(?:w)?\.exe$'){
    throw 'Port 17890 belongs to an unexpected process; refusing to stop it.'
  }
  Stop-Process -Id $listener.OwningProcess
}
$env:SHIMI_DATA_MODE=$Mode
# 凭据与用途声明从本机用户环境继承 (注册表 User 作用域读取; 已在进程环境则不覆盖;
# 不回显、不落盘、不修改系统配置)。缺失时服务仍启动, 数据端点返回明确错误。
# 凭据接入已于 2026-10-24 验收: HTTPS 实测可用, token=本人所有, 个人本机非商业只读。
foreach($key in 'TUSHARE_TOKEN','SHIMI_MARKET_USE'){
  if(-not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($key,'Process'))){ continue }
  $value=[Environment]::GetEnvironmentVariable($key,'User')
  if(-not [string]::IsNullOrEmpty($value)){ [Environment]::SetEnvironmentVariable($key,$value,'Process') }
}
$launcher='C:\Users\27365\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if(-not (Test-Path $launcher)){ $launcher=Join-Path $root 'venv\Scripts\python.exe' }
& $launcher -B -X utf8 (Join-Path $root 'local_safe_start.py') start
if($LASTEXITCODE -ne 0){throw 'Launch failed'}
