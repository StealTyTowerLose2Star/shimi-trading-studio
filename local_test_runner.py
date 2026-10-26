"""
[本地测试运行器] 在出站封锁(socket 层)下离线运行仓库自带冒烟测试 + 本地账号测试。
不触发真实行情/券商/推送接口: 一切非本机连接被拒绝并打印 [EGRESS-BLOCK]。
运行: venv/Scripts/python local_test_runner.py
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# 独立临时运行目录, 不触碰 runtime/ 与仓库数据
_tmp = tempfile.TemporaryDirectory(prefix="shimitest_")
os.environ["SHIMI_RUNTIME_DIR"] = _tmp.name

# 导入 local_run = 应用出站封锁 + Mock 注入 + 构建 app (不启动服务器)
import local_run  # noqa: F401

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for mod_name in ("test_smoke", "test_local_accounts"):
    try:
        mod = __import__(mod_name)
        suite.addTests(loader.loadTestsFromModule(mod))
    except Exception as e:
        print(f"[ runner ] 模块 {mod_name} 加载失败: {e}")

print("=" * 60)
print("离线冒烟 (出站封锁生效中, 真实接口不可达)")
print("=" * 60)
result = unittest.TextTestRunner(verbosity=2).run(suite)
print("=" * 60)
print(f"总计: {result.testsRun} 通过: {result.testsRun - len(result.failures) - len(result.errors)}"
      f" 失败: {len(result.failures)} 错误: {len(result.errors)}")
sys.exit(0 if result.wasSuccessful() else 1)
