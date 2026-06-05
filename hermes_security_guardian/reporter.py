"""
🧼 舒肤佳安全守卫 — 报告生成 & 自动修复
"""
import json
import datetime
import os

from .patterns import PROJECT_DIRS, REPORT_DIR
import os

REPORT_SUMMARY = os.path.join(REPORT_DIR, "latest_summary.json")
REPORT_HISTORY = os.path.join(REPORT_DIR, "findings_history.jsonl")


def generate_report(all_results):
    """生成完整安全报告"""
    now = datetime.datetime.now()
    total_findings = sum(len(r.get("findings", [])) for r in all_results)
    high_sev = sum(1 for r in all_results for f in r.get("findings", []) if f.get("severity") == "high")
    medium_sev = sum(1 for r in all_results for f in r.get("findings", []) if f.get("severity") == "medium")
    modules_passed = sum(1 for r in all_results if r.get("status") == "PASS")

    score = max(10, min(100, 100 - high_sev * 15 - medium_sev * 2))

    report = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "report_id": now.strftime("SEC-%Y%m%d-%H%M%S"),
        "overall_score": score,
        "overall_grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D",
        "summary": {
            "modules_passed": modules_passed, "modules_total": len(all_results),
            "total_findings": total_findings, "high_severity": high_sev, "medium_severity": medium_sev,
        },
        "modules": {}, "priority_actions": [],
    }

    for result in all_results:
        mname = result.get("module", "unknown")
        report["modules"][mname] = {
            "status": result.get("status", "UNKNOWN"),
            "summary": result.get("summary", ""),
            "findings": result.get("findings", []),
        }
        for f in result.get("findings", []):
            if f.get("severity") == "high":
                report["priority_actions"].append(f"[高危] {f.get('type','')} → {f.get('file','')}:{f.get('line','')}")

    return report


def print_report(report, verbose=False):
    """打印报告到终端"""
    now = datetime.datetime.now()
    grade = report["overall_grade"]
    grade_icon = {"A": "🟢", "B": "🟡", "C": "🟠"}.get(grade, "🔴")
    s = report["summary"]

    print(f"\n{'='*60}")
    print(f"  🧼 舒肤佳安全守卫 — 安全巡检报告")
    print(f"  {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")
    print(f"\n  综合评分: {report['overall_score']}/100  {grade_icon} [{grade}]")
    print(f"  模块通过: {s['modules_passed']}/{s['modules_total']}")
    print(f"  高危: {s['high_severity']}  |  中危: {s['medium_severity']}  |  总计: {s['total_findings']}")
    print(f"\n{'─'*60}")

    for mname, mdata in report["modules"].items():
        icon = "✅" if mdata["status"] == "PASS" else "⚠️"
        print(f"  {icon} [{mdata['status']}] {mname}")
        print(f"     {mdata['summary']}")
        if verbose and mdata["findings"]:
            for f in mdata["findings"][:5]:
                sev_i = "🔴" if f.get("severity") == "high" else "🟡" if f.get("severity") == "medium" else "💡"
                print(f"     {sev_i} [{f.get('severity','info').upper()}] {f.get('type','')}")
                if f.get("file"):
                    line_s = f":{f.get('line')}" if f.get("line") else ""
                    print(f"        └─ {f.get('file')}{line_s}")
            if len(mdata["findings"]) > 5:
                print(f"     ... 还有 {len(mdata['findings'])-5} 项")

    if report["priority_actions"]:
        print(f"\n{'!'*60}\n  🚨 优先处理事项:")
        for action in report["priority_actions"][:10]:
            print(f"    • {action}")
        if len(report["priority_actions"]) > 10:
            print(f"    ... 还有 {len(report['priority_actions'])-10} 项")
    print(f"\n{'='*60}\n")


def save_report(report):
    """保存报告到文件"""
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(REPORT_SUMMARY, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    with open(REPORT_HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps({
            "timestamp": report["timestamp"], "score": report["overall_score"],
            "grade": report["overall_grade"], "high": report["summary"]["high_severity"],
            "medium": report["summary"]["medium_severity"], "total": report["summary"]["total_findings"],
        }, ensure_ascii=False) + "\n")
    return REPORT_SUMMARY


def fix_identified_issues(report):
    """自动修复可修复的安全问题"""
    fixes = []
    for module_data in report["modules"].values():
        for finding in module_data["findings"]:
            ftype = finding.get("type", "")
            ffile = finding.get("file", "")
            if ("config.py" in ffile) and ("可疑密钥" in ftype or "硬编码" in ftype):
                filepath = os.path.join(PROJECT_DIRS[0], "config.py")
                if os.path.isfile(filepath):
                    try:
                        with open(filepath, 'r') as f:
                            content = f.read()
                        import re
                        old = re.search(r'TUSHARE_TOKEN = os\.getenv\("TUSHARE_TOKEN", "([^"]+)"\)', content)
                        if old and len(old.group(1)) > 10:
                            new_content = content.replace(old.group(0),
                                'TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "")')
                            if new_content != content:
                                with open(filepath, 'w') as f:
                                    f.write(new_content)
                                fixes.append(f"✅ config.py: 已移除硬编码 TUSHARE_TOKEN 默认值")
                    except Exception as e:
                        fixes.append(f"❌ 修复失败: {e}")
    return fixes
