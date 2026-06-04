#!/usr/bin/env python3
"""
🧼 舒肤佳安全守卫 — Hermes 信息系统安全守护神
============================================
覆盖:
  1. 恶意代码/木马检测 (skills/插件)
  2. 模块解耦性 & 循环依赖检测
  3. 内存泄漏风险 & 代码合规性
  4. 数据治理 & 统一规范审查
  5. 密钥硬编码/敏感文件/日志泄露扫描
  6. HTTPS/CORS/JWT 安全漏洞检查

输出: JSON 报告到 stdout，详细日志到文件
定时: 由 hermes cron 调度运行
"""

import ast
import json
import os
import re
import sys
import time
import pathlib
import datetime
import collections
import importlib.util

# ─── 配置 ──────────────────────────────────────────
HERMES_HOME = os.path.expanduser("~/.hermes")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
PLUGINS_DIR = os.path.join(HERMES_HOME, "plugins")
CRON_DIR = os.path.join(HERMES_HOME, "cron")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
ENV_PATH = os.path.join(HERMES_HOME, ".env")
PROJECT_DIRS = [
    "/root/shi-mi-dashboard",
]
REPORT_DIR = os.path.join(HERMES_HOME, "cache", "security")
os.makedirs(REPORT_DIR, exist_ok=True)

REPORT_HISTORY = os.path.join(REPORT_DIR, "findings_history.jsonl")
REPORT_SUMMARY = os.path.join(REPORT_DIR, "latest_summary.json")

# ─── 恶意代码特征库 ─────────────────────────────────
MALICIOUS_PATTERNS = [
    # 远程控制/反弹 shell
    (r"subprocess\.(Popen|call|run|check_call|check_output).*shell\s*=\s*True", "危险: shell=True 调用"),
    (r"os\.system\(.*['\"].*rm\s+-rf\s+.*\/.*['\"]", "危险: rm -rf /"),
    (r"exec\(.*request", "危险: exec(用户输入)"),
    (r"eval\(.*request", "危险: eval(用户输入)"),
    (r"__import__\('os'\)\.system", "危险: 动态 import os.system"),
    (r"socket\.(connect|bind).*\(.*\[?['\"]0\.0\.0\.0['\"]\)?", "可疑: socket 绑定"),
    (r"base64\.b64decode\(.*\)\s*\)", "可疑: base64 解码执行"),
    (r"marshal\.loads", "可疑: marshal 序列化反解"),
    (r"pickle\.loads\(.*request", "危险: pickle 加载外部数据"),
    (r"compile\(.*['\"].*['\"].*['\"](exec|eval)", "危险: compile(..., 'exec')"),

    # 数据窃取
    (r"open\(.*['\"].*\.(pem|key|env|token|secret)['\"]", "可疑: 读取敏感文件"),
    (r"os\.environ\b", "可疑: 读取环境变量（确认场景）"),
    (r"requests?\.(get|post|put)\(.*['\"](https?://\d+\.\d+\.\d+\.\d+)", "可疑: 外发到纯 IP"),
    (r"SMTP|smtplib\.SMTP", "可疑: SMTP 邮件外发"),

    # 隐藏后门
    (r"\.pyc\b.*__pycache__", "可疑: pyc 缓存文件"),
    (r"compile\(.*__pycache__", "可疑: 编译缓存操作"),

    # 文件篡改
    (r"shutil\.(rmtree|remove).*['\"].*\.(py|yaml|json|db)", "危险: 删除项目文件"),
    (r"os\.(remove|unlink).*['\"].*\.(py|yaml|json|db)", "危险: 删除项目文件"),
]

# ─── 密钥硬编码特征 ─────────────────────────────────
SECRET_PATTERNS = [
    (r"(?i)(api_key|apikey|token|secret|password|passwd)\s*[=:]\s*['\"][a-zA-Z0-9_\-\.]{16,}['\"]", "API Key/Token 硬编码"),
    (r"(?i)(tushare_token|openai_key|anthropic_key|deepseek_key)\s*[=:]\s*['\"][a-zA-Z0-9_\-\.]{8,}['\"]", "服务 Token 硬编码"),
    (r"(?i)(access_key|secret_key|private_key|auth_token)\s*[=:]\s*['\"][a-zA-Z0-9_\-\.\/\+]{16,}['\"]", "访问密钥硬编码"),
    (r"(?i)(jwt_secret|jwt_key|auth_secret)\s*[=:]\s*['\"][a-zA-Z0-9_\-\.]{8,}['\"]", "JWT 密钥硬编码"),
    (r"['\"][A-Za-z0-9_\-\.]{32,}['\"]", "长随机字符串（可疑密钥）"),
    (r"os\.environ\b", "环境变量读取（需确认用途）"),
]

