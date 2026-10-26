"""Credential-free preflight / token-inheriting start for local deployment.

preflight: real-wiring candidate on a temporary runtime and loopback port 17891,
with no credentials and no purpose declaration; explicit failure is required
instead of fake real data.
start: retain normal runtime and real-readonly mode, do not stop any old process.
The caller must verify/stop the exact prior local_run process separately.
start 仅从父进程环境继承 TUSHARE_TOKEN 与 SHIMI_MARKET_USE (由 restart-local.ps1
从本机用户环境读取; 本启动器本身不读注册表/.env, 不回显不落盘)。
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
BOOT = """
import runpy,sys,types
from pathlib import Path
root=Path(sys.argv[1])
sys.path.insert(0,str(root))
dotenv=types.ModuleType('dotenv')
dotenv.load_dotenv=lambda *args,**kwargs:False
sys.modules['dotenv']=dotenv
runpy.run_path(str(root/'local_run.py'),run_name='__main__')
"""


def launch(runtime, port, inherit_credentials=False):
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC'}}
    env.update(SHIMI_DATA_MODE='real', SHIMI_RUNTIME_DIR=str(runtime),
               SHIMI_PORT=str(port), TUSHARE_TOKEN='',
               PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               SHIMI_CORS_ORIGINS=f'http://127.0.0.1:{port}')
    if inherit_credentials:
        # start 模式: 操作者已在本机用户环境确认凭据与用途声明 → 原样传递;
        # 缺失则保持空值, 数据端点将明确失败 (不伪造、不回退)
        for key in ('TUSHARE_TOKEN', 'SHIMI_MARKET_USE'):
            if os.environ.get(key):
                env[key] = os.environ[key]
    runtime.mkdir(parents=True, exist_ok=True)
    log_path = runtime / 'safe-start.log'
    # Refuse to collide with any existing listener; never stop unknown processes.
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', port))
    with log_path.open('ab') as log:
        return subprocess.Popen([str(ROOT/'venv/Scripts/python.exe'), '-B', '-X',
                                 'utf8', '-c', BOOT, str(ROOT)], cwd=ROOT,
                                env=env, stdout=log, stderr=log,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def request(port, path, method='GET', body=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(f'http://127.0.0.1:{port}{path}', method=method,
                                 data=body, headers={'Content-Type':'application/json'})
    try:
        response = opener.open(req, timeout=4)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        data = response.read()
        return response.status, dict(response.headers), data


def ready(process, port):
    end = time.monotonic() + 35
    while time.monotonic() < end:
        if process.poll() is not None:
            raise RuntimeError(f'candidate exited {process.returncode}; inspect its safe-start log')
        try:
            status, _, body = request(port, '/api/local/modules')
            if status == 200:
                return json.loads(body)
        except (OSError, ValueError):
            pass
        time.sleep(0.3)
    raise TimeoutError('candidate not ready in 35 seconds')


def preflight():
    runtime = Path(tempfile.mkdtemp(prefix='shimi-preflight-', dir=ROOT.parent/'tmp'))
    process = launch(runtime, 17891)
    checks = 0
    def check(ok, label):
        nonlocal checks
        if not ok:
            raise AssertionError(label)
        checks += 1
        print('PASS '+label, flush=True)
    try:
        meta = ready(process, 17891)
        check(meta.get('data_backend') == 'tushare-eod', 'saved adapter is active')
        check(meta.get('source_verification') == 'not_verified', 'source remains unverified')
        check(meta.get('trading_enabled') is False, 'trading disabled')
        check(meta.get('real_data_only') is True, 'production real-only policy active')
        status, _, body = request(17891, '/api/health')
        check(status == 200 and json.loads(body).get('market_data_status') == 'not_checked',
              'health does not claim market success')
        for path in ['/api/indices', '/api/sectors', '/api/sector-flow',
                     '/api/hot-stocks', '/api/limit-up', '/api/sentiment',
                     '/api/stock/lookup?q=60', '/api/stock/600000/kline',
                     '/api/us/quote?symbol=AAPL']:
            status, _, body = request(17891, path)
            # HTTPS 传输已验收(2026-10-24); 无凭据/无用途声明时确定性失败为
            # purpose_unconfirmed (用途闸门先于令牌读取), 均为显式502而非伪造数据
            text = body.decode('utf-8')
            check(status == 502 and ('purpose_unconfirmed' in text or 'TUSHARE_TOKEN' in text),
                  path+' fails explicitly before credentials/purpose')
        status, headers, body = request(17891, '/api/dashboard')
        check(status == 200 and headers.get('X-Shimi-Degraded') == 'true'
              and json.loads(body).get('indices') is None, 'dashboard explicitly degraded')
        status, _, body = request(17891, '/')
        check(status == 200 and '未取得数据凭据' in body.decode('utf-8'),
              'home honestly shows no-credential state (not labelled verified)')
        status, _, _ = request(17891, '/api/trade', 'POST', b'{}')
        check(status == 403, 'write outside read-only scope denied')
        print(json.dumps({'preflight':'passed', 'checks':checks,
                          'runtime':str(runtime), 'market_connected':False}))
    finally:
        process.terminate()
        process.wait(timeout=10)
        # Retain this tiny synthetic runtime as audit evidence. No user data touched.


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['preflight','start'])
    args = parser.parse_args()
    if args.action == 'preflight':
        preflight()
    else:
        process = launch(ROOT/'runtime', 17890, inherit_credentials=True)
        meta = ready(process, 17890)
        print(json.dumps({'bootstrap_pid':process.pid, 'server_pid':meta.get('server_pid'),
                          'mode':meta.get('mode'),
                          'data_backend':meta.get('data_backend'),
                          'source_verification':meta.get('source_verification'),
                          'trading_enabled':meta.get('trading_enabled'),
                          'credential_inheritance':bool(os.environ.get('TUSHARE_TOKEN'))}))
