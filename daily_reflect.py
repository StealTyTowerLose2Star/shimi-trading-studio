"""
拾米交易工作室 - 每日22:00自我反思
"""
import os, time
from message_queue import enqueue

BASE = os.path.dirname(__file__)
HERMES_DIR = os.path.expanduser("~/.hermes")
AGENT_PERSONA_PATH = os.path.join(HERMES_DIR, "AGENT_PERSONA.md")


def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return "(文件不存在)"


def send_msg(title, content):
    log_path = os.path.join(BASE, "reflect.log")
    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{'='*60}\n")
        f.write(content)
        f.write(f"\n{'='*60}\n\n")
    enqueue(title, content, priority="normal")
    print(f"[reflect] 已入队: {title}")
    return True, ""


def build_report():
    date_str = time.strftime("%m-%d")
    soul_content = read_file(AGENT_PERSONA_PATH)

    report = f"""📊 拾米反思 {date_str}

── 笔记 ──
今日有无新的技术经验应加入？
有无新的业务逻辑或公式？
数据源方面有无新发现？

── 画像 ──
今日有无新的偏好或要求？
人物描述是否更精确？

── 灵魂 ──
{soul_content.strip()}

以上描述是否仍然准确？回复告诉我需要更新的内容，或直接回复"保持现状"。
⏰ {time.strftime('%Y-%m-%d %H:%M')}"""

    return report


def send_report():
    msg = build_report()
    ok, err = send_msg(f"📊 拾米反思 {time.strftime('%m-%d')}", msg)
    if ok:
        print("[reflect] ✅ 反思报告已发送")
    else:
        print(f"[reflect] ❌ 发送失败: {err}")


def run():
    send_report()


if __name__ == "__main__":
    run()
