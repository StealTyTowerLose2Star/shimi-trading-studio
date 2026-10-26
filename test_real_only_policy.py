"""Test product rejects mock/unknown modes before touching runtime or databases.
Fictional values below are isolated test inputs, not product data.
"""
import ast
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
checks = 0
for mode in ('mock', 'synthetic', 'typo', ''):
    with tempfile.TemporaryDirectory(prefix='shimi-policy-') as folder:
        runtime = Path(folder)/'must-not-exist'
        env = {k:v for k,v in os.environ.items() if k.upper() in {'SYSTEMROOT','WINDIR','TEMP','TMP'}}
        env.update(SHIMI_DATA_MODE=mode, SHIMI_RUNTIME_DIR=str(runtime))
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', str(ROOT/'local_run.py')],
                                env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
        assert result.returncode != 0 and 'real_data_only' in result.stderr, mode
        assert not runtime.exists(), 'invalid mode touched runtime'
        checks += 1
# Execute the exact, pure response guard without importing app/DB/network.
tree = ast.parse((ROOT/'local_run.py').read_text(encoding='utf-8'))
node = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_contains_synthetic')
scope = {}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<exact-source-guard>','exec'),scope)
guard = scope['_contains_synthetic']
assert guard({'rows':[{'_mock':True,'price':10}]})
assert guard({'nested':{'_synthetic':True}})
assert not guard({'price':0,'change':-1,'data_status':'unavailable'})
checks += 3
assert 'os.environ.get("SHIMI_DATA_MODE", "real")' in (ROOT/'local_run.py').read_text(encoding='utf-8')
checks += 1
print(f'PASS {checks} real-only policy checks; invalid modes stopped before runtime creation')
