"""
🧼 舒肤佳安全守卫 — 主入口
"""
import time
import sys
import json
import argparse

from .patterns import SKILLS_DIR, PROJECT_DIRS
from .scanner_security import scan_malware, scan_secrets
from .scanner_decoupling import scan_decoupling, scan_memory_leaks
from .scanner_vulns import scan_data_governance, scan_security_vulns
from .reporter import generate_report, print_report, save_report, fix_identified_issues


def main():
    parser = argparse.ArgumentParser(description="🧼 舒肤佳安全守卫 — 信息系统安全巡检")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--fix", "-f", action="store_true", help="尝试自动修复")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出报告路径")
    args = parser.parse_args()

    print("🧼 舒肤佳安全守卫 — 开始全面安全巡检...")
    print(f"   技能目录: {SKILLS_DIR}")
    print(f"   项目目录: {', '.join(PROJECT_DIRS)}\n")

    start = time.time()
    results = [
        scan_malware(), scan_secrets(),
        scan_decoupling(), scan_memory_leaks(),
        scan_data_governance(), scan_security_vulns(),
    ]
    report = generate_report(results)
    elapsed = time.time() - start

    if args.fix:
        print("\n🔧 自动修复阶段...")
        for f in fix_identified_issues(report) or ["  ✅ 无需自动修复"]:
            print(f"  {f}")
        print()

    print_report(report, verbose=args.verbose)
    report_path = save_report(report)
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  报告已保存: {report_path}")

    if args.output:
        with open(args.output, 'w') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"  报告另存为: {args.output}")

    print()
    print(json.dumps({"score": report["overall_score"], "grade": report["overall_grade"],
                       "high": report["summary"]["high_severity"],
                       "medium": report["summary"]["medium_severity"],
                       "total": report["summary"]["total_findings"]}))

    if report["summary"]["high_severity"] > 0:
        sys.exit(2)
    elif report["summary"]["medium_severity"] > 5:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
