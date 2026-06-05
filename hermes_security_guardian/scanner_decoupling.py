"""
🧼 舒肤佳安全守卫 — 模块解耦 & 内存泄漏检测
"""
import ast
import os
import re

from .patterns import PROJECT_DIRS, MEMORY_LEAK_PATTERNS
from .scanner_base import find_python_files, scan_file_for_patterns


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
                findings.append({"file": rel_path, "severity": "warning",
                                 "type": "语法解析错误", "detail": "无法解析 AST"})
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
                    if neighbor not in graph:
                        continue
                    if color[neighbor] == GRAY:
                        cycle_start = path.index(neighbor)
                        cycles.append(" → ".join(path[cycle_start:] + [neighbor]))
                    elif color[neighbor] == WHITE:
                        dfs(neighbor, path + [neighbor])
                color[node] = BLACK
            for node in graph:
                if color[node] == WHITE:
                    dfs(node, [node])
            return cycles

        for c in find_cycles(import_graph):
            findings.append({"severity": "high", "type": "循环依赖", "detail": c})

        # 超大模块检测
        for pf in py_files:
            rel_path = os.path.relpath(pf, proj_dir)
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            lines = content.count('\n')
            if lines > 1000:
                findings.append({"file": rel_path, "severity": "high",
                                 "type": "模块严重过大", "detail": f"{lines} 行（强烈建议拆分）"})
            elif lines > 500:
                findings.append({"file": rel_path, "severity": "medium",
                                 "type": "模块过大", "detail": f"{lines} 行（建议 < 500 行，考虑拆分）"})

    return {
        "module": "decoupling_check",
        "status": "PASS" if not [f for f in findings if f.get('severity') in ('high', 'warning')] else "WARN",
        "summary": f"检测 {len(import_graph)} 个模块，发现 {len(findings)} 个耦合问题",
        "findings": findings, "modules_scanned": len(import_graph),
    }


def scan_memory_leaks():
    """💾 检测 3: 内存泄漏风险 & 代码合规性"""
    findings = []
    scanned_files = 0

    for proj_dir in PROJECT_DIRS:
        for pf in find_python_files(proj_dir):
            scanned_files += 1
            findings.extend(scan_file_for_patterns(pf, MEMORY_LEAK_PATTERNS))

        for pf in find_python_files(proj_dir):
            try:
                with open(pf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            large_comprehensions = re.findall(r'\[.*for.*in.*for.*in.*\]', content)
            if large_comprehensions:
                findings.append({
                    "file": os.path.relpath(pf, proj_dir), "severity": "info",
                    "type": "嵌套列表推导",
                    "detail": f"发现 {len(large_comprehensions)} 处多层for推导，大数据下可能OOM",
                })

    return {
        "module": "memory_leak_scan",
        "status": "PASS" if not findings else "WARN",
        "summary": f"扫描 {scanned_files} 个文件，发现 {len(findings)} 个潜在风险",
        "findings": findings, "scanned_files": scanned_files,
    }
