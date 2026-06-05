"""
🧼 舒肤佳安全守卫 — 基础扫描工具函数
"""
import os
import re


def find_python_files(directory):
    """递归查找目录下所有 .py 文件"""
    py_files = []
    if not os.path.isdir(directory):
        return py_files
    for root, dirs, files in os.walk(directory):
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
