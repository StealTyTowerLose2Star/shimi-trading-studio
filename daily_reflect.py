"""
拾米交易工作室 - 每日22:00自我反思
读取当前灵魂(AGENT_PERSONA.md)状态，推送给用户评估笔记和画像
"""
import os
import subprocess
import time

BASE = os.path.dirname(__file__)
HERMES_DIR = os.path.expanduser("~/.hermes")
AGENT_PERSONA_PATH = os.path.join(HERMES_DIR, "AGENT_PERSONA.md")


def read_file(path):
    try:
        with open(path) as f:
            return f.read()
    except:
        return "(文件不存在)"


def send_msg(title, content):
    """将反思报告加入待发送队列（由 Agent 代发）"""
    log_path = os.path.join(BASE, "reflect.log")
    with open(log_path, "a") as f:
        f.write(f"\n{'='*60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {title}\n{'='*60}\n")
        f.write(content)
        f.write(f"\n{'='*60}\n\n")
    from message_queue import enqueue
    enqueue(title, content)
    return True, ""


def build_report():
    now = time.strftime("%Y-%m-%d %H:%M")
    date_str = time.strftime("%m-%d")
    soul_content = read_file(AGENT_PERSONA_PATH)

    report = f"""🌙 拾米每日反思 ({date_str})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔄 【1️⃣ 笔记反思】
请评估以下内容是否需要更新:
• 今日有无新的技术经验应加入"技术经验"?
• 今日有无新的业务逻辑/公式?
• 是否有重复或过时的条目?
• 数据源方面有无新发现?

→ 回复时告诉我如何修改，或保持现状。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🖼️ 【2️⃣ 画像反思】
请评估用户画像是否需优化:
• 今日有无新的偏好或要求?
• 有无新的判断逻辑/数据需求?
• 人物描述是否更精确?

→ 回复时告诉我如何修改，或保持现状。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🧠 【3️⃣ 灵魂反思 (当前) 】
{soul_content.strip()}

• 以上描述是否仍然准确?
• 有无新的核心要求需要补充?
• "潜力股挖掘者"定位是否仍然精确?

→ 回复时告诉我如何修改，或保持现状。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⏰ {now}
💡 回复告诉我需要更新的内容，或直接回复"保持现状"。"""

    return report


def send_report():
    msg = build_report()
    ok, err = send_msg(f"🌙 拾米反思 ({time.strftime('%m-%d')})", msg)

    if ok:
        print(f"[reflect] ✅ 反思报告已发送")
    else:
        print(f"[reflect] ❌ 发送失败: {err}")
        log_path = os.path.join(BASE, "reflect.log")
        with open(log_path, "a") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"发送时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"发送失败: {err}\n")
            f.write(f"{'='*60}\n")
            f.write(msg)
        print(f"[reflect] 内容已写入 {log_path}")


def run():
    send_report()


if __name__ == "__main__":
    run()