# ─── 内存泄漏风险特征 ──────────────────────────────
MEMORY_LEAK_PATTERNS = [
    (r"global\s+\w+\s*=", "全局变量赋值（可能阻止GC）"),
    (r"\.append\(.*\)\s*#?\s*.*循环", "循环内 list.append（需确认上限）"),
    (r"while\s+True.*read", "无限循环读取（可能OOM）"),
    (r"\w+\.extend\(.*\)\s*#?\s*.*[\w+~]*$", "list.extend 无限制"),
    (r"open\(.*\)\s*$", "open 未用 with/close（资源泄漏）"),
    (r"\.write\(.*\)", "文件写入（检查是否关闭）"),
]

# ─── JWT/CORS/HTTPS 安全缺陷特征 ────────────────────
SECURITY_VULN_PATTERNS = [
    (r"CORS\(app.*origins?\s*=\s*['\"][*]['\"]", "高危: CORS 允许所有来源 (*)"),
    (r"app\.run\(.*debug\s*=\s*True", "高危: Flask debug 模式启用"),
    (r"app\.run\(.*host\s*=\s*['\"]0\.0\.0\.0['\"]", "注意: 绑定所有接口（需验证环境）"),
    (r"(?i)ssl_certificate\s*=\s*['\"]{0,2}$", "缺失: SSL 证书未配置"),
    (r"(?i)jwt.*=.*['\"][a-z0-9]{1,8}['\"]", "可疑: JWT secret 过短"),
]

# ─── 工具函数 ──────────────────────────────────────


def find_python_files(directory):
    """递归查找目录下所有 .py 文件"""
    py_files = []
    if not os.path.isdir(directory):
        return py_files
    for root, dirs, files in os.walk(directory):
        # 跳过 .git, __pycache__, node_modules, venv
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules',
                                                  'venv', '.venv', '.hermes', '__pycache__')]
        for f in files:
            if f.endswith('.py'):
                py_files.append(os.path.join(root, f))
    return py_files


def find_md_json_yaml_files(directory):
    """递归查找配置/文档文件"""
    files = []
    if not os.path.isdir(directory):
        return files
    for root, dirs, fnames in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__', 'node_modules',
                                                  'venv', '.venv', '__pycache__')]
        for f in fnames:
            if f.endswith(('.md', '.json', '.yaml', '.yml', '.toml')):
                files.append(os.path.join(root, f))
    return files


def scan_file_for_patterns(filepath, patterns, context_lines=2):
    """扫描文件匹配模式，返回匹配结果列表"""
    results = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        return [{"file": filepath, "error": str(e)}]

    lines = content.split('\n')
    for pattern, desc in patterns:
        matches = list(re.finditer(pattern, content, re.MULTILINE))
        for m in matches:
            line_no = content[:m.start()].count('\n') + 1
            ctx_start = max(0, line_no - context_lines - 1)
            ctx_end = min(len(lines), line_no + context_lines)
            context_snippet = '\n'.join(
                f"{i+1}:{lines[i]}" for i in range(ctx_start, ctx_end)
            )
            results.append({
                "file": filepath,
                "line": line_no,
                "severity": "high" if "高危" in desc or "危险" in desc else "medium",
                "type": desc,
                "match": m.group()[:120],
                "context": context_snippet,
            })
    return results


# ─── 检测模块 ──────────────────────────────────────


