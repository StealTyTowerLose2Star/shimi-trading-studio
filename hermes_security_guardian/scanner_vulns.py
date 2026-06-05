"""
🧼 舒肤佳安全守卫 — 数据治理 & 安全漏洞扫描
"""
import os
import re

from .patterns import PROJECT_DIRS, SKILLS_DIR, SECURITY_VULN_PATTERNS
from .scanner_base import find_python_files, scan_file_for_patterns


def scan_data_governance():
    """🗄️ 检测 4: 数据治理 & 统一规范审查"""
    findings = []
    checked_files = 0

    for proj_dir in PROJECT_DIRS:
        data_dir = os.path.join(proj_dir, "data")
        if os.path.isdir(data_dir):
            data_files = [f for f in os.listdir(data_dir) if f.endswith('.py')]
            findings.append({
                "file": "data/", "severity": "info",
                "type": "数据层存在",
                "detail": f"data/ 目录含 {len(data_files)} 个模块" if data_files else "data/ 目录无 Python 模块",
            })
        else:
            findings.append({"file": proj_dir, "severity": "warning",
                             "type": "缺少统一数据层", "detail": "无 data/ 目录"})

        config_file = os.path.join(proj_dir, "config.py")
        if os.path.isfile(config_file):
            checked_files += 1
            with open(config_file, 'r') as f:
                has_valid = "def validate" in f.read()
            findings.append({"file": "config.py", "severity": "info",
                             "type": "配置验证函数存在" if has_valid else "建议增加配置验证",
                             "detail": "config.py 含 validate()" if has_valid else "config.py 无 validate()"})

        for name, label in [("cache.py", "缓存统一管理"), ("db.py", "数据库统一层存在")]:
            fpath = os.path.join(proj_dir, name)
            if os.path.isfile(fpath):
                checked_files += 1
                findings.append({"file": name, "severity": "info",
                                 "type": label, "detail": f"{name} 统一管理"})

    if os.path.isdir(SKILLS_DIR):
        for sf in find_python_files(SKILLS_DIR):
            checked_files += 1
            try:
                with open(sf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                if 'tushare' in content or 'pro_api' in content:
                    findings.append({
                        "file": os.path.relpath(sf, SKILLS_DIR), "severity": "info",
                        "type": "Skill直接引用数据源",
                        "detail": "建议通过统一数据层获取",
                    })
            except Exception:
                pass

    return {
        "module": "data_governance", "status": "PASS",
        "summary": f"检查 {checked_files} 个数据相关文件",
        "findings": findings, "checked_files": checked_files,
    }


def scan_security_vulns():
    """🛡️ 检测 6: HTTPS/CORS/JWT 安全漏洞"""
    findings = []
    checked_files = 0

    for proj_dir in PROJECT_DIRS:
        for pf in find_python_files(proj_dir):
            checked_files += 1
            findings.extend(scan_file_for_patterns(pf, SECURITY_VULN_PATTERNS))

        # CORS 专项检查
        backend = os.path.join(proj_dir, "backend.py")
        if os.path.isfile(backend):
            with open(backend, 'r') as f:
                content = f.read()
            cors = re.search(r'CORS\(([^)]*)\)', content)
            if cors:
                sev = "high" if '*' in cors.group(1) else "info"
                findings.append({"file": "backend.py", "severity": sev,
                                 "type": "CORS 配置过松" if sev == "high" else "CORS 已配置白名单",
                                 "detail": cors.group()[:100]})

        # Nginx SSL
        nginx_dir = os.path.join(proj_dir, "nginx")
        if os.path.isdir(nginx_dir):
            for nf in os.listdir(nginx_dir):
                nginx_file = os.path.join(nginx_dir, nf)
                checked_files += 1
                with open(nginx_file, 'r') as f:
                    content = f.read()
                if 'ssl_certificate' not in content:
                    findings.append({"file": f"nginx/{nf}", "severity": "medium",
                                     "type": "Nginx 无 SSL 配置", "detail": "未配置 ssl_certificate"})
                if 'return 301' not in content and 'rewrite' not in content:
                    findings.append({"file": f"nginx/{nf}", "severity": "medium",
                                     "type": "HTTP→HTTPS 重定向缺失", "detail": "未配置 80→443 自动跳转"})
                missing = [h for h in ['X-Content-Type-Options', 'X-Frame-Options',
                                        'Content-Security-Policy', 'Strict-Transport-Security']
                           if h not in content]
                if missing:
                    findings.append({"file": f"nginx/{nf}", "severity": "info",
                                     "type": "安全响应头缺失", "detail": f"缺失: {', '.join(missing)}"})

        # Docker USER
        dockerfile = os.path.join(proj_dir, "Dockerfile")
        if os.path.isfile(dockerfile):
            checked_files += 1
            if 'USER' not in open(dockerfile).read():
                findings.append({"file": "Dockerfile", "severity": "medium",
                                 "type": "Docker 未设置非 root 用户",
                                 "detail": "建议添加 USER nobody 或创建专用用户"})

    return {
        "module": "security_vuln_scan",
        "status": "PASS" if not [f for f in findings if f.get('severity') == 'high'] else "WARN",
        "summary": f"检查 {checked_files} 个配置文件，发现 {len(findings)} 个安全问题",
        "findings": findings, "checked_files": checked_files,
    }
