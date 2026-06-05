"""
🧼 舒肤佳安全守卫 — 配置常量 & 特征库
"""
import os

# ─── 路径配置 ──────────────────────────────────────────
HERMES_HOME = os.path.expanduser("~/.hermes")
SKILLS_DIR = os.path.join(HERMES_HOME, "skills")
PLUGINS_DIR = os.path.join(HERMES_HOME, "plugins")
CRON_DIR = os.path.join(HERMES_HOME, "cron")
CONFIG_PATH = os.path.join(HERMES_HOME, "config.yaml")
ENV_PATH = os.path.join(HERMES_HOME, ".env")
PROJECT_DIRS = ["/root/shi-mi-dashboard"]
REPORT_DIR = os.path.join(HERMES_HOME, "cache", "security")

# ─── 恶意代码特征 ─────────────────────────────────
MALICIOUS_PATTERNS = [
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
    (r"open\(.*['\"].*\.(pem|key|env|token|secret)['\"]", "可疑: 读取敏感文件"),
    (r"requests?\.(get|post|put)\(.*['\"](https?://\d+\.\d+\.\d+\.\d+)", "可疑: 外发到纯 IP"),
    (r"SMTP|smtplib\.SMTP", "可疑: SMTP 邮件外发"),
    (r"shutil\.(rmtree|remove).*['\"].*\.(py|yaml|json|db)", "危险: 删除项目文件"),
    (r"os\.(remove|unlink).*['\"].*\.(py|yaml|json|db)", "危险: 删除项目文件"),
]

# ─── 密钥硬编码特征 ─────────────────────────────────
SECRET_PATTERNS = [
    (r"(?i)(api_key|apikey|token|secret|password|passwd)\s*[=:]\s*['\"](?![A-Z_]{16,})[a-zA-Z0-9_\-\.\/]{16,}['\"]", "API Key/Token 硬编码"),
    (r"(?i)(tushare_token|openai_key|anthropic_key|deepseek_key)\s*[=:]\s*['\"](?![A-Z_]{8,})[a-zA-Z0-9_\-\.]{8,}['\"]", "服务 Token 硬编码"),
    (r"(?i)(access_key|secret_key|private_key|auth_token)\s*[=:]\s*['\"](?![A-Z_]{16,})[a-zA-Z0-9_\-\.\/\+]{16,}['\"]", "访问密钥硬编码"),
    (r"(?i)(jwt_secret|jwt_key|auth_secret)\s*[=:]\s*['\"](?![A-Z_]{8,})[a-zA-Z0-9_\-\.]{8,}['\"]", "JWT 密钥硬编码"),
    (r"=\s*['\"](?![A-Z_]{32,})[A-Za-z0-9_\-\.]{32,}['\"]", "长随机字符串（可疑密钥）"),
]

# ─── 内存泄漏风险特征 ──────────────────────────────
MEMORY_LEAK_PATTERNS = [
    (r"while\s+True\s*:.*read", "无限循环读取（可能OOM）"),
    (r"open\(.*\)\s*$", "open 未用 with/close（资源泄漏）"),
]

# ─── JWT/CORS/HTTPS 安全缺陷 ────────────────────
SECURITY_VULN_PATTERNS = [
    (r"CORS\(app.*origins?\s*=\s*['\"][*]['\"]", "高危: CORS 允许所有来源 (*)"),
    (r"app\.run\(.*debug\s*=\s*True", "高危: Flask debug 模式启用"),
    (r"app\.run\(.*host\s*=\s*['\"]0\.0\.0\.0['\"]", "注意: 绑定所有接口（需验证环境）"),
    (r"(?i)ssl_certificate\s*=\s*['\"]{0,2}$", "缺失: SSL 证书未配置"),
    (r"(?i)jwt.*=.*['\"][a-z0-9]{1,8}['\"]", "可疑: JWT secret 过短"),
]