def scan_malware():
    """🦠 检测 1: 恶意代码/木马扫描 - skills + plugins"""
    findings = []
    scanned_files = 0

    # 扫描所有 skills 中的 .py 文件
    if os.path.isdir(SKILLS_DIR):
        py_files = find_python_files(SKILLS_DIR)
        scanned_files += len(py_files)
        for pf in py_files:
            findings.extend(scan_file_for_patterns(pf, MALICIOUS_PATTERNS))

    # 扫描 plugins
    if os.path.isdir(PLUGINS_DIR):
        py_files = find_python_files(PLUGINS_DIR)
        scanned_files += len(py_files)
        for pf in py_files:
            findings.extend(scan_file_for_patterns(pf, MALICIOUS_PATTERNS))

    # 扫描 cron 相关
    if os.path.isdir(CRON_DIR):
        for root, dirs, files in os.walk(CRON_DIR):
            for f in files:
                if f.endswith(('.sh', '.py')):
                    fp = os.path.join(root, f)
                    scanned_files += 1
                    findings.extend(scan_file_for_patterns(fp, MALICIOUS_PATTERNS))

    return {
        "module": "malware_scan",
        "status": "PASS" if not findings else "WARN",
        "summary": f"扫描 {scanned_files} 个文件，发现 {len(findings)} 个异常",
        "findings": findings,
        "scanned_files": scanned_files,
    }


def scan_secrets():
    """🔑 检测 5: 密钥硬编码/敏感文件泄露扫描"""
    findings = []
    scanned_files = 0

    for proj_dir in PROJECT_DIRS:
        py_files = find_python_files(proj_dir)
        cfg_files = find_md_json_yaml_files(proj_dir)

        for pf in py_files:
            scanned_files += 1
            findings.extend(scan_file_for_patterns(pf, SECRET_PATTERNS))

        for cf in cfg_files:
            scanned_files += 1
            findings.extend(scan_file_for_patterns(cf, SECRET_PATTERNS))

    # 扫描 skills 目录
    if os.path.isdir(SKILLS_DIR):
        for root, dirs, files in os.walk(SKILLS_DIR):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
            for f in files:
                if f.endswith('.py'):
                    fp = os.path.join(root, f)
                    scanned_files += 1
                    findings.extend(scan_file_for_patterns(fp, SECRET_PATTERNS))

    # 检查 .env 文件是否存在敏感信息
    if os.path.isfile(ENV_PATH):
        scanned_files += 1
        with open(ENV_PATH, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    if val and val.strip('"\'') and val != '***' and val != '':
                        if len(val.strip('"\'')) > 8:
                            findings.append({
                                "file": ENV_PATH,
                                "line": 0,
                                "severity": "info",
                                "type": ".env 含实际密钥（需确认）",
                                "match": f"{key}=***redacted***",
                                "context": line,
                            })

    return {
        "module": "secret_leak_scan",
        "status": "PASS" if not findings else "WARN",
        "summary": f"扫描 {scanned_files} 个文件，发现 {len(findings)} 个潜在泄露",
        "findings": findings,
        "scanned_files": scanned_files,
    }


def scan_decoupling():
    """🔌 检测 2: 模块解耦性 & 循环依赖检测"""
    findings = []
    import_graph = {}

    for proj_dir in PROJECT_DIRS:
        if not os.path.isdir(proj_dir):
            continue
        py_files = find_python_files(proj_dir)

        for pf in py_files:
            rel_path = os.path.relpath(pf, proj_dir)
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    tree = ast.parse(f.read())
            except SyntaxError:
                findings.append({
                    "file": rel_path,
                    "severity": "warning",
                    "type": "语法解析错误",
                    "detail": "无法解析 AST",
                })
                continue

            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)

            import_graph[rel_path] = imports

        # 检测循环依赖
        def find_cycles(graph):
            WHITE, GRAY, BLACK = 0, 1, 2
            color = {n: WHITE for n in graph}
            cycles = []

            def dfs(node, path):
                color[node] = GRAY
                for neighbor in graph.get(node, []):
                    # 只检查项目内部依赖
                    if neighbor not in graph:
                        continue
                    if color[neighbor] == GRAY:
                        # 找到循环
                        cycle_start = path.index(neighbor)
                        cycle_path = path[cycle_start:] + [neighbor]
                        cycles.append(" → ".join(cycle_path))
                    elif color[neighbor] == WHITE:
                        dfs(neighbor, path + [neighbor])
                color[node] = BLACK

            for node in graph:
                if color[node] == WHITE:
                    dfs(node, [node])
            return cycles

        cycles = find_cycles(import_graph)
        for c in cycles:
            findings.append({
                "severity": "high",
                "type": "循环依赖",
                "detail": c,
            })

        # 检测超大模块（内聚性差）
        for pf in py_files:
            rel_path = os.path.relpath(pf, proj_dir)
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            lines = content.count('\n')

            if lines > 500:
                findings.append({
                    "file": rel_path,
                    "severity": "medium",
                    "type": "模块过大",
                    "detail": f"{lines} 行（建议 < 500 行，考虑拆分）",
                })
            elif lines > 1000:
                findings.append({
                    "file": rel_path,
                    "severity": "high",
                    "type": "模块严重过大",
                    "detail": f"{lines} 行（强烈建议拆分）",
                })

    return {
        "module": "decoupling_check",
        "status": "PASS" if not [f for f in findings if f.get('severity') in ('high', 'warning')] else "WARN",
        "summary": f"检测 {len(import_graph)} 个模块，发现 {len(findings)} 个耦合问题",
        "findings": findings,
        "modules_scanned": len(import_graph),
    }


