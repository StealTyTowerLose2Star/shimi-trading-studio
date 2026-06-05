"""
🧼 舒肤佳安全守卫 — 恶意代码 & 密钥泄露扫描
"""
from .patterns import (
    SKILLS_DIR, PLUGINS_DIR, CRON_DIR, PROJECT_DIRS, ENV_PATH,
    MALICIOUS_PATTERNS, SECRET_PATTERNS,
)
from .scanner_base import find_python_files, find_md_json_yaml_files, scan_file_for_patterns
import os


def scan_malware():
    """🦠 检测 1: 恶意代码/木马扫描"""
    findings = []
    scanned_files = 0

    for d in [SKILLS_DIR, PLUGINS_DIR]:
        if os.path.isdir(d):
            for pf in find_python_files(d):
                scanned_files += 1
                findings.extend(scan_file_for_patterns(pf, MALICIOUS_PATTERNS))

    if os.path.isdir(CRON_DIR):
        for root, _, files in os.walk(CRON_DIR):
            for f in files:
                if f.endswith(('.sh', '.py')):
                    scanned_files += 1
                    findings.extend(scan_file_for_patterns(os.path.join(root, f), MALICIOUS_PATTERNS))

    return {
        "module": "malware_scan",
        "status": "PASS" if not findings else "WARN",
        "summary": f"扫描 {scanned_files} 个文件，发现 {len(findings)} 个异常",
        "findings": findings,
        "scanned_files": scanned_files,
    }


def scan_secrets():
    """🔑 检测 5: 密钥硬编码/敏感文件泄露"""
    findings = []
    scanned_files = 0

    for proj_dir in PROJECT_DIRS:
        for pf in find_python_files(proj_dir):
            scanned_files += 1
            findings.extend(scan_file_for_patterns(pf, SECRET_PATTERNS))
        for cf in find_md_json_yaml_files(proj_dir):
            scanned_files += 1
            findings.extend(scan_file_for_patterns(cf, SECRET_PATTERNS))

    if os.path.isdir(SKILLS_DIR):
        for root, dirs, files in os.walk(SKILLS_DIR):
            dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
            for f in files:
                if f.endswith('.py'):
                    scanned_files += 1
                    findings.extend(scan_file_for_patterns(os.path.join(root, f), SECRET_PATTERNS))

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
                                "file": ENV_PATH, "line": 0, "severity": "info",
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