def scan_memory_leaks():
    """💾 检测 3: 内存泄漏风险 & 代码合规性"""
    findings = []
    scanned_files = 0

    for proj_dir in PROJECT_DIRS:
        py_files = find_python_files(proj_dir)
        for pf in py_files:
            scanned_files += 1
            findings.extend(scan_file_for_patterns(pf, MEMORY_LEAK_PATTERNS))

    # 特殊检测: 大列表/字典无上限
    for proj_dir in PROJECT_DIRS:
        if not os.path.isdir(proj_dir):
            continue
        py_files = find_python_files(proj_dir)
        for pf in py_files:
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            # 检测未关闭的文件句柄
            open_without_with = re.findall(
                r'^\\s*open\\([^)]*\\)\\s*$',
                content, re.MULTILINE
            )
            if open_without_with and 'context' not in pf:
                findings.append({
                    "file": os.path.relpath(pf, proj_dir),
                    "severity": "warning",
                    "type": "资源泄漏风险",
                    "detail": f"发现 {len(open_without_with)} 处 open() 可能未用 with 语句",
                })

    return {
        "module": "memory_leak_scan",
        "status": "PASS" if not findings else "WARN",
        "summary": f"扫描 {scanned_files} 个文件，发现 {len(findings)} 个潜在风险",
        "findings": findings,
        "scanned_files": scanned_files,
    }


def scan_data_governance():
    """🗄️ 检测 4: 数据治理 & 统一规范审查"""
    findings = []
    checked_files = 0

    for proj_dir in PROJECT_DIRS:
        if not os.path.isdir(proj_dir):
            continue

        # 检查是否有统一的数据层（data/ 目录或 datasource 类）
        if os.path.isdir(os.path.join(proj_dir, "data")):
            data_files = [f for f in os.listdir(os.path.join(proj_dir, "data"))
                          if f.endswith('.py')]
            if data_files:
                findings.append({
                    "file": "data/",
                    "severity": "info",
                    "type": "数据层存在",
                    "detail": f"data/ 目录含 {len(data_files)} 个模块",
                })
            else:
                findings.append({
                    "file": "data/",
                    "severity": "info",
                    "type": "数据层空目录",
                    "detail": "data/ 目录无 Python 模块",
                })
        else:
            if proj_dir == PROJECT_DIRS[0]:
                # shimi-dashboard 应该有 data/ 目录
                findings.append({
                    "file": proj_dir,
                    "severity": "warning",
                    "type": "缺少统一数据层",
                    "detail": "无 data/ 目录，数据获取分散在各模块",
                })

        # 检查是否使用统一配置模块
        config_file = os.path.join(proj_dir, "config.py")
        if os.path.isfile(config_file):
            checked_files += 1
            with open(config_file, 'r', encoding='utf-8') as f:
                content = f.read()
                has_validate = "def validate" in content
                if has_validate:
                    findings.append({
                        "file": "config.py",
                        "severity": "info",
                        "type": "配置验证函数存在",
                        "detail": "config.py 含 validate() 验证函数",
                    })
                else:
                    findings.append({
                        "file": "config.py",
                        "severity": "info",
                        "type": "建议增加配置验证",
                        "detail": "config.py 无 validate() 函数",
                    })
        else:
            findings.append({
                "file": proj_dir,
                "severity": "warning",
                "type": "无统一配置模块",
                "detail": "建议创建 config.py 集中管理配置",
            })

        # 检查缓存是否统一管理
        cache_file = os.path.join(proj_dir, "cache.py")
        if os.path.isfile(cache_file):
            checked_files += 1
            with open(cache_file, 'r') as f:
                content = f.read()
                ttl_defs = re.findall(r'(?:TTL|ttl|timeout)\s*[=:]\s*\d+', content)
                findings.append({
                    "file": "cache.py",
                    "severity": "info",
                    "type": "缓存统一管理",
                    "detail": f"缓存模块含 {len(ttl_defs)} 个 TTL 定义",
                })
        else:
            findings.append({
                "file": proj_dir,
                "severity": "warning",
                "type": "无统一缓存模块",
                "detail": "建议创建 cache.py 管理缓存策略",
            })

        # 检查数据库是否通过统一层访问
        db_file = os.path.join(proj_dir, "db.py")
        if os.path.isfile(db_file):
            checked_files += 1
            findings.append({
                "file": "db.py",
                "severity": "info",
                "type": "数据库统一层存在",
                "detail": "db.py 统一管理数据库访问",
            })

        # 检查技能目录中的代码是否有数据规范
        skill_py_files = find_python_files(SKILLS_DIR)
        for sf in skill_py_files:
            checked_files += 1
            try:
                with open(sf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if 'tushare' in content or 'pro_api' in content:
                    findings.append({
                        "file": os.path.relpath(sf, SKILLS_DIR),
                        "severity": "info",
                        "type": "Skill直接引用数据源",
                        "detail": "建议通过统一数据层获取，而非直接调用 API",
                    })
            except Exception:
                pass

    return {
        "module": "data_governance",
        "status": "PASS",
        "summary": f"检查 {checked_files} 个数据相关文件",
        "findings": findings,
        "checked_files": checked_files,
    }


def scan_security_vulns():
    """🛡️ 检测 6: HTTPS/CORS/JWT 安全漏洞"""
    findings = []
    checked_files = 0

    for proj_dir in PROJECT_DIRS:
        py_files = find_python_files(proj_dir)
        for pf in py_files:
            checked_files += 1
            findings.extend(scan_file_for_patterns(pf, SECURITY_VULN_PATTERNS))

        # 额外检查
        backend_file = os.path.join(proj_dir, "backend.py")
        if os.path.isfile(backend_file):
            with open(backend_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # 检查 CORS 配置
            cors_match = re.search(r'CORS\(([^)]*)\)', content)
            if cors_match:
                cors_args = cors_match.group(1)
                if '*' in cors_args:
                    findings.append({
                        "file": "backend.py",
                        "severity": "high",
                        "type": "CORS 配置过松",
                        "match": cors_match.group()[:100],
                    })
                else:
                    findings.append({
                        "file": "backend.py",
                        "severity": "info",
                        "type": "CORS 已配置白名单",
                        "detail": cors_match.group()[:100],
                    })
            else:
                findings.append({
                    "file": "backend.py",
                    "severity": "info",
                    "type": "未显式配置 CORS",
                })

            # 检查 JWT Token 有效期
            token_match = re.search(r'TOKEN_EXPIRY_HOURS.*?(\d+)', content)
            if token_match:
                hours = int(token_match.group(1))
                if hours > 168:
                    findings.append({
                        "file": "backend.py",
                        "severity": "medium",
                        "type": "Token 有效期过长",
                        "detail": f"{hours}h（建议 ≤ 72h）",
                    })

        # 检查配置文件中的 SSL/HTTPS
        nginx_dir = os.path.join(proj_dir, "nginx")
        if os.path.isdir(nginx_dir):
            for nf in os.listdir(nginx_dir):
                nginx_file = os.path.join(nginx_dir, nf)
                checked_files += 1
                with open(nginx_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                if 'ssl_certificate' not in content:
                    findings.append({
                        "file": f"nginx/{nf}",
                        "severity": "medium",
                        "type": "Nginx 无 SSL 配置",
                        "detail": "未配置 ssl_certificate",
                    })
                if 'return 301' not in content and 'rewrite' not in content:
                    findings.append({
                        "file": f"nginx/{nf}",
                        "severity": "medium",
                        "type": "HTTP→HTTPS 重定向缺失",
                        "detail": "未配置 80→443 自动跳转",
                    })
                # 检查头安全策略
                security_headers = ['X-Content-Type-Options', 'X-Frame-Options',
                                    'Content-Security-Policy', 'Strict-Transport-Security']
                missing_headers = [h for h in security_headers if h not in content]
                if missing_headers:
                    findings.append({
                        "file": f"nginx/{nf}",
                        "severity": "info",
                        "type": "安全响应头缺失",
                        "detail": f"缺失: {', '.join(missing_headers)}",
                    })

        # 检查 Dockerfile 是否用非 root 用户
        dockerfile = os.path.join(proj_dir, "Dockerfile")
        if os.path.isfile(dockerfile):
            checked_files += 1
            with open(dockerfile, 'r') as f:
                content = f.read()
            if 'USER' not in content:
                findings.append({
                    "file": "Dockerfile",
                    "severity": "medium",
                    "type": "Docker 未设置非 root 用户",
                    "detail": "建议添加 USER nobody 或创建专用用户",
                })

    return {
        "module": "security_vuln_scan",
        "status": "PASS" if not [f for f in findings if f.get('severity') == 'high'] else "WARN",
        "summary": f"检查 {checked_files} 个配置文件，发现 {len(findings)} 个安全问题",
        "findings": findings,
        "checked_files": checked_files,
    }


# ─── 报告生成 ──────────────────────────────────────


def generate_report(all_results):
    """生成完整安全报告"""
    now = datetime.datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    # 计算总体评分
    total_findings = sum(len(r.get("findings", [])) for r in all_results)
    high_sev = sum(
        1 for r in all_results
        for f in r.get("findings", [])
        if f.get("severity") == "high"
    )
    medium_sev = sum(
        1 for r in all_results
        for f in r.get("findings", [])
        if f.get("severity") == "medium"
    )
    modules_passed = sum(1 for r in all_results if r.get("status") == "PASS")
    modules_total = len(all_results)

    # 安全评分（满分 100）
    score = 100
    score -= high_sev * 15  # 高危 -15 分
    score -= medium_sev * 2  # 中危 -2 分
    score = max(10, min(100, score))

    report = {
        "timestamp": timestamp,
        "report_id": now.strftime("SEC-%Y%m%d-%H%M%S"),
        "overall_score": score,
        "overall_grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D",
        "summary": {
            "modules_passed": modules_passed,
            "modules_total": modules_total,
            "total_findings": total_findings,
            "high_severity": high_sev,
            "medium_severity": medium_sev,
        },
        "modules": {},
        "priority_actions": [],
    }

    for result in all_results:
        module_name = result.get("module", "unknown")
        report["modules"][module_name] = {
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("summary", ""),
            "findings": result.get("findings", []),
        }

        # 收集优先处理项
        for f in result.get("findings", []):
            if f.get("severity") == "high":
                report["priority_actions"].append(
                    f"[高危] {f.get('type', '')} → {f.get('file', '')}:{f.get('line', '')}"
                )

    return report


def print_report(report, verbose=False):
    """打印报告到终端"""
    now = datetime.datetime.now()
    print(f"\n{'='*60}")
    print(f"  🧼 舒肤佳安全守卫 — 安全巡检报告")
    print(f"  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    grade = report["overall_grade"]
    if grade == "A":
        grade_icon = "🟢"
    elif grade == "B":
        grade_icon = "🟡"
    elif grade == "C":
        grade_icon = "🟠"
    else:
        grade_icon = "🔴"

    print(f"\n  综合评分: {report['overall_score']}/100  {grade_icon} [{grade}]")
    print(f"  模块通过: {report['summary']['modules_passed']}/{report['summary']['modules_total']}")
    print(f"  高危: {report['summary']['high_severity']}  |  中危: {report['summary']['medium_severity']}  |  总计: {report['summary']['total_findings']}")

    print(f"\n{'─'*60}")
    for module_name, module_data in report["modules"].items():
        icon = "✅" if module_data["status"] == "PASS" else "⚠️"
        print(f"  {icon} [{module_data['status']}] {module_name}")
        print(f"     {module_data['summary']}")
        if verbose and module_data["findings"]:
            for f in module_data["findings"][:5]:
                sev_icon = "🔴" if f.get("severity") == "high" else "🟡" if f.get("severity") == "medium" else "💡"
                print(f"     {sev_icon} [{f.get('severity','info').upper()}] {f.get('type','')}")
                if f.get("file"):
                    line_str = f":{f.get('line')}" if f.get("line") else ""
                    print(f"        └─ {f.get('file')}{line_str}")
            if len(module_data["findings"]) > 5:
                print(f"     ... 还有 {len(module_data['findings'])-5} 项")

    if report["priority_actions"]:
        print(f"\n{'!'*60}")
        print(f"  🚨 优先处理事项:")
        for action in report["priority_actions"][:10]:
            print(f"    • {action}")
        if len(report["priority_actions"]) > 10:
            print(f"    ... 还有 {len(report['priority_actions'])-10} 项")

    print(f"\n{'='*60}\n")


def save_report(report):
    """保存报告到文件"""
    # 保存最新摘要
    with open(REPORT_SUMMARY, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 追加到历史
    with open(REPORT_HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps({
            "timestamp": report["timestamp"],
            "score": report["overall_score"],
            "grade": report["overall_grade"],
            "high": report["summary"]["high_severity"],
            "medium": report["summary"]["medium_severity"],
            "total": report["summary"]["total_findings"],
        }, ensure_ascii=False) + "\n")

    return REPORT_SUMMARY


# ─── 修复模块 ──────────────────────────────────────


def fix_identified_issues(report):
    """自动修复可修复的安全问题"""
    fixes = []

    for module_name, module_data in report["modules"].items():
        for finding in module_data["findings"]:
            ftype = finding.get("type", "")
            ffile = finding.get("file", "")

            # 1. 更新 config.py 中的硬编码默认值 → 改为仅环境变量
            if ("config.py" in ffile) and ("可疑密钥" in ftype or "硬编码" in ftype):
                filepath = os.path.join(PROJECT_DIRS[0], "config.py")
                if os.path.isfile(filepath):
                    fixes.append(f"⚠️  config.py 中有默认 API key 硬编码，建议删除默认值")
                    # 实际修改: 将 TUSHARE_TOKEN 的默认值设为空字符串
                    try:
                        with open(filepath, 'r') as f:
                            content = f.read()
                        old_default = re.search(
                            r"TUSHARE_TOKEN = os\.getenv\(\"TUSHARE_TOKEN\", \"([^\"]+)\"\)",
                            content
                        )
                        if old_default and len(old_default.group(1)) > 10:
                            new_line = content.replace(
                                old_default.group(0),
                                'TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")'
                            )
                            if new_line != content:
                                with open(filepath, 'w') as f:
                                    f.write(new_line)
                                fixes.append(f"✅  config.py: 已移除硬编码 TUSHARE_TOKEN 默认值")
                    except Exception as e:
                        fixes.append(f"❌  修复失败: {e}")

    return fixes


# ─── 主入口 ────────────────────────────────────────


def main():
    import argparse
    parser = argparse.ArgumentParser(description="🧼 舒肤佳安全守卫 — 信息系统安全巡检")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--fix", "-f", action="store_true", help="尝试自动修复")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出报告路径")
    args = parser.parse_args()

    print("🧼 舒肤佳安全守卫 — 开始全面安全巡检...")
    print(f"   技能目录: {SKILLS_DIR}")
    print(f"   项目目录: {', '.join(PROJECT_DIRS)}\n")

    start = time.time()

    # 运行所有检测
    results = [
        scan_malware(),
        scan_secrets(),
        scan_decoupling(),
        scan_memory_leaks(),
        scan_data_governance(),
        scan_security_vulns(),
    ]

    report = generate_report(results)
    elapsed = time.time() - start

    # 自动修复
    if args.fix:
        print("\n🔧 自动修复阶段...")
        fixes = fix_identified_issues(report)
        if fixes:
            for f in fixes:
                print(f"  {f}")
        else:
            print("  ✅ 无需自动修复")
        print()

    print_report(report, verbose=args.verbose)
    report_path = save_report(report)

    print(f"  耗时: {elapsed:.1f}s")
    print(f"  报告已保存: {report_path}")

    # 输出 JSON 给 cron 消费
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  报告另存为: {args.output}")

    # JSON 输出到 stdout 便于外部解析
    print()
    print(json.dumps({"score": report["overall_score"],
                       "grade": report["overall_grade"],
                       "high": report["summary"]["high_severity"],
                       "medium": report["summary"]["medium_severity"],
                       "total": report["summary"]["total_findings"]}))

    # 返回退出码
    if report["summary"]["high_severity"] > 0:
        sys.exit(2)
    elif report["summary"]["medium_severity"] > 5:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
